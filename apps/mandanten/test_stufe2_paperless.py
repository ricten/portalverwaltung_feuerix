"""Tests fuer Ausbaustufe 2 - Paperless je Mandant. Docker/HTTP sind gemockt; den echten Ablauf (Paperless-
Container, Token, Eintrag in Feuerix) prueft der CI-Integrationstest."""
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from django.test import TestCase, override_settings

from . import basis, paperless, provisioning, proxy
from .basis import BereitstellungsFehler
from .models import MandantInstanz


def _verz(tmp):
    return override_settings(INSTANZEN_VERZEICHNIS=tmp, FEUERIX_REPO_PFAD=tmp, PROXY_NUR_HTTP=False)


def _feuerix_checkout(tmp):
    (Path(tmp) / "docker-compose.yml").write_text("services: {}\n")
    (Path(tmp) / "paperless").mkdir(exist_ok=True)
    (Path(tmp) / "paperless" / "docker-compose.yml").write_text("services: {}\n")


def _mandant(**kw):
    daten = dict(kuerzel="pl", verein_name="PL e.V.", domain="pl.example.org",
                 paperless_domain="paperless.pl.example.org", admin_email="a@example.org")
    daten.update(kw)
    return MandantInstanz.objects.create(**daten)


class PaperlessEnvTests(TestCase):
    def test_env_enthaelt_geheimnisse_port_und_url_und_bleibt_bei_wiederholung_stabil(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            m = _mandant()
            pfad = paperless.env_erzeugen(m)
            inhalt = pfad.read_text(encoding="utf-8")
            m.refresh_from_db()
            self.assertEqual(m.paperless_port, 10100)
            self.assertIn("PAPERLESS_URL=https://paperless.pl.example.org", inhalt)
            self.assertIn(f"PAPERLESS_SECRET_KEY={m.paperless_secret_key}", inhalt)
            self.assertIn("PAPERLESS_ADMIN_USER=admin", inhalt)
            alt = (m.paperless_secret_key, m.paperless_postgres_passwort, m.paperless_admin_passwort)
            paperless.env_erzeugen(m)
            m.refresh_from_db()
            self.assertEqual(alt, (m.paperless_secret_key, m.paperless_postgres_passwort,
                                   m.paperless_admin_passwort))

    def test_nur_http_modus_nutzt_http_url(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp), override_settings(PROXY_NUR_HTTP=True):
            m = _mandant()
            self.assertIn("PAPERLESS_URL=http://paperless.pl.example.org",
                          paperless.env_erzeugen(m).read_text(encoding="utf-8"))

    def test_jeder_mandant_bekommt_einen_eigenen_port(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            a = _mandant(kuerzel="a", domain="a.example.org", paperless_domain="pa.example.org")
            b = _mandant(kuerzel="b", domain="b.example.org", paperless_domain="pb.example.org")
            paperless.env_erzeugen(a)
            paperless.env_erzeugen(b)
            self.assertNotEqual(a.paperless_port, b.paperless_port)


class PaperlessComposeTests(TestCase):
    def test_hochfahren_nutzt_eigenen_projektnamen_und_mandanten_verzeichnisse(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            _feuerix_checkout(tmp)
            m = _mandant()
            paperless.env_erzeugen(m)
            with patch("apps.mandanten.basis.subprocess.run", return_value=MagicMock(returncode=0, stdout="")) as run:
                paperless.hochfahren(m)
            befehl = run.call_args.args[0]
            self.assertIn("mandant-pl-paperless", befehl)
            self.assertIn("up", befehl)
            self.assertEqual(Path(run.call_args.kwargs["cwd"]), Path(tmp) / "paperless")
            override = (Path(tmp) / "pl" / "paperless" / "compose.mandant.yml").read_text(encoding="utf-8")
            # ohne Umbiegen wuerden alle Mandanten dasselbe ./export und ./consume im Feuerix-Checkout nutzen
            self.assertIn("/usr/src/paperless/export", override)
            self.assertIn((Path(tmp) / "pl" / "paperless" / "export").resolve().as_posix(), override)
            self.assertTrue((Path(tmp) / "pl" / "paperless" / "consume").is_dir())

    def test_ohne_env_wird_abgelehnt(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            _feuerix_checkout(tmp)
            with self.assertRaises(BereitstellungsFehler):
                paperless.hochfahren(_mandant())

    def test_abbauen_mit_und_ohne_daten(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            _feuerix_checkout(tmp)
            m = _mandant()
            paperless.env_erzeugen(m)
            with patch("apps.mandanten.basis.subprocess.run", return_value=MagicMock(returncode=0, stdout="")) as run:
                paperless.abbauen(m)
                self.assertNotIn("-v", run.call_args.args[0])
                paperless.abbauen(m, daten_loeschen=True)
                self.assertIn("-v", run.call_args.args[0])

    def test_abbauen_ohne_paperless_tut_nichts(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            m = _mandant(paperless_domain="")
            with patch("apps.mandanten.basis.subprocess.run") as run:
                paperless.abbauen(m)
            run.assert_not_called()


@override_settings(PROXY_NUR_HTTP=False)
class PaperlessApiTests(TestCase):
    def test_token_wird_mit_host_header_und_zugangsdaten_geholt(self):
        m = _mandant(paperless_port=10100, paperless_admin_passwort="geheim")
        antwort = MagicMock()
        antwort.__enter__.return_value = io.BytesIO(json.dumps({"token": "abc123"}).encode())
        with patch("apps.mandanten.paperless.urllib.request.urlopen", return_value=antwort) as op:
            self.assertEqual(paperless.token_holen(m), "abc123")
        anfrage = op.call_args.args[0]
        self.assertEqual(anfrage.full_url, "http://127.0.0.1:10100/api/token/")
        self.assertEqual(anfrage.get_header("Host"), "paperless.pl.example.org")
        self.assertEqual(json.loads(anfrage.data), {"username": "admin", "password": "geheim"})

    def test_antwort_ohne_token_ist_ein_fehler(self):
        m = _mandant(paperless_port=10100)
        antwort = MagicMock()
        antwort.__enter__.return_value = io.BytesIO(b"{}")
        with patch("apps.mandanten.paperless.urllib.request.urlopen", return_value=antwort):
            with self.assertRaises(BereitstellungsFehler):
                paperless.token_holen(m)

    def test_token_wird_ueber_stdin_in_feuerix_eingetragen_nicht_per_argument(self):
        m = _mandant(paperless_port=10100)
        with patch("apps.mandanten.basis.subprocess.run", return_value=MagicMock(returncode=0, stdout="")) as run:
            paperless.in_feuerix_eintragen(m, "geheimer-token", ["docker", "compose", "-p", "mandant-pl"])
        befehl = run.call_args.args[0]
        self.assertEqual(befehl[:5], ["docker", "compose", "-p", "mandant-pl", "exec"])
        self.assertNotIn("geheimer-token", " ".join(befehl))
        skript = run.call_args.kwargs["input"]
        self.assertIn("geheimer-token", skript)
        self.assertIn("https://paperless.pl.example.org", skript)
        self.assertIn("PaperlessVerbindung", skript)

    def test_bereitschaft_zeitueberschreitung(self):
        m = _mandant(paperless_port=10100)
        with patch("apps.mandanten.paperless.urllib.request.urlopen", side_effect=ConnectionError("x")):
            with self.assertRaises(BereitstellungsFehler):
                paperless.auf_bereitschaft_warten(m, timeout_sekunden=0.2, intervall_sekunden=0.05)


class ZusatzdienstFlussTests(TestCase):
    def test_reihenfolge_bei_aktivem_paperless(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            _feuerix_checkout(tmp)
            m = _mandant()
            aufrufe = MagicMock()
            with patch("apps.mandanten.basis.subprocess.run", return_value=MagicMock(returncode=0)), \
                 patch("apps.mandanten.provisioning.urllib.request.urlopen") as op, \
                 patch("apps.mandanten.provisioning.paperless.env_erzeugen", aufrufe.env), \
                 patch("apps.mandanten.provisioning.paperless.hochfahren", aufrufe.hoch), \
                 patch("apps.mandanten.provisioning.paperless.auf_bereitschaft_warten", aufrufe.warten), \
                 patch("apps.mandanten.provisioning.paperless.token_holen", aufrufe.token) as _, \
                 patch("apps.mandanten.provisioning.paperless.in_feuerix_eintragen", aufrufe.eintragen):
                op.return_value.__enter__.return_value.status = 200
                aufrufe.token.return_value = "tok"
                provisioning.bereitstellen(m)
            namen = [c[0] for c in aufrufe.mock_calls if c[0] in ("env", "hoch", "warten", "token", "eintragen")]
            self.assertEqual(namen, ["env", "hoch", "warten", "token", "eintragen"])
            m.refresh_from_db()
            self.assertEqual(m.status, "laeuft")

    def test_paperless_fehler_setzt_fehlgeschlagen(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            _feuerix_checkout(tmp)
            m = _mandant()
            with patch("apps.mandanten.basis.subprocess.run", return_value=MagicMock(returncode=0)), \
                 patch("apps.mandanten.provisioning.urllib.request.urlopen") as op, \
                 patch("apps.mandanten.provisioning.paperless.env_erzeugen",
                       side_effect=BereitstellungsFehler("Paperless kaputt")):
                op.return_value.__enter__.return_value.status = 200
                with self.assertRaises(BereitstellungsFehler):
                    provisioning.bereitstellen(m)
            m.refresh_from_db()
            self.assertEqual(m.status, "fehlgeschlagen")
            self.assertIn("Paperless kaputt", m.protokoll)

    def test_ohne_zusatzdomain_wird_paperless_nicht_angefasst(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            _feuerix_checkout(tmp)
            m = _mandant(paperless_domain="")
            with patch("apps.mandanten.basis.subprocess.run", return_value=MagicMock(returncode=0)), \
                 patch("apps.mandanten.provisioning.urllib.request.urlopen") as op, \
                 patch("apps.mandanten.provisioning.paperless.env_erzeugen") as env:
                op.return_value.__enter__.return_value.status = 200
                provisioning.bereitstellen(m)
            env.assert_not_called()

    def test_abbau_baut_paperless_vor_feuerix_ab(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            _feuerix_checkout(tmp)
            m = _mandant(status="laeuft")
            provisioning.env_erzeugen(m)
            paperless.env_erzeugen(m)
            with patch("apps.mandanten.basis.subprocess.run", return_value=MagicMock(returncode=0, stdout="")) as run, \
                 patch("apps.mandanten.provisioning.proxy.proxy_aktualisieren", return_value=True):
                provisioning.abbauen(m)
            projekte = [c.args[0][c.args[0].index("-p") + 1] for c in run.call_args_list]
            self.assertEqual(projekte, ["mandant-pl-paperless", "mandant-pl"])


class EingabenTests(TestCase):
    def test_gleiche_domain_fuer_zwei_dienste_wird_abgelehnt(self):
        m = _mandant(paperless_domain="pl.example.org")
        with self.assertRaises(BereitstellungsFehler):
            provisioning.eingaben_pruefen(m)

    def test_ungueltige_paperless_domain_wird_abgelehnt(self):
        m = _mandant(paperless_domain="x.org {\n respond\n}")
        with self.assertRaises(BereitstellungsFehler):
            provisioning.eingaben_pruefen(m)


@override_settings(PROXY_NUR_HTTP=False)
class ProxyZusatzdienstTests(TestCase):
    def _m(self, **kw):
        return _mandant(status="laeuft", web_port=8100, paperless_port=10100, openslides_port=9100,
                        openslides_domain="os.pl.example.org", **kw)

    def test_zusatzdienste_bekommen_eigene_site_bloecke(self):
        text = proxy.caddyfile_erzeugen([self._m()])
        self.assertIn("pl.example.org {", text)
        self.assertIn("paperless.pl.example.org {", text)
        self.assertIn("reverse_proxy 127.0.0.1:10100", text)
        self.assertIn("os.pl.example.org {", text)
        self.assertIn("flush_interval -1", text)   # OpenSlides-Autoupdate braucht ungepufferte Antworten

    def test_nur_http_modus_setzt_forwarded_proto_nur_bei_feuerix(self):
        with override_settings(PROXY_NUR_HTTP=True):
            text = proxy.caddyfile_erzeugen([self._m()])
        self.assertEqual(text.count("header_up X-Forwarded-Proto https"), 1)
        self.assertIn("http://paperless.pl.example.org {", text)

    def test_ohne_port_kein_block(self):
        m = _mandant(status="laeuft", web_port=8100)
        text = proxy.caddyfile_erzeugen([m])
        self.assertNotIn("paperless.pl.example.org", text)
