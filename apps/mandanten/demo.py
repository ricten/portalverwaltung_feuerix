"""Demo-Instanzen: speichert direkt nach dem Anlegen der Beispieldaten einen **Demo-Standard** (Datenbank und Medien) und setzt
die Instanz jederzeit darauf zurueck - z. B. nachdem Besucher eigene Daten eingegeben haben. Der Standard liegt in
`<instanz>/demo-standard/` (bei aktivem Backup-Ziel zusaetzlich auf der Freigabe unter `<kuerzel>\\demo-standard\\`) und laesst sich
mit "Aktuellen Stand als Demo-Standard speichern" auch neu festlegen."""
import re
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from . import backupziel, basis, betrieb, provisioning
from .basis import BereitstellungsFehler
from .wiederherstellung import _SCHEMA_LEEREN

DB_NAME = "db.sql.gz"
MEDIA_NAME = "media.tar.gz"
_STAMP = re.compile(r"\(\*-(\d{8}-\d{6})\)")        # Ausgabe von backup.sh: "Sicherung abgeschlossen: /data/backups (*-STAMP)"


def standard_ordner(mandant):
    return Path(settings.INSTANZEN_VERZEICHNIS) / mandant.kuerzel / "demo-standard"


def standard_vorhanden(mandant):
    return bool(mandant.demo_standard_am) and (standard_ordner(mandant) / DB_NAME).is_file()


def standard_sichern(mandant):
    """Speichert den jetzigen Stand der laufenden Instanz (Datenbank und Medien) als Demo-Standard."""
    compose = provisioning._compose_basis(mandant)
    ausgabe = basis.docker(compose + ["exec", "-T", "web", "sh", "/app/scripts/backup.sh"], "Demo-Standard: sichern",
                           timeout=3600)
    treffer = _STAMP.search(ausgabe or "")
    if not treffer:
        raise BereitstellungsFehler("Demo-Standard: Der Zeitstempel der Sicherung ist in der Ausgabe nicht zu finden.")
    stamp = treffer.group(1)
    ordner = standard_ordner(mandant)
    ordner.mkdir(parents=True, exist_ok=True)
    basis.docker(compose + ["cp", f"web:/data/backups/db-{stamp}.sql.gz", str(ordner / DB_NAME)],
                 "Demo-Standard: Datenbank kopieren", timeout=3600)
    basis.docker(compose + ["cp", f"web:/data/backups/media-{stamp}.tar.gz", str(ordner / MEDIA_NAME)],
                 "Demo-Standard: Medien kopieren", timeout=3600)
    mandant.demo_standard_am = timezone.now()
    mandant.save(update_fields=["demo_standard_am"])
    mandant.protokollieren(f"Demo-Standard gespeichert ({ordner}).")
    if backupziel.aktuell():
        try:
            backupziel.hochladen(mandant, ordner, "demo-standard")
            mandant.protokollieren("Demo-Standard zusätzlich auf der Netzwerkfreigabe abgelegt.")
        except BereitstellungsFehler as e:
            mandant.protokollieren(f"Hinweis: Demo-Standard konnte nicht auf die Freigabe hochgeladen werden ({e}).")


def standard_speichern_aktion(mandant):
    """Admin-Aktion/Befehl: aktuellen Stand als Demo-Standard speichern, mit sichtbarem Fortschritt."""
    ausgang = basis.status_vor_vormerkung(mandant)
    if ausgang != "laeuft":
        raise BereitstellungsFehler(f"Demo-Standard nur für laufende Instanzen (Status: {ausgang}).")
    if not mandant.demo:
        raise BereitstellungsFehler("Der Demo-Standard gehört zu Demo-Instanzen (Haken „Demo-Instanz“ setzen).")
    mandant.status = "wird_bereitgestellt"
    mandant.save(update_fields=["status"])
    try:
        basis.fortschritt(mandant, 30, "Demo-Standard speichern")
        standard_sichern(mandant)
    except BereitstellungsFehler:
        mandant.status = ausgang
        mandant.schritt = f"Demo-Standard fehlgeschlagen bei: {mandant.schritt}"[:200]
        mandant.save(update_fields=["status", "schritt"])
        raise
    mandant.status = ausgang
    mandant.save(update_fields=["status"])
    basis.fortschritt(mandant, 100, "Fertig")


def zuruecksetzen(mandant):
    """Setzt die Demo-Instanz auf den gespeicherten Demo-Standard zurueck: alle Daten der Instanz (Datenbank und hochgeladene
    Medien) werden durch den Standard ersetzt. Das Admin-Passwort wird danach wieder auf den im Portal gespeicherten Wert gesetzt."""
    ausgang = basis.status_vor_vormerkung(mandant)
    if ausgang not in ("laeuft", "gestoppt", "fehlgeschlagen"):
        raise BereitstellungsFehler(f"Zurücksetzen nicht möglich im Status: {ausgang}.")
    if not mandant.demo:
        raise BereitstellungsFehler("Zurücksetzen gibt es nur für Demo-Instanzen.")
    if not standard_vorhanden(mandant):
        raise BereitstellungsFehler("Es ist noch kein Demo-Standard gespeichert (Aktion „Aktuellen Stand als Demo-Standard speichern“).")
    ordner = standard_ordner(mandant)
    mit_medien = (ordner / MEDIA_NAME).is_file()
    mandant.status = "wird_bereitgestellt"
    mandant.save(update_fields=["status"])
    mandant.protokollieren(f"Demo wird auf den Standard vom {mandant.demo_standard_am:%d.%m.%Y %H:%M} zurückgesetzt.")
    zerstoert = False
    try:
        basis.fortschritt(mandant, 5, "Demo zurücksetzen: Instanz starten")
        provisioning.stack_steuern(mandant, "start")
        provisioning.auf_bereitschaft_warten(mandant)
        compose = provisioning._compose_basis(mandant)
        basis.fortschritt(mandant, 20, "Demo zurücksetzen: Standard bereitstellen")
        namen = ["demo-standard-db.sql.gz"]
        basis.docker(compose + ["cp", str(ordner / DB_NAME), f"web:/data/backups/{namen[0]}"],
                     "Demo-Standard in den Container kopieren", timeout=3600)
        if mit_medien:
            namen.append("demo-standard-media.tar.gz")
            basis.docker(compose + ["cp", str(ordner / MEDIA_NAME), f"web:/data/backups/{namen[1]}"],
                         "Demo-Standard (Medien) in den Container kopieren", timeout=3600)
        basis.fortschritt(mandant, 35, "Demo zurücksetzen: Worker anhalten")
        basis.docker(compose + ["stop", "worker"], "Worker anhalten", timeout=300)
        basis.fortschritt(mandant, 45, "Demo zurücksetzen: Daten entfernen")
        zerstoert = True
        basis.docker(compose + ["exec", "-T", "web", "sh", "-c", _SCHEMA_LEEREN], "Datenbank leeren", timeout=600)
        if mit_medien:
            basis.docker(compose + ["exec", "-T", "web", "sh", "-c", "find /data/media -mindepth 1 -delete"],
                         "Medien leeren", timeout=600)
        basis.fortschritt(mandant, 60, "Demo zurücksetzen: Standard einspielen")
        basis.docker(compose + ["exec", "-T", "web", "sh", "/app/scripts/restore.sh"] + namen,
                     "Demo-Standard einspielen", timeout=7200)
        basis.fortschritt(mandant, 80, "Demo zurücksetzen: neu starten")
        provisioning.stack_steuern(mandant, "start")        # Worker wieder starten
        provisioning.stack_steuern(mandant, "restart")      # web neu: Migrationen laufen beim Start
        provisioning.auf_bereitschaft_warten(mandant)
        basis.fortschritt(mandant, 92, "Demo zurücksetzen: Admin-Passwort angleichen")
        betrieb.admin_passwort_zuruecksetzen(mandant, passwort=mandant.admin_passwort, _pruefen=False)
    except BereitstellungsFehler:
        mandant.status = "fehlgeschlagen" if zerstoert else ausgang
        mandant.schritt = f"Demo-Zurücksetzen fehlgeschlagen bei: {mandant.schritt}"[:200]
        mandant.save(update_fields=["status", "schritt"])
        if zerstoert:
            mandant.protokollieren("ACHTUNG: Das Zurücksetzen brach ab, nachdem die Daten entfernt wurden. Bitte „Demo auf Standard "
                                   "zurücksetzen“ erneut ausführen (der Demo-Standard bleibt erhalten).")
            provisioning.proxy_nachziehen_best_effort(mandant)
        raise
    mandant.status = "laeuft"
    mandant.save(update_fields=["status"])
    basis.fortschritt(mandant, 100, "Fertig")
    mandant.protokollieren("Demo auf den Standard zurückgesetzt.")
