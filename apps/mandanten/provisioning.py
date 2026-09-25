"""Bereitstellungs-Logik fuer eine Mandanten-Instanz (Ausbaustufe 1 + 1b: Feuerix selbst inkl. Reverse-Proxy-
Eintrag, ohne OpenSlides/Paperless - siehe README).

Ablauf (jeder Schritt einzeln aufrufbar und wiederholbar, damit ein Fehler mittendrin nicht die ganze
Instanz in einem unklaren Zustand zuruecklaesst):

1. env_erzeugen()            - prueft die Eingaben, generiert Geheimnisse (einmalig), schreibt die .env-Datei
2. stack_hochfahren()        - startet den Docker-Compose-Stack unter einem eigenen Projektnamen
3. auf_bereitschaft_warten() - wartet, bis die Weboberflaeche antwortet (= Migration/Ersteinrichtung durch,
                               siehe Feuerix' eigenes entrypoint.sh)
4. proxy.proxy_aktualisieren() - traegt die Domain im Reverse Proxy (Caddy) ein (nur mit CADDY_ADMIN_URL)

bereitstellen() fuehrt alle Schritte nacheinander aus und aktualisiert den Status entsprechend,
abbauen() stoppt eine Instanz wieder (optional inkl. Daten) und nimmt sie aus dem Reverse Proxy.
"""
import secrets
import string
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ValidationError

from . import proxy
from .models import MandantInstanz
from .validatoren import gueltige_domain, keine_env_sonderzeichen


class BereitstellungsFehler(Exception):
    pass


def eingaben_pruefen(mandant):
    """Die Werte gehen ungeprueft in .env und Caddyfile - auch wenn der Datensatz per objects.create()
    (ohne Formular-Validierung) angelegt wurde, darf keine Injection durchrutschen."""
    try:
        gueltige_domain(mandant.domain)
        for wert in (mandant.verein_name, mandant.admin_benutzername, mandant.admin_email):
            keine_env_sonderzeichen(wert)
    except ValidationError as e:
        raise BereitstellungsFehler(f"Ungültige Eingabe bei '{mandant.kuerzel}': {'; '.join(e.messages)}")


def _zufallswert(laenge=50):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(laenge))


def naechster_freier_port():
    belegt = set(MandantInstanz.objects.exclude(web_port__isnull=True).values_list("web_port", flat=True))
    port = settings.WEB_PORT_BASIS
    while port in belegt:
        port += 1
    return port


def instanz_verzeichnis(mandant):
    pfad = Path(settings.INSTANZEN_VERZEICHNIS) / mandant.kuerzel
    pfad.mkdir(parents=True, exist_ok=True)
    return pfad


_ENV_VORLAGE = """\
SECRET_KEY={secret_key}
FIELD_ENCRYPTION_KEY={field_encryption_key}
POSTGRES_PASSWORD={postgres_passwort}
POSTGRES_DB=mandant_{kuerzel_unterstrich}
POSTGRES_USER=mandant_{kuerzel_unterstrich}

DEBUG=0
ALLOWED_HOSTS={domain}
CSRF_TRUSTED_ORIGINS=https://{domain}
HTTPS=1
WEB_PORT={web_port}

VEREIN_DOMAIN={domain}
ACME_EMAIL={admin_email}
OPENSLIDES_DOMAIN=
PAPERLESS_DOMAIN=
VEREIN_TLS_CERT=
VEREIN_TLS_KEY=
OPENSLIDES_TLS_CERT=
OPENSLIDES_TLS_KEY=
PAPERLESS_TLS_CERT=
PAPERLESS_TLS_KEY=

ADMIN_USER={admin_benutzername}
ADMIN_PASSWORD={admin_passwort}
ADMIN_EMAIL={admin_email}
VEREIN_NAME={verein_name}
"""


def env_erzeugen(mandant):
    """Erzeugt (beim ersten Aufruf) die Geheimnisse und schreibt die .env-Datei der Instanz. Bereits
    vorhandene Geheimnisse werden bei einem erneuten Aufruf (z. B. nach einem fehlgeschlagenen spaeteren
    Schritt) wiederverwendet, nie neu erzeugt - sonst waere eine schon angelegte Datenbank mit einer neuen
    .env nicht mehr kompatibel."""
    eingaben_pruefen(mandant)
    aenderungen = []
    if not mandant.secret_key:
        mandant.secret_key = _zufallswert(50)
        aenderungen.append("secret_key")
    if not mandant.field_encryption_key:
        mandant.field_encryption_key = Fernet.generate_key().decode()
        aenderungen.append("field_encryption_key")
    if not mandant.postgres_passwort:
        mandant.postgres_passwort = _zufallswert(32)
        aenderungen.append("postgres_passwort")
    if not mandant.admin_passwort:
        mandant.admin_passwort = _zufallswert(16)
        aenderungen.append("admin_passwort")
    if not mandant.web_port:
        mandant.web_port = naechster_freier_port()
        aenderungen.append("web_port")
    if aenderungen:
        mandant.save(update_fields=aenderungen)

    inhalt = _ENV_VORLAGE.format(
        secret_key=mandant.secret_key, field_encryption_key=mandant.field_encryption_key,
        postgres_passwort=mandant.postgres_passwort, kuerzel_unterstrich=mandant.kuerzel.replace("-", "_"),
        domain=mandant.domain, web_port=mandant.web_port, admin_email=mandant.admin_email,
        admin_benutzername=mandant.admin_benutzername, admin_passwort=mandant.admin_passwort,
        verein_name=mandant.verein_name)
    env_pfad = instanz_verzeichnis(mandant) / ".env"
    env_pfad.write_text(inhalt, encoding="utf-8")
    mandant.protokollieren(f".env erzeugt unter {env_pfad}")
    return env_pfad


def compose_override_schreiben(mandant, env_pfad):
    """Feuerix' docker-compose.yml hat `env_file: .env` bei web/worker - das loest relativ zum Compose-File
    auf (also im Feuerix-Checkout), NICHT ueber --env-file (das betrifft nur die Variablen-Interpolation).
    Diese kleine Override-Datei biegt env_file je Mandant auf dessen eigene .env um (`!override` ersetzt die
    Liste statt sie zu ergaenzen; erfordert Docker Compose >= 2.24)."""
    pfad = instanz_verzeichnis(mandant) / "compose.mandant.yml"
    env = env_pfad.resolve().as_posix()
    pfad.write_text(
        "services:\n"
        "  web:\n"
        f"    env_file: !override\n      - \"{env}\"\n"
        "  worker:\n"
        f"    env_file: !override\n      - \"{env}\"\n", encoding="utf-8")
    return pfad




def _compose_basis(mandant):
    """Gemeinsame Argumente fuer alle docker-compose-Aufrufe dieser Instanz (hoch- und abbauen muessen
    dieselben Dateien/Variablen verwenden)."""
    env_pfad = instanz_verzeichnis(mandant) / ".env"
    if not env_pfad.exists():
        raise BereitstellungsFehler("Keine .env vorhanden - zuerst env_erzeugen() aufrufen.")
    compose_datei = Path(settings.FEUERIX_REPO_PFAD) / "docker-compose.yml"
    if not compose_datei.exists():
        raise BereitstellungsFehler(f"docker-compose.yml nicht gefunden unter {compose_datei} - "
                                    "FEUERIX_REPO_PFAD prüfen.")
    override_datei = compose_override_schreiben(mandant, env_pfad)
    return ["docker", "compose", "-p", mandant.docker_projekt, "--env-file", str(env_pfad),
            "-f", str(compose_datei), "-f", str(override_datei)]


def _docker(befehl, was, timeout=900):
    try:
        ergebnis = subprocess.run(befehl, cwd=settings.FEUERIX_REPO_PFAD, capture_output=True, text=True,
                                  timeout=timeout)
    except subprocess.TimeoutExpired:
        raise BereitstellungsFehler(f"{was}: Zeitüberschreitung nach {timeout}s.")
    except OSError as e:
        raise BereitstellungsFehler(f"{was}: docker konnte nicht gestartet werden ({e}).")
    if ergebnis.returncode != 0:
        raise BereitstellungsFehler(f"{was} fehlgeschlagen:\n{ergebnis.stderr[-2000:]}")


def stack_hochfahren(mandant):
    """Startet den Feuerix-Docker-Compose-Stack fuer diesen Mandanten unter einem eigenen Projektnamen -
    nutzt die docker-compose.yml aus dem konfigurierten Feuerix-Quellcode-Checkout direkt (kein Kopieren),
    damit Aenderungen an dieser Datei automatisch fuer neue Bereitstellungen gelten."""
    _docker(_compose_basis(mandant) + ["up", "-d", "--build"], "docker compose up")
    mandant.protokollieren(f"Docker-Compose-Stack '{mandant.docker_projekt}' gestartet.")


def stack_abbauen(mandant, daten_loeschen=False):
    """Stoppt und entfernt die Container; mit daten_loeschen auch die Volumes (Datenbank, Medien) -
    unwiderruflich."""
    befehl = _compose_basis(mandant) + ["down"] + (["-v"] if daten_loeschen else [])
    _docker(befehl, "docker compose down", timeout=300)
    mandant.protokollieren(f"Docker-Compose-Stack '{mandant.docker_projekt}' abgebaut"
                           + (" (inkl. Daten)." if daten_loeschen else " (Daten erhalten)."))


def auf_bereitschaft_warten(mandant, timeout_sekunden=180, intervall_sekunden=5):
    """Wartet, bis die Weboberflaeche antwortet - das bedeutet Migration und Ersteinrichtung sind bereits
    durchgelaufen (siehe Feuerix' entrypoint.sh: migrate + ersteinrichtung laufen dort automatisch beim
    allerersten Start)."""
    url = f"http://127.0.0.1:{mandant.web_port}/login/"
    ablauf = time.monotonic() + timeout_sekunden
    letzter_fehler = None
    # Simuliert den Reverse Proxy: die Instanz erlaubt nur ihre eigene Domain (ALLOWED_HOSTS) und leitet
    # Anfragen ohne "X-Forwarded-Proto: https" auf HTTPS um (SECURE_SSL_REDIRECT) - ohne diese Header
    # antwortet sie auf die direkte Abfrage von 127.0.0.1 mit 400 bzw. einer Weiterleitung.
    anfrage = urllib.request.Request(url, headers={"Host": mandant.domain, "X-Forwarded-Proto": "https"})
    while time.monotonic() < ablauf:
        try:
            with urllib.request.urlopen(anfrage, timeout=5) as antwort:
                if antwort.status == 200:
                    mandant.protokollieren("Weboberfläche antwortet - Instanz ist bereit.")
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            letzter_fehler = e
        time.sleep(intervall_sekunden)
    raise BereitstellungsFehler(
        f"Zeitüberschreitung: {url} antwortet nach {timeout_sekunden}s nicht ({letzter_fehler}).")


def _proxy_nachziehen(mandant):
    try:
        if proxy.proxy_aktualisieren():
            mandant.protokollieren("Reverse Proxy aktualisiert.")
        else:
            mandant.protokollieren("Reverse-Proxy-Automatisierung aus (CADDY_ADMIN_URL leer) - "
                                   "Domain bitte von Hand eintragen.")
    except proxy.ProxyFehler as e:
        raise BereitstellungsFehler(f"Reverse Proxy: {e}")


def bereitstellen(mandant):
    """Fuehrt die komplette Bereitstellung aus und aktualisiert den Status. Erneutes Aufrufen ist gefahrlos
    (Geheimnisse bleiben, docker compose up aendert nichts Unveraendertes)."""
    mandant.status = "wird_bereitgestellt"
    mandant.save(update_fields=["status"])
    try:
        env_erzeugen(mandant)
        stack_hochfahren(mandant)
        auf_bereitschaft_warten(mandant)
        # Erst jetzt "laeuft": der Proxy nimmt nur Instanzen mit diesem Status auf.
        mandant.status = "laeuft"
        mandant.save(update_fields=["status"])
        _proxy_nachziehen(mandant)
    except BereitstellungsFehler as e:
        mandant.status = "fehlgeschlagen"
        mandant.save(update_fields=["status"])
        mandant.protokollieren(f"Fehler: {e}")
        raise


def abbauen(mandant, daten_loeschen=False):
    """Stoppt die Instanz, nimmt sie aus dem Reverse Proxy und setzt den Status auf "pausiert" (Daten
    erhalten) bzw. "geloescht" (mit daten_loeschen)."""
    try:
        stack_abbauen(mandant, daten_loeschen=daten_loeschen)
        mandant.status = "geloescht" if daten_loeschen else "pausiert"
        mandant.save(update_fields=["status"])
        _proxy_nachziehen(mandant)
    except BereitstellungsFehler as e:
        mandant.protokollieren(f"Fehler beim Abbau: {e}")
        raise
