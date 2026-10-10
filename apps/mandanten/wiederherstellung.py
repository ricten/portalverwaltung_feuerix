"""Wiederherstellung einer Feuerix-Instanz aus einer Sicherung (Datenbank und Medien). Quellen sind die lokalen Sicherungen
in `<instanz>/backups` und - bei aktivem Backup-Ziel - die Netzwerkfreigabe.

Ablauf: Instanz starten -> (empfohlen) aktuellen Stand sichern -> Sicherung in den Container kopieren -> Worker anhalten ->
Datenbank leeren -> `scripts/restore.sh` von Feuerix -> Neustart (Migrationen laufen beim Start). Scheitert die
Wiederherstellung, nachdem die Datenbank geleert wurde, steht die Instanz auf "Fehlgeschlagen" und die Sicherung des
vorherigen Stands liegt bereit, um sie auf demselben Weg zurueckzuspielen."""
import re
from datetime import datetime
from pathlib import Path

from django.conf import settings

from . import backupziel, basis, betrieb, provisioning
from .basis import BereitstellungsFehler

DB_MUSTER = re.compile(r"^db-(\d{8}-\d{6})\.sql\.gz$")
MEDIA_MUSTER = re.compile(r"^media-(\d{8}-\d{6})\.tar\.gz$")

# Das Schema wird vor dem Zurueckspielen geleert: ein pg_dump im Textformat enthaelt kein DROP und wuerde sonst an
# bestehenden Tabellen scheitern. Laeuft im web-Container (dort sind psql und die POSTGRES_*-Variablen der Instanz).
_SCHEMA_LEEREN = ('PGPASSWORD="$POSTGRES_PASSWORD" psql -v ON_ERROR_STOP=1 -h "$POSTGRES_HOST" -U "$POSTGRES_USER" '
                  '"$POSTGRES_DB" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"')


def lokaler_ordner(mandant):
    return Path(settings.INSTANZEN_VERZEICHNIS) / mandant.kuerzel / "backups"


def angebote(mandant):
    """Verfuegbare Sicherungen, neueste zuerst. -> (Liste, Fehlertext der Freigabe-Abfrage oder "").
    Eintrag: {stamp, zeit, db, media, ort_db, ort_media, groesse} - ort ist "lokal" oder "freigabe" (lokal hat Vorrang)."""
    eintraege = {}

    def eintrag(stamp):
        return eintraege.setdefault(stamp, {"stamp": stamp, "zeit": datetime.strptime(stamp, "%Y%m%d-%H%M%S"),
                                            "db": None, "media": None, "ort_db": None, "ort_media": None, "groesse": 0})

    def erfassen(name, groesse, ort):
        for muster, feld in ((DB_MUSTER, "db"), (MEDIA_MUSTER, "media")):
            treffer = muster.match(name)
            if treffer:
                e = eintrag(treffer.group(1))
                if e[f"ort_{feld}"] != "lokal":          # lokale Datei hat Vorrang vor der auf der Freigabe
                    if e[feld] is None:
                        e["groesse"] += groesse
                    e[feld], e[f"ort_{feld}"] = name, ort

    ordner = lokaler_ordner(mandant)
    if ordner.is_dir():
        for datei in ordner.iterdir():
            if datei.is_file():
                erfassen(datei.name, datei.stat().st_size, "lokal")
    fehler = ""
    if backupziel.aktuell():
        try:
            for name, groesse in backupziel.auflisten(mandant):
                erfassen(name, groesse, "freigabe")
        except BereitstellungsFehler as e:
            fehler = str(e)
    liste = sorted((e for e in eintraege.values() if e["db"]), key=lambda e: e["stamp"], reverse=True)
    return liste, fehler


def _bereitstellen(mandant, eintrag, mit_medien):
    """Stellt sicher, dass die Dateien lokal vorliegen (von der Freigabe herunterladen) und kopiert sie in den Container.
    -> Liste der Dateinamen im Container."""
    ordner = lokaler_ordner(mandant)
    ordner.mkdir(parents=True, exist_ok=True)
    dateien = [("db", eintrag["db"], eintrag["ort_db"])]
    if mit_medien and eintrag["media"]:
        dateien.append(("media", eintrag["media"], eintrag["ort_media"]))
    compose = provisioning._compose_basis(mandant)
    for _, name, ort in dateien:
        if ort == "freigabe":
            backupziel.herunterladen(mandant, name, ordner)
        basis.docker(compose + ["cp", str(ordner / name), f"web:/data/backups/{name}"],
                     f"Sicherung {name} in den Container kopieren", timeout=3600)
    return [name for _, name, _ in dateien]


def wiederherstellen(mandant, stamp, mit_medien=True, vorher_sichern=True):
    """Spielt die Sicherung `stamp` (z. B. "20261010-020219") in die Instanz zurueck."""
    ausgang = basis.status_vor_vormerkung(mandant)
    if ausgang not in ("laeuft", "gestoppt", "fehlgeschlagen"):
        raise BereitstellungsFehler(f"Wiederherstellung nicht moeglich im Status: {ausgang}.")
    liste, _ = angebote(mandant)
    eintrag = next((e for e in liste if e["stamp"] == stamp), None)
    if eintrag is None:
        raise BereitstellungsFehler(f"Sicherung {stamp} nicht gefunden.")
    mandant.status = "wird_bereitgestellt"
    mandant.save(update_fields=["status"])
    mandant.protokollieren(f"Wiederherstellung aus Sicherung {stamp} gestartet "
                           f"({'mit' if mit_medien and eintrag['media'] else 'ohne'} Medien, "
                           f"{'mit' if vorher_sichern else 'OHNE'} Sicherung des aktuellen Stands).")
    zerstoert = False       # ab hier ist die Datenbank der Instanz veraendert
    try:
        basis.fortschritt(mandant, 5, "Wiederherstellung: Instanz starten")
        provisioning.stack_steuern(mandant, "start")
        provisioning.auf_bereitschaft_warten(mandant)
        compose = provisioning._compose_basis(mandant)
        if vorher_sichern:
            basis.fortschritt(mandant, 15, "Wiederherstellung: aktuellen Stand sichern")
            betrieb.backup(mandant, _pruefen=False)
            vorher, _ = angebote(mandant)
            mandant.protokollieren("Stand vor der Wiederherstellung gesichert"
                                   + (f" (Sicherung {vorher[0]['stamp']})." if vorher else "."))
        basis.fortschritt(mandant, 35, "Wiederherstellung: Sicherung bereitstellen")
        namen = _bereitstellen(mandant, eintrag, mit_medien)
        basis.fortschritt(mandant, 45, "Wiederherstellung: Worker anhalten")
        basis.docker(compose + ["stop", "worker"], "Worker anhalten", timeout=300)
        basis.fortschritt(mandant, 55, "Wiederherstellung: Datenbank leeren")
        zerstoert = True
        basis.docker(compose + ["exec", "-T", "web", "sh", "-c", _SCHEMA_LEEREN], "Datenbank leeren", timeout=600)
        basis.fortschritt(mandant, 65, "Wiederherstellung: Datenbank und Medien zurückspielen")
        basis.docker(compose + ["exec", "-T", "web", "sh", "/app/scripts/restore.sh"] + namen,
                     "Sicherung zurückspielen", timeout=7200)
        basis.fortschritt(mandant, 85, "Wiederherstellung: neu starten")
        provisioning.stack_steuern(mandant, "start")      # Worker wieder starten
        provisioning.stack_steuern(mandant, "restart")    # web neu: Migrationen laufen beim Start
        provisioning.auf_bereitschaft_warten(mandant)
    except BereitstellungsFehler:
        mandant.status = "fehlgeschlagen" if zerstoert else ausgang
        mandant.schritt = f"Wiederherstellung fehlgeschlagen bei: {mandant.schritt}"[:200]
        mandant.save(update_fields=["status", "schritt"])
        if zerstoert:
            mandant.protokollieren("ACHTUNG: Die Wiederherstellung brach ab, nachdem die Datenbank geleert wurde. Die Sicherung "
                                   "vom Stand davor liegt unter Admin > Instanz > Aus Sicherung wiederherstellen bereit.")
            provisioning.proxy_nachziehen_best_effort(mandant)
        raise
    mandant.status = "laeuft"
    mandant.save(update_fields=["status"])
    basis.fortschritt(mandant, 100, "Fertig")
    mandant.protokollieren(f"Wiederherstellung aus Sicherung {stamp} abgeschlossen.")
