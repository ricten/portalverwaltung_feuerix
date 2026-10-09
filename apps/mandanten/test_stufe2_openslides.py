"""Tests fuer OpenSlides je Mandant. Docker/Downloads sind gemockt; den echten Ablauf prueft der CI-Job."""
import tempfile
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from . import openslides
from .basis import BereitstellungsFehler
from .models import MandantInstanz

CONFIG = "host: 127.0.0.1\nport: 9000\ndefaults:\n  tag: 4.3.0\n"


def _verz(tmp):
    return override_settings(INSTANZEN_VERZEICHNIS=tmp, FEUERIX_REPO_PFAD=tmp, PROXY_NUR_HTTP=False)


def _feuerix(tmp):
    (Path(tmp) / "openslides").mkdir(exist_ok=True)
    (Path(tmp) / "openslides" / "config.yml").write_text(CONFIG, encoding="utf-8")


def _mandant(**kw):
    daten = dict(kuerzel="os", verein_name="OS e.V.", domain="os.example.org",
                 openslides_domain="versammlung.os.example.org")
    daten.update(kw)
    return MandantInstanz.objects.create(**daten)


def _ok():
    return MagicMock(returncode=0, stdout="", stderr="")


class ConfigTests(TestCase):
    def test_config_uebernimmt_vorlage_mit_mandanten_port(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            _feuerix(tmp)
            m = _mandant()
            pfad = openslides.config_erzeugen(m)
            text = pfad.read_text(encoding="utf-8")
            self.assertIn("port: 9100", text)
            self.assertNotIn("port: 9000", text)
            self.assertIn("tag: 4.3.0", text)
            self.assertIn("host: 127.0.0.1", text)

    def test_jeder_mandant_bekommt_einen_eigenen_port(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            _feuerix(tmp)
            a, b = _mandant(kuerzel="a"), _mandant(kuerzel="b", domain="b.example.org",
                                                   openslides_domain="v.b.example.org")
            openslides.config_erzeugen(a)
            openslides.config_erzeugen(b)
            self.assertNotEqual(a.openslides_port, b.openslides_port)

    def test_fehlende_vorlage_ist_fehler(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            with self.assertRaises(BereitstellungsFehler):
                openslides.config_erzeugen(_mandant())


class EinrichtenTests(TestCase):
    def _setup_fake(self, tmp, m):
        """Simuliert osmanage setup: legt docker-compose.yml und secrets/superadmin an."""
        def fake(befehl, was, **kw):
            if "setup" in befehl:
                verz = Path(kw["cwd"])
                (verz / "secrets").mkdir(exist_ok=True)
                (verz / "secrets" / "superadmin").write_text("PW-123\n")
                (verz / "docker-compose.yml").write_text("services: {}\n")
            return ""
        return fake

    def test_setup_liest_superadmin_passwort_und_setzt_projektnamen(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            _feuerix(tmp)
            m = _mandant()
            (Path(tmp) / "_werkzeuge").mkdir()
            (Path(tmp) / "_werkzeuge" / "osmanage").write_text("x")
            (Path(tmp) / "_werkzeuge" / "docker-compose.yml.tmpl").write_text("x")
            with patch("apps.mandanten.basis.docker", side_effect=self._setup_fake(tmp, m)) as d:
                openslides.einrichten(m)
            self.assertEqual(d.call_args.kwargs["env"], {"COMPOSE_PROJECT_NAME": "mandant-os-openslides"})
            m.refresh_from_db()
            self.assertEqual(m.openslides_superadmin_passwort, "PW-123")

    def test_setup_wird_bei_vorhandener_compose_datei_nicht_wiederholt(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            _feuerix(tmp)
            m = _mandant()
            (Path(tmp) / "_werkzeuge").mkdir()
            (Path(tmp) / "_werkzeuge" / "osmanage").write_text("x")
            (Path(tmp) / "_werkzeuge" / "docker-compose.yml.tmpl").write_text("x")
            verz = Path(tmp) / "os" / "openslides"
            (verz / "secrets").mkdir(parents=True)
            (verz / "secrets" / "superadmin").write_text("ALT")
            (verz / "docker-compose.yml").write_text("services: {}\n")
            with patch("apps.mandanten.basis.docker") as d:
                openslides.einrichten(m)
            d.assert_not_called()
            m.refresh_from_db()
            self.assertEqual(m.openslides_superadmin_passwort, "ALT")

    def test_fehlendes_geheimnis_ist_fehler(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            _feuerix(tmp)
            (Path(tmp) / "_werkzeuge").mkdir()
            (Path(tmp) / "_werkzeuge" / "osmanage").write_text("x")
            (Path(tmp) / "_werkzeuge" / "docker-compose.yml.tmpl").write_text("x")
            with patch("apps.mandanten.basis.docker"):
                with self.assertRaises(BereitstellungsFehler):
                    openslides.einrichten(_mandant())

    def test_download_fehler_wird_zum_bereitstellungsfehler(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            with patch("apps.mandanten.openslides.urllib.request.urlopen",
                       side_effect=urllib.error.URLError("offline")):
                with self.assertRaises(BereitstellungsFehler):
                    openslides.werkzeuge_bereitstellen()

    def test_werkzeuge_werden_nur_einmal_geladen(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            antwort = MagicMock()
            antwort.__enter__.return_value.read.return_value = b"daten"
            with patch("apps.mandanten.openslides.urllib.request.urlopen", return_value=antwort) as op:
                openslides.werkzeuge_bereitstellen()
                openslides.werkzeuge_bereitstellen()
            self.assertEqual(op.call_count, 2)   # osmanage + Vorlage, beim zweiten Mal aus dem Cache


class StartdatenTests(TestCase):
    def test_wiederholt_bis_erfolgreich_und_merkt_sich_initialisierung(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            m = _mandant()
            folge = [BereitstellungsFehler("noch nicht bereit"), BereitstellungsFehler("noch nicht"), ""]
            with patch("apps.mandanten.basis.docker", side_effect=folge) as d:
                openslides.startdaten_anlegen(m, Path("osmanage"), pause_sekunden=0)
            self.assertEqual(d.call_count, 3)
            m.refresh_from_db()
            self.assertTrue(m.openslides_initialisiert)

    def test_nicht_erneut_wenn_schon_initialisiert(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            m = _mandant(openslides_initialisiert=True)
            with patch("apps.mandanten.basis.docker") as d:
                openslides.startdaten_anlegen(m, Path("osmanage"), pause_sekunden=0)
            d.assert_not_called()

    def test_gibt_nach_allen_versuchen_auf(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            m = _mandant()
            with patch("apps.mandanten.basis.docker", side_effect=BereitstellungsFehler("nein")) as d:
                with self.assertRaises(BereitstellungsFehler):
                    openslides.startdaten_anlegen(m, Path("osmanage"), versuche=3, pause_sekunden=0)
            self.assertEqual(d.call_count, 3)
            m.refresh_from_db()
            self.assertFalse(m.openslides_initialisiert)


@override_settings(PROXY_NUR_HTTP=False)
class AnbindungTests(TestCase):
    def test_passwort_geht_ueber_stdin_nicht_als_argument(self):
        m = _mandant(openslides_superadmin_passwort="sehr-geheim")
        with patch("apps.mandanten.basis.subprocess.run", return_value=_ok()) as run:
            openslides.in_feuerix_eintragen(m, ["docker", "compose", "-p", "mandant-os"])
        befehl = run.call_args.args[0]
        self.assertNotIn("sehr-geheim", " ".join(befehl))
        skript = run.call_args.kwargs["input"]
        self.assertIn("sehr-geheim", skript)
        self.assertIn("https://versammlung.os.example.org", skript)
        self.assertIn("OpenSlidesVerbindung", skript)

    def test_bereitschaft_zeitueberschreitung(self):
        m = _mandant(openslides_port=9100)
        with patch("apps.mandanten.openslides.urllib.request.urlopen", side_effect=ConnectionError("x")):
            with self.assertRaises(BereitstellungsFehler):
                openslides.auf_bereitschaft_warten(m, timeout_sekunden=0.2, intervall_sekunden=0.05)


class AbbauTests(TestCase):
    def test_abbau_mit_daten_und_projektnamen(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            m = _mandant()
            verz = Path(tmp) / "os" / "openslides"
            verz.mkdir(parents=True)
            (verz / "docker-compose.yml").write_text("services: {}\n")
            with patch("apps.mandanten.basis.subprocess.run", return_value=_ok()) as run:
                openslides.abbauen(m, daten_loeschen=True)
            self.assertEqual(run.call_args.args[0], ["docker", "compose", "down", "-v"])
            self.assertEqual(run.call_args.kwargs["env"]["COMPOSE_PROJECT_NAME"], "mandant-os-openslides")

    def test_ohne_openslides_tut_abbau_nichts(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            with patch("apps.mandanten.basis.subprocess.run") as run:
                openslides.abbauen(_mandant(openslides_domain=""))
            run.assert_not_called()


class VerwaltungsportTests(TestCase):
    def test_neue_stacks_bekommen_eigene_verwaltungsports_in_der_config(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            _feuerix(tmp)
            a = _mandant(kuerzel="a")
            b = _mandant(kuerzel="b", domain="b.example.org", openslides_domain="v.b.example.org")
            text_a = openslides.config_erzeugen(a).read_text(encoding="utf-8")
            text_b = openslides.config_erzeugen(b).read_text(encoding="utf-8")
        self.assertNotEqual(a.openslides_manage_port, b.openslides_manage_port)
        self.assertGreaterEqual(a.openslides_manage_port, 9200)
        self.assertIn(f"backendManagePort: {a.openslides_manage_port}", text_a)
        self.assertIn(f"backendManagePort: {b.openslides_manage_port}", text_b)
        self.assertEqual(text_a.count("backendManagePort"), 1)

    def test_vorhandene_config_behaelt_ihren_versions_tag(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            _feuerix(tmp)
            m = _mandant()
            verz = Path(tmp) / "os" / "openslides"
            verz.mkdir(parents=True)
            (verz / "config.yml").write_text("port: 9100\ndefaults:\n  tag: 4.3.7\n", encoding="utf-8")
            (verz / "docker-compose.yml").write_text("services: {}\n")
            text = openslides.config_erzeugen(m).read_text(encoding="utf-8")
        self.assertIn("tag: 4.3.7", text)
        self.assertNotIn("tag: 4.3.0", text)

    def test_alte_instanz_mit_vorhandener_compose_datei_bekommt_keinen_neuen_port(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            _feuerix(tmp)
            m = _mandant()
            verz = Path(tmp) / "os" / "openslides"
            verz.mkdir(parents=True)
            (verz / "docker-compose.yml").write_text("services: {}\n")
            text = openslides.config_erzeugen(m).read_text(encoding="utf-8")
        self.assertIsNone(m.openslides_manage_port)
        self.assertNotIn("backendManagePort", text)
        self.assertEqual(openslides.verwaltungsport(m), 9002)

    def test_initial_data_nutzt_den_verwaltungsport_und_die_passwortdatei(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            m = _mandant(openslides_manage_port=9201)
            verz = Path(tmp) / "os" / "openslides"
            (verz / "secrets").mkdir(parents=True)
            (verz / "secrets" / "internal_auth_password").write_text("x")
            with patch("apps.mandanten.basis.docker") as d:
                openslides.startdaten_anlegen(m, Path("osmanage"), pause_sekunden=0)
        befehl = d.call_args.args[0]
        self.assertEqual(befehl[befehl.index("--address") + 1], "localhost:9201")
        self.assertIn("--password-file", befehl)
        self.assertEqual(befehl[befehl.index("--password-file") + 1], "secrets/internal_auth_password")

    def test_alte_instanz_spricht_weiter_port_9002_an(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            m = _mandant()
            with patch("apps.mandanten.basis.docker") as d:
                openslides.startdaten_anlegen(m, Path("osmanage"), pause_sekunden=0)
        befehl = d.call_args.args[0]
        self.assertEqual(befehl[befehl.index("--address") + 1], "localhost:9002")


class KollisionReparaturTests(TestCase):
    def _vorbereiten(self, tmp, **kw):
        _feuerix(tmp)
        (Path(tmp) / "_werkzeuge").mkdir()
        (Path(tmp) / "_werkzeuge" / "osmanage").write_text("x")
        (Path(tmp) / "_werkzeuge" / "docker-compose.yml.tmpl").write_text("x")
        m = _mandant(**kw)
        verz = Path(tmp) / m.kuerzel / "openslides"
        (verz / "secrets").mkdir(parents=True)
        (verz / "secrets" / "superadmin").write_text("ALT")
        (verz / "docker-compose.yml").write_text("services: {}  # 127.0.0.1:9002:9002\n")
        return m, verz

    def _fake(self):
        def fake(befehl, was, **kw):
            if "setup" in befehl:
                verz = Path(kw["cwd"])
                (verz / "secrets").mkdir(exist_ok=True)
                (verz / "secrets" / "superadmin").write_text("NEU")
                (verz / "docker-compose.yml").write_text("services: {}  # neu\n")
            return ""
        return fake

    def test_nicht_initialisierte_kollision_wird_neu_erzeugt(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            andere = _mandant(kuerzel="andere", domain="andere.example.org", openslides_domain="v.andere.example.org")
            m, verz = self._vorbereiten(tmp)
            with patch("apps.mandanten.basis.docker", side_effect=self._fake()) as d:
                openslides.einrichten(m)
            befehle = [c.args[0] for c in d.call_args_list]
            self.assertIn(["docker", "compose", "down", "-v"], befehle)
            self.assertTrue(any("setup" in b for b in befehle))
            self.assertIn("neu", (verz / "docker-compose.yml").read_text())
            self.assertIn("backendManagePort", (verz / "config.yml").read_text())
            m.refresh_from_db()
            self.assertIsNotNone(m.openslides_manage_port)
            self.assertEqual(m.openslides_superadmin_passwort, "NEU")

    def test_initialisierte_instanz_wird_nie_angefasst(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            _mandant(kuerzel="andere", domain="andere.example.org", openslides_domain="v.andere.example.org")
            m, verz = self._vorbereiten(tmp, openslides_initialisiert=True)
            with patch("apps.mandanten.basis.docker") as d:
                openslides.einrichten(m)
            d.assert_not_called()
            self.assertIn("9002", (verz / "docker-compose.yml").read_text())

    def test_ohne_andere_instanz_bleibt_alles_wie_es_ist(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            m, verz = self._vorbereiten(tmp)
            with patch("apps.mandanten.basis.docker") as d:
                openslides.einrichten(m)
            d.assert_not_called()
