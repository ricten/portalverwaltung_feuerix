import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, override_settings

from . import provisioning
from .models import MandantInstanz


class MandantInstanzModelTests(TestCase):
    def test_docker_projekt_name(self):
        m = MandantInstanz.objects.create(kuerzel="feuerwehr-x", verein_name="FF X e.V.",
                                          domain="x.example.org")
        self.assertEqual(m.docker_projekt, "mandant-feuerwehr-x")

    def test_protokollieren_haengt_zeilen_an(self):
        m = MandantInstanz.objects.create(kuerzel="a", verein_name="A", domain="a.example.org")
        m.protokollieren("Schritt 1")
        m.protokollieren("Schritt 2")
        self.assertIn("Schritt 1", m.protokoll)
        self.assertIn("Schritt 2", m.protokoll)

    def test_geheimnisse_werden_verschluesselt_gespeichert(self):
        m = MandantInstanz.objects.create(kuerzel="b", verein_name="B", domain="b.example.org",
                                          secret_key="geheim-wert-123")
        with connection.cursor() as cur:
            cur.execute("SELECT secret_key FROM mandanten_mandantinstanz WHERE id = %s", [m.pk])
            roh = cur.fetchone()[0]
        self.assertNotIn("geheim-wert-123", roh)


class PortAllokationTests(TestCase):
    def test_erster_mandant_bekommt_basis_port(self):
        self.assertEqual(provisioning.naechster_freier_port(), 8100)

    def test_belegte_ports_werden_uebersprungen(self):
        MandantInstanz.objects.create(kuerzel="a", verein_name="A", domain="a.example.org", web_port=8100)
        MandantInstanz.objects.create(kuerzel="b", verein_name="B", domain="b.example.org", web_port=8101)
        self.assertEqual(provisioning.naechster_freier_port(), 8102)


class EnvErzeugenTests(TestCase):
    def test_env_datei_enthaelt_erwartete_werte_und_generiert_geheimnisse_einmalig(self):
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(INSTANZEN_VERZEICHNIS=tmp):
                m = MandantInstanz.objects.create(kuerzel="test-verein", verein_name="Test e.V.",
                                                  domain="test.example.org", admin_email="admin@example.org")
                pfad = provisioning.env_erzeugen(m)
                inhalt = pfad.read_text(encoding="utf-8")
                self.assertIn("VEREIN_NAME=Test e.V.", inhalt)
                self.assertIn("VEREIN_DOMAIN=test.example.org", inhalt)
                self.assertIn("POSTGRES_DB=mandant_test_verein", inhalt)

                m.refresh_from_db()
                self.assertTrue(m.secret_key)
                self.assertEqual(m.web_port, 8100)

                alter_schluessel = m.secret_key
                provisioning.env_erzeugen(m)  # erneuter Aufruf darf Geheimnisse nicht neu erzeugen
                m.refresh_from_db()
                self.assertEqual(m.secret_key, alter_schluessel)


class StackHochfahrenTests(TestCase):
    def test_ohne_env_datei_wird_abgelehnt(self):
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(INSTANZEN_VERZEICHNIS=tmp):
                m = MandantInstanz.objects.create(kuerzel="x", verein_name="X", domain="x.example.org")
                with self.assertRaises(provisioning.BereitstellungsFehler):
                    provisioning.stack_hochfahren(m)

    def test_docker_compose_wird_mit_korrekten_argumenten_aufgerufen(self):
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(INSTANZEN_VERZEICHNIS=tmp, FEUERIX_REPO_PFAD=tmp):
                (Path(tmp) / "docker-compose.yml").write_text("services: {}\n")
                m = MandantInstanz.objects.create(kuerzel="y", verein_name="Y", domain="y.example.org")
                provisioning.env_erzeugen(m)
                fake = MagicMock(returncode=0, stderr="")
                with patch("apps.mandanten.provisioning.subprocess.run", return_value=fake) as mock_run:
                    provisioning.stack_hochfahren(m)
                befehl = mock_run.call_args.args[0]
                self.assertIn("mandant-y", befehl)
                self.assertIn("up", befehl)
                self.assertIn("--build", befehl)
                # env_file von web/worker muss auf die Mandanten-.env umgebogen sein, sonst sucht Compose
                # die .env im Feuerix-Checkout (Regression aus dem ersten echten CI-Lauf)
                override = (Path(tmp) / "y" / "compose.mandant.yml").read_text(encoding="utf-8")
                self.assertIn("!override", override)
                self.assertIn((Path(tmp) / "y" / ".env").resolve().as_posix(), override)
                self.assertIn(str(Path(tmp) / "y" / "compose.mandant.yml"), befehl)

    def test_fehlschlag_wirft_fehler_mit_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(INSTANZEN_VERZEICHNIS=tmp, FEUERIX_REPO_PFAD=tmp):
                (Path(tmp) / "docker-compose.yml").write_text("services: {}\n")
                m = MandantInstanz.objects.create(kuerzel="z2", verein_name="Z2", domain="z2.example.org")
                provisioning.env_erzeugen(m)
                fake = MagicMock(returncode=1, stderr="port bereits belegt")
                with patch("apps.mandanten.provisioning.subprocess.run", return_value=fake):
                    with self.assertRaises(provisioning.BereitstellungsFehler) as ctx:
                        provisioning.stack_hochfahren(m)
                self.assertIn("port bereits belegt", str(ctx.exception))


def _fake_urlopen_context(status=200):
    cm = MagicMock()
    cm.__enter__.return_value.status = status
    return cm


class BereitstellenPipelineTests(TestCase):
    def test_erfolgreicher_durchlauf_setzt_status_laeuft(self):
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(INSTANZEN_VERZEICHNIS=tmp, FEUERIX_REPO_PFAD=tmp):
                (Path(tmp) / "docker-compose.yml").write_text("services: {}\n")
                m = MandantInstanz.objects.create(kuerzel="w", verein_name="W", domain="w.example.org")
                fake_run = MagicMock(returncode=0, stderr="")
                with patch("apps.mandanten.provisioning.subprocess.run", return_value=fake_run), \
                     patch("apps.mandanten.provisioning.urllib.request.urlopen",
                           return_value=_fake_urlopen_context()):
                    provisioning.bereitstellen(m)
                m.refresh_from_db()
                self.assertEqual(m.status, "laeuft")
                self.assertIn("Bereitstellung abgeschlossen", m.protokoll)

    def test_fehlschlag_bei_compose_setzt_status_fehlgeschlagen(self):
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(INSTANZEN_VERZEICHNIS=tmp, FEUERIX_REPO_PFAD=tmp):
                (Path(tmp) / "docker-compose.yml").write_text("services: {}\n")
                m = MandantInstanz.objects.create(kuerzel="v", verein_name="V", domain="v.example.org")
                fake_run = MagicMock(returncode=1, stderr="kaputt")
                with patch("apps.mandanten.provisioning.subprocess.run", return_value=fake_run):
                    with self.assertRaises(provisioning.BereitstellungsFehler):
                        provisioning.bereitstellen(m)
                m.refresh_from_db()
                self.assertEqual(m.status, "fehlgeschlagen")
                self.assertIn("Fehler:", m.protokoll)

    def test_bereitschaftspruefung_bei_zeitueberschreitung_setzt_status_fehlgeschlagen(self):
        """Nutzt auf_bereitschaft_warten() direkt mit kurzem Timeout statt ueber bereitstellen() (Standard
        180s) - sonst wuerde der Test real so lange dauern."""
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(INSTANZEN_VERZEICHNIS=tmp, FEUERIX_REPO_PFAD=tmp):
                (Path(tmp) / "docker-compose.yml").write_text("services: {}\n")
                m = MandantInstanz.objects.create(kuerzel="u", verein_name="U", domain="u.example.org",
                                                  web_port=8199)
                with patch("apps.mandanten.provisioning.urllib.request.urlopen",
                           side_effect=ConnectionError("verbindung abgelehnt")):
                    with self.assertRaises(provisioning.BereitstellungsFehler):
                        provisioning.auf_bereitschaft_warten(m, timeout_sekunden=0.2, intervall_sekunden=0.05)


class ManagementCommandTests(TestCase):
    def test_unbekanntes_kuerzel_meldet_fehler(self):
        with self.assertRaises(CommandError):
            call_command("mandant_bereitstellen", "nicht-vorhanden")

    def test_erfolgreicher_aufruf_gibt_port_aus(self):
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(INSTANZEN_VERZEICHNIS=tmp, FEUERIX_REPO_PFAD=tmp):
                (Path(tmp) / "docker-compose.yml").write_text("services: {}\n")
                MandantInstanz.objects.create(kuerzel="cmd-test", verein_name="Cmd", domain="cmd.example.org")
                fake_run = MagicMock(returncode=0, stderr="")
                out = __import__("io").StringIO()
                with patch("apps.mandanten.provisioning.subprocess.run", return_value=fake_run), \
                     patch("apps.mandanten.provisioning.urllib.request.urlopen",
                           return_value=_fake_urlopen_context()):
                    call_command("mandant_bereitstellen", "cmd-test", stdout=out)
                self.assertIn("läuft auf Port", out.getvalue())
