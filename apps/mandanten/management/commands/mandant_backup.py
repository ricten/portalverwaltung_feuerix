from django.core.management.base import BaseCommand, CommandError

from apps.mandanten import betrieb
from apps.mandanten.basis import BereitstellungsFehler
from apps.mandanten.models import MandantInstanz


class Command(BaseCommand):
    help = "Sichert Datenbank und Medien einer laufenden Instanz (ohne Kürzel: aller laufenden Instanzen)."

    def add_arguments(self, parser):
        parser.add_argument("kuerzel", nargs="?")

    def handle(self, *args, **options):
        mandanten = MandantInstanz.objects.filter(status="laeuft")
        if options["kuerzel"]:
            mandanten = MandantInstanz.objects.filter(kuerzel=options["kuerzel"])
            if not mandanten:
                raise CommandError(f"Keine Instanz '{options['kuerzel']}' gefunden.")
        fehler = 0
        for m in mandanten:
            try:
                ziel = betrieb.backup(m)
                self.stdout.write(self.style.SUCCESS(f"{m.kuerzel}: gesichert nach {ziel}"))
            except BereitstellungsFehler as e:
                fehler += 1
                self.stderr.write(f"{m.kuerzel}: {e}")
        if fehler:
            raise CommandError(f"{fehler} Sicherung(en) fehlgeschlagen.")
