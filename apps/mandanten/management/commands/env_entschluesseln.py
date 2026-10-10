import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from apps.mandanten import fields


class Command(BaseCommand):
    help = ("Entschlüsselt eine mit der Sicherung abgelegte .env-Datei (env-<hash>.enc) mit dem FIELD_ENCRYPTION_KEY der "
            "Portalverwaltung. Ohne --ausgabe wird der Klartext auf die Standardausgabe geschrieben.")

    def add_arguments(self, parser):
        parser.add_argument("datei", help="Pfad zur env-….enc")
        parser.add_argument("--ausgabe", help="Zieldatei (wird mit Rechten 600 angelegt, nie überschrieben)")

    def handle(self, *args, datei, ausgabe, **opts):
        quelle = Path(datei)
        if not quelle.is_file():
            raise CommandError(f"Datei nicht gefunden: {datei}")
        try:
            klartext = fields.entschluesseln(quelle.read_bytes())
        except ImproperlyConfigured as e:
            raise CommandError(str(e))
        if ausgabe:
            ziel = Path(ausgabe)
            if ziel.exists():
                raise CommandError(f"{ausgabe} existiert bereits - nicht überschrieben.")
            ziel.write_bytes(klartext)
            try:
                ziel.chmod(0o600)
            except OSError:
                pass
            self.stdout.write(f".env nach {ausgabe} geschrieben.")
        else:
            sys.stdout.write(klartext.decode("utf-8"))
