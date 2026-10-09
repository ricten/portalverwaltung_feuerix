from django.core.management.base import BaseCommand, CommandError

from apps.mandanten import updates
from apps.mandanten.basis import BereitstellungsFehler


class Command(BaseCommand):
    help = ("Prüft, ob eine neuere Feuerix-Version verfügbar ist (git fetch) und meldet veraltete Instanzen. "
            "Mit --ausrollen wird das Update (inkl. git pull) sofort auf alle veralteten Instanzen ausgerollt.")

    def add_arguments(self, parser):
        parser.add_argument("--ausrollen", action="store_true", help="Update direkt ausrollen")

    def handle(self, *args, ausrollen, **opts):
        z = updates.pruefen()
        if z.fehler:
            raise CommandError(f"Prüfung fehlgeschlagen: {z.fehler}")
        self.stdout.write(f"Installiert: {z.lokale_version or '?'}  Verfügbar: {z.remote_version or '?'}")
        for m in z.veraltete_instanzen:
            self.stdout.write(f"  veraltet: {m.kuerzel} ({m.feuerix_version or '?'})")
        if ausrollen:
            try:
                self.stdout.write(updates.ausrollen())
            except BereitstellungsFehler as e:
                raise CommandError(str(e))
        elif z.rollout_empfohlen:
            self.stdout.write("Rollout empfohlen: --ausrollen oder im Admin „Update ausrollen“.")
