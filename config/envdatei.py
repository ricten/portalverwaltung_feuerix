"""Liest die .env im Projektverzeichnis in os.environ (ohne vorhandene Variablen zu ueberschreiben), damit
`manage.py ...` auch ohne vorheriges `set -a; . ./.env` funktioniert. systemd (EnvironmentFile) und der
Installer setzen die Variablen weiterhin selbst - sie haben Vorrang."""
import os
from pathlib import Path


def laden(pfad=None):
    pfad = Path(pfad) if pfad else Path(__file__).resolve().parent.parent / ".env"
    try:
        zeilen = pfad.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for zeile in zeilen:
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        name, wert = zeile.split("=", 1)
        name, wert = name.strip(), wert.strip()
        if len(wert) >= 2 and wert[0] == wert[-1] and wert[0] in "\"'":
            wert = wert[1:-1]
        os.environ.setdefault(name, wert)
