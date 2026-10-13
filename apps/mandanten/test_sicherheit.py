"""Regressionstests zur Sicherheitspruefung: Rechte der Admin-Aktionen, Betreiber-Einstellungen, Redirect, Meldungen, Dateirechte."""
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase, override_settings

from . import basis, rechte
from .models import BackupZiel, Benachrichtigung, MandantInstanz

AKTIONEN_SUPERUSER = {"zugangsdaten_anzeigen", "admin_passwort_zuruecksetzen", "wiederherstellen", "endgueltig_loeschen",
                      "haengende_zuruecksetzen", "paperless_passwort_zuruecksetzen", "openslides_passwort_zuruecksetzen"}
ALLE_AKTIONEN = {"bereitstellen", "starten", "stoppen", "neustarten", "stilllegen", "demo_zuruecksetzen",
                 "demo_standard_speichern", "beispieldaten", "sichern", "aktualisieren"} | AKTIONEN_SUPERUSER


def _staff(name, *rechte_codenamen, app="mandanten"):
    u = get_user_model().objects.create_user(name, is_staff=True)
    u.user_permissions.add(*Permission.objects.filter(content_type__app_label=app, codename__in=rechte_codenamen))
    return u


def _m(kuerzel="si1", **kw):
    daten = dict(kuerzel=kuerzel, verein_name="Verein SI", domain=f"{kuerzel}.example.org", status="laeuft", web_port=8100)
    daten.update(kw)
    return MandantInstanz.objects.create(**daten)


class AktionenRechteTests(TestCase):
    def _angebotene_aktionen(self, user):
        admin = site._registry[MandantInstanz]
        request = RequestFactory().get("/")
        request.user = user
        return set(admin.get_actions(request)) - {"delete_selected"}

    def test_leser_sieht_und_darf_keine_aktion(self):
        leser = _staff("leser", "view_mandantinstanz")
        self.assertEqual(self._angebotene_aktionen(leser), set())

    def test_leser_kann_keine_aktion_ueber_den_server_ausloesen(self):
        m = _m()
        self.client.force_login(_staff("leser2", "view_mandantinstanz"))
        for aktion, task in (("stoppen", "mandant_stoppen_task"), ("sichern", "mandant_backup_task"),
                             ("starten", "mandant_starten_task"), ("aktualisieren", "mandant_update_task")):
            with patch(f"apps.mandanten.tasks.{task}.delay") as delay:
                self.client.post("/admin/mandanten/mandantinstanz/", {"action": aktion, "_selected_action": [m.pk]})
            delay.assert_not_called()
        m.refresh_from_db()
        self.assertEqual(m.status, "laeuft")

    def test_bearbeiter_darf_betrieb_aber_nicht_die_sensiblen_aktionen(self):
        bearbeiter = _staff("bearb", "view_mandantinstanz", "change_mandantinstanz")
        angebot = self._angebotene_aktionen(bearbeiter)
        self.assertEqual(angebot, ALLE_AKTIONEN - AKTIONEN_SUPERUSER)

    def test_superuser_sieht_alle_aktionen(self):
        chef = get_user_model().objects.create_superuser("chef", "c@example.org", "pw")
        self.assertEqual(self._angebotene_aktionen(chef), ALLE_AKTIONEN)

    def test_bearbeiter_kann_zugangsdaten_und_passwort_reset_nicht_ausloesen(self):
        m = _m(admin_passwort="GeheimesPasswort1")
        self.client.force_login(_staff("bearb2", "view_mandantinstanz", "change_mandantinstanz"))
        for aktion in ("zugangsdaten_anzeigen", "admin_passwort_zuruecksetzen", "endgueltig_loeschen", "wiederherstellen"):
            antwort = self.client.post("/admin/mandanten/mandantinstanz/", {"action": aktion, "_selected_action": [m.pk]},
                                       follow=True)
            self.assertNotContains(antwort, "GeheimesPasswort1")
        with patch("apps.mandanten.betrieb.admin_passwort_zuruecksetzen") as reset:
            self.client.post("/admin/mandanten/mandantinstanz/", {"action": "admin_passwort_zuruecksetzen",
                                                                   "_selected_action": [m.pk]})
        reset.assert_not_called()

    def test_jede_aktion_ist_an_ein_recht_gebunden(self):
        admin = site._registry[MandantInstanz]
        for name in ALLE_AKTIONEN:
            aktion = getattr(admin, name)
            self.assertTrue(getattr(aktion, "allowed_permissions", None), f"{name} hat kein Recht gesetzt")


class BetreiberEinstellungenTests(TestCase):
    def test_nur_superuser_darf_backup_ziel_und_benachrichtigung_sehen_oder_aendern(self):
        BackupZiel.holen()
        Benachrichtigung.holen()
        bearbeiter = _staff("bearb3", "view_backupziel", "change_backupziel", "view_benachrichtigung",
                            "change_benachrichtigung")
        self.client.force_login(bearbeiter)
        antwort = self.client.post("/admin/mandanten/backupziel/1/change/", {
            "aktiv": "on", "server": "angreifer.example.org", "freigabe": "x", "benutzer": "u", "passwort_neu": "p",
            "aufbewahrung_tage": "0"})
        self.assertEqual(antwort.status_code, 403)
        self.assertEqual(BackupZiel.holen().server, "")
        self.assertEqual(self.client.get("/admin/mandanten/backupziel/1/change/").status_code, 403)
        antwort = self.client.post("/admin/mandanten/benachrichtigung/1/change/", {
            "aktiv": "on", "empfaenger": "x@y.de", "absender": "a@b.de", "smtp_host": "angreifer.example.org", "smtp_port": "25",
            "smtp_sicherheit": "keine", "ausfall_minuten": "10"})
        self.assertEqual(antwort.status_code, 403)
        self.assertEqual(Benachrichtigung.holen().smtp_host, "")

    def test_superuser_darf_beides(self):
        self.client.force_login(get_user_model().objects.create_superuser("chef2", "c@example.org", "pw"))
        BackupZiel.holen()
        Benachrichtigung.holen()
        self.assertEqual(self.client.get("/admin/mandanten/backupziel/1/change/").status_code, 200)
        self.assertEqual(self.client.get("/admin/mandanten/benachrichtigung/1/change/").status_code, 200)


class RedirectUndMeldungenTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("rm", "r@example.org", "pw"))
        self.url = "/admin/mandanten/mandantinstanz/update/pruefen/"

    def _post(self, referer):
        with patch("apps.mandanten.updates.pruefen") as pruefen:
            pruefen.return_value = MagicZustand()
            return self.client.post(self.url, HTTP_REFERER=referer, HTTP_HOST="portal.example.org")

    def test_fremde_referer_adresse_fuehrt_nicht_zu_einer_weiterleitung_dorthin(self):
        with override_settings(ALLOWED_HOSTS=["portal.example.org"]):
            antwort = self._post("https://boese.example.net/phishing")
        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(antwort["Location"], "/admin/")

    def test_eigene_referer_adresse_wird_beibehalten(self):
        with override_settings(ALLOWED_HOSTS=["portal.example.org"]):
            antwort = self._post("http://portal.example.org/admin/mandanten/mandantinstanz/")
        self.assertEqual(antwort["Location"], "http://portal.example.org/admin/mandanten/mandantinstanz/")

    def test_meldungen_mit_zugangsdaten_liegen_nicht_im_browser_cookie(self):
        m = _m("si2", admin_passwort="NurInDerSitzung1")
        antwort = self.client.post("/admin/mandanten/mandantinstanz/", {"action": "zugangsdaten_anzeigen",
                                                                         "_selected_action": [m.pk]})
        self.assertNotIn("messages", antwort.cookies)
        self.assertNotIn("NurInDerSitzung1", str(antwort.cookies))
        self.assertContains(self.client.get("/admin/mandanten/mandantinstanz/"), "NurInDerSitzung1")

    def test_sitzungsdauer_ist_begrenzt(self):
        from django.conf import settings
        self.assertEqual(settings.MESSAGE_STORAGE, "django.contrib.messages.storage.session.SessionStorage")
        self.assertLessEqual(settings.SESSION_COOKIE_AGE, 12 * 3600)


class MagicZustand:
    fehler = ""
    rollout_empfohlen = False
    lokale_version = "1.0.0"
    ziel_version = "1.0.0"
    dienste_fehler = ""
    dienste_unbekannt = []


@unittest.skipUnless(os.name == "posix", "Dateirechte gibt es nur unter Linux")
class DateirechteTests(TestCase):
    def _modus(self, pfad):
        return stat.S_IMODE(Path(pfad).stat().st_mode)

    def test_instanzordner_und_unterordner_sind_nur_fuer_den_besitzer(self):
        m = _m("si3")
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp):
            os.chmod(tmp, 0o755)
            pfad = basis.instanz_verzeichnis(m, "backups")
            self.assertEqual(self._modus(pfad), 0o700)
            self.assertEqual(self._modus(Path(tmp) / "si3"), 0o700)
            self.assertEqual(self._modus(tmp), 0o700)

    def test_geheim_schreiben_legt_dateien_mit_0600_an_auch_bei_vorhandener_datei(self):
        with tempfile.TemporaryDirectory() as tmp:
            datei = Path(tmp) / ".env"
            datei.write_text("alt")
            os.chmod(datei, 0o644)
            rechte.geheim_schreiben(datei, "SECRET_KEY=abc\n")
            self.assertEqual(self._modus(datei), 0o600)
            self.assertEqual(datei.read_text(), "SECRET_KEY=abc\n")
            neu = Path(tmp) / "dump.sql"
            rechte.geheim_schreiben(neu, b"daten")
            self.assertEqual(self._modus(neu), 0o600)

    def test_env_der_instanz_wird_mit_0600_geschrieben(self):
        from . import provisioning
        m = _m("si4", admin_email="a@example.org")
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp):
            pfad = provisioning.env_erzeugen(m)
            self.assertEqual(self._modus(pfad), 0o600)
            self.assertIn("FIELD_ENCRYPTION_KEY=", pfad.read_text())

    def test_haerten_repariert_bestehende_installationen_und_laesst_export_und_consume_in_ruhe(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp):
            wurzel = Path(tmp)
            for d in ("a/paperless/export", "a/backups", "_geloescht/alt-2026/x", "_werkzeuge"):
                (wurzel / d).mkdir(parents=True)
                for teil in [wurzel / d] + list((wurzel / d).parents):
                    if str(teil).startswith(str(wurzel)):
                        os.chmod(teil, 0o755)
            for env in (wurzel / "a" / ".env", wurzel / "a" / "paperless" / ".env", wurzel / "_geloescht" / "alt-2026" / ".env"):
                env.write_text("X=1\n")
                os.chmod(env, 0o644)
            os.chmod(wurzel / "a" / "paperless" / "export", 0o775)
            geaendert, fehler = rechte.haerten()
            self.assertEqual(fehler, [])
            self.assertTrue(geaendert)
            for ordner in (wurzel, wurzel / "a", wurzel / "_geloescht", wurzel / "_geloescht" / "alt-2026", wurzel / "_werkzeuge"):
                self.assertEqual(self._modus(ordner), 0o700, ordner)
            for env in (wurzel / "a" / ".env", wurzel / "a" / "paperless" / ".env", wurzel / "_geloescht" / "alt-2026" / ".env"):
                self.assertEqual(self._modus(env), 0o600, env)
            self.assertEqual(self._modus(wurzel / "a" / "paperless" / "export"), 0o775)   # Container-Benutzer schreibt dort
            self.assertEqual(rechte.haerten(), ([], []))                                 # idempotent


class DateirechteOhnePosixTests(TestCase):
    """Laeuft ueberall: die Logik darf unter Windows nichts kaputtmachen und fragt nur das Betriebssystem."""

    def test_haerten_unter_windows_tut_nichts(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp), \
             patch("apps.mandanten.rechte.os.name", "nt"):
            self.assertEqual(rechte.haerten(), ([], []))
            datei = Path(tmp) / ".env"
            rechte.geheim_schreiben(datei, "A=1\n")
            self.assertEqual(datei.read_text(), "A=1\n")

    def test_online_check_zieht_die_rechte_nach_und_ein_fehler_dort_stoert_nicht(self):
        from . import onlinecheck
        with patch("apps.mandanten.onlinecheck.rechte.haerten", side_effect=OSError("kein Zugriff")) as haerten:
            onlinecheck.alle_pruefen()
        haerten.assert_called_once()

    def test_befehl_meldet_aenderungen_und_fehler(self):
        import io

        from django.core.management import call_command
        out, err = io.StringIO(), io.StringIO()
        with patch("apps.mandanten.rechte.haerten", return_value=(["/a"], ["/b: Keine Berechtigung"])):
            call_command("rechte_haerten", stdout=out, stderr=err)
        self.assertIn("gesichert: /a", out.getvalue())
        self.assertIn("1 Pfad(e) angepasst, 1 Fehler", out.getvalue())
        self.assertIn("/b: Keine Berechtigung", err.getvalue())
