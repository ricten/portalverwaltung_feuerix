from django.core.management.base import BaseCommand, CommandError

from apps.mandanten.basis import BereitstellungsFehler
from apps.mandanten.models import MandantInstanz
from apps.mandanten.provisioning import beispieldaten_nachtragen


class Command(BaseCommand):
    help = ("Legt in einer laufenden DEMO-Instanz Beispieldaten an (fiktive Mitglieder, Rechnungen, Zahlungen, Inventar). "
            "Die Instanz muss als Demo-Instanz markiert sein und bekommt die Daten nur einmal.")

    def add_arguments(self, parser):
        parser.add_argument("kuerzel")
        parser.add_argument("--anzahl", type=int, help="Anzahl Mitglieder (überschreibt die Einstellung der Instanz)")
        parser.add_argument("--ohne-inventar", action="store_true", help="Kein Beispiel-Inventar anlegen")

    def handle(self, *args, kuerzel, anzahl, ohne_inventar, **opts):
        try:
            m = MandantInstanz.objects.get(kuerzel=kuerzel)
        except MandantInstanz.DoesNotExist:
            raise CommandError(f"Keine Instanz '{kuerzel}' gefunden.")
        if anzahl:
            m.demo_anzahl = anzahl
        if ohne_inventar:
            m.demo_inventar = False
        m.save(update_fields=["demo_anzahl", "demo_inventar"])
        try:
            beispieldaten_nachtragen(m)
        except BereitstellungsFehler as e:
            raise CommandError(str(e))
        self.stdout.write(self.style.SUCCESS(f"{kuerzel}: Beispieldaten angelegt."))
