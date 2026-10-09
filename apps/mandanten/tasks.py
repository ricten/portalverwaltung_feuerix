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
def mandant_pausieren_task(mandant_id):
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
def mandant_backup_task(mandant_id):
    _ausfuehren(betrieb.backup, mandant_id, protokollieren=True)


@shared_task
def mandant_update_task(mandant_id):
    _ausfuehren(betrieb.update_mit_sicherung, mandant_id, protokollieren=True)


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
