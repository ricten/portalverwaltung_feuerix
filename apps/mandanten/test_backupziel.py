"""Tests fuer Backup-Ziel (SMB-Freigabe, simuliert) und regelmaessigen Online-Check."""
import io
import ntpath
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from . import backupziel, betrieb, onlinecheck
from .basis import BereitstellungsFehler
from .models import BackupZiel, MandantInstanz


class FakeSMB:
    """Minimale In-Memory-Nachbildung von smbclient (Pfade im Windows-Stil)."""

    class _Eintrag:
        def __init__(self, name, groesse, mtime):
            self.name, self._groesse, self._mtime = name, groesse, mtime

        def is_file(self):
            return True

        def stat(self):
            return MagicMock(st_size=self._groesse, st_mtime=self._mtime)

    def __init__(self, fehler=None):
        self.dateien, self.ordner, self.sitzungen, self.fehler = {}, set(), [], fehler

    def register_session(self, server, username=None, password=None, **kw):
        if self.fehler:
            raise OSError(self.fehler)
        self.sitzungen.append((server, username, password))

    def makedirs(self, pfad, exist_ok=False):
        self.ordner.add(pfad)

    def rmdir(self, pfad):
        self.ordner.discard(pfad)

    def scandir(self, pfad):
        return [self._Eintrag(ntpath.basename(p), len(d), t) for p, (d, t) in self.dateien.items()
                if ntpath.dirname(p) == pfad]

    def stat(self, pfad):
        d, t = self.dateien[pfad]
        return MagicMock(st_size=len(d), st_mtime=t)

    def remove(self, pfad):
        del self.dateien[pfad]

    def open_file(self, pfad, mode="rb"):
        fake = self

        class Datei(io.BytesIO):
            def close(self_):
                if "w" in mode:
                    fake.dateien[pfad] = (self_.getvalue(), time.time())
                super().close()

        return Datei()


def _ziel(**kw):
    daten = dict(aktiv=True, server="nas.local", freigabe="backups", unterordner="feuerix/portal",
                 benutzer="DOM\\backup", passwort="geheim")
    daten.update(kw)
    ziel, _ = BackupZiel.objects.update_or_create(pk=1, defaults=daten)
    return ziel


def _m(kuerzel="b1", **kw):
    daten = dict(kuerzel=kuerzel, verein_name=kuerzel, domain=f"{kuerzel}.example.org", status="laeuft", web_port=8100)
    daten.update(kw)
    return MandantInstanz.objects.create(**daten)


class ModellTests(TestCase):
    def test_passwort_wird_verschluesselt_gespeichert(self):
        _ziel()
        with __import__("django.db", fromlist=["connection"]).connection.cursor() as c:
            c.execute("select passwort from mandanten_backupziel")
            roh = c.fetchone()[0]
        self.assertNotIn("geheim", roh)
        self.assertEqual(BackupZiel.holen().passwort, "geheim")

    def test_validierung(self):
        with self.assertRaises(ValidationError):
            BackupZiel(aktiv=True, server="nas.local", freigabe="x\\y", benutzer="u").full_clean()
        with self.assertRaises(ValidationError):
            BackupZiel(aktiv=True, server="nas local", freigabe="x", benutzer="u").full_clean()
        with self.assertRaises(ValidationError):
            BackupZiel(aktiv=True, server="nas", freigabe="x", benutzer="u", unterordner="../etc").full_clean()
        with self.assertRaises(ValidationError):
            BackupZiel(aktiv=True, server="", freigabe="x", benutzer="u").full_clean()
        BackupZiel(aktiv=False).full_clean()   # inaktiv und leer ist in Ordnung
        BackupZiel(aktiv=True, server="192.168.1.20", freigabe="backups", benutzer="u",
                   unterordner="a/b").full_clean()


class UploadTests(TestCase):
    def _lokal(self, tmp, dateien):
        verz = Path(tmp)
        for name, inhalt in dateien.items():
            (verz / name).write_bytes(inhalt)
        return verz

    def test_verbindungstest_schreibt_und_loescht(self):
        fake = FakeSMB()
        with patch("apps.mandanten.backupziel._smb", return_value=fake):
            text = backupziel.testen(_ziel())
        self.assertIn("erfolgreich", text)
        self.assertEqual(fake.sitzungen, [("nas.local", "DOM\\backup", "geheim")])
        self.assertEqual(fake.dateien, {})

    def test_verbindungsfehler_wird_gemeldet(self):
        with patch("apps.mandanten.backupziel._smb", return_value=FakeSMB(fehler="Zugriff verweigert")):
            with self.assertRaises(BereitstellungsFehler) as ctx:
                backupziel.testen(_ziel())
        self.assertIn("Zugriff verweigert", str(ctx.exception))

    def test_nur_neue_dateien_werden_hochgeladen_pfad_enthaelt_kuerzel(self):
        _ziel()
        m = _m()
        fake = FakeSMB()
        with tempfile.TemporaryDirectory() as tmp, patch("apps.mandanten.backupziel._smb", return_value=fake):
            verz = self._lokal(tmp, {"a.sql": b"aaa", "b.sql": b"bbbb"})
            self.assertEqual(backupziel.hochladen(m, verz), 2)
            self.assertEqual(backupziel.hochladen(m, verz), 0)          # nichts Neues
            (verz / "c.sql").write_bytes(b"c")
            self.assertEqual(backupziel.hochladen(m, verz), 1)
        self.assertIn("\\\\nas.local\\backups\\feuerix\\portal\\b1\\a.sql", fake.dateien)
        self.assertEqual(len(fake.dateien), 3)

    def test_lokale_kopie_wird_auf_wunsch_geloescht(self):
        _ziel(lokal_loeschen=True)
        fake = FakeSMB()
        with tempfile.TemporaryDirectory() as tmp, patch("apps.mandanten.backupziel._smb", return_value=fake):
            verz = self._lokal(tmp, {"a.sql": b"aaa"})
            backupziel.hochladen(_m(), verz)
            self.assertEqual(list(verz.iterdir()), [])
        self.assertEqual(len(fake.dateien), 1)

    def test_aufbewahrung_loescht_alte_sicherungen_nur_im_instanzordner(self):
        _ziel(aufbewahrung_tage=7)
        fake = FakeSMB()
        alt = time.time() - 10 * 86400
        fake.dateien["\\\\nas.local\\backups\\feuerix\\portal\\b1\\alt.sql"] = (b"x", alt)
        fake.dateien["\\\\nas.local\\backups\\feuerix\\portal\\andere\\alt.sql"] = (b"x", alt)
        with tempfile.TemporaryDirectory() as tmp, patch("apps.mandanten.backupziel._smb", return_value=fake):
            backupziel.hochladen(_m(), self._lokal(tmp, {"neu.sql": b"n"}))
        namen = sorted(fake.dateien)
        self.assertTrue(any(n.endswith("b1\\neu.sql") for n in namen))
        self.assertFalse(any(n.endswith("b1\\alt.sql") for n in namen))
        self.assertTrue(any(n.endswith("andere\\alt.sql") for n in namen))

    def test_ohne_aktives_ziel_passiert_nichts(self):
        _ziel(aktiv=False)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(backupziel.hochladen(_m(), Path(tmp)), 0)


class BackupIntegrationTests(TestCase):
    def _backup(self, fake):
        m = _m()
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp, FEUERIX_REPO_PFAD=tmp):
            (Path(tmp) / "docker-compose.yml").write_text("services: {}\n")
            (Path(tmp) / m.kuerzel).mkdir()
            (Path(tmp) / m.kuerzel / ".env").write_text("A=b\n")

            def docker(befehl, *a, **k):
                if "cp" in befehl:
                    ziel = Path(befehl[-1])
                    ziel.mkdir(parents=True, exist_ok=True)
                    (ziel / "db.sql").write_bytes(b"daten")
                return ""

            with patch("apps.mandanten.betrieb.basis.docker", side_effect=docker), \
                 patch("apps.mandanten.backupziel._smb", return_value=fake):
                try:
                    betrieb.backup(m)
                finally:
                    m.refresh_from_db()
        return m

    def test_backup_laedt_hoch_und_merkt_sich_den_zeitpunkt(self):
        _ziel()
        fake = FakeSMB()
        m = self._backup(fake)
        self.assertEqual(len(fake.dateien), 1)
        self.assertIsNotNone(m.sicherung_am)
        self.assertTrue(m.sicherung_aktuell)
        self.assertIn("Netzwerkfreigabe abgelegt", m.protokoll)

    def test_fehlgeschlagener_upload_macht_die_sicherung_nicht_aktuell(self):
        _ziel()
        with self.assertRaises(BereitstellungsFehler):
            self._backup(FakeSMB(fehler="Netz weg"))
        m = MandantInstanz.objects.get(kuerzel="b1")
        self.assertIsNone(m.sicherung_am)
        self.assertFalse(m.sicherung_aktuell)       # trotz lokaler Datei
        self.assertIn("Upload zur Netzwerkfreigabe schlug fehl", m.protokoll)

    def test_ohne_ziel_gilt_weiter_die_lokale_datei(self):
        m = _m()
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp):
            verz = Path(tmp) / "b1" / "backups"
            verz.mkdir(parents=True)
            datei = verz / "x.sql"
            datei.write_text("x")
            self.assertTrue(m.sicherung_aktuell)
            alt = time.time() - 72 * 3600
            os.utime(datei, (alt, alt))
            self.assertFalse(m.sicherung_aktuell)


class AdminTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("bz", "b@example.org", "pw"))

    def test_liste_fuehrt_zum_einzigen_formular(self):
        antwort = self.client.get("/admin/mandanten/backupziel/")
        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(self.client.get(antwort.url).status_code, 200)

    def test_speichern_setzt_verschluesseltes_passwort_und_behaelt_es_bei_leerem_feld(self):
        url = "/admin/mandanten/backupziel/1/change/"
        self.client.get("/admin/mandanten/backupziel/")
        daten = dict(aktiv="on", server="nas.local", freigabe="backups", unterordner="", benutzer="u",
                     passwort_neu="s3cret", aufbewahrung_tage="0")
        self.assertEqual(self.client.post(url, daten).status_code, 302)
        self.assertEqual(BackupZiel.holen().passwort, "s3cret")
        daten["passwort_neu"] = ""
        self.client.post(url, daten)
        self.assertEqual(BackupZiel.holen().passwort, "s3cret")
        self.assertNotContains(self.client.get(url), "s3cret")

    def test_aktiv_ohne_passwort_wird_abgelehnt(self):
        url = "/admin/mandanten/backupziel/1/change/"
        self.client.get("/admin/mandanten/backupziel/")
        antwort = self.client.post(url, dict(aktiv="on", server="nas", freigabe="b", benutzer="u", aufbewahrung_tage="0"))
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Ein Passwort ist nötig")

    def test_speichern_und_testen(self):
        url = "/admin/mandanten/backupziel/1/change/"
        self.client.get("/admin/mandanten/backupziel/")
        daten = dict(aktiv="on", server="nas.local", freigabe="backups", benutzer="u", passwort_neu="pw",
                     aufbewahrung_tage="0", _testen="1")
        with patch("apps.mandanten.backupziel._smb", return_value=FakeSMB()):
            antwort = self.client.post(url, daten, follow=True)
        self.assertContains(antwort, "erfolgreich")
        with patch("apps.mandanten.backupziel._smb", return_value=FakeSMB(fehler="falsches Passwort")):
            antwort = self.client.post(url, daten, follow=True)
        self.assertContains(antwort, "Test fehlgeschlagen")


class OnlineCheckTests(TestCase):
    def test_wechsel_online_offline_online_wird_protokolliert(self):
        m = _m()
        with patch("apps.mandanten.betrieb.gesundheit", return_value={"feuerix": False, "paperless": True}):
            online, ausgefallen = onlinecheck.instanz_pruefen(m)
        self.assertFalse(online)
        self.assertEqual(ausgefallen, ["feuerix"])
        m.refresh_from_db()
        self.assertFalse(m.online)
        self.assertIsNotNone(m.offline_seit)
        self.assertIn("Nicht erreichbar: feuerix", m.protokoll)
        seit = m.offline_seit
        with patch("apps.mandanten.betrieb.gesundheit", return_value={"feuerix": False}):
            onlinecheck.instanz_pruefen(m)       # weiter offline: kein neuer Eintrag, Zeit bleibt
        m.refresh_from_db()
        self.assertEqual(m.offline_seit, seit)
        self.assertEqual(m.protokoll.count("Nicht erreichbar"), 1)
        with patch("apps.mandanten.betrieb.gesundheit", return_value={"feuerix": True}):
            onlinecheck.instanz_pruefen(m)
        m.refresh_from_db()
        self.assertTrue(m.online)
        self.assertIsNone(m.offline_seit)
        self.assertIn("Wieder erreichbar", m.protokoll)

    def test_nicht_laufende_instanzen_haben_keinen_zustand(self):
        m = _m(status="gestoppt", online=False)
        with patch("apps.mandanten.betrieb.gesundheit"):
            onlinecheck.alle_pruefen()
        m.refresh_from_db()
        self.assertIsNone(m.online)

    def test_backupziel_check(self):
        _ziel()
        with patch("apps.mandanten.backupziel._smb", return_value=FakeSMB()):
            self.assertTrue(onlinecheck.backupziel_pruefen())
        with patch("apps.mandanten.backupziel._smb", return_value=FakeSMB(fehler="Host nicht erreichbar")):
            self.assertFalse(onlinecheck.backupziel_pruefen())
        z = BackupZiel.holen()
        self.assertFalse(z.online)
        self.assertIn("Host nicht erreichbar", z.online_fehler)
        _ziel(aktiv=False)
        self.assertIsNone(onlinecheck.backupziel_pruefen())

    def test_befehl_und_streng(self):
        _m()
        out = io.StringIO()
        with patch("apps.mandanten.betrieb.gesundheit", return_value={"feuerix": False}):
            call_command("mandant_online_check", stdout=out)
            with self.assertRaises(SystemExit):
                call_command("mandant_online_check", "--streng", stdout=out)
        self.assertIn("OFFLINE", out.getvalue())

    def test_ausfaelle_stehen_im_admin_hinweis(self):
        self.client.force_login(get_user_model().objects.create_superuser("oc", "o@example.org", "pw"))
        _m("aus", online=False, offline_seit=timezone.now(), online_geprueft_am=timezone.now())
        _ziel(online=False, online_fehler="Host nicht erreichbar", online_geprueft_am=timezone.now())
        html = self.client.get("/admin/").content.decode()
        self.assertIn("Nicht erreichbar:", html)
        self.assertIn("aus", html)
        self.assertIn("Backup-Ziel", html)
        _m("ok", online=True)
        MandantInstanz.objects.filter(kuerzel="aus").update(online=True)
        BackupZiel.objects.filter(pk=1).update(online=True)
        self.assertNotIn("Nicht erreichbar:", self.client.get("/admin/").content.decode())
