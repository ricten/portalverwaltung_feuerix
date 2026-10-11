"""Tests fuer die Haertung (0.32.0): Anmelde-Sperre, SMB-Verschluesselung."""
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from . import backupziel, loginsperre
from .models import BackupZiel, LoginSperre

PW = "ein-sehr-gutes-Passwort-1"


def _anmelden(client, name, passwort, adresse="10.0.0.5"):
    return client.post("/admin/login/?next=/admin/", {"username": name, "password": passwort},
                       REMOTE_ADDR=adresse)


@override_settings(LOGIN_MAX_VERSUCHE=3, LOGIN_SPERRE_MINUTEN=15)
class LoginSperreTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser("chef", "c@example.org", PW)

    def test_richtige_anmeldung_funktioniert(self):
        self.assertEqual(_anmelden(self.client, "chef", PW).status_code, 302)

    def test_nach_zu_vielen_fehlversuchen_ist_auch_das_richtige_passwort_gesperrt(self):
        for _ in range(3):
            self.assertEqual(_anmelden(self.client, "chef", "falsch").status_code, 200)
        antwort = _anmelden(self.client, "chef", PW)
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "gesperrt")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_sperre_gilt_je_benutzername_auch_von_anderer_adresse(self):
        for i in range(3):
            _anmelden(self.client, "chef", "falsch", adresse=f"10.0.0.{i + 10}")
        self.assertContains(_anmelden(self.client, "chef", PW, adresse="10.9.9.9"), "gesperrt")

    def test_sperre_gilt_je_adresse_auch_fuer_andere_benutzer(self):
        get_user_model().objects.create_superuser("zweite", "z@example.org", PW)
        for i in range(3):
            _anmelden(self.client, f"gibtsnicht{i}", "falsch", adresse="10.0.0.7")
        self.assertContains(_anmelden(self.client, "zweite", PW, adresse="10.0.0.7"), "gesperrt")
        self.assertEqual(_anmelden(self.client, "zweite", PW, adresse="10.0.0.8").status_code, 302)

    def test_nach_ablauf_ist_die_anmeldung_wieder_frei(self):
        for _ in range(3):
            _anmelden(self.client, "chef", "falsch")
        LoginSperre.objects.update(gesperrt_bis=timezone.now() - timedelta(minutes=1))
        self.assertEqual(_anmelden(self.client, "chef", PW).status_code, 302)

    def test_erfolg_setzt_die_zaehler_zurueck(self):
        _anmelden(self.client, "chef", "falsch")
        _anmelden(self.client, "chef", "falsch")
        self.assertEqual(_anmelden(self.client, "chef", PW).status_code, 302)
        self.assertFalse(LoginSperre.objects.exists())

    def test_gesperrter_versuch_verlaengert_die_sperre_nicht(self):
        for _ in range(3):
            _anmelden(self.client, "chef", "falsch")
        bis = LoginSperre.objects.get(schluessel="user:chef").gesperrt_bis
        _anmelden(self.client, "chef", "falsch")
        self.assertEqual(LoginSperre.objects.get(schluessel="user:chef").gesperrt_bis, bis)

    def test_adresse_hinter_dem_proxy_ist_der_letzte_forwarded_eintrag(self):
        req = MagicMock(META={"REMOTE_ADDR": "127.0.0.1", "HTTP_X_FORWARDED_FOR": "6.6.6.6, 192.168.1.9"})
        self.assertEqual(loginsperre.client_adresse(req), "192.168.1.9")
        req = MagicMock(META={"REMOTE_ADDR": "203.0.113.4", "HTTP_X_FORWARDED_FOR": "6.6.6.6"})
        self.assertEqual(loginsperre.client_adresse(req), "203.0.113.4")   # ohne Proxy zaehlt die echte Adresse

    def test_befehl_entsperren(self):
        for _ in range(3):
            _anmelden(self.client, "chef", "falsch")
        call_command("login_entsperren", stdout=MagicMock())
        self.assertEqual(_anmelden(self.client, "chef", PW).status_code, 302)


class SmbVerschluesselungTests(TestCase):
    def _verbinden(self, **kw):
        ziel = BackupZiel.objects.create(pk=1, aktiv=True, server="nas.local", freigabe="b", benutzer="u",
                                         passwort="p", **kw)
        smb = MagicMock()
        backupziel._verbinden(ziel, smb)
        return smb.register_session.call_args.kwargs

    def test_standard_erzwingt_verschluesselung(self):
        self.assertIs(self._verbinden()["encrypt"], True)

    def test_abschaltbar(self):
        self.assertIsNone(self._verbinden(verschluesselung=False)["encrypt"])
