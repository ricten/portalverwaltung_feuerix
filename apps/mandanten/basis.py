"""Gemeinsame Bausteine fuer Bereitstellung von Feuerix und Zusatzdiensten (Paperless, OpenSlides)."""
import os
import re
import secrets
import socket
import string
import subprocess
from pathlib import Path

from django.conf import settings


class BereitstellungsFehler(Exception):
    pass


# Marker fuer eine in der Weboberflaeche vorgemerkte Aktualisierung (Status "wird_bereitgestellt" + dieser Schritt),
# damit der Fortschrittsbalken sofort erscheint und das Update sie trotz geaendertem Status annimmt.
WARTESCHLANGE_UPDATE = "Update: in der Warteschlange"
WARTESCHLANGE_UPDATE_GESTOPPT = WARTESCHLANGE_UPDATE + " (gestoppte Instanz)"
WARTESCHLANGE_DIENST = "Dienst-Update: in der Warteschlange"


def zufallswert(laenge=50):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(laenge))


def instanz_verzeichnis(mandant, unterordner=None):
    pfad = Path(settings.INSTANZEN_VERZEICHNIS) / mandant.kuerzel
    if unterordner:
        pfad = pfad / unterordner
    pfad.mkdir(parents=True, exist_ok=True)
    return pfad


PORT_FELDER = ("web_port", "paperless_port", "openslides_port", "openslides_manage_port")


def port_frei(port):
    """True, wenn der Port auf diesem Server gerade nicht gebunden ist (egal von wem: andere Instanz, Docker, ein
    fremdes Programm). Pruefung per Bind-Versuch auf 127.0.0.1."""
    if not settings.PORT_PRUEFUNG:
        return True
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def naechster_freier_port(feld, basis):
    """Naechster freier Port ab `basis` fuer die Instanz-Eigenschaft `feld`. Frei heisst: von KEINER Instanz in
    irgendeinem Port-Feld (Web, Paperless, OpenSlides, OpenSlides-Verwaltung) vergeben UND auf dem Server tatsaechlich
    nicht belegt (auch nicht durch gestoppte-aber-bestehende Docker-Ports oder fremde Programme)."""
    from .models import MandantInstanz
    belegt = set()
    for f in PORT_FELDER:
        belegt.update(MandantInstanz.objects.exclude(**{f"{f}__isnull": True}).values_list(f, flat=True))
    port = basis
    while port in belegt or not port_frei(port):
        port += 1
        if port > 65000:
            raise BereitstellungsFehler(f"Kein freier Port ab {basis} gefunden.")
    return port


# Variablen der Portalverwaltung selbst (aus deren .env, per systemd EnvironmentFile im Prozess). Docker Compose
# laesst die Shell-Umgebung bei der Variablen-Interpolation VOR --env-file gewinnen: ohne diese Bereinigung
# bekaeme der db-Container POSTGRES_USER/-PASSWORD/-DB der Portalverwaltung, waehrend Feuerix (env_file) die der
# Instanz verwendet -> "password authentication failed".
_PORTAL_VARIABLEN = (
    "SECRET_KEY", "FIELD_ENCRYPTION_KEY", "DEBUG", "ALLOWED_HOSTS", "HTTPS", "CSRF_TRUSTED_ORIGINS",
    "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_HOST", "POSTGRES_PORT", "REDIS_URL",
    "FEUERIX_REPO_PFAD", "INSTANZEN_VERZEICHNIS", "WEB_PORT", "WEB_PORT_BASIS", "PAPERLESS_PORT_BASIS",
    "OPENSLIDES_PORT_BASIS", "CADDY_ADMIN_URL", "CADDY_ACME_EMAIL", "PROXY_NUR_HTTP", "PORTAL_DOMAIN",
    "PORTAL_PORT", "DJANGO_SETTINGS_MODULE",
)


def docker_umgebung(zusatz=None):
    """Prozessumgebung fuer docker-Aufrufe ohne die Variablen der Portalverwaltung, plus `zusatz`."""
    umgebung = {k: v for k, v in os.environ.items() if k not in _PORTAL_VARIABLEN}
    umgebung.update(zusatz or {})
    return umgebung


def docker(befehl, was, timeout=900, cwd=None, eingabe=None, env=None):
    """Fuehrt einen docker-Befehl aus. Jede Art von Scheitern (Exitcode, Timeout, docker fehlt) wird zu
    einem BereitstellungsFehler, damit der Status der Instanz nie in "wird bereitgestellt" haengen bleibt."""
    try:
        ergebnis = subprocess.run(befehl, cwd=cwd or settings.FEUERIX_REPO_PFAD, capture_output=True, text=True,
                                  timeout=timeout, input=eingabe,
                                  env=docker_umgebung(env))
    except subprocess.TimeoutExpired:
        raise BereitstellungsFehler(f"{was}: Zeitüberschreitung nach {timeout}s.")
    except OSError as e:
        raise BereitstellungsFehler(f"{was}: docker konnte nicht gestartet werden ({e}).")
    if ergebnis.returncode != 0:
        raise BereitstellungsFehler(f"{was} fehlgeschlagen:\n{ergebnis.stderr[-2000:]}")
    return ergebnis.stdout


def yaml_pfad(pfad):
    """Pfad fuer eine Compose-Datei (immer mit / und in Anfuehrungszeichen, auch unter Windows)."""
    return '"' + Path(pfad).resolve().as_posix() + '"'


def url_schema():
    return "http" if settings.PROXY_NUR_HTTP else "https"


def status_vor_vormerkung(mandant):
    """Der echte Status vor einer im Admin vorgemerkten Aktion (steht im Schritt als "(vorher: laeuft)"), sonst der
    aktuelle. Damit eine fehlgeschlagene Aktion die Instanz nicht auf "In Arbeit" haengen laesst."""
    if mandant.status == "wird_bereitgestellt":
        treffer = re.search(r"\(vorher: (\w+)\)$", mandant.schritt)
        if treffer:
            return treffer.group(1)
    return mandant.status


def vormerken(mandant, text):
    """Setzt die Instanz sofort auf "In Arbeit" (Balken im Admin) und merkt sich den bisherigen Status."""
    vorher = status_vor_vormerkung(mandant)
    mandant.status, mandant.fortschritt, mandant.schritt = "wird_bereitgestellt", 0, f"{text} (vorher: {vorher})"
    mandant.save(update_fields=["status", "fortschritt", "schritt", "aktualisiert_am"])


def fortschritt(mandant, prozent, schritt):
    """Meldet den Bereitstellungsfortschritt (fuer den Fortschrittsbalken im Admin)."""
    mandant.fortschritt = prozent
    mandant.schritt = schritt
    mandant.save(update_fields=["fortschritt", "schritt", "aktualisiert_am"])
