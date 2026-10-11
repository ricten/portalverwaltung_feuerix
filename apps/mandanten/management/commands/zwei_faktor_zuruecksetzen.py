from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice


class Command(BaseCommand):
    help = "Entfernt Authenticator-App und Notfallcodes eines Benutzers (Handy verloren). Beim nächsten Login wird neu eingerichtet."

    def add_arguments(self, parser):
        parser.add_argument("benutzer")

    def handle(self, *args, benutzer, **optionen):
        try:
            user = get_user_model().objects.get(**{get_user_model().USERNAME_FIELD: benutzer})
        except get_user_model().DoesNotExist:
            raise CommandError(f"Benutzer {benutzer} nicht gefunden.")
        anzahl = TOTPDevice.objects.filter(user=user).delete()[0] + StaticDevice.objects.filter(user=user).delete()[0]
        self.stdout.write(f"{anzahl} Eintrag/Einträge von {benutzer} entfernt - beim nächsten Login wird die Zwei-Faktor-Anmeldung neu eingerichtet.")
