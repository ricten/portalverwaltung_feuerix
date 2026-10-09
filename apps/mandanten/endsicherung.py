"""Endgueltiges Loeschen einer Instanz: vorher eine vollstaendige Abschlusssicherung, danach Archivierung des
Instanzordners. Wird irgendein Schritt der Sicherung nicht erfolgreich, ABBRICHT das Loeschen - es wird nichts entfernt.

Was gesichert wird: Feuerix (Datenbank + Medien ueber backup.sh, bei aktivem Backup-Ziel auch auf die Netzwerkfreigabe),
Paperless-ngx (Export mit document_exporter) und OpenSlides (Datenbank-Dump). Der Instanzordner (mit .env und den
Schluesseln, die zum Entschluesseln der Sicherung noetig sind) wird nach dem Loeschen der Volumes nach
`<INSTANZEN_VERZEICHNIS>/_geloescht/<kuerzel>-<Zeitstempel>` verschoben und nie automatisch entfernt."""
import shutil
from datetime import datetime
from pathlib import Path

from django.conf import settings

from . import basis, betrieb, dienstupdate, dienstversionen, openslides, paperless, provisioning
from .basis import BereitstellungsFehler


def _paperless_vorhanden(mandant):
    return paperless.aktiv(mandant) and (paperless._verzeichnis(mandant) / ".env").exists()


def _openslides_vorhanden(mandant):
    return openslides.aktiv(mandant) and (openslides._verzeichnis(mandant) / "docker-compose.yml").exists()


def abschlusssicherung(mandant):
    """Startet die Instanz bei Bedarf (gestoppt/stillgelegt) und sichert alle Dienste. Wirft BereitstellungsFehler,
    wenn etwas nicht gesichert werden konnte."""
    paperless_da, openslides_da = _paperless_vorhanden(mandant), _openslides_vorhanden(mandant)
    basis.fortschritt(mandant, 5, "Abschlusssicherung: Instanz starten")
    provisioning.stack_steuern(mandant, "start")
    if paperless_da:
        paperless.steuern(mandant, "start")
    if openslides_da:
        openslides.steuern(mandant, "start")
    basis.fortschritt(mandant, 15, "Abschlusssicherung: warten, bis alles läuft")
    provisioning.auf_bereitschaft_warten(mandant)
    if paperless_da:
        paperless.auf_bereitschaft_warten(mandant)
    if openslides_da:
        openslides.auf_bereitschaft_warten(mandant)

    basis.fortschritt(mandant, 25, "Abschlusssicherung: Feuerix sichern")
    betrieb.backup(mandant, _pruefen=False)
    gesichert = ["Feuerix"]
    if paperless_da:
        basis.fortschritt(mandant, 40, "Abschlusssicherung: Paperless exportieren")
        dienstupdate.paperless_export(mandant, "Paperless: Export vor dem Löschen")
        gesichert.append("Paperless")
    if openslides_da:
        basis.fortschritt(mandant, 50, "Abschlusssicherung: OpenSlides sichern")
        dienstupdate.openslides_dump(mandant, dienstversionen.openslides_konfiguriert(mandant) or "unbekannt",
                                     "OpenSlides: Datenbank-Dump vor dem Löschen")
        gesichert.append("OpenSlides")
    mandant.protokollieren("Abschlusssicherung vollständig (" + ", ".join(gesichert) + ") - Daten werden jetzt gelöscht.")


def archiv_verzeichnis():
    return Path(settings.INSTANZEN_VERZEICHNIS) / "_geloescht"


def archivieren(mandant):
    """Verschiebt den Instanzordner (.env, Sicherungen, Exporte) nach _geloescht/. -> Zielpfad oder None."""
    quelle = Path(settings.INSTANZEN_VERZEICHNIS) / mandant.kuerzel
    if not quelle.exists():
        return None
    ziel = archiv_verzeichnis() / f"{mandant.kuerzel}-{datetime.now():%Y%m%d-%H%M%S}"
    ziel.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(quelle), str(ziel))
    mandant.protokollieren(f"Instanzordner mit .env, Sicherungen und Exporten aufbewahrt in {ziel}.")
    return ziel
