from django.core.management.base import BaseCommand, CommandError

from apps.mandanten import betrieb
from apps.mandanten.basis import BereitstellungsFehler
from apps.mandanten.models import MandantInstanz

AKTIONEN = {"stop": betrieb.stoppen, "start": betrieb.starten, "restart": betrieb.neustarten}


class Command(BaseCommand):
    help = "Stoppt, startet oder startet eine Instanz neu (Container bleiben erhalten, kein Neubau)."

    def add_arguments(self, parser):
        parser.add_argument("kuerzel")
        parser.add_argument("aktion", choices=sorted(AKTIONEN))

    def handle(self, *args, kuerzel, aktion, **opts):
        try:
            mandant = MandantInstanz.objects.get(kuerzel=kuerzel)
        except MandantInstanz.DoesNotExist:
            raise CommandError(f"Instanz '{kuerzel}' nicht gefunden.")
        try:
            AKTIONEN[aktion](mandant)
        except BereitstellungsFehler as e:
            raise CommandError(str(e))
        self.stdout.write(f"{kuerzel}: {aktion} ausgeführt (Status: {mandant.status}).")
