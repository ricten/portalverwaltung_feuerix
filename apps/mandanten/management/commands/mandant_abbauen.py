from django.core.management.base import BaseCommand, CommandError

from apps.mandanten.models import MandantInstanz
from apps.mandanten.provisioning import BereitstellungsFehler, abbauen


class Command(BaseCommand):
    help = ("Stoppt eine Mandanten-Instanz und nimmt sie aus dem Reverse Proxy. Ohne --daten-loeschen bleiben "
           "Datenbank und Medien erhalten (Status 'pausiert' = stillgelegt, mit mandant_dienst <kuerzel> start wieder startbar).")

    def add_arguments(self, parser):
        parser.add_argument("kuerzel")
        parser.add_argument("--daten-loeschen", action="store_true",
                            help="Volumes (Datenbank, Medien) UNWIDERRUFLICH mit entfernen - vorher wird eine "
                                 "Abschlusssicherung angelegt; scheitert sie, wird nichts gelöscht")
        parser.add_argument("--ohne-sicherung", action="store_true",
                            help="NOTFALL: mit --daten-loeschen die Abschlusssicherung überspringen")

    def handle(self, *args, **options):
        try:
            mandant = MandantInstanz.objects.get(kuerzel=options["kuerzel"])
        except MandantInstanz.DoesNotExist:
            raise CommandError(f"Keine Mandanten-Instanz mit Kürzel '{options['kuerzel']}' gefunden.")
        try:
            if options["ohne_sicherung"] and not options["daten_loeschen"]:
                raise CommandError("--ohne-sicherung gilt nur zusammen mit --daten-loeschen.")
            abbauen(mandant, daten_loeschen=options["daten_loeschen"], sicherung=not options["ohne_sicherung"])
        except BereitstellungsFehler as e:
            raise CommandError(str(e))
        self.stdout.write(self.style.SUCCESS(f"Instanz '{mandant.kuerzel}' abgebaut (Status: {mandant.status})."))
