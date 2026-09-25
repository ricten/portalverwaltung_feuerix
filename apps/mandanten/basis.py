"""Gemeinsame Bausteine fuer Bereitstellung von Feuerix und Zusatzdiensten (Paperless, OpenSlides)."""
import os
import secrets
import string
import subprocess
from pathlib import Path

from django.conf import settings


class BereitstellungsFehler(Exception):
    pass


def zufallswert(laenge=50):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(laenge))


def instanz_verzeichnis(mandant, unterordner=None):
    pfad = Path(settings.INSTANZEN_VERZEICHNIS) / mandant.kuerzel
    if unterordner:
        pfad = pfad / unterordner
    pfad.mkdir(parents=True, exist_ok=True)
    return pfad


def naechster_freier_port(feld, basis):
    """Naechster noch von keiner Instanz belegter Port ab `basis` fuer das Modellfeld `feld`
    (web_port, paperless_port, openslides_port)."""
    from .models import MandantInstanz
    belegt = set(MandantInstanz.objects.exclude(**{f"{feld}__isnull": True}).values_list(feld, flat=True))
    port = basis
    while port in belegt:
        port += 1
    return port


def docker(befehl, was, timeout=900, cwd=None, eingabe=None, env=None):
    """Fuehrt einen docker-Befehl aus. Jede Art von Scheitern (Exitcode, Timeout, docker fehlt) wird zu
    einem BereitstellungsFehler, damit der Status der Instanz nie in "wird bereitgestellt" haengen bleibt."""
    try:
        ergebnis = subprocess.run(befehl, cwd=cwd or settings.FEUERIX_REPO_PFAD, capture_output=True, text=True,
                                  timeout=timeout, input=eingabe,
                                  env={**os.environ, **env} if env else None)
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
