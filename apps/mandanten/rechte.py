"""Dateirechte fuer Geheimnisse: Die Instanzordner enthalten `.env`-Dateien (SECRET_KEY, FIELD_ENCRYPTION_KEY, Passwoerter) und
Sicherungen mit allen Daten. Sie duerfen nur dem Dienstbenutzer gehoeren/lesbar sein - nicht anderen Benutzern des Servers.

Verzeichnisse bekommen 0700, `.env`-Dateien 0600. Die Ordner `export` und `consume` von Paperless werden NICHT angefasst: dort schreibt
der Container-Benutzer (die Bind-Mounts bleiben von den Rechten der uebergeordneten Ordner unberuehrt). Unter Windows (Entwicklung)
passiert nichts."""
import os
import stat
from pathlib import Path

from django.conf import settings

ORDNER = 0o700
DATEI = 0o600


def _posix():
    return os.name == "posix"


def verzeichnis_sichern(pfad):
    """Setzt 0700 auf ein Verzeichnis, falls noetig. -> True, wenn geaendert wurde."""
    if not _posix():
        return False
    pfad = Path(pfad)
    try:
        if stat.S_IMODE(pfad.stat().st_mode) != ORDNER:
            os.chmod(pfad, ORDNER)
            return True
    except OSError:
        raise
    return False


def datei_sichern(pfad):
    if not _posix():
        return False
    pfad = Path(pfad)
    if stat.S_IMODE(pfad.stat().st_mode) != DATEI:
        os.chmod(pfad, DATEI)
        return True
    return False


def geheim_schreiben(pfad, inhalt):
    """Schreibt eine Datei mit Geheimnissen so, dass sie von Anfang an nur dem Besitzer gehoert (kein Zeitfenster mit offenen Rechten)."""
    daten = inhalt.encode("utf-8") if isinstance(inhalt, str) else inhalt
    if not _posix():
        Path(pfad).write_bytes(daten)
        return
    fd = os.open(pfad, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, DATEI)
    with os.fdopen(fd, "wb") as f:
        f.write(daten)
    os.chmod(pfad, DATEI)       # auch fuer eine schon vorhandene Datei mit anderen Rechten


def haerten():
    """Bringt die Rechte im Instanzverzeichnis in Ordnung: Wurzel, jeder Instanzordner (auch `_werkzeuge`, `_geloescht` und dessen
    Archive) auf 0700, jede `.env` auf 0600. -> (geaenderte Pfade, Fehlertexte)."""
    geaendert, fehler = [], []
    wurzel = Path(settings.INSTANZEN_VERZEICHNIS)
    if not _posix() or not wurzel.is_dir():
        return geaendert, fehler

    def versuchen(aktion, pfad):
        try:
            if aktion(pfad):
                geaendert.append(str(pfad))
        except OSError as e:
            fehler.append(f"{pfad}: {e.strerror or e}")

    versuchen(verzeichnis_sichern, wurzel)
    for ordner in sorted(p for p in wurzel.iterdir() if p.is_dir()):
        versuchen(verzeichnis_sichern, ordner)
        for env in (ordner / ".env", ordner / "paperless" / ".env"):
            if env.is_file():
                versuchen(datei_sichern, env)
        if ordner.name == "_geloescht":
            for archiv in sorted(p for p in ordner.iterdir() if p.is_dir()):
                versuchen(verzeichnis_sichern, archiv)
                if (archiv / ".env").is_file():
                    versuchen(datei_sichern, archiv / ".env")
    return geaendert, fehler
