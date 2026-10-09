"""Tests fuer die E-Mail-Benachrichtigung (SMTP durch das lokale Test-Postfach von Django ersetzt)."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.mail import get_connection
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from . import benachrichtigung, betrieb, onlinecheck, updates
from .basis import BereitstellungsFehler
from .models import BackupZiel, Benachrichtigung, MandantInstanz, UpdateZustand
from .test_backupziel import FakeSMB, _ziel
from .test_updates import _git_antworten


def _cfg(**kw):
    daten = dict(aktiv=True, empfaenger="admin@example.org, zweite@example.org", absender="portal@example.org",
                 smtp_host="mail.example.org", smtp_benutzer="u", smtp_passwort="pw", ausfall_minuten=10)
    daten.update(kw)
    return Benachrichtigung.objects.update_or_create(pk=1, defaults=daten)[0]


def _m(kuerzel="n1", **kw):
    daten = dict(kuerzel=kuerzel, verein_name="Verein N", domain=f"{kuerzel}.example.org", status="laeuft",
                 web_port=8100)
    daten.update(kw)
    return MandantInstanz.objects.create(**daten)


def _postfach():
    return patch("apps.mandanten.benachrichtigung._verbindung",
                 side_effect=lambda cfg: get_connection("django.core.mail.backends.locmem.EmailBackend"))


class VersandTests(TestCase):
    def test_ohne_oder_inaktive_konfiguration_wird_nichts_gesendet(self):
        with _postfach():
            self.assertFalse(benachrichtigung.senden("ausfall", "x", "y"))
            _cfg(aktiv=False)
            self.assertFalse(benachrichtigung.senden("ausfall", "x", "y"))
            _cfg(bei_ausfall=False)
            self.assertFalse(benachrichtigung.senden("ausfall", "x", "y"))
        self.assertEqual(mail.outbox, [])

    def test_mail_geht_an_alle_empfaenger_mit_betreff_praefix(self):
        _cfg()
        with _postfach():
            self.assertTrue(benachrichtigung.senden("ausfall", "Test", "Hallo"))
        self.assertEqual(len(mail.outbox), 1)
        nachricht = mail.outbox[0]
        self.assertEqual(nachricht.to, ["admin@example.org", "zweite@example.org"])
        self.assertEqual(nachricht.subject, "[Portalverwaltung] Test")
        self.assertEqual(nachricht.from_email, "portal@example.org")
        self.assertIn("Hallo", nachricht.body)
        self.assertIsNotNone(Benachrichtigung.holen().letzte_mail_am)

    def test_versandfehler_wird_gespeichert_und_wirft_nicht(self):
        _cfg()
        with patch("apps.mandanten.benachrichtigung._verschicken", side_effect=OSError("Verbindung abgelehnt")):
            self.assertFalse(benachrichtigung.senden("ausfall", "x", "y"))
        self.assertIn("Verbindung abgelehnt", Benachrichtigung.holen().letzter_fehler)

    def test_validierung(self):
        with self.assertRaises(ValidationError):
            Benachrichtigung(aktiv=True, smtp_host="", absender="a@b.de", empfaenger="x@y.de").full_clean()
        with self.assertRaises(ValidationError):
            Benachrichtigung(aktiv=True, smtp_host="h", absender="a@b.de", empfaenger="kaputt").full_clean()
        with self.assertRaises(ValidationError):
            Benachrichtigung(aktiv=True, smtp_host="h", absender="a@b.de", empfaenger="").full_clean()
        Benachrichtigung(aktiv=False).full_clean()


class AusfallMailTests(TestCase):
    def _pruefen(self, m, **dienste):
        with patch("apps.mandanten.betrieb.gesundheit", return_value=dienste), _postfach():
            return onlinecheck.instanz_pruefen(m)

    def test_ausfall_mail_erst_nach_der_eingestellten_dauer_und_nur_einmal(self):
        _cfg(ausfall_minuten=10)
        m = _m()
        self._pruefen(m, feuerix=False)
        self.assertEqual(mail.outbox, [])                         # gerade erst ausgefallen
        MandantInstanz.objects.filter(pk=m.pk).update(offline_seit=timezone.now() - timedelta(minutes=15))
        m.refresh_from_db()
        self._pruefen(m, feuerix=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("n1 ist nicht erreichbar", mail.outbox[0].subject)
        self.assertIn("feuerix", mail.outbox[0].body)
        m.refresh_from_db()
        self.assertTrue(m.offline_mail_gesendet)
        self._pruefen(m, feuerix=False)
        self.assertEqual(len(mail.outbox), 1)                     # nicht erneut

    def test_wiederkehr_mail_nur_wenn_ausfall_gemeldet_wurde(self):
        _cfg(ausfall_minuten=0)
        m = _m()
        self._pruefen(m, feuerix=False)
        m.refresh_from_db()
        self._pruefen(m, feuerix=False)          # Ausfall wird gemeldet
        self.assertEqual(len(mail.outbox), 1)
        m.refresh_from_db()
        self._pruefen(m, feuerix=True)
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("wieder erreichbar", mail.outbox[1].subject)
        m.refresh_from_db()
        self.assertFalse(m.offline_mail_gesendet)

    def test_kurzer_ausfall_ohne_meldung_bringt_auch_keine_wiederkehr_mail(self):
        _cfg(ausfall_minuten=60)
        m = _m()
        self._pruefen(m, feuerix=False)
        m.refresh_from_db()
        self._pruefen(m, feuerix=True)
        self.assertEqual(mail.outbox, [])

    def test_ohne_konfiguration_keine_mails_aber_check_funktioniert(self):
        m = _m()
        self._pruefen(m, feuerix=False)
        m.refresh_from_db()
        self.assertFalse(m.online)
        self.assertEqual(mail.outbox, [])

    def test_backupziel_ausfall_und_wiederkehr(self):
        _cfg(ausfall_minuten=0)
        _ziel()
        with patch("apps.mandanten.backupziel._smb", return_value=FakeSMB(fehler="Host weg")), _postfach():
            onlinecheck.backupziel_pruefen()                       # erster Fehlschlag: Zeit wird gemerkt
            onlinecheck.backupziel_pruefen()                       # Ausfall >= 0 Minuten -> Mail
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Backup-Ziel ist nicht erreichbar", mail.outbox[0].subject)
        self.assertIn("Host weg", mail.outbox[0].body)
        with patch("apps.mandanten.backupziel._smb", return_value=FakeSMB()), _postfach():
            onlinecheck.backupziel_pruefen()
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("wieder erreichbar", mail.outbox[1].subject)
        self.assertFalse(BackupZiel.holen().offline_mail_gesendet)


class WeitereEreignisseTests(TestCase):
    def test_fehlgeschlagene_sicherung_schickt_mail(self):
        _cfg()
        m = _m()
        with patch("apps.mandanten.betrieb._backup", side_effect=BereitstellungsFehler("Platte voll")), _postfach():
            with self.assertRaises(BereitstellungsFehler):
                betrieb.backup(m)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Sicherung von n1 fehlgeschlagen", mail.outbox[0].subject)
        self.assertIn("Platte voll", mail.outbox[0].body)

    def test_falscher_status_ist_kein_sicherungsfehler(self):
        _cfg()
        with _postfach():
            with self.assertRaises(BereitstellungsFehler):
                betrieb.backup(_m(status="gestoppt"))
        self.assertEqual(mail.outbox, [])

    def test_neue_version_wird_je_version_nur_einmal_gemeldet(self):
        _cfg()
        with patch("apps.mandanten.updates._git", side_effect=_git_antworten(remote="1.59.0")), \
             patch("apps.mandanten.provisioning.feuerix_version", return_value="1.58.0"), _postfach():
            updates.pruefen()
            updates.pruefen()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Feuerix 1.59.0 verfügbar", mail.outbox[0].subject)
        self.assertEqual(UpdateZustand.holen().update_mail_version, "1.59.0")
        with patch("apps.mandanten.updates._git", side_effect=_git_antworten(remote="1.60.0")), \
             patch("apps.mandanten.provisioning.feuerix_version", return_value="1.58.0"), _postfach():
            updates.pruefen()
        self.assertEqual(len(mail.outbox), 2)

    def test_update_mail_bleibt_aus_wenn_nichts_neues_da_ist(self):
        _cfg()
        with patch("apps.mandanten.updates._git", side_effect=_git_antworten(remote="1.58.0")), \
             patch("apps.mandanten.provisioning.feuerix_version", return_value="1.58.0"), _postfach():
            updates.pruefen()
        self.assertEqual(mail.outbox, [])


class AdminTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("bn", "b@example.org", "pw"))
        self.url = self.client.get("/admin/mandanten/benachrichtigung/").url

    def _daten(self, **kw):
        daten = dict(aktiv="on", empfaenger="a@example.org", absender="p@example.org", smtp_host="mail.example.org",
                     smtp_port="587", smtp_sicherheit="starttls", smtp_benutzer="u", smtp_passwort_neu="geheim",
                     bei_ausfall="on", ausfall_minuten="10", bei_backup_fehler="on", bei_update="on")
        daten.update(kw)
        return daten

    def test_speichern_verschluesselt_und_behaelt_passwort(self):
        self.assertEqual(self.client.post(self.url, self._daten()).status_code, 302)
        self.assertEqual(Benachrichtigung.holen().smtp_passwort, "geheim")
        self.client.post(self.url, self._daten(smtp_passwort_neu=""))
        self.assertEqual(Benachrichtigung.holen().smtp_passwort, "geheim")
        self.assertNotContains(self.client.get(self.url), "geheim")

    def test_aktiv_ohne_server_wird_abgelehnt(self):
        antwort = self.client.post(self.url, self._daten(smtp_host=""))
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "SMTP-Server ist nötig")

    def test_testmail_knopf(self):
        with _postfach():
            antwort = self.client.post(self.url, self._daten(_testmail="1"), follow=True)
        self.assertContains(antwort, "Test-Mail an a@example.org gesendet")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Testnachricht", mail.outbox[0].subject)
        with patch("apps.mandanten.benachrichtigung._verschicken", side_effect=OSError("Zeitüberschreitung")):
            antwort = self.client.post(self.url, self._daten(_testmail="1"), follow=True)
        self.assertContains(antwort, "Test-Mail fehlgeschlagen")
