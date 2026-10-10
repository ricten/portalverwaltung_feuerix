"""Tests fuer die Wiederherstellung einer Instanz aus einer Sicherung (Docker gemockt, Freigabe simuliert)."""
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from . import backupziel, wiederherstellung
from .basis import BereitstellungsFehler, status_vor_vormerkung, vormerken
from .models import LogEintrag, MandantInstanz
from .test_backupziel import FakeSMB, _ziel

ROOT = "\\\\nas.local\\backups\\feuerix\\portal\\wh1"


def _m(kuerzel="wh1", **kw):
    daten = dict(kuerzel=kuerzel, verein_name="Verein WH", domain=f"{kuerzel}.example.org", status="laeuft",
                 web_port=8100)
    daten.update(kw)
    return MandantInstanz.objects.create(**daten)


def _lokal(tmp, kuerzel="wh1", **dateien):
    ordner = Path(tmp) / kuerzel / "backups"
    ordner.mkdir(parents=True, exist_ok=True)
    for name, inhalt in dateien.items():
        (ordner / name.replace("_", "-", 1).replace("_sql_gz", ".sql.gz").replace("_tar_gz", ".tar.gz")).write_text(inhalt)
    return ordner


def _sicherungen(ordner, *stamps, media=True):
    for stamp in stamps:
        (ordner / f"db-{stamp}.sql.gz").write_text("db")
        if media:
            (ordner / f"media-{stamp}.tar.gz").write_text("media")


class AngeboteTests(TestCase):
    def test_lokale_sicherungen_werden_gepaart_und_sortiert(self):
        m = _m()
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp):
            ordner = Path(tmp) / "wh1" / "backups"
            ordner.mkdir(parents=True)
            _sicherungen(ordner, "20261009-010000", "20261010-020000")
            (ordner / "db-20261008-000000.sql.gz").write_text("nur-db")
            (ordner / "media-20261007-000000.tar.gz").write_text("ohne-db")     # ohne Datenbank: kein Angebot
            (ordner / "unbekannt.txt").write_text("x")
            liste, fehler = wiederherstellung.angebote(m)
        self.assertEqual([a["stamp"] for a in liste], ["20261010-020000", "20261009-010000", "20261008-000000"])
        self.assertEqual((liste[0]["ort_db"], liste[0]["ort_media"]), ("lokal", "lokal"))
        self.assertIsNone(liste[2]["media"])
        self.assertEqual(fehler, "")

    def test_freigabe_wird_ergaenzt_lokal_hat_vorrang(self):
        _ziel()
        m = _m()
        fake = FakeSMB()
        fake.dateien[ROOT + "\\db-20261010-020000.sql.gz"] = (b"remote-db", time.time())
        fake.dateien[ROOT + "\\media-20261010-020000.tar.gz"] = (b"remote-media", time.time())
        fake.dateien[ROOT + "\\db-20261001-000000.sql.gz"] = (b"alt", time.time())
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp), \
             patch("apps.mandanten.backupziel._smb", return_value=fake):
            ordner = Path(tmp) / "wh1" / "backups"
            ordner.mkdir(parents=True)
            (ordner / "db-20261010-020000.sql.gz").write_text("lokal")
            liste, _ = wiederherstellung.angebote(m)
        eintraege = {a["stamp"]: a for a in liste}
        self.assertEqual((eintraege["20261010-020000"]["ort_db"], eintraege["20261010-020000"]["ort_media"]),
                         ("lokal", "freigabe"))
        self.assertEqual(eintraege["20261001-000000"]["ort_db"], "freigabe")

    def test_nicht_erreichbare_freigabe_liefert_lokale_sicherungen_und_den_fehler(self):
        _ziel()
        m = _m()
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp), \
             patch("apps.mandanten.backupziel._smb", return_value=FakeSMB(fehler="Host weg")):
            _sicherungen(_lokal(tmp), "20261010-020000")
            liste, fehler = wiederherstellung.angebote(m)
        self.assertEqual(len(liste), 1)
        self.assertIn("Host weg", fehler)

    def test_herunterladen_prueft_den_dateinamen(self):
        _ziel()
        m = _m()
        with tempfile.TemporaryDirectory() as tmp:
            for schlecht in ("..\\x.gz", "../x", "a/b.gz", ""):
                with self.assertRaises(BereitstellungsFehler):
                    backupziel.herunterladen(m, schlecht, Path(tmp))


class _Docker:
    def __init__(self, fehler_bei=None):
        self.befehle, self.fehler_bei = [], fehler_bei

    def __call__(self, befehl, was, **kw):
        self.befehle.append((befehl, was))
        if self.fehler_bei and self.fehler_bei in was:
            raise BereitstellungsFehler(f"{was} fehlgeschlagen:\nkaputt")
        return ""

    def reihenfolge(self):
        return [was for _, was in self.befehle]


class WiederherstellenTests(TestCase):
    def _lauf(self, m, stamp="20261010-020000", docker=None, vorher_backup=None, **kw):
        docker = docker or _Docker()
        mit = tempfile.TemporaryDirectory()
        self.addCleanup(mit.cleanup)
        with override_settings(INSTANZEN_VERZEICHNIS=mit.name), \
             patch("apps.mandanten.wiederherstellung.basis.docker", side_effect=docker), \
             patch("apps.mandanten.wiederherstellung.provisioning._compose_basis", return_value=["docker", "compose"]), \
             patch("apps.mandanten.wiederherstellung.provisioning.stack_steuern") as steuern, \
             patch("apps.mandanten.wiederherstellung.provisioning.auf_bereitschaft_warten"), \
             patch("apps.mandanten.wiederherstellung.provisioning.proxy_nachziehen_best_effort"), \
             patch("apps.mandanten.wiederherstellung.betrieb.backup", side_effect=vorher_backup) as backup:
            _sicherungen(_lokal(mit.name), stamp, "20261009-010000")
            try:
                wiederherstellung.wiederherstellen(m, stamp, **kw)
            finally:
                self.steuern, self.backup, self.docker = steuern, backup, docker
        return docker

    def test_ablauf_in_der_richtigen_reihenfolge(self):
        m = _m()
        docker = self._lauf(m)
        self.assertEqual(docker.reihenfolge(), [
            "Sicherung db-20261010-020000.sql.gz in den Container kopieren",
            "Sicherung media-20261010-020000.tar.gz in den Container kopieren",
            "Worker anhalten", "Datenbank leeren", "Sicherung zurückspielen"])
        self.backup.assert_called_once()                     # Stand davor gesichert
        restore = docker.befehle[-1][0]
        self.assertEqual(restore[-3:], ["/app/scripts/restore.sh", "db-20261010-020000.sql.gz", "media-20261010-020000.tar.gz"])
        self.assertIn("DROP SCHEMA public CASCADE", docker.befehle[3][0][-1])
        self.assertEqual([c.args[1] for c in self.steuern.call_args_list], ["start", "start", "restart"])
        m.refresh_from_db()
        self.assertEqual((m.status, m.fortschritt), ("laeuft", 100))
        self.assertIn("Wiederherstellung aus Sicherung 20261010-020000 abgeschlossen", m.protokoll)

    def test_ohne_medien_und_ohne_vorherige_sicherung(self):
        docker = self._lauf(_m(), mit_medien=False, vorher_sichern=False)
        self.backup.assert_not_called()
        self.assertEqual(docker.befehle[-1][0][-2:], ["/app/scripts/restore.sh", "db-20261010-020000.sql.gz"])
        self.assertEqual(len([w for w in docker.reihenfolge() if "kopieren" in w]), 1)

    def test_fehlgeschlagene_vorsicherung_bricht_ab_bevor_etwas_veraendert_wird(self):
        m = _m(status="gestoppt")
        with self.assertRaises(BereitstellungsFehler):
            self._lauf(m, vorher_backup=BereitstellungsFehler("Platte voll"))
        self.assertEqual(self.docker.befehle, [])             # nichts kopiert, nichts geleert
        m.refresh_from_db()
        self.assertEqual(m.status, "gestoppt")                # unveraenderter Ausgangsstatus

    def test_fehler_vor_dem_leeren_laesst_den_status(self):
        m = _m()
        with self.assertRaises(BereitstellungsFehler):
            self._lauf(m, docker=_Docker(fehler_bei="Worker anhalten"))
        m.refresh_from_db()
        self.assertEqual(m.status, "laeuft")

    def test_fehler_nach_dem_leeren_markiert_fehlgeschlagen_mit_hinweis(self):
        m = _m()
        with self.assertRaises(BereitstellungsFehler):
            self._lauf(m, docker=_Docker(fehler_bei="zurückspielen"))
        m.refresh_from_db()
        self.assertEqual(m.status, "fehlgeschlagen")
        self.assertIn("nachdem die Datenbank geleert wurde", m.protokoll)
        self.assertIn("Wiederherstellung fehlgeschlagen bei", m.schritt)

    def test_unbekannte_sicherung_und_falscher_status(self):
        m = _m()
        with self.assertRaises(BereitstellungsFehler):
            wiederherstellung.wiederherstellen(m, "20200101-000000")
        with self.assertRaises(BereitstellungsFehler):
            wiederherstellung.wiederherstellen(_m("wh2", status="pausiert"), "20261010-020000")

    def test_sicherung_von_der_freigabe_wird_heruntergeladen_und_in_den_container_kopiert(self):
        _ziel()
        m = _m()
        fake = FakeSMB()
        fake.dateien[ROOT + "\\db-20261011-030000.sql.gz"] = (b"db-von-der-freigabe", time.time())
        docker = _Docker()
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp), \
             patch("apps.mandanten.backupziel._smb", return_value=fake), \
             patch("apps.mandanten.wiederherstellung.basis.docker", side_effect=docker), \
             patch("apps.mandanten.wiederherstellung.provisioning._compose_basis", return_value=["docker", "compose"]), \
             patch("apps.mandanten.wiederherstellung.provisioning.stack_steuern"), \
             patch("apps.mandanten.wiederherstellung.provisioning.auf_bereitschaft_warten"), \
             patch("apps.mandanten.wiederherstellung.betrieb.backup"):
            wiederherstellung.wiederherstellen(m, "20261011-030000", vorher_sichern=False)
            heruntergeladen = Path(tmp) / "wh1" / "backups" / "db-20261011-030000.sql.gz"
            self.assertEqual(heruntergeladen.read_bytes(), b"db-von-der-freigabe")
        self.assertTrue(any("kopieren" in w for w in docker.reihenfolge()))


class AdminTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("rs", "r@example.org", "pw"))

    def _url(self, m):
        return f"/admin/mandanten/mandantinstanz/{m.pk}/wiederherstellen/"

    def _mit_sicherungen(self, tmp):
        _sicherungen(_lokal(tmp), "20261010-020000", "20261009-010000")

    def test_seite_listet_sicherungen_mit_warnung(self):
        m = _m()
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp):
            self._mit_sicherungen(tmp)
            antwort = self.client.get(self._url(m))
        self.assertContains(antwort, "10.10.2026 02:00:00")
        self.assertContains(antwort, "09.10.2026 01:00:00")
        self.assertContains(antwort, "Die aktuelle Datenbank der Instanz wird ersetzt")
        self.assertContains(antwort, "Vorher den jetzigen Stand sichern")

    def test_ohne_sicherungen_gibt_es_einen_hinweis(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp):
            antwort = self.client.get(self._url(_m()))
        self.assertContains(antwort, "keine Sicherungen gefunden")

    def test_bestaetigung_startet_den_auftrag_mit_den_optionen(self):
        m = _m(status="gestoppt")
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp), \
             patch("apps.mandanten.tasks.mandant_wiederherstellen_task.delay") as delay:
            self._mit_sicherungen(tmp)
            antwort = self.client.post(self._url(m), {"stamp": "20261009-010000", "kuerzel": "wh1", "vorher_sichern": "on"})
        self.assertEqual(antwort.status_code, 302)
        delay.assert_called_once_with(m.pk, "20261009-010000", False, True)     # ohne mit_medien-Haken
        m.refresh_from_db()
        self.assertEqual(m.status, "wird_bereitgestellt")
        self.assertEqual(status_vor_vormerkung(m), "gestoppt")
        self.assertTrue(LogEintrag.objects.filter(text__contains="Wiederherstellung aus Sicherung 20261009-010000",
                                                  benutzer="rs").exists())

    def test_falsches_kuerzel_oder_unbekannte_sicherung_startet_nichts(self):
        m = _m()
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp), \
             patch("apps.mandanten.tasks.mandant_wiederherstellen_task.delay") as delay:
            self._mit_sicherungen(tmp)
            a1 = self.client.post(self._url(m), {"stamp": "20261010-020000", "kuerzel": "falsch"})
            a2 = self.client.post(self._url(m), {"stamp": "../../etc/passwd", "kuerzel": "wh1"})
        delay.assert_not_called()
        self.assertContains(a1, "Kürzel stimmt nicht")
        self.assertContains(a2, "eine der angezeigten Sicherungen")
        m.refresh_from_db()
        self.assertEqual(m.status, "laeuft")

    def test_nur_superuser_und_geeigneter_status(self):
        m = _m()
        self.client.force_login(get_user_model().objects.create_user("st2", is_staff=True))
        self.assertEqual(self.client.get(self._url(m)).status_code, 403)
        self.client.force_login(get_user_model().objects.get(username="rs"))
        MandantInstanz.objects.filter(pk=m.pk).update(status="pausiert")
        self.assertContains(self.client.get(self._url(m), follow=True), "Wiederherstellen nicht möglich")

    def test_aktion_fuehrt_zur_seite_und_verlangt_genau_eine_instanz(self):
        from django.contrib.admin.sites import site
        from django.test import RequestFactory
        from unittest.mock import MagicMock
        a, b = _m("a2"), _m("b2")
        admin = site._registry[MandantInstanz]
        request = RequestFactory().post("/")
        request.user = get_user_model().objects.get(username="rs")
        request._messages = MagicMock()
        self.assertEqual(admin.wiederherstellen(request, MandantInstanz.objects.filter(pk=a.pk)).url, self._url(a))
        self.assertIsNone(admin.wiederherstellen(request, MandantInstanz.objects.all()))
