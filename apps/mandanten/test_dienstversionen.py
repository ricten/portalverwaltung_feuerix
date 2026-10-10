"""Tests fuer die Versions-Erfassung und -Pruefung von Paperless-ngx und OpenSlides (GitHub und HTTP gemockt)."""
import io
import tempfile
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.mail import get_connection
from django.test import TestCase, override_settings

from . import dienstversionen, updates
from .basis import BereitstellungsFehler
from .models import Benachrichtigung, LogEintrag, MandantInstanz, UpdateZustand
from .test_updates import _git_antworten

OS_CONFIG = """host: 127.0.0.1
port: 9100
defaults:
  containerRegistry: ghcr.io/openslides/openslides
  tag: 4.2.0           # feste Version
"""


def _m(kuerzel="d1", **kw):
    daten = dict(kuerzel=kuerzel, verein_name="Verein D", domain=f"{kuerzel}.example.org", status="laeuft",
                 web_port=8100, paperless_domain=f"p.{kuerzel}.example.org", paperless_port=10100,
                 openslides_domain=f"o.{kuerzel}.example.org", openslides_port=9100)
    daten.update(kw)
    return MandantInstanz.objects.create(**daten)


def _antwort(header):
    r = MagicMock()
    r.headers = header
    r.__enter__ = lambda s: s
    r.__exit__ = lambda s, *a: False
    return r


def _github(paperless="v2.15.0", openslides="4.3.1"):
    def holen(url, timeout=10):
        return {"tag_name": paperless if "paperless" in url else openslides}
    return holen


class ErfassenTests(TestCase):
    def _docker(self, ausgabe=None, fehler=None):
        def docker(befehl, was, **kw):
            if fehler:
                raise BereitstellungsFehler(fehler)
            docker.befehl = befehl
            return ausgabe
        return docker

    def test_paperless_version_aus_dem_container(self):
        m = _m()
        docker = self._docker("2.14.7\n")
        with patch("apps.mandanten.dienstversionen.paperless._compose_basis", return_value=(["docker", "compose"], Path("."))), \
             patch("apps.mandanten.dienstversionen.basis.docker", side_effect=docker):
            self.assertEqual(dienstversionen.paperless_laufend(m), "2.14.7")
        self.assertEqual(docker.befehl[-4:-1], ["webserver", "python3", "-c"])
        self.assertIn("__full_version_str__", docker.befehl[-1])

    def test_ausgabe_mit_warnzeilen_und_fuehrendem_v(self):
        m = _m()
        with patch("apps.mandanten.dienstversionen.paperless._compose_basis", return_value=(["docker"], Path("."))), \
             patch("apps.mandanten.dienstversionen.basis.docker", side_effect=self._docker("WARN irgendwas\nv3.2.1\n")):
            self.assertEqual(dienstversionen.paperless_laufend(m), "3.2.1")

    def test_unerwartete_ausgabe_und_dockerfehler_sind_fehler_mit_grund(self):
        m = _m()
        with patch("apps.mandanten.dienstversionen.paperless._compose_basis", return_value=(["docker"], Path("."))):
            with patch("apps.mandanten.dienstversionen.basis.docker", side_effect=self._docker("Traceback ...")):
                with self.assertRaises(BereitstellungsFehler):
                    dienstversionen.paperless_laufend(m)
            with patch("apps.mandanten.dienstversionen.basis.docker", side_effect=self._docker(fehler="Container läuft nicht")):
                with self.assertRaises(BereitstellungsFehler):
                    dienstversionen.paperless_laufend(m)

    def test_erfassen_sammelt_den_grund_und_stoert_nicht(self):
        m = _m()
        gruende = []
        with patch("apps.mandanten.dienstversionen.paperless_laufend",
                   side_effect=BereitstellungsFehler("Container läuft nicht")):
            dienstversionen.erfassen(m, gruende)
        self.assertEqual(gruende, ["d1: Container läuft nicht"])
        m.refresh_from_db()
        self.assertEqual(m.paperless_version, "")

    def test_pruefen_zeigt_den_grund_im_hinweis_der_zusatzdienste(self):
        m = _m()
        z = UpdateZustand.holen()
        with patch("apps.mandanten.dienstversionen.paperless_laufend",
                   side_effect=BereitstellungsFehler("Container läuft nicht")), \
             patch("apps.mandanten.dienstversionen._json_holen", side_effect=_github()):
            dienstversionen.pruefen(z)
        self.assertIn("d1: Container läuft nicht", z.dienste_fehler)

    def test_openslides_version_aus_der_config(self):
        m = _m()
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp):
            (Path(tmp) / "d1" / "openslides").mkdir(parents=True)
            (Path(tmp) / "d1" / "openslides" / "config.yml").write_text(OS_CONFIG)
            self.assertEqual(dienstversionen.openslides_konfiguriert(m), "4.2.0")
            self.assertEqual(dienstversionen.openslides_konfiguriert(_m("d2")), "")

    def test_instanzen_erfassen_speichert_nur_fuer_eingerichtete_dienste(self):
        m = _m()
        ohne = _m("d3", paperless_domain="", paperless_port=None, openslides_domain="", openslides_port=None)
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp), \
             patch("apps.mandanten.dienstversionen.paperless_laufend", return_value="2.14.0"):
            (Path(tmp) / "d1" / "openslides").mkdir(parents=True)
            (Path(tmp) / "d1" / "openslides" / "config.yml").write_text(OS_CONFIG)
            dienstversionen.instanzen_erfassen()
        m.refresh_from_db()
        ohne.refresh_from_db()
        self.assertEqual((m.paperless_version, m.openslides_version), ("2.14.0", "4.2.0"))
        self.assertEqual((ohne.paperless_version, ohne.openslides_version), ("", ""))


class PruefenTests(TestCase):
    def test_neueste_version_ohne_fuehrendes_v(self):
        with patch("apps.mandanten.dienstversionen._json_holen", side_effect=_github()):
            self.assertEqual(dienstversionen.neueste_version("paperless"), "2.15.0")
            self.assertEqual(dienstversionen.neueste_version("openslides"), "4.3.1")

    def test_hinweise_nur_fuer_aeltere_versionen(self):
        _m("alt", paperless_version="2.14.0", openslides_version="4.3.1")
        _m("neu", paperless_version="2.15.0", openslides_version="4.2.0")
        z = UpdateZustand.holen()
        z.paperless_neueste, z.openslides_neueste = "2.15.0", "4.3.1"
        h = {x["dienst"]: x["instanzen"] for x in z.dienste_hinweise}
        self.assertEqual(h["paperless"], [("alt", "2.14.0")])
        self.assertEqual(h["openslides"], [("neu", "4.2.0")])

    def test_pruefen_holt_neueste_meldet_im_log_und_nur_einmal(self):
        _m(paperless_version="2.14.0", openslides_version="4.3.1")
        z = UpdateZustand.holen()
        with patch("apps.mandanten.dienstversionen._json_holen", side_effect=_github()), \
             patch("apps.mandanten.dienstversionen.instanzen_erfassen"):
            dienstversionen.pruefen(z)
            dienstversionen.pruefen(z)
        self.assertEqual((z.paperless_neueste, z.openslides_neueste), ("2.15.0", "4.3.1"))
        eintraege = LogEintrag.objects.filter(text__contains="Paperless-ngx 2.15.0 verfügbar")
        self.assertEqual(eintraege.count(), 1)
        self.assertEqual(z.paperless_mail_version, "2.15.0")
        self.assertEqual(z.openslides_mail_version, "")     # OpenSlides ist aktuell

    def test_mail_bei_neuer_dienstversion(self):
        Benachrichtigung.objects.update_or_create(pk=1, defaults=dict(
            aktiv=True, empfaenger="a@example.org", absender="p@example.org", smtp_host="h"))
        _m(paperless_version="2.14.0")
        z = UpdateZustand.holen()
        with patch("apps.mandanten.dienstversionen._json_holen", side_effect=_github(openslides="4.2.0")), \
             patch("apps.mandanten.dienstversionen.instanzen_erfassen"), \
             patch("apps.mandanten.benachrichtigung._verbindung",
                   side_effect=lambda c: get_connection("django.core.mail.backends.locmem.EmailBackend")):
            dienstversionen.pruefen(z)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Paperless-ngx 2.15.0 verfügbar", mail.outbox[0].subject)
        self.assertIn("d1 (2.14.0)", mail.outbox[0].body)

    def test_github_fehler_wird_gespeichert_und_blockiert_nichts(self):
        z = UpdateZustand.holen()
        with patch("apps.mandanten.dienstversionen._json_holen", side_effect=urllib.error.URLError("kein Netz")), \
             patch("apps.mandanten.dienstversionen.instanzen_erfassen"):
            dienstversionen.pruefen(z)
        self.assertIn("kein Netz", z.dienste_fehler)

    @override_settings(UPDATE_DIENSTE_PRUEFEN=True)
    def test_update_pruefung_ruft_die_dienstepruefung_auf_und_ein_fehler_dort_stoert_nicht(self):
        with patch("apps.mandanten.updates._git", side_effect=_git_antworten(remote="1.58.0")), \
             patch("apps.mandanten.provisioning.feuerix_version", return_value="1.58.0"), \
             patch("apps.mandanten.updates.dienstversionen.pruefen", side_effect=RuntimeError("boom")):
            z = updates.pruefen()
        self.assertEqual(z.remote_version, "1.58.0")
        self.assertIn("boom", z.dienste_fehler)


class OberflaecheTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("dv", "d@example.org", "pw"))

    def test_spalte_und_banner(self):
        _m(paperless_version="2.14.0", openslides_version="4.3.1")
        UpdateZustand.objects.update_or_create(pk=1, defaults=dict(
            lokale_version="1.58.0", remote_version="1.58.0", paperless_neueste="2.15.0", openslides_neueste="4.3.1"))
        html = self.client.get("/admin/mandanten/mandantinstanz/").content.decode()
        self.assertIn("Paperless 2.14.0 → 2.15.0 verfügbar", html)
        self.assertIn("OpenSlides 4.3.1", html)
        self.assertNotIn("OpenSlides 4.3.1 →", html)
        self.assertIn("Paperless-ngx 2.15.0 verfügbar.", html)
        self.assertIn("nicht automatisch", html)
