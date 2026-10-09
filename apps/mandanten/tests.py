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
                with patch("apps.mandanten.basis.subprocess.run", return_value=fake) as mock_run:
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
                with patch("apps.mandanten.basis.subprocess.run", return_value=fake):
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
                with patch("apps.mandanten.basis.subprocess.run", return_value=fake_run), \
                     patch("apps.mandanten.provisioning.urllib.request.urlopen",
                           return_value=_fake_urlopen_context()):
                    provisioning.bereitstellen(m)
                m.refresh_from_db()
                self.assertEqual(m.status, "laeuft")
                self.assertIn("Instanz ist bereit", m.protokoll)

    def test_fehlschlag_bei_compose_setzt_status_fehlgeschlagen(self):
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(INSTANZEN_VERZEICHNIS=tmp, FEUERIX_REPO_PFAD=tmp):
                (Path(tmp) / "docker-compose.yml").write_text("services: {}\n")
                m = MandantInstanz.objects.create(kuerzel="v", verein_name="V", domain="v.example.org")
                fake_run = MagicMock(returncode=1, stderr="kaputt")
                with patch("apps.mandanten.basis.subprocess.run", return_value=fake_run):
                    with self.assertRaises(provisioning.BereitstellungsFehler):
                        provisioning.bereitstellen(m)
                m.refresh_from_db()
                self.assertEqual(m.status, "fehlgeschlagen")
                self.assertIn("Fehler:", m.protokoll)

    def test_bereitschaftspruefung_simuliert_den_reverse_proxy(self):
        """Regression aus dem echten CI-Lauf: ohne Host-Header der Instanz-Domain (ALLOWED_HOSTS) und ohne
        X-Forwarded-Proto: https (SECURE_SSL_REDIRECT) antwortet Feuerix auf 127.0.0.1 mit 400."""
        m = MandantInstanz.objects.create(kuerzel="p", verein_name="P", domain="p.example.org", web_port=8198)
        with patch("apps.mandanten.provisioning.urllib.request.urlopen",
                   return_value=_fake_urlopen_context()) as mock_open:
            provisioning.auf_bereitschaft_warten(m, timeout_sekunden=1, intervall_sekunden=0)
        anfrage = mock_open.call_args.args[0]
        self.assertEqual(anfrage.get_header("Host"), "p.example.org")
        self.assertEqual(anfrage.get_header("X-forwarded-proto"), "https")

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
                with patch("apps.mandanten.basis.subprocess.run", return_value=fake_run), \
                     patch("apps.mandanten.provisioning.urllib.request.urlopen",
                           return_value=_fake_urlopen_context()):
                    call_command("mandant_bereitstellen", "cmd-test", stdout=out)
                self.assertIn("läuft auf Port", out.getvalue())


class DockerUmgebungTests(TestCase):
    def test_portal_variablen_werden_nicht_an_docker_durchgereicht(self):
        from unittest import mock
        from . import basis
        with mock.patch.dict("os.environ", {"POSTGRES_USER": "portal", "POSTGRES_PASSWORD": "geheim",
                                             "PATH_TEST_BEHALTEN": "ja"}):
            umgebung = basis.docker_umgebung({"EXTRA": "1"})
        self.assertNotIn("POSTGRES_USER", umgebung)
        self.assertNotIn("POSTGRES_PASSWORD", umgebung)
        self.assertEqual(umgebung["PATH_TEST_BEHALTEN"], "ja")
        self.assertEqual(umgebung["EXTRA"], "1")


from django.contrib.auth.models import Permission  # noqa: E402


class ZugangsdatenAnzeigenTests(TestCase):
    def test_nur_superuser_sieht_passwort(self):
        from django.contrib.auth import get_user_model
        U = get_user_model()
        m = MandantInstanz.objects.create(kuerzel="zd-test", verein_name="Test", domain="zd.example.org",
                                          admin_email="a@example.org", admin_passwort="geheim123")
        normal = U.objects.create_user("normal", is_staff=True)
        normal.user_permissions.add(*Permission.objects.filter(content_type__app_label="mandanten"))
        self.client.force_login(normal)
        antwort = self.client.post("/admin/mandanten/mandantinstanz/", {
            "action": "zugangsdaten_anzeigen", "_selected_action": [m.pk]}, follow=True)
        self.assertNotContains(antwort, "geheim123")
        self.client.force_login(U.objects.create_superuser("chef", "c@example.org", "pw"))
        antwort = self.client.post("/admin/mandanten/mandantinstanz/", {
            "action": "zugangsdaten_anzeigen", "_selected_action": [m.pk]}, follow=True)
        self.assertContains(antwort, "geheim123")


class VersionInFusszeileTests(TestCase):
    def test_version_steht_in_der_fusszeile(self):
        from django.conf import settings
        self.assertRegex(settings.PORTAL_VERSION, r"^\d+\.\d+\.\d+$")
        antwort = self.client.get("/admin/login/")
        self.assertContains(antwort, f"Version {settings.PORTAL_VERSION}")


class EnvDateiTests(TestCase):
    def test_laedt_ohne_vorhandene_variablen_zu_ueberschreiben(self):
        import os
        from config import envdatei
        with tempfile.TemporaryDirectory() as tmp:
            datei = Path(tmp) / ".env"
            datei.write_text('# Kommentar\nENVTEST_NEU=abc=def\nENVTEST_ALT=neu\nENVTEST_Q="mit Leerzeichen"\n\n',
                             encoding="utf-8")
            with patch.dict(os.environ, {"ENVTEST_ALT": "bleibt"}):
                envdatei.laden(datei)
                self.assertEqual(os.environ["ENVTEST_NEU"], "abc=def")
                self.assertEqual(os.environ["ENVTEST_ALT"], "bleibt")
                self.assertEqual(os.environ["ENVTEST_Q"], "mit Leerzeichen")
                del os.environ["ENVTEST_NEU"], os.environ["ENVTEST_Q"]

    def test_fehlende_datei_ist_kein_fehler(self):
        from config import envdatei
        envdatei.laden("/gibt/es/nicht/.env")


class PortvergabeTests(TestCase):
    def _m(self, kuerzel, **kw):
        return MandantInstanz.objects.create(kuerzel=kuerzel, verein_name=kuerzel, domain=f"{kuerzel}.example.org", **kw)

    def test_belegte_ports_in_allen_feldern_werden_uebersprungen(self):
        from . import basis
        self._m("a", web_port=8100, paperless_port=8101, openslides_port=8102, openslides_manage_port=8103)
        self.assertEqual(basis.naechster_freier_port("web_port", 8100), 8104)

    def test_tatsaechlich_belegte_ports_werden_uebersprungen(self):
        from . import basis
        belegt = {8100, 8101}
        with override_settings(PORT_PRUEFUNG=True), \
             patch("apps.mandanten.basis.port_frei", side_effect=lambda p: p not in belegt):
            self.assertEqual(basis.naechster_freier_port("web_port", 8100), 8102)

    def test_port_frei_erkennt_einen_gebundenen_port(self):
        import socket
        from . import basis
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s, override_settings(PORT_PRUEFUNG=True):
            s.bind(("127.0.0.1", 0))
            s.listen()
            port = s.getsockname()[1]
            self.assertFalse(basis.port_frei(port))
        with override_settings(PORT_PRUEFUNG=True):
            self.assertTrue(basis.port_frei(port))      # nach dem Schliessen wieder frei
        with override_settings(PORT_PRUEFUNG=False):
            self.assertTrue(basis.port_frei(1))          # Pruefung abgeschaltet

    def test_ohne_freien_port_gibt_es_einen_klaren_fehler(self):
        from . import basis
        from .basis import BereitstellungsFehler
        with patch("apps.mandanten.basis.port_frei", return_value=False):
            with self.assertRaises(BereitstellungsFehler):
                basis.naechster_freier_port("web_port", 64990)
