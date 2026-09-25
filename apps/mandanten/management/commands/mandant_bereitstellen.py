from django.core.management.base import BaseCommand, CommandError

from apps.mandanten.models import MandantInstanz
from apps.mandanten.provisioning import BereitstellungsFehler, bereitstellen


class Command(BaseCommand):
    help = ("Stellt eine Mandanten-Instanz bereit (Ausbaustufe 1: nur Feuerix, ohne Reverse-Proxy-Eintrag "
           "und ohne OpenSlides/Paperless).")

    def add_arguments(self, parser):
        parser.add_argument("kuerzel")

    def handle(self, *args, **options):
        try:
            mandant = MandantInstanz.objects.get(kuerzel=options["kuerzel"])
        except MandantInstanz.DoesNotExist:
            raise CommandError(f"Keine Mandanten-Instanz mit Kürzel '{options['kuerzel']}' gefunden.")
        try:
            bereitstellen(mandant)
        except BereitstellungsFehler as e:
            raise CommandError(str(e))
        self.stdout.write(self.style.SUCCESS(f"Instanz '{mandant.kuerzel}' läuft auf Port {mandant.web_port}."))
