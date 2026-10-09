from django.core.management.base import BaseCommand

from apps.mandanten import onlinecheck


class Command(BaseCommand):
    help = ("Prüft, ob die laufenden Instanzen und das Backup-Ziel erreichbar sind, und speichert das Ergebnis "
            "(Anzeige im Admin). Gedacht für einen systemd-Timer oder Cron, z. B. alle 5 Minuten.")

    def add_arguments(self, parser):
        parser.add_argument("--streng", action="store_true", help="Mit Fehlercode 1 enden, wenn etwas offline ist")
        parser.add_argument("--timeout", type=int, default=5, help="Sekunden je Anfrage")

    def handle(self, *args, streng, timeout, **opts):
        r = onlinecheck.alle_pruefen(timeout=timeout)
        probleme = 0
        for kuerzel, (online, ausgefallen) in r["instanzen"].items():
            self.stdout.write(f"{kuerzel:20} {'online' if online else 'OFFLINE ' + ', '.join(ausgefallen)}")
            probleme += 0 if online else 1
        if r["backupziel"] is not None:
            self.stdout.write(f"{'Backup-Ziel':20} {'online' if r['backupziel'] else 'OFFLINE'}")
            probleme += 0 if r["backupziel"] else 1
        if not r["instanzen"] and r["backupziel"] is None:
            self.stdout.write("Nichts zu prüfen (keine laufende Instanz, kein Backup-Ziel).")
        if streng and probleme:
            raise SystemExit(1)
