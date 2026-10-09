"""OpenSlides 4 je Mandant (Ausbaustufe 2): eigener Compose-Stack im Verzeichnis `<instanz>/openslides`, erzeugt
mit `osmanage` (wie Feuerix' openslides/install.sh). Der Stack bekommt den Projektnamen
`<projekt>-openslides` (COMPOSE_PROJECT_NAME), damit sich mehrere Mandanten nicht in die Quere kommen.
Danach wird die Anbindung (Superadmin) in der Feuerix-Instanz des Mandanten hinterlegt.
"""
import re
import shutil
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
    if pfad.exists():
        # Bereits eingerichtet/aktualisiert: den dort stehenden Versions-Tag behalten, nicht auf den der Vorlage zuruecksetzen
        vorhanden = re.search(r"(?m)^(\s*)tag:\s*(\S+)", pfad.read_text(encoding="utf-8"))
        if vorhanden:
            text = re.sub(r"(?m)^(\s*tag:\s*)\S+", lambda m: m.group(1) + vorhanden.group(2), text, count=1)
    if not mandant.openslides_manage_port and not (_verzeichnis(mandant) / "docker-compose.yml").exists():
        # Neuer Stack: eigener Verwaltungsport statt des gemeinsamen Standards 9002
        mandant.openslides_manage_port = basis.naechster_freier_port("openslides_manage_port",
                                                                     settings.OPENSLIDES_MANAGE_PORT_BASIS)
        mandant.save(update_fields=["openslides_manage_port"])
    if mandant.openslides_manage_port:
        zeile = f"backendManagePort: {int(mandant.openslides_manage_port)}"
        if re.search(r"(?m)^backendManagePort:", text):
            text = re.sub(r"(?m)^backendManagePort:.*$", zeile, text)
        else:
            text = re.sub(r"(?m)^(port:.*)$", lambda m: m.group(1) + "\n" + zeile, text, count=1)
    pfad.write_text(text, encoding="utf-8")
    return pfad


def verwaltungsport(mandant):
    """Host-Port des Verwaltungsdienstes (backendManage), an den `osmanage` sich wendet."""
    return mandant.openslides_manage_port or 9002


def _kollision_reparieren(mandant, verz):
    """Ein noch nicht initialisierter Stack, der mit dem gemeinsamen Verwaltungsport 9002 erzeugt wurde, kollidiert mit
    anderen Instanzen (`initial-data` landet bei der falschen Instanz -> 401). Er wird samt (leerer) Datenbank entfernt
    und mit eigenem Port neu erzeugt. Eine bereits initialisierte Instanz wird NIE angefasst."""
    from .models import MandantInstanz
    if mandant.openslides_initialisiert or mandant.openslides_manage_port or not (verz / "docker-compose.yml").exists():
        return
    if not MandantInstanz.objects.exclude(pk=mandant.pk).filter(
            openslides_domain__gt="", openslides_manage_port__isnull=True).exists():
        return
    mandant.protokollieren("OpenSlides: Verwaltungsport 9002 wird von mehreren Instanzen genutzt - Stack wird mit "
                           "eigenem Port neu erzeugt (noch keine Daten vorhanden).")
    basis.docker(["docker", "compose", "down", "-v"], "OpenSlides: alten Stack entfernen", timeout=300, cwd=verz,
                 env=_env(mandant))
    (verz / "docker-compose.yml").unlink()
    geheimnisse = verz / "secrets"
    if geheimnisse.exists():
        shutil.rmtree(geheimnisse)


def einrichten(mandant):
    verz = _verzeichnis(mandant)
    osmanage, vorlage = werkzeuge_bereitstellen()
    _kollision_reparieren(mandant, verz)
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
            befehl = [str(osmanage), "initial-data", "--address", f"localhost:{verwaltungsport(mandant)}"]
            if (verz / "secrets" / "internal_auth_password").exists():
                befehl += ["--password-file", "secrets/internal_auth_password"]
            befehl += ["--superadmin-password-file", "secrets/superadmin"]
            basis.docker(befehl, "OpenSlides: initial-data", timeout=120, cwd=verz, env=_env(mandant))
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
    basis.fortschritt(mandant, 75, "OpenSlides: Stack einrichten")
    osmanage = einrichten(mandant)
    basis.fortschritt(mandant, 80, "OpenSlides: Images laden und starten")
    hochfahren(mandant)
    basis.fortschritt(mandant, 88, "OpenSlides: Startdaten anlegen")
    startdaten_anlegen(mandant, osmanage)
    basis.fortschritt(mandant, 93, "OpenSlides: warten, bis es antwortet")
    auf_bereitschaft_warten(mandant)
    basis.fortschritt(mandant, 96, "OpenSlides: Anbindung in Feuerix eintragen")
    in_feuerix_eintragen(mandant, feuerix_compose_basis)


_STEUERN = {"stop": ["stop"], "start": ["up", "--detach"], "restart": ["restart"]}


def steuern(mandant, aktion):
    verz = Path(settings.INSTANZEN_VERZEICHNIS) / mandant.kuerzel / "openslides"
    if not aktiv(mandant) or not (verz / "docker-compose.yml").exists():
        return
    basis.docker(["docker", "compose"] + _STEUERN[aktion], f"OpenSlides: docker compose {aktion}", timeout=300,
                 cwd=verz, env=_env(mandant))
    mandant.protokollieren(f"OpenSlides-Stack '{projektname(mandant)}': {aktion}.")


def abbauen(mandant, daten_loeschen=False):
    verz = Path(settings.INSTANZEN_VERZEICHNIS) / mandant.kuerzel / "openslides"
    if not aktiv(mandant) or not (verz / "docker-compose.yml").exists():
        return
    basis.docker(["docker", "compose", "down"] + (["-v"] if daten_loeschen else []),
                 "OpenSlides: docker compose down", timeout=300, cwd=verz, env=_env(mandant))
    mandant.protokollieren("OpenSlides-Stack abgebaut" + (" (inkl. Daten)." if daten_loeschen else "."))
