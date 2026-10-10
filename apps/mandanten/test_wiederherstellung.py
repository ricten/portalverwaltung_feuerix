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
        self.assertContains(antwort, "Die aktuellen Daten des gewählten Dienstes werden ersetzt")
        self.assertContains(antwort, "Vorher den jetzigen Stand sichern")

    def test_ohne_sicherungen_gibt_es_einen_hinweis(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp):
            antwort = self.client.get(self._url(_m()))
        self.assertContains(antwort, "Keine Feuerix-Sicherungen gefunden")

    def test_bestaetigung_startet_den_auftrag_mit_den_optionen(self):
        m = _m(status="gestoppt")
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp), \
             patch("apps.mandanten.tasks.mandant_wiederherstellen_task.delay") as delay:
            self._mit_sicherungen(tmp)
            antwort = self.client.post(self._url(m), {"stamp": "20261009-010000", "kuerzel": "wh1", "vorher_sichern": "on"})
        self.assertEqual(antwort.status_code, 302)
        delay.assert_called_once_with(m.pk, "20261009-010000", False, True, "feuerix")     # ohne mit_medien-Haken
        m.refresh_from_db()
        self.assertEqual(m.status, "wird_bereitgestellt")
        self.assertEqual(status_vor_vormerkung(m), "gestoppt")
        self.assertTrue(LogEintrag.objects.filter(text__contains="Wiederherstellung (feuerix) aus 20261009-010000",
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


# ----------------------------------------------------------------------------------------------- OpenSlides
OS_ROOT = "\\\\nas.local\\backups\\feuerix\\portal\\wh1\\openslides"


def _os_m(kuerzel="wh1", **kw):
    return _m(kuerzel, openslides_domain=f"o.{kuerzel}.example.org", openslides_port=9100, **kw)


class OpenSlidesAngeboteTests(TestCase):
    def test_dumps_lokal_und_freigabe_neueste_zuerst(self):
        _ziel()
        m = _os_m()
        fake = FakeSMB()
        fake.dateien[OS_ROOT + "\\openslides-4.3.5-20261011-030000.sql"] = (b"remote", time.time())
        fake.dateien[OS_ROOT + "\\kaputt.sql"] = (b"x", time.time())
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp), \
             patch("apps.mandanten.backupziel._smb", return_value=fake):
            ordner = Path(tmp) / "wh1" / "openslides" / "backups"
            ordner.mkdir(parents=True)
            (ordner / "openslides-4.3.0-20261009-010000.sql").write_text("lokal")
            (ordner / "openslides-4.3.5-20261011-030000.sql").write_text("lokal-gleich")
            liste, fehler = wiederherstellung.angebote_openslides(m)
        self.assertEqual([(e["version"], e["stamp"], e["ort"]) for e in liste],
                         [("4.3.5", "20261011-030000", "lokal"), ("4.3.0", "20261009-010000", "lokal")])
        self.assertEqual(fehler, "")


class _OsDocker:
    def __init__(self, fehler_bei=None, pg_bereit_nach=0):
        self.befehle, self.fehler_bei, self.pg_aufrufe, self.pg_bereit_nach = [], fehler_bei, 0, pg_bereit_nach

    def __call__(self, befehl, was, **kw):
        self.befehle.append((befehl, was, kw))
        if "pg_isready" in befehl:
            self.pg_aufrufe += 1
            if self.pg_aufrufe <= self.pg_bereit_nach:
                raise BereitstellungsFehler("noch nicht bereit")
        if self.fehler_bei and self.fehler_bei in was:
            raise BereitstellungsFehler(f"{was} fehlgeschlagen:\nkaputt")
        return ""

    def namen(self):
        return [was for _, was, _ in self.befehle]


class OpenSlidesWiederherstellenTests(TestCase):
    def _lauf(self, m, docker=None, name="openslides-4.3.0-20261009-010000.sql", **kw):
        docker = docker or _OsDocker()
        mit = tempfile.TemporaryDirectory()
        self.addCleanup(mit.cleanup)
        with override_settings(INSTANZEN_VERZEICHNIS=mit.name), \
             patch("apps.mandanten.wiederherstellung.basis.docker", side_effect=docker), \
             patch("apps.mandanten.wiederherstellung.openslides.steuern") as steuern, \
             patch("apps.mandanten.wiederherstellung.openslides.auf_bereitschaft_warten"), \
             patch("apps.mandanten.wiederherstellung.dienstupdate.openslides_dump") as dump, \
             patch("apps.mandanten.wiederherstellung.dienstversionen.openslides_konfiguriert", return_value="4.3.0"), \
             patch("apps.mandanten.wiederherstellung.time.sleep"):
            ordner = Path(mit.name) / m.kuerzel / "openslides" / "backups"
            ordner.mkdir(parents=True)
            (ordner / "openslides-4.3.0-20261009-010000.sql").write_text("CREATE TABLE x();\n")
            self.steuern, self.dump, self.docker = steuern, dump, docker
            wiederherstellung.wiederherstellen_openslides(m, name, **kw)
        return docker

    def test_ablauf_nach_der_offiziellen_anleitung(self):
        m = _os_m()
        docker = self._lauf(m)
        self.assertEqual(docker.namen(), [
            "OpenSlides: herunterfahren", "OpenSlides: Datenbank starten", "OpenSlides: Datenbank prüfen",
            "OpenSlides: Datenbank leeren", "OpenSlides: Dump einspielen", "OpenSlides: starten"])
        self.dump.assert_called_once()                      # Stand davor gesichert
        leeren = docker.befehle[3][0]
        self.assertIn("DROP SCHEMA", leeren[-1])
        self.assertIn("CREATE SCHEMA public", leeren[-1])
        einspielen = docker.befehle[4]
        self.assertEqual(einspielen[2]["eingabe"], "CREATE TABLE x();\n")        # der Dump geht ueber stdin
        self.assertIn("ON_ERROR_STOP=1", einspielen[0])
        m.refresh_from_db()
        self.assertEqual((m.status, m.fortschritt), ("laeuft", 100))

    def test_wartet_auf_die_datenbank(self):
        docker = self._lauf(_os_m(), docker=_OsDocker(pg_bereit_nach=3), vorher_sichern=False)
        self.assertEqual(docker.pg_aufrufe, 4)
        self.dump.assert_not_called()

    def test_fehler_vor_dem_leeren_aendert_nichts_und_stellt_den_status_her(self):
        m = _os_m(status="gestoppt")
        with self.assertRaises(BereitstellungsFehler):
            self._lauf(m, docker=_OsDocker(fehler_bei="Datenbank starten"))
        self.assertNotIn("OpenSlides: Datenbank leeren", self.docker.namen())
        m.refresh_from_db()
        self.assertEqual(m.status, "gestoppt")

    def test_fehler_nach_dem_leeren_ist_deutlich_und_laesst_feuerix_laufen(self):
        m = _os_m()
        with self.assertRaises(BereitstellungsFehler):
            self._lauf(m, docker=_OsDocker(fehler_bei="Dump einspielen"))
        m.refresh_from_db()
        self.assertEqual(m.status, "laeuft")                  # Feuerix selbst ist nicht betroffen
        self.assertIn("nachdem die Datenbank geleert wurde", m.protokoll)
        self.assertIn("OpenSlides-Wiederherstellung fehlgeschlagen bei", m.schritt)

    def test_leerer_dump_wird_abgelehnt_bevor_etwas_passiert(self):
        m = _os_m()
        docker = _OsDocker()
        mit = tempfile.TemporaryDirectory()
        self.addCleanup(mit.cleanup)
        with override_settings(INSTANZEN_VERZEICHNIS=mit.name), \
             patch("apps.mandanten.wiederherstellung.basis.docker", side_effect=docker), \
             patch("apps.mandanten.wiederherstellung.openslides.steuern"), \
             patch("apps.mandanten.wiederherstellung.openslides.auf_bereitschaft_warten"), \
             patch("apps.mandanten.wiederherstellung.dienstupdate.openslides_dump"):
            ordner = Path(mit.name) / "wh1" / "openslides" / "backups"
            ordner.mkdir(parents=True)
            (ordner / "openslides-4.3.0-20261009-010000.sql").write_text("  \n")
            with self.assertRaises(BereitstellungsFehler):
                wiederherstellung.wiederherstellen_openslides(m, "openslides-4.3.0-20261009-010000.sql")
        self.assertEqual(docker.befehle, [])

    def test_dump_von_der_freigabe_wird_heruntergeladen(self):
        _ziel()
        m = _os_m()
        fake = FakeSMB()
        fake.dateien[OS_ROOT + "\\openslides-4.3.5-20261011-030000.sql"] = (b"CREATE TABLE y();", time.time())
        docker = _OsDocker()
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp), \
             patch("apps.mandanten.backupziel._smb", return_value=fake), \
             patch("apps.mandanten.wiederherstellung.basis.docker", side_effect=docker), \
             patch("apps.mandanten.wiederherstellung.openslides.steuern"), \
             patch("apps.mandanten.wiederherstellung.openslides.auf_bereitschaft_warten"), \
             patch("apps.mandanten.wiederherstellung.time.sleep"):
            wiederherstellung.wiederherstellen_openslides(m, "openslides-4.3.5-20261011-030000.sql", vorher_sichern=False)
        einspielen = [b for b in docker.befehle if b[1] == "OpenSlides: Dump einspielen"][0]
        self.assertEqual(einspielen[2]["eingabe"], "CREATE TABLE y();")

    def test_nicht_eingerichtet_unbekannt_und_falscher_status(self):
        with self.assertRaises(BereitstellungsFehler):
            wiederherstellung.wiederherstellen_openslides(_m(), "openslides-4.3.0-20261009-010000.sql")
        with self.assertRaises(BereitstellungsFehler):
            wiederherstellung.wiederherstellen_openslides(_os_m("wh2"), "openslides-0.0.0-20000101-000000.sql")
        with self.assertRaises(BereitstellungsFehler):
            wiederherstellung.wiederherstellen_openslides(_os_m("wh3", status="pausiert"), "x.sql")


# ------------------------------------------------------------------------------------------------ Paperless
def _pl_m(kuerzel="wh1", **kw):
    return _m(kuerzel, paperless_domain=f"p.{kuerzel}.example.org", paperless_port=10100, **kw)


class PaperlessAngeboteTests(TestCase):
    def test_letzter_export_und_vorher_exporte(self):
        m = _pl_m()
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp):
            export = Path(tmp) / "wh1" / "paperless" / "export"
            (export / "_vorher-20261010-120000").mkdir(parents=True)
            (export / "_vorher-20261010-120000" / "manifest.json").write_text("[]")
            (export / "_vorher-20261001-120000").mkdir(parents=True)          # ohne manifest: kein Angebot
            (export / "manifest.json").write_text("[]")
            liste = wiederherstellung.angebote_paperless(m)
        self.assertEqual(sorted(e["name"] for e in liste), [".", "_vorher-20261010-120000"])

    def test_ohne_export_keine_angebote(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp):
            self.assertEqual(wiederherstellung.angebote_paperless(_pl_m()), [])


class PaperlessWiederherstellenTests(TestCase):
    def _lauf(self, m, docker=None, name=".", **kw):
        docker = docker or _Docker()
        mit = tempfile.TemporaryDirectory()
        self.addCleanup(mit.cleanup)
        with override_settings(INSTANZEN_VERZEICHNIS=mit.name), \
             patch("apps.mandanten.wiederherstellung.basis.docker", side_effect=docker), \
             patch("apps.mandanten.wiederherstellung.paperless._compose_basis",
                   return_value=(["docker", "compose", "-p", "x"], Path(mit.name))), \
             patch("apps.mandanten.wiederherstellung.paperless.steuern"), \
             patch("apps.mandanten.wiederherstellung.paperless.auf_bereitschaft_warten"), \
             patch("apps.mandanten.wiederherstellung.paperless.token_holen", return_value="tok") as token, \
             patch("apps.mandanten.wiederherstellung.paperless.in_feuerix_eintragen") as eintragen, \
             patch("apps.mandanten.wiederherstellung.provisioning._compose_basis", return_value=["docker"]), \
             patch("apps.mandanten.wiederherstellung.dienstversionen.erfassen_sicher"):
            export = Path(mit.name) / "wh1" / "paperless" / "export"
            export.mkdir(parents=True)
            (export / "manifest.json").write_text("[]")
            (export / "_vorher-20261001-120000").mkdir()
            (export / "_vorher-20261001-120000" / "manifest.json").write_text("[]")
            self.docker, self.token, self.eintragen = docker, token, eintragen
            wiederherstellung.wiederherstellen_paperless(m, name, **kw)
        return docker

    def test_ablauf_mit_einmaligem_container_statt_normalem_start(self):
        m = _pl_m()
        docker = self._lauf(m)
        self.assertEqual(docker.reihenfolge(), [
            "Paperless: Export vor der Wiederherstellung", "Paperless: anhalten", "Paperless: Datenbank starten",
            "Paperless: Datenbank leeren", "Paperless: Export importieren", "Paperless: starten"])
        export = docker.befehle[0][0]
        self.assertEqual(export[-2], "document_exporter")
        self.assertTrue(export[-1].startswith("../export/_vorher-"))      # nie in den Ordner, der wiederhergestellt wird
        leeren = docker.befehle[3][0]
        self.assertEqual(leeren[-2:][0], "-c")
        self.assertIn("DROP SCHEMA", leeren[-1])
        importieren = docker.befehle[4][0]
        self.assertIn("run", importieren)
        self.assertIn("--no-deps", importieren)
        self.assertEqual(importieren[importieren.index("--entrypoint") + 1], "")          # kein normaler Start (kein Admin)
        self.assertEqual(importieren[importieren.index("--user") + 1], "paperless")
        self.assertIn("manage.py migrate", importieren[-1])
        self.assertIn("document_importer ../export --no-progress-bar", importieren[-1])
        self.token.assert_called_once()
        self.eintragen.assert_called_once()
        m.refresh_from_db()
        self.assertEqual((m.status, m.fortschritt), ("laeuft", 100))

    def test_vorher_export_wird_als_quelle_unterstuetzt(self):
        docker = self._lauf(_pl_m(), name="_vorher-20261001-120000", vorher_sichern=False)
        importieren = docker.befehle[3][0]            # ohne Vorab-Export ist der Import der vierte Befehl
        self.assertIn("document_importer ../export/_vorher-20261001-120000", importieren[-1])
        self.assertNotIn("Paperless: Export vor der Wiederherstellung", docker.reihenfolge())

    def test_fehler_vor_dem_leeren_aendert_nichts(self):
        m = _pl_m(status="gestoppt")
        with self.assertRaises(BereitstellungsFehler):
            self._lauf(m, docker=_Docker(fehler_bei="Export vor der Wiederherstellung"))
        self.assertEqual(self.docker.reihenfolge(), ["Paperless: Export vor der Wiederherstellung"])
        m.refresh_from_db()
        self.assertEqual(m.status, "gestoppt")

    def test_fehler_im_importer_meldet_klar_wo_der_alte_stand_liegt(self):
        m = _pl_m()
        with self.assertRaises(BereitstellungsFehler):
            self._lauf(m, docker=_Docker(fehler_bei="Export importieren"))
        m.refresh_from_db()
        self.assertEqual(m.status, "laeuft")
        self.assertIn("nachdem die Datenbank geleert wurde", m.protokoll)
        self.assertIn("_vorher-", m.protokoll)

    def test_token_fehler_ist_nur_ein_hinweis(self):
        m = _pl_m()
        mit = tempfile.TemporaryDirectory()
        self.addCleanup(mit.cleanup)
        with override_settings(INSTANZEN_VERZEICHNIS=mit.name), \
             patch("apps.mandanten.wiederherstellung.basis.docker", side_effect=_Docker()), \
             patch("apps.mandanten.wiederherstellung.paperless._compose_basis", return_value=(["docker"], Path(mit.name))), \
             patch("apps.mandanten.wiederherstellung.paperless.steuern"), \
             patch("apps.mandanten.wiederherstellung.paperless.auf_bereitschaft_warten"), \
             patch("apps.mandanten.wiederherstellung.paperless.token_holen", side_effect=BereitstellungsFehler("falsches Passwort")), \
             patch("apps.mandanten.wiederherstellung.dienstversionen.erfassen_sicher"):
            export = Path(mit.name) / "wh1" / "paperless" / "export"
            export.mkdir(parents=True)
            (export / "manifest.json").write_text("[]")
            wiederherstellung.wiederherstellen_paperless(m, ".", vorher_sichern=False)
        m.refresh_from_db()
        self.assertEqual(m.status, "laeuft")
        self.assertIn("Anbindung in Feuerix konnte nicht erneuert werden", m.protokoll)

    def test_ohne_export_nicht_eingerichtet_und_falscher_status(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp):
            with self.assertRaises(BereitstellungsFehler):
                wiederherstellung.wiederherstellen_paperless(_pl_m(), ".")
            with self.assertRaises(BereitstellungsFehler):
                wiederherstellung.wiederherstellen_paperless(_m("wh2"), ".")
            with self.assertRaises(BereitstellungsFehler):
                wiederherstellung.wiederherstellen_paperless(_pl_m("wh3", status="pausiert"), ".")


# --------------------------------------------------------------------------------------------- Aufgabe und Admin
class DienstAuswahlTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("ds", "d@example.org", "pw"))

    def _url(self, m):
        return f"/admin/mandanten/mandantinstanz/{m.pk}/wiederherstellen/"

    def test_task_leitet_an_den_richtigen_dienst(self):
        from . import tasks
        m = _m()
        with patch("apps.mandanten.wiederherstellung.wiederherstellen_openslides") as os_, \
             patch("apps.mandanten.wiederherstellung.wiederherstellen_paperless") as pl, \
             patch("apps.mandanten.wiederherstellung.wiederherstellen") as fx:
            tasks.mandant_wiederherstellen_task(m.pk, "a.sql", True, False, "openslides")
            tasks.mandant_wiederherstellen_task(m.pk, ".", True, True, "paperless")
            tasks.mandant_wiederherstellen_task(m.pk, "20261010-020000", False, True)
        os_.assert_called_once_with(m, "a.sql", False)
        pl.assert_called_once_with(m, ".", True)
        fx.assert_called_once_with(m, "20261010-020000", False, True)

    def test_seite_zeigt_nur_eingerichtete_dienste_und_markiert_paperless_als_experimentell(self):
        m = _pl_m()
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp):
            (Path(tmp) / "wh1" / "paperless" / "export").mkdir(parents=True)
            (Path(tmp) / "wh1" / "paperless" / "export" / "manifest.json").write_text("[]")
            html = self.client.get(self._url(m)).content.decode()
        self.assertIn("Paperless-ngx (Export)", html)
        self.assertIn("experimentell", html)
        self.assertNotIn("OpenSlides (Datenbank-Dump)", html)

    def test_post_fuer_openslides_und_paperless_startet_den_passenden_auftrag(self):
        m = _os_m(paperless_domain="p.wh1.example.org", paperless_port=10100)
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp), \
             patch("apps.mandanten.tasks.mandant_wiederherstellen_task.delay") as delay:
            ordner = Path(tmp) / "wh1" / "openslides" / "backups"
            ordner.mkdir(parents=True)
            (ordner / "openslides-4.3.0-20261009-010000.sql").write_text("x")
            export = Path(tmp) / "wh1" / "paperless" / "export"
            export.mkdir(parents=True)
            (export / "manifest.json").write_text("[]")
            self.client.post(self._url(m), {"dienst": "openslides", "stamp": "openslides-4.3.0-20261009-010000.sql",
                                            "kuerzel": "wh1", "vorher_sichern": "on"})
            delay.assert_called_once_with(m.pk, "openslides-4.3.0-20261009-010000.sql", False, True, "openslides")
            MandantInstanz.objects.filter(pk=m.pk).update(status="laeuft", schritt="")
            delay.reset_mock()
            self.client.post(self._url(m), {"dienst": "paperless", "stamp": ".", "kuerzel": "wh1"})
            delay.assert_called_once_with(m.pk, ".", False, False, "paperless")

    def test_auswahl_muss_zum_dienst_passen(self):
        m = _os_m()
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp), \
             patch("apps.mandanten.tasks.mandant_wiederherstellen_task.delay") as delay:
            _sicherungen(_lokal(tmp), "20261010-020000")                       # nur Feuerix-Sicherungen vorhanden
            for dienst, auswahl in (("openslides", "20261010-020000"), ("paperless", "."), ("unbekannt", "x"),
                                    ("openslides", "../../etc/passwd")):
                self.client.post(self._url(m), {"dienst": dienst, "stamp": auswahl, "kuerzel": "wh1"})
        delay.assert_not_called()
