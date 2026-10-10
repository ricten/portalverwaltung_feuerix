"""Update von Paperless-ngx und OpenSlides einer Instanz - nur auf ausdruecklichen Klick, nie automatisch.

Sicherheitsnetz: Vor jeder Aenderung wird eine Sicherung des Dienstes angelegt (Paperless: Export mit `document_exporter`
in `<instanz>/paperless/export`, OpenSlides: Datenbank-Dump in `<instanz>/openslides/backups`). Schlaegt sie fehl, wird
NICHTS veraendert. Beim Update laeuft die Instanz selbst (Feuerix) unveraendert weiter."""
import re
from datetime import datetime
from pathlib import Path

from django.conf import settings

from . import backupziel, basis, benachrichtigung, dienstversionen, openslides, paperless
from .basis import WARTESCHLANGE_DIENST, BereitstellungsFehler
from .dienstversionen import NAMEN
from .models import UpdateZustand
from .updates import versionstupel


def _schritt(mandant, prozent, text):
    basis.fortschritt(mandant, prozent, text)


def _auf_freigabe(mandant, ordner, unterordner, rekursiv=False):
    """Legt die Sicherung zusaetzlich auf der Netzwerkfreigabe ab, falls ein Backup-Ziel aktiv ist. Schlaegt das fehl,
    wird der Vorgang (Update/Loeschen) abgebrochen - die Sicherung gilt dann nicht als vollstaendig."""
    if not backupziel.aktuell():
        return
    try:
        neu = backupziel.hochladen(mandant, ordner, unterordner, rekursiv)
    except BereitstellungsFehler as e:
        mandant.protokollieren(f"{unterordner}-Sicherung lokal angelegt, aber der Upload zur Netzwerkfreigabe schlug fehl: {e}")
        benachrichtigung.senden("backup", f"Sicherung ({unterordner}) von {mandant.kuerzel} fehlgeschlagen",
                                f"Der Upload der {unterordner}-Sicherung der Instanz {mandant.kuerzel} auf die "
                                f"Netzwerkfreigabe ist fehlgeschlagen:\n\n{str(e)[:1500]}")
        raise BereitstellungsFehler(f"Upload zur Netzwerkfreigabe fehlgeschlagen: {e}")
    mandant.protokollieren(f"{unterordner}-Sicherung auf der Netzwerkfreigabe abgelegt ({neu} neue Datei(en)).")


def paperless_export(mandant, bezeichnung="Paperless: Export vor dem Update"):
    """Exportiert alle Dokumente und Metadaten mit `document_exporter` in den Ordner export der Instanz."""
    befehl, cwd = paperless._compose_basis(mandant)
    basis.docker(befehl + ["exec", "-T", "webserver", "document_exporter", "../export"],
                 bezeichnung, timeout=7200, cwd=cwd)
    pfad = paperless._verzeichnis(mandant) / "export"
    mandant.protokollieren(f"Paperless-Export angelegt in {pfad}.")
    _auf_freigabe(mandant, pfad, "paperless", rekursiv=True)
    return pfad


def openslides_dump(mandant, markierung, bezeichnung="OpenSlides: Datenbank-Dump vor dem Update"):
    """Sichert die OpenSlides-Datenbank als SQL-Dump nach `<instanz>/openslides/backups`. Wirft einen Fehler, wenn der
    Dump fehlschlaegt oder leer ist."""
    verz = openslides._verzeichnis(mandant)
    dump = basis.docker(["docker", "compose", "exec", "-T", "postgres", "pg_dump", "-U", "openslides", "-d", "openslides"],
                        bezeichnung, timeout=3600, cwd=verz, env=openslides._env(mandant))
    if not dump.strip():
        raise BereitstellungsFehler("OpenSlides: Datenbank-Dump ist leer - abgebrochen.")
    ziel = verz / "backups"
    ziel.mkdir(exist_ok=True)
    datei = ziel / f"openslides-{markierung}-{datetime.now():%Y%m%d-%H%M%S}.sql"
    datei.write_text(dump, encoding="utf-8")
    mandant.protokollieren(f"OpenSlides-Datenbank gesichert in {datei}.")
    _auf_freigabe(mandant, ziel, "openslides")
    return datei


def paperless_aktualisieren(mandant):
    befehl, cwd = paperless._compose_basis(mandant)
    _schritt(mandant, 20, "Paperless: Export anlegen (Sicherung)")
    paperless_export(mandant)
    _schritt(mandant, 45, "Paperless: neues Image laden")
    basis.docker(befehl + ["pull"], "Paperless: docker compose pull", timeout=1800, cwd=cwd)
    _schritt(mandant, 70, "Paperless: neu starten")
    basis.docker(befehl + ["up", "-d"], "Paperless: docker compose up", timeout=600, cwd=cwd)
    _schritt(mandant, 85, "Paperless: warten, bis es antwortet")
    paperless.auf_bereitschaft_warten(mandant)
    neu = dienstversionen.paperless_laufend(mandant)
    if neu:
        mandant.paperless_version = neu
        mandant.save(update_fields=["paperless_version"])
    return neu


def openslides_aktualisieren(mandant, neu):
    """Patch-Update (gleiche Haupt- und Nebenversion, z. B. 4.3.0 -> 4.3.1) nach der offiziellen OpenSlides-Anleitung:
    Tag in der config.yml setzen, Compose-Datei mit `osmanage config --force` neu erzeugen, `pull`, `up --detach`.
    Spruenge der Minor-/Major-Version bringen Datenmigrationen und teils einen PostgreSQL-Wechsel mit (siehe
    INSTALL.md von OpenSlides) und werden NICHT automatisch durchgefuehrt."""
    verz = openslides._verzeichnis(mandant)
    compose, config = verz / "docker-compose.yml", verz / "config.yml"
    alt = dienstversionen.openslides_konfiguriert(mandant)
    if not compose.exists() or not config.exists() or not alt:
        raise BereitstellungsFehler("OpenSlides: Stack-Konfiguration der Instanz nicht gefunden.")
    if not re.fullmatch(r"\d+\.\d+\.\d+", neu) or versionstupel(neu) <= versionstupel(alt):
        raise BereitstellungsFehler(f"OpenSlides: Version {neu} ist keine Aktualisierung von {alt}.")
    if versionstupel(neu)[:2] != versionstupel(alt)[:2]:
        raise BereitstellungsFehler(
            f"OpenSlides: Der Sprung {alt} → {neu} ist kein Patch-Update. Bitte von Hand nach der Anleitung von "
            "OpenSlides durchführen (Datenmigrationen, ggf. neue PostgreSQL-Version).")
    umgebung = openslides._env(mandant)
    compose_befehl = ["docker", "compose"]

    _schritt(mandant, 15, "OpenSlides: Datenbank sichern")
    openslides_dump(mandant, alt)

    _schritt(mandant, 35, f"OpenSlides: Version {alt} → {neu} eintragen")
    original_compose, original_config = compose.read_text(encoding="utf-8"), config.read_text(encoding="utf-8")
    neue_config, anzahl = re.subn(rf"""(?m)^(\s*tag:\s*['"]?){re.escape(alt)}""", rf"\g<1>{neu}", original_config)
    if not anzahl:
        raise BereitstellungsFehler(f"OpenSlides: Tag {alt} in config.yml nicht gefunden - nichts geändert.")
    osmanage, vorlage = openslides.werkzeuge_bereitstellen()
    config.write_text(neue_config, encoding="utf-8")
    try:
        basis.docker([str(osmanage), "config", "--force", "--config", "config.yml", "--template", str(vorlage), "."],
                     "OpenSlides: Compose-Datei neu erzeugen", timeout=300, cwd=verz, env=umgebung)
        if dienstversionen.openslides_konfiguriert(mandant) != neu:
            raise BereitstellungsFehler("OpenSlides: Die neue Compose-Datei enthält die Version "
                                        f"{neu} nicht - nichts geändert.")
        _schritt(mandant, 50, "OpenSlides: neue Images laden")
        basis.docker(compose_befehl + ["pull"], "OpenSlides: docker compose pull", timeout=1800, cwd=verz,
                     env=umgebung)
        _schritt(mandant, 70, "OpenSlides: neu starten")
        basis.docker(compose_befehl + ["up", "--detach"], "OpenSlides: docker compose up", timeout=600, cwd=verz,
                     env=umgebung)
    except BereitstellungsFehler:
        compose.write_text(original_compose, encoding="utf-8")      # nichts Halbes zuruecklassen
        config.write_text(original_config, encoding="utf-8")
        raise
    _schritt(mandant, 85, "OpenSlides: warten, bis es antwortet")
    openslides.auf_bereitschaft_warten(mandant)
    mandant.openslides_version = neu
    mandant.save(update_fields=["openslides_version"])
    return neu


def _ausgang_ok(mandant):
    return mandant.status == "laeuft" or (
        mandant.status == "wird_bereitgestellt" and mandant.schritt == WARTESCHLANGE_DIENST)


def dienst_aktualisieren(mandant, dienst):
    """Aktualisiert Paperless-ngx (`dienst="paperless"`) oder OpenSlides (`"openslides"`) auf die zuletzt geprueft
    gefundene neueste Version. Die Instanz steht dabei auf "In Arbeit" und danach wieder auf "Laeuft"."""
    if dienst not in NAMEN:
        raise BereitstellungsFehler(f"Unbekannter Dienst: {dienst}")
    name = NAMEN[dienst]
    if not _ausgang_ok(mandant):
        raise BereitstellungsFehler(f"Update von {name} nur für laufende Instanzen möglich (Status: {mandant.status}).")
    if not getattr(mandant, f"{dienst}_domain"):
        raise BereitstellungsFehler(f"{name} ist bei dieser Instanz nicht eingerichtet.")
    zustand = UpdateZustand.holen()
    if zustand.rollout_laeuft:
        raise BereitstellungsFehler("Während eines Rollouts ist kein Dienst-Update möglich.")
    neu = getattr(zustand, f"{dienst}_neueste")
    if not neu:
        raise BereitstellungsFehler(f"Neueste Version von {name} ist unbekannt - bitte zuerst die Update-Prüfung ausführen.")
    mandant.status = "wird_bereitgestellt"
    mandant.save(update_fields=["status"])
    alt = getattr(mandant, f"{dienst}_version") or "?"
    mandant.protokollieren(f"Update von {name} gestartet ({alt} → {neu}).")
    try:
        if dienst == "paperless":
            erreicht = paperless_aktualisieren(mandant)
        else:
            erreicht = openslides_aktualisieren(mandant, neu)
    except BereitstellungsFehler as e:
        # Feuerix selbst ist nicht betroffen: die Instanz laeuft weiter, der Fehler steht im Balken und im Protokoll
        mandant.status = "laeuft"
        mandant.schritt = f"Update von {name} fehlgeschlagen bei: {mandant.schritt}"[:200]
        mandant.save(update_fields=["status", "schritt"])
        mandant.protokollieren(f"Update von {name} fehlgeschlagen: {e}")
        benachrichtigung.senden(
            "update", f"Update von {name} bei {mandant.kuerzel} fehlgeschlagen",
            f"Das Update von {name} der Instanz {mandant.verein_name} ({mandant.kuerzel}) ist fehlgeschlagen:\n\n"
            f"{str(e)[:1500]}\n\nDie Instanz selbst läuft weiter; die Sicherung vor dem Update bleibt erhalten.")
        raise
    mandant.status = "laeuft"
    mandant.save(update_fields=["status"])
    basis.fortschritt(mandant, 100, "Fertig")
    mandant.protokollieren(f"Update von {name} abgeschlossen (jetzt {erreicht or neu}).")
    return erreicht
