"""OpenSlides 4 je Mandant (Ausbaustufe 2): eigener Compose-Stack im Verzeichnis `<instanz>/openslides`, erzeugt
mit `osmanage` (wie Feuerix' openslides/install.sh). Der Stack bekommt den Projektnamen
`<projekt>-openslides` (COMPOSE_PROJECT_NAME), damit sich mehrere Mandanten nicht in die Quere kommen.
Danach wird die Anbindung (Superadmin) in der Feuerix-Instanz des Mandanten hinterlegt.
"""
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings

from . import basis
from .basis import BereitstellungsFehler

OSMANAGE_URL = "https://github.com/OpenSlides/openslides-cli/releases/download/latest/osmanage"
VORLAGE_URL = ("https://raw.githubusercontent.com/OpenSlides/openslides-cli/refs/heads/main/contrib/"
               "docker-compose.yml.tmpl")


def aktiv(mandant):
    return bool(mandant.openslides_domain)


def projektname(mandant):
    return f"{mandant.docker_projekt}-openslides"


def url(mandant):
    return f"{basis.url_schema()}://{mandant.openslides_domain}"


def _verzeichnis(mandant):
    return basis.instanz_verzeichnis(mandant, "openslides")


def _env(mandant):
    return {"COMPOSE_PROJECT_NAME": projektname(mandant)}


def _herunterladen(quelle, ziel, ausfuehrbar=False):
    if ziel.exists():
        return ziel
    try:
        with urllib.request.urlopen(quelle, timeout=120) as antwort:
            daten = antwort.read()
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise BereitstellungsFehler(f"OpenSlides: Download von {quelle} fehlgeschlagen: {e}")
    ziel.write_bytes(daten)
    if ausfuehrbar:
        ziel.chmod(0o755)
    return ziel


def werkzeuge_bereitstellen():
    """osmanage und die Compose-Vorlage werden einmal fuer alle Mandanten geladen und zwischengespeichert."""
    verz = Path(settings.INSTANZEN_VERZEICHNIS) / "_werkzeuge"
    verz.mkdir(parents=True, exist_ok=True)
    return (_herunterladen(OSMANAGE_URL, verz / "osmanage", ausfuehrbar=True),
            _herunterladen(VORLAGE_URL, verz / "docker-compose.yml.tmpl"))


def config_erzeugen(mandant):
    """config.yml aus der Vorlage in Feuerix (Tag, Registry) mit dem Mandanten-Port."""
    quelle = Path(settings.FEUERIX_REPO_PFAD) / "openslides" / "config.yml"
    if not quelle.exists():
        raise BereitstellungsFehler(f"openslides/config.yml nicht gefunden unter {quelle} - "
                                    "FEUERIX_REPO_PFAD prüfen.")
    if not mandant.openslides_port:
        mandant.openslides_port = basis.naechster_freier_port("openslides_port", settings.OPENSLIDES_PORT_BASIS)
        mandant.save(update_fields=["openslides_port"])
    text = re.sub(r"(?m)^port:.*$", f"port: {int(mandant.openslides_port)}", quelle.read_text(encoding="utf-8"))
    pfad = _verzeichnis(mandant) / "config.yml"
    pfad.write_text(text, encoding="utf-8")
    return pfad


def einrichten(mandant):
    verz = _verzeichnis(mandant)
    osmanage, vorlage = werkzeuge_bereitstellen()
    config = config_erzeugen(mandant)
    if not (verz / "docker-compose.yml").exists():
        basis.docker([str(osmanage), "setup", "-c", str(config), "-t", str(vorlage), "."],
                     "OpenSlides: osmanage setup", timeout=300, cwd=verz, env=_env(mandant))
        mandant.protokollieren("OpenSlides: Stack-Konfiguration erzeugt.")
    geheim = verz / "secrets" / "superadmin"
    if not geheim.exists():
        raise BereitstellungsFehler("OpenSlides: secrets/superadmin fehlt nach osmanage setup.")
    passwort = geheim.read_text(encoding="utf-8").strip()
    if mandant.openslides_superadmin_passwort != passwort:
        mandant.openslides_superadmin_passwort = passwort
        mandant.save(update_fields=["openslides_superadmin_passwort"])
    return osmanage


def hochfahren(mandant):
    verz = _verzeichnis(mandant)
    basis.docker(["docker", "compose", "pull"], "OpenSlides: docker compose pull", timeout=1200, cwd=verz,
                 env=_env(mandant))
    basis.docker(["docker", "compose", "up", "--detach"], "OpenSlides: docker compose up", cwd=verz,
                 env=_env(mandant))
    mandant.protokollieren(f"OpenSlides-Stack '{projektname(mandant)}' gestartet.")


def startdaten_anlegen(mandant, osmanage, versuche=18, pause_sekunden=10):
    """Einmalig nach dem ersten Start (idempotent ueber openslides_initialisiert). Die Dienste brauchen einen
    Moment, deshalb wird wiederholt - wie in install.sh."""
    if mandant.openslides_initialisiert:
        return
    verz = _verzeichnis(mandant)
    letzter = None
    for _ in range(versuche):
        try:
            basis.docker([str(osmanage), "initial-data", "--superadmin-password-file", "secrets/superadmin"],
                         "OpenSlides: initial-data", timeout=120, cwd=verz, env=_env(mandant))
            mandant.openslides_initialisiert = True
            mandant.save(update_fields=["openslides_initialisiert"])
            mandant.protokollieren("OpenSlides: Startdaten angelegt.")
            return
        except BereitstellungsFehler as e:
            letzter = e
            time.sleep(pause_sekunden)
    raise BereitstellungsFehler(f"OpenSlides: Startdaten konnten nicht angelegt werden ({letzter}).")


def auf_bereitschaft_warten(mandant, timeout_sekunden=300, intervall_sekunden=5):
    ziel = f"http://127.0.0.1:{mandant.openslides_port}/"
    ende = time.monotonic() + timeout_sekunden
    letzter_fehler = "keine Antwort"
    while True:
        anfrage = urllib.request.Request(ziel, headers={"Host": mandant.openslides_domain})
        try:
            with urllib.request.urlopen(anfrage, timeout=5) as antwort:
                if antwort.status == 200:
                    return
                letzter_fehler = f"HTTP {antwort.status}"
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            letzter_fehler = str(e)
        if time.monotonic() >= ende:
            raise BereitstellungsFehler(f"OpenSlides: {ziel} antwortet nach {timeout_sekunden}s nicht "
                                        f"({letzter_fehler}).")
        time.sleep(intervall_sekunden)


def in_feuerix_eintragen(mandant, feuerix_compose_basis):
    """Traegt die OpenSlides-Anbindung in der Feuerix-Instanz ein (Skript ueber stdin, damit das Passwort nicht
    in der Prozessliste steht)."""
    skript = (
        "from apps.core.models import Verein\n"
        "from apps.openslides.models import OpenSlidesVerbindung\n"
        "v = Verein.objects.order_by('pk').first()\n"
        "OpenSlidesVerbindung.objects.update_or_create(verein=v, defaults=dict("
        f"url={url(mandant)!r}, benutzername='superadmin', passwort={mandant.openslides_superadmin_passwort!r}, "
        f"aktiv=True, tls_pruefen={not settings.PROXY_NUR_HTTP!r}))\n")
    befehl = feuerix_compose_basis + [
        "exec", "-T", "web", "python", "-c",
        "import os, django; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings'); "
        "django.setup(); exec(open(0).read())"]
    basis.docker(befehl, "OpenSlides: Anbindung in Feuerix eintragen", timeout=120, eingabe=skript)
    mandant.protokollieren("OpenSlides-Anbindung in Feuerix hinterlegt.")


def bereitstellen(mandant, feuerix_compose_basis):
    osmanage = einrichten(mandant)
    hochfahren(mandant)
    startdaten_anlegen(mandant, osmanage)
    auf_bereitschaft_warten(mandant)
    in_feuerix_eintragen(mandant, feuerix_compose_basis)


def abbauen(mandant, daten_loeschen=False):
    verz = Path(settings.INSTANZEN_VERZEICHNIS) / mandant.kuerzel / "openslides"
    if not aktiv(mandant) or not (verz / "docker-compose.yml").exists():
        return
    basis.docker(["docker", "compose", "down"] + (["-v"] if daten_loeschen else []),
                 "OpenSlides: docker compose down", timeout=300, cwd=verz, env=_env(mandant))
    mandant.protokollieren("OpenSlides-Stack abgebaut" + (" (inkl. Daten)." if daten_loeschen else "."))
