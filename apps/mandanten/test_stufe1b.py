"""Tests fuer Ausbaustufe 1b (Reverse-Proxy-Automatisierung), Abbau/Lifecycle und die Eingabe-Haertung.
Docker und Caddy sind gemockt - den echten Ablauf prueft der CI-Integrationstest."""
import io
import subprocess
import tempfile
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from . import provisioning, proxy
from .models import MandantInstanz


def _urlopen_ok():
    cm = MagicMock()
    cm.__enter__.return_value.status = 200
    return cm


def _ok():
    return MagicMock(returncode=0, stderr="")


def _verz(tmp):
    return override_settings(INSTANZEN_VERZEICHNIS=tmp, FEUERIX_REPO_PFAD=tmp)


def _compose_datei(tmp):
    (Path(tmp) / "docker-compose.yml").write_text("services: {}\n")


class EingabenPruefungTests(TestCase):
    def test_zeilenumbruch_im_vereinsnamen_wird_abgelehnt(self):
        """Ohne Pruefung waere das eine Injection weiterer Variablen in die .env der Instanz."""
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            m = MandantInstanz.objects.create(kuerzel="inj", verein_name="X\nDEBUG=1", domain="x.example.org")
            with self.assertRaises(provisioning.BereitstellungsFehler):
                provisioning.env_erzeugen(m)
            self.assertFalse((Path(tmp) / "inj" / ".env").exists())

    def test_ungueltige_domain_wird_abgelehnt(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            m = MandantInstanz.objects.create(kuerzel="dom", verein_name="X", domain="x.org {\n respond 200\n}")
            with self.assertRaises(provisioning.BereitstellungsFehler):
                provisioning.env_erzeugen(m)

    def test_validierung_greift_auch_im_formular(self):
        with self.assertRaises(ValidationError):
            MandantInstanz(kuerzel="v", verein_name="ok", domain="Falsch Mit Leerzeichen").full_clean()

    def test_normale_namen_mit_umlauten_und_punkten_sind_erlaubt(self):
        m = MandantInstanz(kuerzel="ok", verein_name="Freiwillige Feuerwehr Größenhain e.V.",
                           domain="ff-groessenhain.example.org", admin_email="a@example.org")
        m.full_clean()


class ProxyTests(TestCase):
    def _mandant(self, kuerzel, port, status="laeuft", domain=None):
        return MandantInstanz.objects.create(kuerzel=kuerzel, verein_name=kuerzel.upper(), status=status,
                                             domain=domain or f"{kuerzel}.example.org", web_port=port)

    def test_caddyfile_enthaelt_nur_laufende_instanzen(self):
        self._mandant("a", 8100)
        self._mandant("b", 8101, status="pausiert")
        self._mandant("c", 8102, status="fehlgeschlagen")
        text = proxy.caddyfile_erzeugen(MandantInstanz.objects.filter(status="laeuft"))
        self.assertIn("a.example.org {", text)
        self.assertIn("reverse_proxy 127.0.0.1:8100", text)
        self.assertNotIn("b.example.org", text)
        self.assertNotIn("c.example.org", text)

    def test_nur_http_modus_setzt_forwarded_proto(self):
        self._mandant("a", 8100)
        with override_settings(PROXY_NUR_HTTP=True):
            text = proxy.caddyfile_erzeugen(MandantInstanz.objects.all())
        self.assertIn("auto_https off", text)
        self.assertIn("http://a.example.org {", text)
        self.assertIn("header_up X-Forwarded-Proto https", text)

    def test_ungueltige_domain_im_datenbestand_wird_nie_ausgeliefert(self):
        m = self._mandant("evil", 8100, domain="x.org {")
        with self.assertRaises(proxy.ProxyFehler):
            proxy.caddyfile_erzeugen([m])

    def test_ohne_admin_url_ist_die_automatisierung_aus(self):
        with override_settings(CADDY_ADMIN_URL=""), patch("apps.mandanten.proxy.urllib.request.urlopen") as op:
            self.assertFalse(proxy.proxy_aktualisieren())
        op.assert_not_called()

    def test_konfiguration_wird_per_post_an_load_gesendet(self):
        self._mandant("a", 8100)
        with override_settings(CADDY_ADMIN_URL="http://127.0.0.1:2019/"), \
             patch("apps.mandanten.proxy.urllib.request.urlopen") as op:
            self.assertTrue(proxy.proxy_aktualisieren())
        anfrage = op.call_args.args[0]
        self.assertEqual(anfrage.full_url, "http://127.0.0.1:2019/load")
        self.assertEqual(anfrage.get_method(), "POST")
        self.assertEqual(anfrage.get_header("Content-type"), "text/caddyfile")
        self.assertIn(b"a.example.org", anfrage.data)

    def test_abgelehnte_konfiguration_wird_gemeldet(self):
        err = urllib.error.HTTPError("u", 400, "Bad", {}, io.BytesIO(b"parse-fehler"))
        with override_settings(CADDY_ADMIN_URL="http://127.0.0.1:2019"), \
             patch("apps.mandanten.proxy.urllib.request.urlopen", side_effect=err):
            with self.assertRaises(proxy.ProxyFehler) as ctx:
                proxy.proxy_aktualisieren()
        self.assertIn("parse-fehler", str(ctx.exception))

    def test_nicht_erreichbarer_proxy_wird_gemeldet(self):
        with override_settings(CADDY_ADMIN_URL="http://127.0.0.1:2019"), \
             patch("apps.mandanten.proxy.urllib.request.urlopen", side_effect=ConnectionError("refused")):
            with self.assertRaises(proxy.ProxyFehler):
                proxy.proxy_aktualisieren()


class BereitstellenMitProxyTests(TestCase):
    def _lauf(self, tmp, proxy_seiteneffekt=None):
        _compose_datei(tmp)
        m = MandantInstanz.objects.create(kuerzel="px", verein_name="PX", domain="px.example.org")
        with patch("apps.mandanten.basis.subprocess.run", return_value=_ok()), \
             patch("apps.mandanten.provisioning.urllib.request.urlopen", return_value=_urlopen_ok()), \
             patch("apps.mandanten.provisioning.proxy.proxy_aktualisieren", side_effect=proxy_seiteneffekt,
                   return_value=True) as pa:
            try:
                provisioning.bereitstellen(m)
            except provisioning.BereitstellungsFehler:
                pass
        m.refresh_from_db()
        return m, pa

    def test_proxy_wird_nach_erfolgreicher_bereitstellung_aktualisiert(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            m, pa = self._lauf(tmp)
        pa.assert_called_once()
        self.assertEqual(m.status, "laeuft")

    def test_proxy_fehler_setzt_status_fehlgeschlagen(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            m, _ = self._lauf(tmp, proxy_seiteneffekt=proxy.ProxyFehler("abgelehnt"))
        self.assertEqual(m.status, "fehlgeschlagen")
        self.assertIn("Reverse Proxy: abgelehnt", m.protokoll)


class DockerFehlerTests(TestCase):
    def _mandant_mit_env(self, tmp):
        _compose_datei(tmp)
        m = MandantInstanz.objects.create(kuerzel="df", verein_name="DF", domain="df.example.org")
        provisioning.env_erzeugen(m)
        return m

    def test_fehlendes_docker_wird_als_bereitstellungsfehler_gemeldet(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            m = self._mandant_mit_env(tmp)
            with patch("apps.mandanten.basis.subprocess.run", side_effect=FileNotFoundError("docker")):
                with self.assertRaises(provisioning.BereitstellungsFehler):
                    provisioning.bereitstellen(m)
        m.refresh_from_db()
        self.assertEqual(m.status, "fehlgeschlagen")  # nicht in "wird_bereitgestellt" haengen geblieben

    def test_docker_timeout_wird_als_bereitstellungsfehler_gemeldet(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            m = self._mandant_mit_env(tmp)
            with patch("apps.mandanten.basis.subprocess.run",
                       side_effect=subprocess.TimeoutExpired("docker", 900)):
                with self.assertRaises(provisioning.BereitstellungsFehler):
                    provisioning.bereitstellen(m)
        m.refresh_from_db()
        self.assertEqual(m.status, "fehlgeschlagen")


class AbbauenTests(TestCase):
    def _laufende_instanz(self, tmp):
        _compose_datei(tmp)
        m = MandantInstanz.objects.create(kuerzel="ab", verein_name="AB", domain="ab.example.org",
                                          status="laeuft")
        provisioning.env_erzeugen(m)
        return m

    def test_abbauen_ohne_datenloeschung_setzt_pausiert(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            m = self._laufende_instanz(tmp)
            with patch("apps.mandanten.basis.subprocess.run", return_value=_ok()) as run, \
                 patch("apps.mandanten.provisioning.proxy.proxy_aktualisieren", return_value=True) as pa:
                provisioning.abbauen(m)
        befehl = run.call_args.args[0]
        self.assertIn("down", befehl)
        self.assertNotIn("-v", befehl)
        pa.assert_called_once()
        m.refresh_from_db()
        self.assertEqual(m.status, "pausiert")

    def test_abbauen_mit_datenloeschung_entfernt_volumes_und_setzt_geloescht(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            m = self._laufende_instanz(tmp)
            with patch("apps.mandanten.basis.subprocess.run", return_value=_ok()) as run, \
                 patch("apps.mandanten.provisioning.proxy.proxy_aktualisieren", return_value=True):
                provisioning.abbauen(m, daten_loeschen=True)
        self.assertIn("-v", run.call_args.args[0])
        m.refresh_from_db()
        self.assertEqual(m.status, "geloescht")

    def test_fehlschlag_beim_abbau_laesst_status_unveraendert(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            m = self._laufende_instanz(tmp)
            with patch("apps.mandanten.basis.subprocess.run",
                       return_value=MagicMock(returncode=1, stderr="kaputt")):
                with self.assertRaises(provisioning.BereitstellungsFehler):
                    provisioning.abbauen(m)
        m.refresh_from_db()
        self.assertEqual(m.status, "laeuft")
        self.assertIn("Fehler beim Abbau", m.protokoll)

    def test_management_command_abbauen(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            self._laufende_instanz(tmp)
            out = io.StringIO()
            with patch("apps.mandanten.basis.subprocess.run", return_value=_ok()), \
                 patch("apps.mandanten.provisioning.proxy.proxy_aktualisieren", return_value=True):
                call_command("mandant_abbauen", "ab", stdout=out)
        self.assertIn("pausiert", out.getvalue())
        with self.assertRaises(CommandError):
            call_command("mandant_abbauen", "gibt-es-nicht")


class WartungsseiteTests(TestCase):
    def _m(self, kuerzel, port, status, **kw):
        return MandantInstanz.objects.create(kuerzel=kuerzel, verein_name=kuerzel.upper(), status=status,
                                             domain=f"{kuerzel}.example.org", web_port=port, **kw)

    def test_seite_ist_fuer_caddy_geeignet(self):
        self.assertNotIn("`", proxy.WARTUNGSSEITE)
        self.assertNotIn("{", proxy.WARTUNGSSEITE)
        self.assertNotIn("\n", proxy.WARTUNGSSEITE)

    def test_gestoppte_instanz_zeigt_wartungsseite_mit_503(self):
        self._m("a", 8100, "gestoppt", paperless_domain="p.a.example.org", paperless_port=10100)
        text = proxy.caddyfile_erzeugen(MandantInstanz.objects.filter(status__in=proxy.WARTUNGS_STATUS))
        block = text.split("a.example.org {")[1].split("}")[0]
        self.assertIn("respond `", block)
        self.assertIn("` 503", block)
        self.assertNotIn("reverse_proxy", block)
        self.assertIn("p.a.example.org {", text)   # auch Zusatzdienste
        self.assertNotIn("reverse_proxy 127.0.0.1:10100", text)

    def test_laufende_instanz_wird_durchgereicht_und_faengt_ausfaelle_ab(self):
        self._m("b", 8101, "laeuft")
        text = proxy.caddyfile_erzeugen(MandantInstanz.objects.all())
        self.assertIn("(ausfall) {", text)
        self.assertIn("import ausfall", text)
        self.assertIn("reverse_proxy 127.0.0.1:8101", text)
        self.assertIn("http.error.status_code} in [502, 503, 504]", text)

    def test_geloeschte_instanzen_bekommen_keine_eintraege_neue_schon(self):
        self._m("n", None, "neu")
        self._m("g", 8102, "geloescht")
        with patch("apps.mandanten.proxy.urllib.request.urlopen") as op, \
             override_settings(CADDY_ADMIN_URL="http://127.0.0.1:2019"):
            proxy.proxy_aktualisieren()
        daten = op.call_args.args[0].data.decode()
        self.assertIn("n.example.org {", daten)
        self.assertNotIn("g.example.org", daten)

    def test_proxy_aktualisieren_nimmt_wartungs_instanzen_mit(self):
        self._m("p", 8103, "pausiert")
        with patch("apps.mandanten.proxy.urllib.request.urlopen") as op, \
             override_settings(CADDY_ADMIN_URL="http://127.0.0.1:2019"):
            proxy.proxy_aktualisieren()
        self.assertIn("p.example.org {", op.call_args.args[0].data.decode())


class StilllegenTests(TestCase):
    def _m(self, kuerzel, status="laeuft", **kw):
        return MandantInstanz.objects.create(kuerzel=kuerzel, verein_name=kuerzel, domain=f"{kuerzel}.example.org",
                                             status=status, web_port=8100, **kw)

    def test_abbauen_meldet_schritte_und_endet_auf_pausiert(self):
        from . import provisioning
        m = self._m("st1", paperless_domain="p.st1.example.org")
        schritte = []
        orig = provisioning.basis.fortschritt

        def merken(mandant, prozent, text):
            schritte.append((mandant.status, prozent))
            orig(mandant, prozent, text)

        with patch("apps.mandanten.provisioning.basis.fortschritt", side_effect=merken), \
             patch("apps.mandanten.provisioning.paperless.abbauen"), \
             patch("apps.mandanten.provisioning.stack_abbauen"), \
             patch("apps.mandanten.provisioning._proxy_nachziehen"):
            provisioning.abbauen(m)
        self.assertEqual([s[1] for s in schritte], [40, 70, 95])
        self.assertTrue(all(s[0] == "wird_bereitgestellt" for s in schritte))
        m.refresh_from_db()
        self.assertEqual(m.status, "pausiert")
        self.assertEqual(m.get_status_display(), "Stillgelegt (Container entfernt, Daten bleiben)")

    def test_fehler_nach_teilweisem_abbau_markiert_fehlgeschlagen(self):
        from . import provisioning
        from .basis import BereitstellungsFehler
        m = self._m("st2", paperless_domain="p.st2.example.org")
        with patch("apps.mandanten.provisioning.paperless.abbauen"), \
             patch("apps.mandanten.provisioning.stack_abbauen", side_effect=BereitstellungsFehler("kaputt")), \
             patch("apps.mandanten.provisioning.proxy_nachziehen_best_effort"):
            with self.assertRaises(BereitstellungsFehler):
                provisioning.abbauen(m)
        m.refresh_from_db()
        self.assertEqual(m.status, "fehlgeschlagen")
        self.assertIn("Stilllegen fehlgeschlagen bei", m.schritt)

    def test_admin_aktion_stilllegen_merkt_vor_und_startet_task(self):
        from django.contrib.auth import get_user_model
        from django.contrib.admin.sites import site
        from django.test import RequestFactory
        from unittest.mock import MagicMock
        laeuft, schon = self._m("st3"), self._m("st4", status="pausiert")
        admin = site._registry[MandantInstanz]
        request = RequestFactory().post("/")
        request.user = get_user_model().objects.create_superuser("stu", "s@example.org", "x")
        request._messages = MagicMock()
        with patch("apps.mandanten.tasks.mandant_stilllegen_task.delay") as delay:
            admin.stilllegen(request, MandantInstanz.objects.filter(pk__in=[laeuft.pk, schon.pk]))
        delay.assert_called_once_with(laeuft.pk)
        laeuft.refresh_from_db()
        self.assertEqual(laeuft.status, "wird_bereitgestellt")
