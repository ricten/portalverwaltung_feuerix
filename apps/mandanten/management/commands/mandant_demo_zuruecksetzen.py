from django.core.management.base import BaseCommand, CommandError

from apps.mandanten import demo
from apps.mandanten.basis import BereitstellungsFehler
from apps.mandanten.models import MandantInstanz


class Command(BaseCommand):
    help = ("Setzt eine Demo-Instanz auf den gespeicherten Demo-Standard zurück (alle Daten der Instanz werden ersetzt). "
            "Mit --standard-speichern wird stattdessen der jetzige Stand als neuer Demo-Standard gespeichert. "
            "Geeignet für Cron, z. B. nächtlich.")

    def add_arguments(self, parser):
        parser.add_argument("kuerzel")
        parser.add_argument("--standard-speichern", action="store_true", help="Jetzigen Stand als Demo-Standard speichern")

    def handle(self, *args, kuerzel, standard_speichern, **opts):
        try:
            m = MandantInstanz.objects.get(kuerzel=kuerzel)
        except MandantInstanz.DoesNotExist:
            raise CommandError(f"Keine Instanz '{kuerzel}' gefunden.")
        try:
            if standard_speichern:
                demo.standard_speichern_aktion(m)
                self.stdout.write(self.style.SUCCESS(f"{kuerzel}: Demo-Standard gespeichert."))
            else:
                demo.zuruecksetzen(m)
                self.stdout.write(self.style.SUCCESS(f"{kuerzel}: Demo auf den Standard zurückgesetzt."))
        except BereitstellungsFehler as e:
            raise CommandError(str(e))
