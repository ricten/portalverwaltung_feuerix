"""Tests: Passwoerter von Paperless und OpenSlides zuruecksetzen bzw. anzeigen (Docker gemockt)."""
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from . import dienstpasswort
from .basis import BereitstellungsFehler
from .models import LogEintrag, MandantInstanz


def _m(**kw):
    daten = dict(kuerzel="pw1", verein_name="Verein PW", domain="pw1.example.org", status="laeuft", web_port=8100,
                 admin_benutzername="chef", admin_email="c@example.org", admin_passwort="feuerix-pw",
                 paperless_domain="p.pw1.example.org", paperless_port=10100, paperless_admin_passwort="alt-p",
                 paperless_secret_key="s" * 50, paperless_postgres_passwort="x" * 32,
                 openslides_domain="o.pw1.example.org", openslides_port=9100, openslides_superadmin_passwort="alt-o")
    daten.update(kw)
    return MandantInstanz.objects.create(**daten)


class PaperlessTests(TestCase):
    def test_setzt_passwort_per_stdin_und_speichert_es(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp, FEUERIX_REPO_PFAD=tmp):
            (Path(tmp) / "paperless").mkdir()
            (Path(tmp) / "paperless" / "docker-compose.yml").write_text("services: {}")
            m = _m()
            (Path(tmp) / "pw1" / "paperless").mkdir(parents=True, exist_ok=True)
            (Path(tmp) / "pw1" / "paperless" / ".env").write_text("x=1")
            with patch("apps.mandanten.basis.docker") as d:
                neu = dienstpasswort.paperless_zuruecksetzen(m)
            befehl, eingabe = d.call_args.args[0], d.call_args.kwargs["eingabe"]
            self.assertIn("webserver", befehl)
            self.assertNotIn(neu, " ".join(befehl))            # nie in der Befehlszeile
            self.assertIn(repr(neu), eingabe)
            m.refresh_from_db()
            self.assertEqual(m.paperless_admin_passwort, neu)
            self.assertIn(f"PAPERLESS_ADMIN_PASSWORD={neu}", (Path(tmp) / "pw1" / "paperless" / ".env").read_text())
            self.assertFalse(LogEintrag.objects.filter(text__contains=neu).exists())

    def test_nur_bei_laufender_instanz_mit_paperless(self):
        with self.assertRaises(BereitstellungsFehler):
            dienstpasswort.paperless_zuruecksetzen(_m(status="gestoppt"))
        with self.assertRaises(BereitstellungsFehler):
            dienstpasswort.paperless_zuruecksetzen(_m(kuerzel="pw2", domain="pw2.example.org", paperless_domain=""))

    def test_fehler_laesst_das_gespeicherte_passwort_unveraendert(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp, FEUERIX_REPO_PFAD=tmp):
            (Path(tmp) / "paperless").mkdir()
            (Path(tmp) / "paperless" / "docker-compose.yml").write_text("services: {}")
            m = _m()
            (Path(tmp) / "pw1" / "paperless").mkdir(parents=True, exist_ok=True)
            (Path(tmp) / "pw1" / "paperless" / ".env").write_text("x=1")
            with patch("apps.mandanten.basis.docker", side_effect=BereitstellungsFehler("kaputt")):
                with self.assertRaises(BereitstellungsFehler):
                    dienstpasswort.paperless_zuruecksetzen(m)
            m.refresh_from_db()
            self.assertEqual(m.paperless_admin_passwort, "alt-p")


class OpenSlidesTests(TestCase):
    def _umgebung(self, tmp):
        os_verz = Path(tmp) / "pw1" / "openslides"
        (os_verz / "secrets").mkdir(parents=True)
        (os_verz / "docker-compose.yml").write_text("services: {}")
        (os_verz / "secrets" / "internal_auth_password").write_text("intern")
        (os_verz / "secrets" / "superadmin").write_text("alt-o")
        return os_verz

    def test_osmanage_set_password_datei_und_feuerix_werden_nachgezogen(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp, FEUERIX_REPO_PFAD=tmp):
            os_verz = self._umgebung(tmp)
            m = _m()
            with patch("apps.mandanten.dienstpasswort.basis.docker") as d, \
                    patch("apps.mandanten.dienstpasswort.openslides.werkzeuge_bereitstellen",
                          return_value=(Path("osmanage"), Path("v.tmpl"))), \
                    patch("apps.mandanten.dienstpasswort.provisioning._compose_basis", return_value=["docker", "compose"]), \
                    patch("apps.mandanten.dienstpasswort.openslides.in_feuerix_eintragen") as eintragen:
                neu = dienstpasswort.openslides_zuruecksetzen(m)
            befehl = d.call_args.args[0]
            self.assertEqual(befehl[1], "set-password")
            self.assertIn("--user_id", befehl)
            self.assertIn("secrets/internal_auth_password", befehl)
            self.assertEqual((os_verz / "secrets" / "superadmin").read_text(), neu)
            m.refresh_from_db()
            self.assertEqual(m.openslides_superadmin_passwort, neu)
            eintragen.assert_called_once()

    def test_ohne_stack_fehler(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp, FEUERIX_REPO_PFAD=tmp):
            with self.assertRaises(BereitstellungsFehler):
                dienstpasswort.openslides_zuruecksetzen(_m())


class AdminTests(TestCase):
    def setUp(self):
        self.chef = get_user_model().objects.create_superuser("chef", "c@example.org", "ein-sehr-gutes-Passwort-1")
        self.client.force_login(self.chef)
        self.m = _m()

    def _aktion(self, name):
        return self.client.post("/admin/mandanten/mandantinstanz/",
                                {"action": name, "_selected_action": [self.m.pk]}, follow=True)

    def test_zugangsdaten_zeigen_alle_dienste(self):
        text = self._aktion("zugangsdaten_anzeigen").content.decode()
        for erwartet in ("feuerix-pw", "alt-p", "alt-o", "superadmin"):
            self.assertIn(erwartet, text)

    def test_aktionen_zeigen_neues_passwort(self):
        with patch("apps.mandanten.admin.dienstpasswort.paperless_zuruecksetzen", return_value="NEUPASS123"):
            self.assertContains(self._aktion("paperless_passwort_zuruecksetzen"), "NEUPASS123")
        with patch("apps.mandanten.admin.dienstpasswort.openslides_zuruecksetzen", return_value="NEUOS456"):
            self.assertContains(self._aktion("openslides_passwort_zuruecksetzen"), "NEUOS456")

    def test_fehler_wird_gemeldet(self):
        with patch("apps.mandanten.admin.dienstpasswort.paperless_zuruecksetzen", side_effect=BereitstellungsFehler("kaputt")):
            self.assertContains(self._aktion("paperless_passwort_zuruecksetzen"), "kaputt")
