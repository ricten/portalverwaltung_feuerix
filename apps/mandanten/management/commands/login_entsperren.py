from django.core.management.base import BaseCommand

from apps.mandanten.models import LoginSperre


class Command(BaseCommand):
    help = "Hebt alle Anmelde-Sperren auf (z. B. wenn sich der einzige Administrator ausgesperrt hat)."

    def handle(self, *args, **optionen):
        anzahl, _ = LoginSperre.objects.all().delete()
        self.stdout.write(f"{anzahl} Eintrag/Einträge gelöscht - Anmeldung ist wieder frei.")
