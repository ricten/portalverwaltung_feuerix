"""Tests: Demo-Instanzen mit Beispieldaten (Docker gemockt)."""
import io
import tempfile
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from . import demo, provisioning, tasks
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


@contextmanager
def _patch(docker):
    """Docker und den Demo-Standard ersetzen - ein Test darf nie ein echtes docker aufrufen."""
    with patch.multiple("apps.mandanten.provisioning", docker=docker,
                        _compose_basis=MagicMock(return_value=["docker", "compose"])), \
         patch("apps.mandanten.demo.standard_sichern") as standard:
        yield standard


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


# --------------------------------------------------------------------------------------- Demo-Standard und Zuruecksetzen
BACKUP_AUSGABE = "Sicherung abgeschlossen: /data/backups (*-20261012-101500)\nWICHTIG: FIELD_ENCRYPTION_KEY separat sichern!\n"


class _DemoDocker:
    def __init__(self, fehler_bei=None, ausgabe=BACKUP_AUSGABE):
        self.befehle, self.fehler_bei, self.ausgabe = [], fehler_bei, ausgabe

    def __call__(self, befehl, was, **kw):
        self.befehle.append((befehl, was, kw))
        if self.fehler_bei and self.fehler_bei in was:
            raise BereitstellungsFehler(f"{was} fehlgeschlagen:\nkaputt")
        if any("backup.sh" in teil for teil in befehl):
            return self.ausgabe
        return ""

    def namen(self):
        return [was for _, was, _ in self.befehle]


def _ordner(tmp, m, mit_medien=True):
    ordner = Path(tmp) / m.kuerzel / "demo-standard"
    ordner.mkdir(parents=True)
    (ordner / "db.sql.gz").write_text("db")
    if mit_medien:
        (ordner / "media.tar.gz").write_text("media")
    return ordner


class StandardSichernTests(TestCase):
    def _lauf(self, m, docker, tmp):
        with override_settings(INSTANZEN_VERZEICHNIS=tmp), \
             patch("apps.mandanten.demo.basis.docker", side_effect=docker), \
             patch("apps.mandanten.demo.provisioning._compose_basis", return_value=["docker", "compose"]):
            demo.standard_sichern(m)

    def test_sichert_mit_dem_zeitstempel_aus_der_ausgabe_und_merkt_sich_den_zeitpunkt(self):
        m = _m("ds1")
        docker = _DemoDocker()
        with tempfile.TemporaryDirectory() as tmp:
            self._lauf(m, docker, tmp)
            kopien = [b for b, was, _ in docker.befehle if "cp" in b]
            self.assertEqual([k[-2] for k in kopien], ["web:/data/backups/db-20261012-101500.sql.gz",
                                                       "web:/data/backups/media-20261012-101500.tar.gz"])
            self.assertTrue(kopien[0][-1].endswith("db.sql.gz"))
            self.assertTrue(kopien[1][-1].endswith("media.tar.gz"))
        m.refresh_from_db()
        self.assertIsNotNone(m.demo_standard_am)
        self.assertIn("Demo-Standard gespeichert", m.protokoll)

    def test_ohne_erkennbaren_zeitstempel_oder_bei_dockerfehler_wird_nichts_markiert(self):
        for docker in (_DemoDocker(ausgabe="irgendwas"), _DemoDocker(fehler_bei="Medien kopieren")):
            m = _m(f"ds{id(docker) % 1000}")
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(BereitstellungsFehler):
                    self._lauf(m, docker, tmp)
            m.refresh_from_db()
            self.assertIsNone(m.demo_standard_am)

    def test_wird_bei_aktivem_backup_ziel_auch_auf_die_freigabe_gelegt_ein_fehler_dort_stoert_nicht(self):
        m = _m("ds2")
        with tempfile.TemporaryDirectory() as tmp, patch("apps.mandanten.demo.backupziel.aktuell", return_value=True), \
             patch("apps.mandanten.demo.backupziel.hochladen") as hoch:
            self._lauf(m, _DemoDocker(), tmp)
            hoch.assert_called_once()
            self.assertEqual(hoch.call_args.args[2], "demo-standard")
        with tempfile.TemporaryDirectory() as tmp, patch("apps.mandanten.demo.backupziel.aktuell", return_value=True), \
             patch("apps.mandanten.demo.backupziel.hochladen", side_effect=BereitstellungsFehler("Netz weg")):
            self._lauf(m, _DemoDocker(), tmp)
        m.refresh_from_db()
        self.assertIn("nicht auf die Freigabe hochgeladen", m.protokoll)

    def test_nach_den_beispieldaten_wird_der_standard_gespeichert_ein_fehler_dort_ist_nur_ein_hinweis(self):
        m = _m("ds3")
        docker = _Docker()
        with _patch(docker) as standard:
            provisioning.beispieldaten_anlegen(m)
        standard.assert_called_once_with(m)
        m2 = _m("ds4")
        with _patch(_Docker()) as standard:
            standard.side_effect = BereitstellungsFehler("Platte voll")
            self.assertTrue(provisioning.beispieldaten_anlegen(m2))
        m2.refresh_from_db()
        self.assertTrue(m2.beispieldaten_angelegt)
        self.assertIn("Demo-Standard konnte nicht gespeichert werden", m2.protokoll)

    def test_aktion_speichert_nur_fuer_laufende_demo_instanzen_und_stellt_den_status_wieder_her(self):
        m = _m("ds5")
        with patch("apps.mandanten.demo.standard_sichern") as sichern:
            demo.standard_speichern_aktion(m)
        sichern.assert_called_once()
        m.refresh_from_db()
        self.assertEqual((m.status, m.fortschritt), ("laeuft", 100))
        with patch("apps.mandanten.demo.standard_sichern", side_effect=BereitstellungsFehler("kaputt")):
            with self.assertRaises(BereitstellungsFehler):
                demo.standard_speichern_aktion(m)
        m.refresh_from_db()
        self.assertEqual(m.status, "laeuft")
        for kw in (dict(demo=False), dict(status="gestoppt")):
            with self.assertRaises(BereitstellungsFehler):
                demo.standard_speichern_aktion(_m(f"dz{len(kw)}{list(kw)[0][:2]}", **kw))


class ZuruecksetzenTests(TestCase):
    def _lauf(self, m, docker=None, mit_medien=True, tmp_ordner=None):
        docker = docker or _DemoDocker()
        mit = tempfile.TemporaryDirectory()
        self.addCleanup(mit.cleanup)
        with override_settings(INSTANZEN_VERZEICHNIS=mit.name):
            _ordner(mit.name, m, mit_medien)
            with patch("apps.mandanten.demo.basis.docker", side_effect=docker), \
                 patch("apps.mandanten.demo.provisioning._compose_basis", return_value=["docker", "compose"]), \
                 patch("apps.mandanten.demo.provisioning.stack_steuern") as steuern, \
                 patch("apps.mandanten.demo.provisioning.auf_bereitschaft_warten"), \
                 patch("apps.mandanten.demo.provisioning.proxy_nachziehen_best_effort"), \
                 patch("apps.mandanten.demo.betrieb.admin_passwort_zuruecksetzen") as passwort:
                self.steuern, self.passwort, self.docker = steuern, passwort, docker
                demo.zuruecksetzen(m)
        return docker

    def _demo(self, kuerzel="zr1", **kw):
        return _m(kuerzel, demo_standard_am=timezone.now() - timedelta(days=1), admin_passwort="StandardPw", **kw)

    def test_ablauf_leert_datenbank_und_medien_und_spielt_den_standard_ein(self):
        m = self._demo()
        docker = self._lauf(m)
        self.assertEqual(docker.namen(), [
            "Demo-Standard in den Container kopieren", "Demo-Standard (Medien) in den Container kopieren", "Worker anhalten",
            "Datenbank leeren", "Medien leeren", "Demo-Standard einspielen"])
        self.assertIn("DROP SCHEMA public CASCADE", docker.befehle[3][0][-1])
        self.assertIn("find /data/media -mindepth 1 -delete", docker.befehle[4][0][-1])
        self.assertEqual(docker.befehle[-1][0][-3:], ["/app/scripts/restore.sh", "demo-standard-db.sql.gz",
                                                      "demo-standard-media.tar.gz"])
        self.assertEqual([c.args[1] for c in self.steuern.call_args_list], ["start", "start", "restart"])
        self.passwort.assert_called_once()
        self.assertEqual(self.passwort.call_args.kwargs, {"passwort": "StandardPw", "_pruefen": False})
        m.refresh_from_db()
        self.assertEqual((m.status, m.fortschritt), ("laeuft", 100))
        self.assertIn("Demo auf den Standard zurückgesetzt", m.protokoll)

    def test_ohne_medien_im_standard_werden_die_medien_nicht_angefasst(self):
        docker = self._lauf(self._demo("zr2"), mit_medien=False)
        self.assertNotIn("Medien leeren", docker.namen())
        self.assertEqual(docker.befehle[-1][0][-2:], ["/app/scripts/restore.sh", "demo-standard-db.sql.gz"])

    def test_fehler_vor_dem_leeren_aendert_nichts(self):
        m = self._demo("zr3", status="gestoppt")
        with self.assertRaises(BereitstellungsFehler):
            self._lauf(m, docker=_DemoDocker(fehler_bei="Worker anhalten"))
        self.assertNotIn("Datenbank leeren", self.docker.namen())
        m.refresh_from_db()
        self.assertEqual(m.status, "gestoppt")

    def test_fehler_nach_dem_leeren_markiert_fehlgeschlagen_und_der_standard_bleibt(self):
        m = self._demo("zr4")
        with self.assertRaises(BereitstellungsFehler):
            self._lauf(m, docker=_DemoDocker(fehler_bei="Demo-Standard einspielen"))
        m.refresh_from_db()
        self.assertEqual(m.status, "fehlgeschlagen")
        self.assertIn("nachdem die Daten entfernt wurden", m.protokoll)
        self.assertIsNotNone(m.demo_standard_am)

    def test_voraussetzungen(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp):
            with self.assertRaises(BereitstellungsFehler):
                demo.zuruecksetzen(_m("zr5"))                                              # kein Standard gespeichert
            with self.assertRaises(BereitstellungsFehler):
                demo.zuruecksetzen(self._demo("zr6", demo=False))                          # keine Demo
            with self.assertRaises(BereitstellungsFehler):
                demo.zuruecksetzen(self._demo("zr7", status="pausiert"))                   # falscher Status
            m = self._demo("zr8")                                                          # Zeitpunkt da, Datei fehlt
            with self.assertRaises(BereitstellungsFehler):
                demo.zuruecksetzen(m)

    def test_admin_passwort_erhaelt_den_gespeicherten_wert(self):
        from . import betrieb
        m = _m("zr9", admin_passwort="Fest12345")
        with patch("apps.mandanten.betrieb.provisioning._compose_basis", return_value=["docker"]), \
             patch("apps.mandanten.betrieb.basis.docker") as d:
            neu = betrieb.admin_passwort_zuruecksetzen(m, passwort="Fest12345", _pruefen=False)
        self.assertEqual(neu, "Fest12345")
        self.assertIn("Fest12345", d.call_args.kwargs["eingabe"])
        m.refresh_from_db()
        self.assertIn("auf den im Portal gespeicherten Wert gesetzt", m.protokoll)


class DemoAdminTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("dza", "d@example.org", "pw"))

    def _demo(self, kuerzel, **kw):
        return _m(kuerzel, demo_standard_am=timezone.now(), **kw)

    def _aktion(self, name, m):
        admin = site._registry[MandantInstanz]
        request = RequestFactory().post("/")
        request.user = get_user_model().objects.get(username="dza")
        request._messages = MagicMock()
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp), \
             patch("apps.mandanten.tasks.demo_zuruecksetzen_task.delay") as zr, \
             patch("apps.mandanten.tasks.demo_standard_task.delay") as st:
            if m.demo_standard_am:
                _ordner(tmp, m)
            getattr(admin, name)(request, MandantInstanz.objects.filter(pk=m.pk))
        return zr, st

    def test_zuruecksetzen_nur_mit_standard_demo_und_geeignetem_status(self):
        m = self._demo("da1")
        zr, _ = self._aktion("demo_zuruecksetzen", m)
        zr.assert_called_once_with(m.pk)
        m.refresh_from_db()
        self.assertEqual(status_vor_vormerkung(m), "laeuft")
        self.assertTrue(LogEintrag.objects.filter(text__contains="Demo-Zurücksetzen gestartet", benutzer="dza").exists())
        for kw in (dict(demo=False), dict(status="pausiert")):
            fremd = self._demo(f"db{len(kw)}{list(kw)[0][:2]}", **kw)
            self._aktion("demo_zuruecksetzen", fremd)[0].assert_not_called()
        ohne_standard = _m("da2")
        self._aktion("demo_zuruecksetzen", ohne_standard)[0].assert_not_called()

    def test_standard_speichern_nur_fuer_laufende_demo_instanzen(self):
        m = _m("da3")
        _, st = self._aktion("demo_standard_speichern", m)
        st.assert_called_once_with(m.pk)
        gestoppt = _m("da4", status="gestoppt")
        self._aktion("demo_standard_speichern", gestoppt)[1].assert_not_called()

    def test_zeilenknopf_nur_bei_laufender_demo_mit_standard_und_klick_startet_das_zuruecksetzen(self):
        m = self._demo("da5")
        _m("da6", demo_standard_am=None)                      # Demo ohne Standard
        _m("da7", demo=False)
        html = self.client.get("/admin/mandanten/mandantinstanz/").content.decode()
        self.assertEqual(html.count("pv-knopf pv-demo"), 1)
        self.assertIn(f"/{m.pk}/dienst/demo_zuruecksetzen/", html)
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp), \
             patch("apps.mandanten.tasks.demo_zuruecksetzen_task.delay") as delay:
            _ordner(tmp, m)
            self.assertEqual(self.client.post(f"/admin/mandanten/mandantinstanz/{m.pk}/dienst/demo_zuruecksetzen/").status_code, 302)
        delay.assert_called_once_with(m.pk)
        self.client.force_login(get_user_model().objects.create_user("ro4", is_staff=True))
        self.assertEqual(self.client.post(f"/admin/mandanten/mandantinstanz/{m.pk}/dienst/demo_zuruecksetzen/").status_code, 403)

    def test_befehl(self):
        _m("da8")
        with patch("apps.mandanten.demo.zuruecksetzen") as zr, patch("apps.mandanten.demo.standard_speichern_aktion") as st:
            out = io.StringIO()
            call_command("mandant_demo_zuruecksetzen", "da8", stdout=out)
            call_command("mandant_demo_zuruecksetzen", "da8", "--standard-speichern", stdout=out)
        zr.assert_called_once()
        st.assert_called_once()
        with self.assertRaises(CommandError):
            call_command("mandant_demo_zuruecksetzen", "gibt-es-nicht", stdout=io.StringIO())
        with patch("apps.mandanten.demo.zuruecksetzen", side_effect=BereitstellungsFehler("kein Standard")):
            with self.assertRaises(CommandError):
                call_command("mandant_demo_zuruecksetzen", "da8", stdout=io.StringIO())
