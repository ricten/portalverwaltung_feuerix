"""Bereitstellungs-Logik fuer eine Mandanten-Instanz: Feuerix selbst (Ausbaustufe 1), Reverse-Proxy-Eintrag
(1b) und optionale Zusatzdienste Paperless-ngx / OpenSlides (Ausbaustufe 2).

Ablauf von bereitstellen() (jeder Schritt einzeln aufrufbar und wiederholbar, damit ein Fehler mittendrin
die Instanz nicht in einem unklaren Zustand zuruecklaesst):

1. env_erzeugen()            - prueft die Eingaben, generiert Geheimnisse (einmalig), schreibt die .env-Datei
2. stack_hochfahren()        - startet den Docker-Compose-Stack unter einem eigenen Projektnamen
3. auf_bereitschaft_warten() - wartet, bis die Weboberflaeche antwortet (= Migration/Ersteinrichtung durch,
                               siehe Feuerix' eigenes entrypoint.sh)
4. Zusatzdienste             - je nach gesetzter Domain: Paperless (paperless.py), OpenSlides (openslides.py)
5. proxy.proxy_aktualisieren() - traegt alle Domains im Reverse Proxy (Caddy) ein (nur mit CADDY_ADMIN_URL)

abbauen() stoppt eine Instanz samt Zusatzdiensten wieder (optional inkl. Daten) und nimmt sie aus dem Proxy.
"""
import time
import urllib.error
import urllib.request
from pathlib import Path

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.text import slugify

from . import basis, dienstversionen, openslides, paperless, proxy
from .basis import BereitstellungsFehler, docker, instanz_verzeichnis
from .validatoren import gueltige_domain, keine_env_sonderzeichen


def eingaben_pruefen(mandant):
    """Die Werte gehen ungeprueft in .env und Caddyfile - auch wenn der Datensatz per objects.create()
    (ohne Formular-Validierung) angelegt wurde, darf keine Injection durchrutschen."""
    try:
        gueltige_domain(mandant.domain)
        for zusatz in (mandant.paperless_domain, mandant.openslides_domain):
            if zusatz:
                gueltige_domain(zusatz)
        for wert in (mandant.verein_name, mandant.admin_benutzername, mandant.admin_email):
            keine_env_sonderzeichen(wert)
    except ValidationError as e:
        raise BereitstellungsFehler(f"Ungültige Eingabe bei '{mandant.kuerzel}': {'; '.join(e.messages)}")
    domains = [d for d in (mandant.domain, mandant.paperless_domain, mandant.openslides_domain) if d]
    if len(domains) != len(set(domains)):
        raise BereitstellungsFehler(f"Ungültige Eingabe bei '{mandant.kuerzel}': Feuerix, Paperless und "
                                    "OpenSlides brauchen jeweils eine eigene Domain.")


def naechster_freier_port():
    return basis.naechster_freier_port("web_port", settings.WEB_PORT_BASIS)


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
OPENSLIDES_DOMAIN={openslides_domain}
PAPERLESS_DOMAIN={paperless_domain}
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
        mandant.secret_key = basis.zufallswert(50)
        aenderungen.append("secret_key")
    if not mandant.field_encryption_key:
        mandant.field_encryption_key = Fernet.generate_key().decode()
        aenderungen.append("field_encryption_key")
    if not mandant.postgres_passwort:
        mandant.postgres_passwort = basis.zufallswert(32)
        aenderungen.append("postgres_passwort")
    if not mandant.admin_passwort:
        mandant.admin_passwort = basis.zufallswert(16)
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
        verein_name=mandant.verein_name, openslides_domain=mandant.openslides_domain,
        paperless_domain=mandant.paperless_domain)
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
    env = basis.yaml_pfad(env_pfad)
    pfad.write_text(
        "services:\n"
        "  web:\n"
        f"    env_file: !override\n      - {env}\n"
        "  worker:\n"
        f"    env_file: !override\n      - {env}\n", encoding="utf-8")
    return pfad


def _compose_basis(mandant):
    """Gemeinsame Argumente fuer alle docker-compose-Aufrufe der Feuerix-Instanz (hoch- und abbauen muessen
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


def stack_bauen(mandant):
    """Baut nur das Image mit dem aktuellen Code des Checkouts (ohne Container zu starten oder zu aendern) -
    Update fuer eine gestoppte Instanz: beim naechsten Start (`up -d`) werden die Container aus dem neuen Image
    angelegt, die Migrationen laufen dabei automatisch."""
    docker(_compose_basis(mandant) + ["build"], "docker compose build")
    mandant.protokollieren(f"Image fuer '{mandant.docker_projekt}' neu gebaut (Instanz bleibt gestoppt).")


def stack_hochfahren(mandant):
    """Startet den Feuerix-Docker-Compose-Stack fuer diesen Mandanten unter einem eigenen Projektnamen -
    nutzt die docker-compose.yml aus dem konfigurierten Feuerix-Quellcode-Checkout direkt (kein Kopieren),
    damit Aenderungen an dieser Datei automatisch fuer neue Bereitstellungen gelten."""
    docker(_compose_basis(mandant) + ["up", "-d", "--build"], "docker compose up")
    mandant.protokollieren(f"Docker-Compose-Stack '{mandant.docker_projekt}' gestartet.")


_STEUERN = {"stop": ["stop"], "start": ["up", "-d"], "restart": ["restart"]}


def stack_steuern(mandant, aktion):
    """Stoppt, startet (ohne Neubau) oder startet neu - nur die Container, nichts wird entfernt."""
    docker(_compose_basis(mandant) + _STEUERN[aktion], f"docker compose {aktion}", timeout=300)
    mandant.protokollieren(f"Stack '{mandant.docker_projekt}': {aktion}.")


def stack_abbauen(mandant, daten_loeschen=False):
    """Stoppt und entfernt die Container; mit daten_loeschen auch die Volumes (Datenbank, Medien) -
    unwiderruflich."""
    befehl = _compose_basis(mandant) + ["down"] + (["-v"] if daten_loeschen else [])
    docker(befehl, "docker compose down", timeout=300)
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


def zusatzdienste_bereitstellen(mandant):
    """Paperless/OpenSlides je nach gesetzter Domain - erst NACH der laufenden Feuerix-Instanz, da die
    Anbindung dort eingetragen wird."""
    if paperless.aktiv(mandant):
        basis.fortschritt(mandant, 50, "Paperless: Dienste starten")
        paperless.env_erzeugen(mandant)
        paperless.hochfahren(mandant)
        basis.fortschritt(mandant, 60, "Paperless: warten, bis es antwortet")
        paperless.auf_bereitschaft_warten(mandant)
        basis.fortschritt(mandant, 70, "Paperless: Anbindung in Feuerix eintragen")
        paperless.in_feuerix_eintragen(mandant, paperless.token_holen(mandant), _compose_basis(mandant))
    if openslides.aktiv(mandant):
        openslides.bereitstellen(mandant, _compose_basis(mandant))


def _proxy_nachziehen(mandant):
    try:
        if proxy.proxy_aktualisieren():
            mandant.protokollieren("Reverse Proxy aktualisiert.")
        else:
            mandant.protokollieren("Reverse-Proxy-Automatisierung aus (CADDY_ADMIN_URL leer) - "
                                   "Domains bitte von Hand eintragen.")
    except proxy.ProxyFehler as e:
        raise BereitstellungsFehler(f"Reverse Proxy: {e}")


def proxy_nachziehen_best_effort(mandant):
    """Fuer Fehlerpfade/Speichern: Proxy aktualisieren (Wartungsseite), ohne einen Fehler zu verdecken."""
    try:
        proxy.proxy_aktualisieren()
    except proxy.ProxyFehler as e:
        mandant.protokollieren(f"Reverse Proxy konnte nicht aktualisiert werden: {e}")


def feuerix_version():
    try:
        return (Path(settings.FEUERIX_REPO_PFAD) / "VERSION").read_text(encoding="utf-8").strip()[:20]
    except OSError:
        return ""


def beispieldaten_anlegen(mandant):
    """Legt in der laufenden Feuerix-Instanz die Beispieldaten an (Feuerix-Befehl `beispieldaten`: fiktive Mitglieder mit
    Rechnungen, Zahlungen und Mahnungen, Beispiel-Inventar). Nur fuer Demo-Instanzen und nur einmal - der Befehl legt bei jedem
    Aufruf neue Mitglieder an, deshalb merkt sich das Portal, dass es schon geschehen ist."""
    if not mandant.demo:
        raise BereitstellungsFehler("Beispieldaten gibt es nur für Demo-Instanzen (Haken „Demo-Instanz“ setzen).")
    if mandant.beispieldaten_angelegt:
        mandant.protokollieren("Beispieldaten sind bereits angelegt - übersprungen.")
        return False
    verein = slugify(mandant.verein_name)[:50] or "verein"      # so legt Feuerix' Ersteinrichtung das Vereinskuerzel an
    befehl = _compose_basis(mandant) + ["exec", "-T", "web", "python", "manage.py", "beispieldaten", "--verein", verein,
                                        "--anzahl", str(int(mandant.demo_anzahl))]
    if not mandant.demo_inventar:
        befehl.append("--ohne-inventar")
    docker(befehl, "Beispieldaten anlegen", timeout=1800)
    mandant.beispieldaten_angelegt = True
    mandant.save(update_fields=["beispieldaten_angelegt"])
    mandant.protokollieren(f"Beispieldaten angelegt ({mandant.demo_anzahl} Mitglieder"
                           f"{'' if mandant.demo_inventar else ', ohne Inventar'}).")
    from . import demo      # erst hier: demo -> provisioning
    try:
        demo.standard_sichern(mandant)         # den frischen Stand als Demo-Standard merken
    except BereitstellungsFehler as e:
        mandant.protokollieren(f"Hinweis: Der Demo-Standard konnte nicht gespeichert werden ({e}). Mit der Aktion „Aktuellen Stand "
                               "als Demo-Standard speichern“ nachholen.")
    return True


def beispieldaten_nachtragen(mandant):
    """Beispieldaten fuer eine bereits laufende Demo-Instanz (Admin-Aktion/Befehl), mit sichtbarem Fortschritt."""
    ausgang = basis.status_vor_vormerkung(mandant)
    if ausgang != "laeuft":
        raise BereitstellungsFehler(f"Beispieldaten nur für laufende Instanzen (Status: {ausgang}).")
    if not mandant.demo:
        raise BereitstellungsFehler("Beispieldaten gibt es nur für Demo-Instanzen (Haken „Demo-Instanz“ setzen).")
    mandant.status = "wird_bereitgestellt"
    mandant.save(update_fields=["status"])
    try:
        basis.fortschritt(mandant, 30, "Beispieldaten anlegen (kann etwas dauern)")
        beispieldaten_anlegen(mandant)
    except BereitstellungsFehler:
        mandant.status = ausgang
        mandant.schritt = f"Beispieldaten fehlgeschlagen bei: {mandant.schritt}"[:200]
        mandant.save(update_fields=["status", "schritt"])
        raise
    mandant.status = ausgang
    mandant.save(update_fields=["status"])
    basis.fortschritt(mandant, 100, "Fertig")


def bereitstellen(mandant):
    """Fuehrt die komplette Bereitstellung aus und aktualisiert den Status. Erneutes Aufrufen ist gefahrlos
    (Geheimnisse bleiben, docker compose up aendert nichts Unveraendertes)."""
    mandant.status = "wird_bereitgestellt"
    mandant.save(update_fields=["status"])
    try:
        basis.fortschritt(mandant, 5, "Konfiguration (.env) erzeugen")
        env_erzeugen(mandant)
        basis.fortschritt(mandant, 10, "Feuerix bauen und starten (kann einige Minuten dauern)")
        stack_hochfahren(mandant)
        basis.fortschritt(mandant, 35, "Feuerix: warten auf Migration und Ersteinrichtung")
        auf_bereitschaft_warten(mandant)
        if mandant.demo and not mandant.beispieldaten_angelegt:
            basis.fortschritt(mandant, 40, "Beispieldaten anlegen (Demo-Instanz)")
            beispieldaten_anlegen(mandant)
        basis.fortschritt(mandant, 45, "Zusatzdienste")
        zusatzdienste_bereitstellen(mandant)
        # Erst jetzt "laeuft": der Proxy nimmt nur Instanzen mit diesem Status auf.
        basis.fortschritt(mandant, 97, "Reverse Proxy eintragen")
        mandant.status = "laeuft"
        mandant.feuerix_version = feuerix_version()
        mandant.save(update_fields=["status", "feuerix_version"])
        _proxy_nachziehen(mandant)
        dienstversionen.erfassen_sicher(mandant)
        basis.fortschritt(mandant, 100, "Fertig")
    except BereitstellungsFehler as e:
        mandant.status = "fehlgeschlagen"
        mandant.schritt = f"Fehlgeschlagen bei: {mandant.schritt}"[:200]
        mandant.save(update_fields=["status", "schritt"])
        mandant.protokollieren(f"Fehler: {e}")
        proxy_nachziehen_best_effort(mandant)
        raise


def abbauen(mandant, daten_loeschen=False, sicherung=True):
    """Legt die Instanz samt Zusatzdiensten still (Container und Netzwerk werden entfernt, Datenbank und Medien
    bleiben in den Volumes), nimmt sie aus dem Reverse Proxy und setzt den Status auf "pausiert" (= stillgelegt)
    bzw. "geloescht" (mit daten_loeschen: Volumes werden mit entfernt). Meldet den Fortschritt.
    Mit `daten_loeschen` wird VORHER eine Abschlusssicherung aller Dienste angelegt (endsicherung); scheitert sie, wird
    nichts entfernt. `sicherung=False` ueberspringt sie ausdruecklich (nur fuer Notfaelle, z. B. Kommandozeile)."""
    vorher = basis.status_vor_vormerkung(mandant)
    erledigt = 0   # abgeschlossene Teilschritte: scheitert schon der erste, ist noch nichts entfernt
    mandant.status = "wird_bereitgestellt"
    mandant.save(update_fields=["status"])
    schritte = (65, 75, 85, 95) if daten_loeschen else (10, 40, 70, 95)
    try:
        if daten_loeschen and sicherung:
            from . import endsicherung
            endsicherung.abschlusssicherung(mandant)
        elif daten_loeschen:
            mandant.protokollieren("ACHTUNG: Daten werden OHNE Abschlusssicherung gelöscht.")
        if openslides.aktiv(mandant):
            basis.fortschritt(mandant, schritte[0], "OpenSlides entfernen")
            openslides.abbauen(mandant, daten_loeschen=daten_loeschen)
            erledigt += 1
        if paperless.aktiv(mandant):
            basis.fortschritt(mandant, schritte[1], "Paperless entfernen")
            paperless.abbauen(mandant, daten_loeschen=daten_loeschen)
            erledigt += 1
        basis.fortschritt(mandant, schritte[2], "Feuerix entfernen")
        stack_abbauen(mandant, daten_loeschen=daten_loeschen)
        if daten_loeschen:
            from . import endsicherung
            endsicherung.archivieren(mandant)
        basis.fortschritt(mandant, schritte[3], "Reverse Proxy aktualisieren")
        mandant.status = "geloescht" if daten_loeschen else "pausiert"
        mandant.fortschritt, mandant.schritt = 0, ""
        mandant.save(update_fields=["status", "fortschritt", "schritt"])
        _proxy_nachziehen(mandant)
    except BereitstellungsFehler as e:
        # Nichts entfernt: Instanz laeuft unveraendert weiter. Teilweise entfernt: Zustand unklar -> fehlgeschlagen.
        mandant.status = vorher if erledigt == 0 else "fehlgeschlagen"
        mandant.schritt = (f"{'Löschen' if daten_loeschen else 'Stilllegen'} fehlgeschlagen bei: "
                           f"{mandant.schritt}")[:200]
        mandant.save(update_fields=["status", "schritt"])
        mandant.protokollieren(f"Fehler beim Abbau: {e}")
        if erledigt:
            proxy_nachziehen_best_effort(mandant)
        raise
