"""OpenSlides je Mandant - wird in einem eigenen Schritt umgesetzt."""
from .basis import BereitstellungsFehler


def aktiv(mandant):
    return bool(mandant.openslides_domain)


def bereitstellen(mandant, feuerix_compose_basis):
    raise BereitstellungsFehler("OpenSlides: noch nicht implementiert.")


def abbauen(mandant, daten_loeschen=False):
    return
