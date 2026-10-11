from celery import shared_task

from . import betrieb
from .basis import BereitstellungsFehler
from .models import MandantInstanz
from .provisioning import abbauen, bereitstellen


def _ausfuehren(funktion, mandant_id, protokollieren):
    mandant = MandantInstanz.objects.get(pk=mandant_id)
    try:
        funktion(mandant)
    except BereitstellungsFehler as e:
        if protokollieren:
            mandant.protokollieren(f"Fehler: {e}")
        # sonst: Status/Protokoll sind bereits in der Funktion gesetzt - hier nur nicht crashen lassen


@shared_task
def mandant_bereitstellen_task(mandant_id):
    _ausfuehren(bereitstellen, mandant_id, protokollieren=False)


@shared_task
def mandant_stilllegen_task(mandant_id):
    _ausfuehren(abbauen, mandant_id, protokollieren=False)


@shared_task
def mandant_stoppen_task(mandant_id):
    _ausfuehren(betrieb.stoppen, mandant_id, protokollieren=False)


@shared_task
def mandant_starten_task(mandant_id):
    _ausfuehren(betrieb.starten, mandant_id, protokollieren=False)


@shared_task
def mandant_neustarten_task(mandant_id):
    _ausfuehren(betrieb.neustarten, mandant_id, protokollieren=False)


@shared_task
def dienst_update_task(mandant_id, dienst):
    from . import dienstupdate
    _ausfuehren(lambda m: dienstupdate.dienst_aktualisieren(m, dienst), mandant_id, protokollieren=False)


@shared_task
def mandant_loeschen_task(mandant_id):
    _ausfuehren(lambda m: abbauen(m, daten_loeschen=True), mandant_id, protokollieren=False)


@shared_task
def mandant_wiederherstellen_task(mandant_id, auswahl, mit_medien=True, vorher_sichern=True, dienst="feuerix"):
    from . import wiederherstellung

    def lauf(m):
        if dienst == "openslides":
            wiederherstellung.wiederherstellen_openslides(m, auswahl, vorher_sichern)
        elif dienst == "paperless":
            wiederherstellung.wiederherstellen_paperless(m, auswahl, vorher_sichern)
        else:
            wiederherstellung.wiederherstellen(m, auswahl, mit_medien, vorher_sichern)

    _ausfuehren(lauf, mandant_id, protokollieren=True)


@shared_task
def mandant_beispieldaten_task(mandant_id):
    from . import provisioning
    _ausfuehren(provisioning.beispieldaten_nachtragen, mandant_id, protokollieren=True)


@shared_task
def demo_zuruecksetzen_task(mandant_id):
    from . import demo
    _ausfuehren(demo.zuruecksetzen, mandant_id, protokollieren=True)


@shared_task
def demo_standard_task(mandant_id):
    from . import demo
    _ausfuehren(demo.standard_speichern_aktion, mandant_id, protokollieren=True)


@shared_task
def mandant_backup_task(mandant_id):
    _ausfuehren(betrieb.vollsicherung, mandant_id, protokollieren=True)


@shared_task
def mandant_update_task(mandant_id):
    from . import updates
    _ausfuehren(lambda m: betrieb.update_mit_sicherung(m, vorher=updates.checkout_aktualisieren),
                mandant_id, protokollieren=True)


@shared_task
def update_pruefen_task():
    from . import updates
    updates.pruefen()


@shared_task
def update_ausrollen_task():
    from . import updates
    try:
        updates.ausrollen()
    except BereitstellungsFehler:
        pass   # "laeuft bereits" - der laufende Rollout meldet sein Ergebnis selbst
