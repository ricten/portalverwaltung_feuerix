from django.core.management.base import BaseCommand, CommandError

from apps.mandanten import betrieb
from apps.mandanten.models import MandantInstanz


class Command(BaseCommand):
    help = "Zeigt Status und Erreichbarkeit aller Instanzen. Endet mit Fehlercode, wenn eine laufende Instanz nicht antwortet."

    def handle(self, *args, **options):
        defekt = []
        for m in MandantInstanz.objects.all():
            zeile = f"{m.kuerzel:20} {m.status:20} {m.feuerix_version or '-':8}"
            if m.status == "laeuft":
                dienste = betrieb.gesundheit(m)
                zeile += " " + " ".join(f"{d}={'ok' if ok else 'AUSFALL'}" for d, ok in dienste.items())
                if not all(dienste.values()):
                    defekt.append(m.kuerzel)
            self.stdout.write(zeile)
        if defekt:
            raise CommandError("Nicht erreichbar: " + ", ".join(defekt))
