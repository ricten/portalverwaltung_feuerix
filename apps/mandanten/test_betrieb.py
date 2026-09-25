"""Tests fuer Sicherung, Gesundheitspruefung, Update-Rollout, Befehle und Admin-Aktionen (Docker gemockt)."""
import io
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, TestCase, override_settings

from . import betrieb
from .basis import BereitstellungsFehler
from .models import MandantInstanz


def _m(kuerzel="b1", status="laeuft", **kw):
    daten = dict(kuerzel=kuerzel, verein_name="B e.V.", domain=f"{kuerzel}.example.org", status=status,
                 web_port=8100)
    daten.update(kw)
    return MandantInstanz.objects.create(**daten)


def _feuerix(tmp, m):
    (Path(tmp) / "docker-compose.yml").write_text("services: {}\n")
    (Path(tmp) / m.kuerzel).mkdir(exist_ok=True)
    (Path(tmp) / m.kuerzel / ".env").write_text("A=b\n")


def _verz(tmp):
    return override_settings(INSTANZEN_VERZEICHNIS=tmp, FEUERIX_REPO_PFAD=tmp)


class BackupTests(TestCase):
    def test_backup_fuehrt_script_aus_und_kopiert_heraus(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            m = _m()
            _feuerix(tmp, m)
            with patch("apps.mandanten.basis.subprocess.run",
                       return_value=MagicMock(returncode=0, stdout="", stderr="")) as run:
                ziel = betrieb.backup(m)
            befehle = [c.args[0] for c in run.call_args_list]
            self.assertIn("/app/scripts/backup.sh", befehle[0])
            self.assertEqual(befehle[1][-3:-1], ["cp", "web:/data/backups/."])
            self.assertEqual(Path(ziel), Path(tmp) / "b1" / "backups")

    def test_nur_laufende_instanzen(self):
        with self.assertRaises(BereitstellungsFehler):
            betrieb.backup(_m(status="pausiert"))


class GesundheitTests(TestCase):
    def test_meldet_je_dienst(self):
        m = _m(paperless_domain="p.example.org", paperless_port=10100)
        with patch("apps.mandanten.betrieb._antwortet", side_effect=[True, False]):
            self.assertEqual(betrieb.gesundheit(m), {"feuerix": True, "paperless": False})

    def test_status_befehl_meldet_ausfall_per_fehlercode(self):
        _m()
        out = io.StringIO()
        with patch("apps.mandanten.betrieb._antwortet", return_value=False):
            with self.assertRaises(CommandError):
                call_command("mandant_status", stdout=out)
        self.assertIn("AUSFALL", out.getvalue())

    def test_status_befehl_ok(self):
        _m()
        out = io.StringIO()
        with patch("apps.mandanten.betrieb._antwortet", return_value=True):
            call_command("mandant_status", stdout=out)
        self.assertIn("feuerix=ok", out.getvalue())


class UpdateTests(TestCase):
    def test_update_setzt_version(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            m = _m()
            _feuerix(tmp, m)
            (Path(tmp) / "VERSION").write_text("9.9.9\n")
            with patch("apps.mandanten.betrieb.provisioning.stack_hochfahren") as hoch, \
                 patch("apps.mandanten.betrieb.provisioning.auf_bereitschaft_warten") as warten:
                betrieb.update_ausrollen(m)
            hoch.assert_called_once()
            warten.assert_called_once()
            m.refresh_from_db()
            self.assertEqual(m.feuerix_version, "9.9.9")

    def test_fehler_bei_einer_instanz_stoppt_die_anderen_nicht(self):
        a, b = _m("a"), _m("b", web_port=8101)
        _m("c", status="pausiert")

        def fake(mandant):
            if mandant.kuerzel == "a":
                raise BereitstellungsFehler("kaputt")
        with patch("apps.mandanten.betrieb.update_ausrollen", side_effect=fake):
            ok, fehler = betrieb.alle_aktualisieren(pause_sekunden=0)
        self.assertEqual(ok, ["b"])
        self.assertEqual(list(fehler), ["a"])

    def test_befehl_endet_bei_fehler_mit_fehlercode(self):
        _m()
        with patch("apps.mandanten.betrieb.alle_aktualisieren", return_value=([], {"b1": "x"})):
            with self.assertRaises(CommandError):
                call_command("mandant_update", stderr=io.StringIO())


class AdminAktionTests(TestCase):
    def test_aktionen_starten_die_tasks(self):
        m = _m()
        admin = site._registry[MandantInstanz]
        request = RequestFactory().post("/")
        request.user = get_user_model().objects.create_superuser("a", "a@example.org", "x")
        request._messages = MagicMock()
        with patch("apps.mandanten.tasks.mandant_backup_task.delay") as backup, \
             patch("apps.mandanten.tasks.mandant_bereitstellen_task.delay") as bereit:
            admin.sichern(request, MandantInstanz.objects.all())
            admin.bereitstellen(request, MandantInstanz.objects.all())
        backup.assert_called_once_with(m.pk)
        bereit.assert_called_once_with(m.pk)
        self.assertEqual({"bereitstellen", "pausieren", "sichern", "aktualisieren"}, set(admin.actions))
