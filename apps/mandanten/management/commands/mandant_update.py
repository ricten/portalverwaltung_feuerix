from django.core.management.base import BaseCommand, CommandError

from apps.mandanten import betrieb
from apps.mandanten.basis import BereitstellungsFehler


class Command(BaseCommand):
    help = ("Rollt den aktuellen Feuerix-Code (Checkout unter FEUERIX_REPO_PFAD) nacheinander auf alle "
            "laufenden Instanzen aus.")

    def add_arguments(self, parser):
        parser.add_argument("--git-pull", action="store_true", help="vorher 'git pull --ff-only' im Checkout")
        parser.add_argument("--pause", type=int, default=10, help="Sekunden Pause zwischen den Instanzen")

    def handle(self, *args, **options):
        try:
            ok, fehler = betrieb.alle_aktualisieren(pause_sekunden=options["pause"], git_pull=options["git_pull"])
        except BereitstellungsFehler as e:
            raise CommandError(str(e))
        for k in ok:
            self.stdout.write(self.style.SUCCESS(f"{k}: aktualisiert"))
        for k, text in fehler.items():
            self.stderr.write(f"{k}: FEHLER {text}")
        if fehler:
            raise CommandError(f"{len(fehler)} Instanz(en) nicht aktualisiert.")
