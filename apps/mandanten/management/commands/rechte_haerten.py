from django.core.management.base import BaseCommand

from apps.mandanten import rechte


class Command(BaseCommand):
    help = ("Setzt die Dateirechte im Instanzverzeichnis: Ordner 0700, .env-Dateien 0600, damit andere Benutzer des Servers keine "
            "Geheimnisse und Sicherungen lesen können. Läuft auch automatisch mit dem Online-Check. Ordner export/consume von "
            "Paperless bleiben unberührt.")

    def handle(self, *args, **opts):
        geaendert, fehler = rechte.haerten()
        for pfad in geaendert:
            self.stdout.write(f"gesichert: {pfad}")
        for text in fehler:
            self.stderr.write(f"Fehler: {text}  (gehört der Ordner einem anderen Benutzer, z. B. root? -> sudo chown -R $USER: <Ordner>)")
        self.stdout.write(f"{len(geaendert)} Pfad(e) angepasst, {len(fehler)} Fehler.")
