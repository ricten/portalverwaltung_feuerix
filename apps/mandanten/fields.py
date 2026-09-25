from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models


def _fernet():
    if not settings.FIELD_ENCRYPTION_KEY:
        raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY ist nicht gesetzt (siehe .env.example).")
    return Fernet(settings.FIELD_ENCRYPTION_KEY.encode())


class VerschluesseltesTextField(models.TextField):
    """Speichert Text symmetrisch verschluesselt (Fernet) in der Datenbank - fuer die Geheimnisse
    (SECRET_KEY/FIELD_ENCRYPTION_KEY/Passwoerter) jeder verwalteten Mandanten-Instanz."""

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if not value:
            return value
        return _fernet().encrypt(value.encode()).decode()

    def from_db_value(self, value, expression, connection):
        if not value:
            return value
        try:
            return _fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            raise ImproperlyConfigured(
                "Gespeicherter Wert kann nicht entschluesselt werden - falscher FIELD_ENCRYPTION_KEY?")
