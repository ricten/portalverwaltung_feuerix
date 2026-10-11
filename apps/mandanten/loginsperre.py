"""Vorübergehende Sperre der Anmeldung nach zu vielen Fehlversuchen (Schutz vor Passwort-Raten).

Gezaehlt wird getrennt nach Adresse des Clients und nach Benutzername; ist einer von beiden gesperrt, wird auch ein
richtiges Passwort abgelehnt. Nach `LOGIN_SPERRE_MINUTEN` ist die Anmeldung wieder frei. Die Zaehler liegen in der
Datenbank (mehrere Gunicorn-Prozesse teilen sich den Stand). Aufheben von Hand: `manage.py login_entsperren`."""
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.dispatch import receiver
from django.utils import timezone

from .models import LoginSperre


def client_adresse(request):
    """Adresse des Clients. Hinter dem lokalen Reverse Proxy (Caddy) steht sie als letzter Eintrag in X-Forwarded-For -
    der Proxy haengt sie selbst an, vorangestellte Angaben des Clients zaehlen nicht."""
    adresse = request.META.get("REMOTE_ADDR", "")
    if adresse in ("127.0.0.1", "::1"):
        weiter = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if weiter.strip():
            adresse = weiter.split(",")[-1].strip()
    return adresse


def _schluessel(request, benutzername):
    liste = []
    if request is not None:
        liste.append(f"ip:{client_adresse(request)}")
    if benutzername:
        liste.append(f"user:{str(benutzername).strip().lower()[:150]}")
    return liste


def gesperrt_bis(request, benutzername):
    """Zeitpunkt, bis zu dem die Anmeldung gesperrt ist - oder None."""
    jetzt = timezone.now()
    zeiten = LoginSperre.objects.filter(schluessel__in=_schluessel(request, benutzername), gesperrt_bis__gt=jetzt) \
        .values_list("gesperrt_bis", flat=True)
    return max(zeiten, default=None)


@transaction.atomic
def fehlversuch(request, benutzername):
    jetzt = timezone.now()
    fenster = timedelta(minutes=settings.LOGIN_SPERRE_MINUTEN)
    for schluessel in _schluessel(request, benutzername):
        zeile, _ = LoginSperre.objects.select_for_update().get_or_create(schluessel=schluessel)
        if zeile.gesperrt_bis and zeile.gesperrt_bis > jetzt:
            continue                                   # schon gesperrt: Sperre nicht endlos verlaengern
        if zeile.fehlversuche and zeile.letzter_versuch < jetzt - fenster:
            zeile.fehlversuche = 0                     # alter Versuch: neu zaehlen
        zeile.fehlversuche += 1
        if zeile.fehlversuche >= settings.LOGIN_MAX_VERSUCHE:
            zeile.gesperrt_bis, zeile.fehlversuche = jetzt + fenster, 0
        zeile.save()
    LoginSperre.objects.filter(letzter_versuch__lt=jetzt - timedelta(days=1)).delete()


def zuruecksetzen(request, benutzername):
    LoginSperre.objects.filter(schluessel__in=_schluessel(request, benutzername)).delete()


class SperrBackend(ModelBackend):
    """Wie die normale Anmeldung, lehnt aber waehrend einer Sperre jeden Versuch ab."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if request is not None and gesperrt_bis(request, username):
            raise PermissionDenied("Anmeldung vorübergehend gesperrt.")
        return super().authenticate(request, username=username, password=password, **kwargs)


@receiver(user_login_failed)
def _bei_fehlversuch(sender, credentials=None, request=None, **kwargs):
    benutzername = (credentials or {}).get("username")
    if request is not None:
        fehlversuch(request, benutzername)


@receiver(user_logged_in)
def _bei_erfolg(sender, request=None, user=None, **kwargs):
    if request is not None and user is not None:
        zuruecksetzen(request, user.get_username())
