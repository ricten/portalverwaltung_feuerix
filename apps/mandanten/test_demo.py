"""Tests: Demo-Instanzen mit Beispieldaten (Docker gemockt)."""
import io
from unittest.mock import MagicMock, patch

from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, TestCase

from . import provisioning, tasks
from .basis import BereitstellungsFehler, status_vor_vormerkung
from .models import LogEintrag, MandantInstanz


def _m(kuerzel="dm1", **kw):
    daten = dict(kuerzel=kuerzel, verein_name="Freiwillige Feuerwehr Größenhain e.V.", domain=f"{kuerzel}.example.org",
                 status="laeuft", web_port=8100, demo=True)
    daten.update(kw)
    return MandantInstanz.objects.create(**daten)


class _Docker:
    def __init__(self, fehler=False):
        self.befehle, self.fehler = [], fehler

    def __call__(self, befehl, was, **kw):
        self.befehle.append((befehl, was))
        if self.fehler:
            raise BereitstellungsFehler(f"{was} fehlgeschlagen:\nkaputt")
        return ""


def _patch(docker):
    return patch.multiple("apps.mandanten.provisioning", docker=docker,
                          _compose_basis=MagicMock(return_value=["docker", "compose"]))


class BeispieldatenTests(TestCase):
    def test_befehl_mit_vereinskuerzel_wie_in_feuerix_und_einstellungen(self):
        m = _m(demo_anzahl=25, demo_inventar=False)
        docker = _Docker()
        with _patch(docker):
            self.assertTrue(provisioning.beispieldaten_anlegen(m))
        befehl, was = docker.befehle[0]
        self.assertEqual(befehl[:7], ["docker", "compose", "exec", "-T", "web", "python", "manage.py"])
        self.assertEqual(befehl[7:], ["beispieldaten", "--verein", "freiwillige-feuerwehr-groenhain-ev", "--anzahl", "25",
                                      "--ohne-inventar"])
        m.refresh_from_db()
        self.assertTrue(m.beispieldaten_angelegt)
        self.assertIn("Beispieldaten angelegt (25 Mitglieder, ohne Inventar)", m.protokoll)

    def test_standard_mit_inventar_und_40_mitgliedern(self):
        docker = _Docker()
        with _patch(docker):
            provisioning.beispieldaten_anlegen(_m())
        befehl = docker.befehle[0][0]
        self.assertEqual(befehl[befehl.index("--anzahl") + 1], "40")
        self.assertNotIn("--ohne-inventar", befehl)

    def test_nur_einmal_nie_doppelt(self):
        m = _m()
        docker = _Docker()
        with _patch(docker):
            self.assertTrue(provisioning.beispieldaten_anlegen(m))
            self.assertFalse(provisioning.beispieldaten_anlegen(m))
        self.assertEqual(len(docker.befehle), 1)

    def test_nur_fuer_demo_instanzen(self):
        docker = _Docker()
        with _patch(docker):
            with self.assertRaises(BereitstellungsFehler):
                provisioning.beispieldaten_anlegen(_m(demo=False))
        self.assertEqual(docker.befehle, [])

    def test_fehler_laesst_die_markierung_aus_damit_es_wiederholbar_ist(self):
        m = _m()
        with _patch(_Docker(fehler=True)):
            with self.assertRaises(BereitstellungsFehler):
                provisioning.beispieldaten_anlegen(m)
        m.refresh_from_db()
        self.assertFalse(m.beispieldaten_angelegt)


class BereitstellungTests(TestCase):
    def _bereitstellen(self, m):
        with patch("apps.mandanten.provisioning.env_erzeugen"), patch("apps.mandanten.provisioning.stack_hochfahren"), \
             patch("apps.mandanten.provisioning.auf_bereitschaft_warten"), \
             patch("apps.mandanten.provisioning.zusatzdienste_bereitstellen"), \
             patch("apps.mandanten.provisioning._proxy_nachziehen"), \
             patch("apps.mandanten.provisioning.dienstversionen.erfassen_sicher"), \
             patch("apps.mandanten.provisioning.beispieldaten_anlegen") as bsp:
            provisioning.bereitstellen(m)
        return bsp

    def test_demo_instanz_bekommt_die_beispieldaten_vor_den_zusatzdiensten(self):
        m = _m(status="neu")
        bsp = self._bereitstellen(m)
        bsp.assert_called_once()
        m.refresh_from_db()
        self.assertEqual(m.status, "laeuft")

    def test_normale_instanz_bekommt_keine(self):
        bsp = self._bereitstellen(_m("dm2", status="neu", demo=False))
        bsp.assert_not_called()

    def test_erneutes_bereitstellen_legt_sie_nicht_noch_einmal_an(self):
        bsp = self._bereitstellen(_m("dm3", status="neu", beispieldaten_angelegt=True))
        bsp.assert_not_called()

    def test_fehler_bei_den_beispieldaten_macht_die_bereitstellung_wiederholbar(self):
        m = _m("dm4", status="neu")
        with patch("apps.mandanten.provisioning.env_erzeugen"), patch("apps.mandanten.provisioning.stack_hochfahren"), \
             patch("apps.mandanten.provisioning.auf_bereitschaft_warten"), \
             patch("apps.mandanten.provisioning.proxy_nachziehen_best_effort"), \
             patch("apps.mandanten.provisioning.beispieldaten_anlegen", side_effect=BereitstellungsFehler("kaputt")):
            with self.assertRaises(BereitstellungsFehler):
                provisioning.bereitstellen(m)
        m.refresh_from_db()
        self.assertEqual(m.status, "fehlgeschlagen")
        self.assertFalse(m.beispieldaten_angelegt)


class NachtragenTests(TestCase):
    def test_laufende_demo_instanz_bekommt_sie_nachtraeglich_mit_fortschritt_und_status_zurueck(self):
        m = _m()
        with patch("apps.mandanten.provisioning.beispieldaten_anlegen") as bsp:
            provisioning.beispieldaten_nachtragen(m)
        bsp.assert_called_once()
        m.refresh_from_db()
        self.assertEqual((m.status, m.fortschritt), ("laeuft", 100))

    def test_fehler_stellt_den_status_wieder_her(self):
        m = _m()
        with patch("apps.mandanten.provisioning.beispieldaten_anlegen", side_effect=BereitstellungsFehler("kaputt")):
            with self.assertRaises(BereitstellungsFehler):
                provisioning.beispieldaten_nachtragen(m)
        m.refresh_from_db()
        self.assertEqual(m.status, "laeuft")
        self.assertIn("Beispieldaten fehlgeschlagen bei", m.schritt)

    def test_voraussetzungen(self):
        for kw in (dict(demo=False), dict(status="gestoppt")):
            with self.assertRaises(BereitstellungsFehler):
                provisioning.beispieldaten_nachtragen(_m(f"x{len(kw)}{list(kw)[0][:2]}", **kw))

    def test_task_und_befehl(self):
        m = _m("dm5")
        with patch("apps.mandanten.provisioning.beispieldaten_nachtragen") as nachtragen:
            tasks.mandant_beispieldaten_task(m.pk)
            out = io.StringIO()
            call_command("mandant_beispieldaten", "dm5", "--anzahl", "12", "--ohne-inventar", stdout=out)
        self.assertEqual(nachtragen.call_count, 2)
        m.refresh_from_db()
        self.assertEqual((m.demo_anzahl, m.demo_inventar), (12, False))
        self.assertIn("Beispieldaten angelegt", out.getvalue())
        with self.assertRaises(CommandError):
            call_command("mandant_beispieldaten", "gibt-es-nicht", stdout=io.StringIO())


class ModellUndAdminTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("dmo", "d@example.org", "pw"))

    def test_anzahl_wird_begrenzt(self):
        for schlecht in (0, 501):
            with self.assertRaises(ValidationError):
                MandantInstanz(kuerzel="v", verein_name="V", domain="v.example.org", demo_anzahl=schlecht).full_clean()
        MandantInstanz(kuerzel="v", verein_name="V", domain="v.example.org", demo_anzahl=500).full_clean()

    def test_liste_zeigt_demo_abzeichen_und_filter(self):
        _m("dm6")
        _m("dm7", demo=False, verein_name="Echter Verein e.V.")
        html = self.client.get("/admin/mandanten/mandantinstanz/").content.decode()
        self.assertEqual(html.count(">DEMO<"), 1)
        nur_demo = self.client.get("/admin/mandanten/mandantinstanz/?demo__exact=1").content.decode()
        self.assertIn("dm6.example.org", nur_demo)
        self.assertNotIn("dm7.example.org", nur_demo)

    def test_formular_hat_die_demo_felder_und_speichert_sie(self):
        antwort = self.client.get("/admin/mandanten/mandantinstanz/add/")
        self.assertContains(antwort, "Demo-Instanz (mit Beispieldaten)")
        self.assertContains(antwort, "Anzahl Beispiel-Mitglieder")
        self.client.post("/admin/mandanten/mandantinstanz/add/", dict(
            kuerzel="demo-neu", verein_name="Demo e.V.", domain="demo.example.org", admin_benutzername="admin",
            admin_email="", demo="on", demo_anzahl="15", demo_inventar="on"))
        m = MandantInstanz.objects.get(kuerzel="demo-neu")
        self.assertEqual((m.demo, m.demo_anzahl, m.demo_inventar), (True, 15, True))
        self.assertFalse(m.beispieldaten_angelegt)

    def _aktion(self, m):
        admin = site._registry[MandantInstanz]
        request = RequestFactory().post("/")
        request.user = get_user_model().objects.get(username="dmo")
        request._messages = MagicMock()
        with patch("apps.mandanten.tasks.mandant_beispieldaten_task.delay") as delay:
            admin.beispieldaten(request, MandantInstanz.objects.filter(pk=m.pk))
        return delay

    def test_aktion_startet_nur_bei_laufender_noch_nicht_befuellter_demo_instanz(self):
        m = _m("dm8")
        delay = self._aktion(m)
        delay.assert_called_once_with(m.pk)
        m.refresh_from_db()
        self.assertEqual(status_vor_vormerkung(m), "laeuft")
        self.assertTrue(LogEintrag.objects.filter(text__contains="Beispieldaten anlegen gestartet", benutzer="dmo").exists())
        for kw in (dict(demo=False), dict(beispieldaten_angelegt=True), dict(status="gestoppt")):
            fremd = _m(f"y{len(kw)}{list(kw)[0][:3]}", **kw)
            self._aktion(fremd).assert_not_called()
