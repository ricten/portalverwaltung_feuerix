from celery import shared_task

from .models import MandantInstanz
from .provisioning import BereitstellungsFehler, bereitstellen


@shared_task
def mandant_bereitstellen_task(mandant_id):
    mandant = MandantInstanz.objects.get(pk=mandant_id)
    try:
        bereitstellen(mandant)
    except BereitstellungsFehler:
        pass  # Status/Protokoll sind bereits in bereitstellen() gesetzt - hier nur nicht crashen lassen
