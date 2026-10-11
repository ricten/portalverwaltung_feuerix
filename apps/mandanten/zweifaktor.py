"""Zwei-Faktor-Anmeldung (TOTP mit Authenticator-App, dazu Einmal-Notfallcodes) fuer die Weboberflaeche.

Superuser muessen sie nutzen (Einstellung ZWEI_FAKTOR), andere Benutzer koennen sie freiwillig einrichten; wer ein
Geraet hat, muss den Code bei jeder Anmeldung eingeben. Verloren gegangenes Geraet: `manage.py zwei_faktor_zuruecksetzen`.
Das Raten der Codes bremst django-otp selbst (zunehmende Wartezeit nach Fehlversuchen)."""
import base64

import segno
from django.conf import settings
from django.contrib import admin
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django_otp import login as otp_login, match_token, user_has_device
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

FREI = ("/zwei-faktor/", "/admin/login/", "/admin/logout/", "/admin/jsi18n/", "/static/")
NOTFALLCODES = 10


def pflicht(user):
    """Muss dieser Benutzer einen zweiten Faktor vorweisen (einrichten bzw. eingeben)?"""
    modus = settings.ZWEI_FAKTOR
    if modus == "aus":
        return False
    return modus == "alle" or user.is_superuser or user_has_device(user)


class ZweiFaktorMiddleware:
    """Leitet angemeldete Benutzer ohne bestaetigten zweiten Faktor auf die Pruefung bzw. Einrichtung um."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if (user.is_authenticated and not user.is_verified() and not request.path.startswith(FREI)
                and pflicht(user)):
            ziel = "zweifaktor_pruefen" if user_has_device(user) else "zweifaktor_einrichten"
            return redirect(f"{reverse(ziel)}?next={request.get_full_path()}")
        return self.get_response(request)


def _weiter(request):
    ziel = request.POST.get("next") or request.GET.get("next") or ""
    if not url_has_allowed_host_and_scheme(ziel, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        ziel = reverse("admin:index")
    return ziel


def _seite(request, vorlage, **kontext):
    return render(request, vorlage, dict(admin.site.each_context(request), next=_weiter(request), **kontext))


def pruefen(request):
    """Code aus der Authenticator-App oder ein Notfallcode."""
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path(), reverse("admin:login"))
    if request.user.is_verified():
        return redirect(_weiter(request))
    if not user_has_device(request.user):
        return redirect(f"{reverse('zweifaktor_einrichten')}?next={_weiter(request)}")
    fehler = ""
    if request.method == "POST":
        code = request.POST.get("code", "").replace(" ", "").strip().lower()
        geraet = match_token(request.user, code) if code else None
        if geraet:
            otp_login(request, geraet)
            return redirect(_weiter(request))
        fehler = "Der Code stimmt nicht (oder die Wartezeit nach Fehlversuchen läuft noch). Bitte erneut versuchen."
    return _seite(request, "zweifaktor/pruefen.html", title="Zwei-Faktor-Anmeldung", fehler=fehler)


def _notfallcodes_erzeugen(user):
    StaticDevice.objects.filter(user=user).delete()
    geraet = StaticDevice.objects.create(user=user, name="Notfallcodes", confirmed=True)
    codes = [StaticToken.random_token() for _ in range(NOTFALLCODES)]
    StaticToken.objects.bulk_create([StaticToken(device=geraet, token=c) for c in codes])
    return codes


def einrichten(request):
    """Authenticator-App koppeln: QR-Code zeigen, mit einem Code bestaetigen, Notfallcodes ausgeben."""
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path(), reverse("admin:login"))
    if user_has_device(request.user) and not request.user.is_verified():
        return redirect(f"{reverse('zweifaktor_pruefen')}?next={_weiter(request)}")
    if user_has_device(request.user):
        return redirect("zweifaktor_status")           # schon eingerichtet; neu koppeln nur nach Zuruecksetzen
    geraet = TOTPDevice.objects.filter(user=request.user, confirmed=False).first() \
        or TOTPDevice.objects.create(user=request.user, name="Authenticator-App", confirmed=False)
    fehler = ""
    if request.method == "POST":
        code = request.POST.get("code", "").replace(" ", "")
        if code.isdigit() and geraet.verify_token(int(code)):
            geraet.confirmed = True
            geraet.save()
            otp_login(request, geraet)
            return _seite(request, "zweifaktor/verwalten.html", title="Zwei-Faktor-Anmeldung", neu_codes=_notfallcodes_erzeugen(request.user),
                          aktiviert=True)
        fehler = "Der Code stimmt nicht. Bitte den aktuellen Code der App eingeben (Uhrzeit des Handys muss stimmen)."
    return _seite(request, "zweifaktor/einrichten.html", title="Zwei-Faktor-Anmeldung einrichten", fehler=fehler,
                  qr=segno.make(geraet.config_url, error="m").svg_inline(scale=5, dark="#000", light="#fff"),
                  geheimnis=base64.b32encode(bytes.fromhex(geraet.key)).decode())


def verwalten(request):
    """Status; Notfallcodes neu erzeugen."""
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path(), reverse("admin:login"))
    if not user_has_device(request.user):
        return redirect("zweifaktor_einrichten")
    if not request.user.is_verified():
        return redirect(f"{reverse('zweifaktor_pruefen')}?next={reverse('zweifaktor_status')}")
    codes = None
    if request.method == "POST":
        codes = _notfallcodes_erzeugen(request.user)
    uebrig = StaticToken.objects.filter(device__user=request.user).count()
    return _seite(request, "zweifaktor/verwalten.html", title="Zwei-Faktor-Anmeldung", neu_codes=codes, uebrig=uebrig)
