"""Wiederherstellung einer Feuerix-Instanz aus einer Sicherung (Datenbank und Medien). Quellen sind die lokalen Sicherungen
in `<instanz>/backups` und - bei aktivem Backup-Ziel - die Netzwerkfreigabe.

Ablauf: Instanz starten -> (empfohlen) aktuellen Stand sichern -> Sicherung in den Container kopieren -> Worker anhalten ->
Datenbank leeren -> `scripts/restore.sh` von Feuerix -> Neustart (Migrationen laufen beim Start). Scheitert die
Wiederherstellung, nachdem die Datenbank geleert wurde, steht die Instanz auf "Fehlgeschlagen" und die Sicherung des
vorherigen Stands liegt bereit, um sie auf demselben Weg zurueckzuspielen."""
import re
import time
from datetime import datetime
from pathlib import Path

from django.conf import settings

from . import backupziel, basis, betrieb, dienstupdate, dienstversionen, openslides, paperless, provisioning
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


# --------------------------------------------------------------------------------------------------- OpenSlides
OS_DUMP_MUSTER = re.compile(r"^openslides-([\w.]+)-(\d{8}-\d{6})\.sql$")

# Alle Nicht-System-Schemas der Datenbank loeschen und "public" neu anlegen: ein Dump aus dem Portal (ohne --clean) wuerde
# sonst an vorhandenen Objekten scheitern; ein offizieller Dump mit --clean laeuft auf der leeren Datenbank ebenfalls.
_SCHEMAS_LEEREN = ("DO $$ DECLARE s text; BEGIN FOR s IN SELECT nspname FROM pg_namespace "
                   "WHERE nspname NOT LIKE 'pg\\_%' AND nspname <> 'information_schema' LOOP "
                   "EXECUTE format('DROP SCHEMA %I CASCADE', s); END LOOP; CREATE SCHEMA public; END $$;")


def angebote_openslides(mandant):
    """Dumps der OpenSlides-Datenbank (lokal und auf der Freigabe), neueste zuerst. -> (Liste, Fehlertext)."""
    eintraege = {}

    def erfassen(name, groesse, ort):
        treffer = OS_DUMP_MUSTER.match(name)
        if treffer and (name not in eintraege or ort == "lokal"):
            eintraege[name] = {"name": name, "version": treffer.group(1), "stamp": treffer.group(2),
                               "zeit": datetime.strptime(treffer.group(2), "%Y%m%d-%H%M%S"), "ort": ort,
                               "groesse": groesse}

    ordner = openslides._verzeichnis(mandant) / "backups"
    if ordner.is_dir():
        for datei in ordner.iterdir():
            if datei.is_file():
                erfassen(datei.name, datei.stat().st_size, "lokal")
    fehler = ""
    if backupziel.aktuell():
        try:
            for name, groesse in backupziel.auflisten(mandant, "openslides"):
                erfassen(name, groesse, "freigabe")
        except BereitstellungsFehler as e:
            fehler = str(e)
    return sorted(eintraege.values(), key=lambda e: e["stamp"], reverse=True), fehler


def _auf_postgres_warten(verz, umgebung, versuche=30, pause=2):
    for _ in range(versuche):
        try:
            basis.docker(["docker", "compose", "exec", "-T", "postgres", "pg_isready", "-U", "openslides"],
                         "OpenSlides: Datenbank prüfen", timeout=30, cwd=verz, env=umgebung)
            return
        except BereitstellungsFehler:
            time.sleep(pause)
    raise BereitstellungsFehler("OpenSlides: Die Datenbank startet nicht.")


def wiederherstellen_openslides(mandant, name, vorher_sichern=True):
    """Spielt den OpenSlides-Dump `name` zurueck - nach der offiziellen Anleitung: Dienste herunterfahren, nur die
    Datenbank starten, Dump einspielen, alles wieder starten. Feuerix und Paperless der Instanz laufen weiter."""
    ausgang = basis.status_vor_vormerkung(mandant)
    if ausgang not in ("laeuft", "gestoppt", "fehlgeschlagen"):
        raise BereitstellungsFehler(f"Wiederherstellung nicht moeglich im Status: {ausgang}.")
    if not openslides.aktiv(mandant):
        raise BereitstellungsFehler("OpenSlides ist bei dieser Instanz nicht eingerichtet.")
    liste, _ = angebote_openslides(mandant)
    eintrag = next((e for e in liste if e["name"] == name), None)
    if eintrag is None:
        raise BereitstellungsFehler(f"OpenSlides-Sicherung {name} nicht gefunden.")
    verz = openslides._verzeichnis(mandant)
    umgebung = openslides._env(mandant)
    mandant.status = "wird_bereitgestellt"
    mandant.save(update_fields=["status"])
    mandant.protokollieren(f"Wiederherstellung von OpenSlides aus {name} gestartet "
                           f"({'mit' if vorher_sichern else 'OHNE'} Sicherung des aktuellen Stands).")
    zerstoert = False
    try:
        basis.fortschritt(mandant, 5, "OpenSlides-Wiederherstellung: OpenSlides starten")
        openslides.steuern(mandant, "start")
        openslides.auf_bereitschaft_warten(mandant)
        if vorher_sichern:
            basis.fortschritt(mandant, 15, "OpenSlides-Wiederherstellung: aktuellen Stand sichern")
            dienstupdate.openslides_dump(mandant, dienstversionen.openslides_konfiguriert(mandant) or "unbekannt",
                                         "OpenSlides: Dump vor der Wiederherstellung")
        basis.fortschritt(mandant, 30, "OpenSlides-Wiederherstellung: Sicherung bereitstellen")
        datei = verz / "backups" / name
        if eintrag["ort"] == "freigabe":
            (verz / "backups").mkdir(exist_ok=True)
            backupziel.herunterladen(mandant, name, verz / "backups", "openslides")
        sql = datei.read_text(encoding="utf-8")
        if not sql.strip():
            raise BereitstellungsFehler("OpenSlides: Die Sicherung ist leer.")
        basis.fortschritt(mandant, 40, "OpenSlides-Wiederherstellung: OpenSlides herunterfahren")
        basis.docker(["docker", "compose", "down"], "OpenSlides: herunterfahren", timeout=300, cwd=verz, env=umgebung)
        basis.docker(["docker", "compose", "up", "--detach", "postgres"], "OpenSlides: Datenbank starten",
                     timeout=300, cwd=verz, env=umgebung)
        _auf_postgres_warten(verz, umgebung)
        basis.fortschritt(mandant, 55, "OpenSlides-Wiederherstellung: Datenbank leeren")
        zerstoert = True
        psql = ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "openslides", "-d", "openslides",
                "-v", "ON_ERROR_STOP=1"]
        basis.docker(psql + ["-c", _SCHEMAS_LEEREN], "OpenSlides: Datenbank leeren", timeout=600, cwd=verz, env=umgebung)
        basis.fortschritt(mandant, 65, "OpenSlides-Wiederherstellung: Dump einspielen")
        basis.docker(psql, "OpenSlides: Dump einspielen", timeout=7200, cwd=verz, env=umgebung, eingabe=sql)
        basis.fortschritt(mandant, 85, "OpenSlides-Wiederherstellung: OpenSlides starten")
        basis.docker(["docker", "compose", "up", "--detach"], "OpenSlides: starten", timeout=600, cwd=verz, env=umgebung)
        openslides.auf_bereitschaft_warten(mandant)
    except BereitstellungsFehler:
        # Feuerix selbst ist nicht betroffen: Status zurueck, der Fehler steht im Balken und im Protokoll
        mandant.status = ausgang
        mandant.schritt = f"OpenSlides-Wiederherstellung fehlgeschlagen bei: {mandant.schritt}"[:200]
        mandant.save(update_fields=["status", "schritt"])
        if zerstoert:
            mandant.protokollieren("ACHTUNG: Die OpenSlides-Wiederherstellung brach ab, nachdem die Datenbank geleert wurde. "
                                   "Der Dump vom Stand davor liegt in openslides/backups und kann über dieselbe Seite "
                                   "zurückgespielt werden.")
        raise
    mandant.status = ausgang if ausgang != "fehlgeschlagen" else "laeuft"
    mandant.save(update_fields=["status"])
    basis.fortschritt(mandant, 100, "Fertig")
    mandant.protokollieren(f"Wiederherstellung von OpenSlides aus {name} abgeschlossen.")


# --------------------------------------------------------------------------------------------------- Paperless
_PAPERLESS_SCHEMAS_LEEREN = _SCHEMAS_LEEREN


def angebote_paperless(mandant):
    """Exporte der Instanz (lokal): der letzte Export im Ordner export und die Exporte `_vorher-*` vom Stand vor einer
    Wiederherstellung. Ein Export enthaelt alle Dokumente und Einstellungen zum Zeitpunkt der Erstellung (Paperless
    aktualisiert den Ordner `export` bei jedem Export - aeltere Staende gibt es dort nicht). -> Liste von
    {name, ordner, zeit, anzeige}."""
    wurzel = paperless._verzeichnis(mandant) / "export"
    ergebnis = []

    def aufnehmen(ordner, name, anzeige):
        manifest = ordner / "manifest.json"
        if manifest.is_file():
            ergebnis.append({"name": name, "ordner": ordner, "anzeige": anzeige,
                             "zeit": datetime.fromtimestamp(manifest.stat().st_mtime)})

    aufnehmen(wurzel, ".", "Letzter Export (Ordner export)")
    if wurzel.is_dir():
        for unter in sorted(wurzel.glob("_vorher-*"), reverse=True):
            if unter.is_dir():
                aufnehmen(unter, unter.name, f"Stand vor einer Wiederherstellung ({unter.name})")
    return sorted(ergebnis, key=lambda e: e["zeit"], reverse=True)


def wiederherstellen_paperless(mandant, name=".", vorher_sichern=True):
    """EXPERIMENTELL. Spielt einen Paperless-Export zurueck: Paperless anhalten, Datenbank leeren, in einem einmaligen
    Container (ohne den normalen Start, der sonst schon einen Admin anlegt) `migrate` und `document_importer` ausfuehren,
    Paperless starten. Der Hersteller verlangt fuer den Importer eine leere Installation."""
    ausgang = basis.status_vor_vormerkung(mandant)
    if ausgang not in ("laeuft", "gestoppt", "fehlgeschlagen"):
        raise BereitstellungsFehler(f"Wiederherstellung nicht moeglich im Status: {ausgang}.")
    if not paperless.aktiv(mandant):
        raise BereitstellungsFehler("Paperless ist bei dieser Instanz nicht eingerichtet.")
    eintrag = next((e for e in angebote_paperless(mandant) if e["name"] == name), None)
    if eintrag is None:
        raise BereitstellungsFehler("Paperless-Export nicht gefunden (kein manifest.json).")
    befehl, cwd = paperless._compose_basis(mandant)
    quelle = "../export" if name == "." else f"../export/{name}"
    mandant.status = "wird_bereitgestellt"
    mandant.save(update_fields=["status"])
    mandant.protokollieren(f"Wiederherstellung von Paperless aus {eintrag['anzeige']} gestartet "
                           f"({'mit' if vorher_sichern else 'OHNE'} Sicherung des aktuellen Stands).")
    zerstoert = False
    try:
        basis.fortschritt(mandant, 5, "Paperless-Wiederherstellung: Paperless starten")
        paperless.steuern(mandant, "start")
        paperless.auf_bereitschaft_warten(mandant)
        if vorher_sichern:
            basis.fortschritt(mandant, 15, "Paperless-Wiederherstellung: aktuellen Stand exportieren")
            ziel = f"../export/_vorher-{datetime.now():%Y%m%d-%H%M%S}"
            basis.docker(befehl + ["exec", "-T", "webserver", "document_exporter", ziel],
                         "Paperless: Export vor der Wiederherstellung", timeout=7200, cwd=cwd)
            mandant.protokollieren(f"Stand vor der Wiederherstellung exportiert nach {paperless._verzeichnis(mandant) / 'export'} "
                                   f"({ziel.rsplit('/', 1)[-1]}).")
        basis.fortschritt(mandant, 35, "Paperless-Wiederherstellung: Paperless anhalten")
        basis.docker(befehl + ["stop", "webserver"], "Paperless: anhalten", timeout=300, cwd=cwd)
        basis.docker(befehl + ["up", "-d", "--wait", "db", "broker"], "Paperless: Datenbank starten", timeout=300, cwd=cwd)
        basis.fortschritt(mandant, 45, "Paperless-Wiederherstellung: Datenbank leeren")
        zerstoert = True
        basis.docker(befehl + ["exec", "-T", "db", "psql", "-U", "paperless", "-d", "paperless", "-v", "ON_ERROR_STOP=1",
                               "-c", _PAPERLESS_SCHEMAS_LEEREN], "Paperless: Datenbank leeren", timeout=600, cwd=cwd)
        basis.fortschritt(mandant, 55, "Paperless-Wiederherstellung: Export importieren (kann dauern)")
        basis.docker(befehl + ["run", "--rm", "--no-deps", "-T", "--user", "paperless", "--entrypoint", "",
                               "webserver", "sh", "-c",
                               f"cd /usr/src/paperless/src && python3 manage.py migrate --noinput && "
                               f"python3 manage.py document_importer {quelle} --no-progress-bar"],
                     "Paperless: Export importieren", timeout=14400, cwd=cwd)
        basis.fortschritt(mandant, 85, "Paperless-Wiederherstellung: Paperless starten")
        basis.docker(befehl + ["up", "-d"], "Paperless: starten", timeout=600, cwd=cwd)
        paperless.auf_bereitschaft_warten(mandant)
    except BereitstellungsFehler:
        mandant.status = ausgang
        mandant.schritt = f"Paperless-Wiederherstellung fehlgeschlagen bei: {mandant.schritt}"[:200]
        mandant.save(update_fields=["status", "schritt"])
        if zerstoert:
            mandant.protokollieren("ACHTUNG: Die Paperless-Wiederherstellung brach ab, nachdem die Datenbank geleert wurde. "
                                   "Der Stand davor liegt als Export _vorher-… im Ordner export und kann über dieselbe "
                                   "Seite zurückgespielt werden.")
        raise
    # Der Importer uebernimmt keine API-Tokens: die Anbindung in Feuerix mit einem neuen Token versorgen (best effort)
    try:
        paperless.in_feuerix_eintragen(mandant, paperless.token_holen(mandant), provisioning._compose_basis(mandant))
        mandant.protokollieren("Paperless-Anbindung in Feuerix mit neuem Token aktualisiert.")
    except BereitstellungsFehler as e:
        mandant.protokollieren(f"Hinweis: Die Paperless-Anbindung in Feuerix konnte nicht erneuert werden ({e}). "
                               "Bitte in Feuerix unter Verwaltung > Paperless-Anbindung einen neuen Token eintragen.")
    mandant.status = ausgang if ausgang != "fehlgeschlagen" else "laeuft"
    mandant.save(update_fields=["status"])
    dienstversionen.erfassen_sicher(mandant)
    basis.fortschritt(mandant, 100, "Fertig")
    mandant.protokollieren(f"Wiederherstellung von Paperless aus {eintrag['anzeige']} abgeschlossen.")
