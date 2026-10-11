"""Tests der Zwei-Faktor-Anmeldung (TOTP und Notfallcodes)."""
import io
import time

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django_otp.oath import TOTP
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice

PW = "ein-sehr-gutes-Passwort-1"


def _code(geraet):
    t = TOTP(geraet.bin_key, geraet.step, geraet.t0, geraet.digits, geraet.drift)
    t.time = time.time()
    return f"{t.token():06d}"


@override_settings(ZWEI_FAKTOR="superuser")
class ZweiFaktorTests(TestCase):
    def setUp(self):
        self.chef = get_user_model().objects.create_superuser("chef", "c@example.org", PW)
        self.normal = get_user_model().objects.create_user("normal", password=PW, is_staff=True)

    def _einrichten(self, client=None):
        client = client or self.client
        client.post("/admin/login/?next=/admin/", {"username": "chef", "password": PW})
        seite = client.get("/zwei-faktor/einrichten/")
        geraet = TOTPDevice.objects.get(user=self.chef, confirmed=False)
        return client, seite, geraet

    def test_superuser_ohne_geraet_muss_einrichten(self):
        self.client.force_login(self.chef)
        antwort = self.client.get("/admin/")
        self.assertEqual(antwort.status_code, 302)
        self.assertTrue(antwort["Location"].startswith("/zwei-faktor/einrichten/"))

    def test_normaler_benutzer_ohne_geraet_darf_weiter(self):
        self.client.force_login(self.normal)
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_einrichtung_zeigt_qr_und_schluessel_und_bestaetigt_mit_code(self):
        client, seite, geraet = self._einrichten()
        self.assertContains(seite, "<svg")
        antwort = client.post("/zwei-faktor/einrichten/", {"code": _code(geraet)})
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Notfallcodes")
        self.assertTrue(TOTPDevice.objects.get(pk=geraet.pk).confirmed)
        self.assertEqual(StaticDevice.objects.get(user=self.chef).token_set.count(), 10)
        self.assertEqual(client.get("/admin/").status_code, 200)          # danach verifiziert

    def test_falscher_code_bei_der_einrichtung(self):
        client, _, geraet = self._einrichten()
        antwort = client.post("/zwei-faktor/einrichten/", {"code": "000000"})
        self.assertContains(antwort, "stimmt nicht")
        self.assertFalse(TOTPDevice.objects.get(pk=geraet.pk).confirmed)

    def _mit_geraet(self):
        geraet = TOTPDevice.objects.create(user=self.chef, name="x", confirmed=True)
        return geraet

    def test_mit_geraet_fordert_login_den_code(self):
        self._mit_geraet()
        self.client.post("/admin/login/?next=/admin/", {"username": "chef", "password": PW})
        antwort = self.client.get("/admin/")
        self.assertTrue(antwort["Location"].startswith("/zwei-faktor/pruefen/"))

    def test_richtiger_code_schaltet_frei_falscher_nicht(self):
        geraet = self._mit_geraet()
        self.client.force_login(self.chef)
        self.assertContains(self.client.post("/zwei-faktor/pruefen/", {"code": "123456"}), "stimmt nicht")
        self.assertEqual(self.client.get("/admin/").status_code, 302)
        geraet.refresh_from_db()
        geraet.throttling_failure_count = 0
        geraet.throttling_failure_timestamp = None
        geraet.save()
        antwort = self.client.post("/zwei-faktor/pruefen/", {"code": _code(geraet), "next": "/admin/"})
        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_notfallcode_gilt_einmal(self):
        self._mit_geraet()
        from apps.mandanten.zweifaktor import _notfallcodes_erzeugen
        codes = _notfallcodes_erzeugen(self.chef)
        self.client.force_login(self.chef)
        self.assertEqual(self.client.post("/zwei-faktor/pruefen/", {"code": codes[0]}).status_code, 302)
        self.assertEqual(self.client.get("/admin/").status_code, 200)
        self.client.logout()
        self.client.force_login(self.chef)
        self.assertContains(self.client.post("/zwei-faktor/pruefen/", {"code": codes[0]}), "stimmt nicht")

    def test_weiterleitung_nur_auf_eigene_seite(self):
        self._mit_geraet()
        self.client.force_login(self.chef)
        antwort = self.client.post("/zwei-faktor/pruefen/", {"code": "x", "next": "https://boese.example/"})
        self.assertContains(antwort, 'value="/admin/"')

    def test_ohne_anmeldung_zur_anmeldeseite(self):
        self.assertEqual(self.client.get("/zwei-faktor/pruefen/")["Location"].split("?")[0], "/admin/login/")

    def test_abgeschaltet(self):
        with override_settings(ZWEI_FAKTOR="aus"):
            self.client.force_login(self.chef)
            self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_modus_alle_trifft_auch_normale_benutzer(self):
        with override_settings(ZWEI_FAKTOR="alle"):
            self.client.force_login(self.normal)
            self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_zuruecksetzen_befehl(self):
        self._mit_geraet()
        call_command("zwei_faktor_zuruecksetzen", "chef", stdout=io.StringIO())
        self.assertFalse(TOTPDevice.objects.filter(user=self.chef).exists())
        self.client.force_login(self.chef)
        self.assertTrue(self.client.get("/admin/")["Location"].startswith("/zwei-faktor/einrichten/"))

    def test_verwaltung_zeigt_neue_codes(self):
        geraet = self._mit_geraet()
        self.client.force_login(self.chef)
        self.client.post("/zwei-faktor/pruefen/", {"code": _code(geraet)})
        antwort = self.client.post("/zwei-faktor/")
        self.assertContains(antwort, "Notfallcodes")
        self.assertEqual(StaticDevice.objects.get(user=self.chef).token_set.count(), 10)
