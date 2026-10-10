"""Tests: .env verschluesselt mit der Sicherung ablegen; haengende Instanzen und verwaiste Rollouts zuruecksetzen."""
import io
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from . import betrieb, endsicherung, fields
from .basis import BereitstellungsFehler, fortschritt
from .models import LogEintrag, MandantInstanz, UpdateZustand
from .test_backupziel import FakeSMB, _ziel

ROOT = "\\\\nas.local\\backups\\feuerix\\portal\\ev1"


def _m(kuerzel="ev1", **kw):
    daten = dict(kuerzel=kuerzel, verein_name="Verein EV", domain=f"{kuerzel}.example.org", status="laeuft",
                 web_port=8100)
    daten.update(kw)
    return MandantInstanz.objects.create(**daten)


def _vorbereiten(tmp, m, env="FIELD_ENCRYPTION_KEY=geheim\nSECRET_KEY=abc\n"):
    (Path(tmp) / "docker-compose.yml").write_text("services: {}\n")
    (Path(tmp) / m.kuerzel).mkdir(exist_ok=True)
    (Path(tmp) / m.kuerzel / ".env").write_bytes(env.encode())     # Bytes: unter Windows kein 



def _docker(befehl, *a, **k):
    if "cp" in befehl:
        Path(befehl[-1]).mkdir(parents=True, exist_ok=True)
        (Path(befehl[-1]) / "db-20261010-020000.sql.gz").write_text("db")
    return ""


def _sichern(m, tmp, fake=None):
    with override_settings(INSTANZEN_VERZEICHNIS=tmp, FEUERIX_REPO_PFAD=tmp), \
         patch("apps.mandanten.betrieb.basis.docker", side_effect=_docker), \
         patch("apps.mandanten.backupziel._smb", return_value=fake or FakeSMB()):
        return betrieb.backup(m)


class EnvMitsichernTests(TestCase):
    def test_env_liegt_verschluesselt_bei_der_sicherung_und_laesst_sich_entschluesseln(self):
        m = _m()
        with tempfile.TemporaryDirectory() as tmp:
            _vorbereiten(tmp, m)
            ordner = Path(_sichern(m, tmp))
            dateien = sorted(f.name for f in ordner.glob("env-*.enc"))
            self.assertEqual(len(dateien), 1)
            roh = (ordner / dateien[0]).read_bytes()
            self.assertNotIn(b"geheim", roh)                                # nicht im Klartext
            self.assertEqual(fields.entschluesseln(roh), b"FIELD_ENCRYPTION_KEY=geheim\nSECRET_KEY=abc\n")
        m.refresh_from_db()
        self.assertIn(".env verschlüsselt mitgesichert", m.protokoll)

    def test_gleicher_inhalt_keine_duplikate_geaenderter_inhalt_neue_datei(self):
        m = _m()
        with tempfile.TemporaryDirectory() as tmp:
            _vorbereiten(tmp, m)
            ordner = Path(_sichern(m, tmp))
            _sichern(m, tmp)
            self.assertEqual(len(list(ordner.glob("env-*.enc"))), 1)
            (Path(tmp) / "ev1" / ".env").write_text("FIELD_ENCRYPTION_KEY=neu\n")
            _sichern(m, tmp)
            self.assertEqual(len(list(ordner.glob("env-*.enc"))), 2)

    def test_env_wird_auf_die_netzwerkfreigabe_hochgeladen(self):
        _ziel()
        m = _m()
        fake = FakeSMB()
        with tempfile.TemporaryDirectory() as tmp:
            _vorbereiten(tmp, m)
            _sichern(m, tmp, fake)
        env_dateien = [n for n in fake.dateien if "\\env-" in n and n.endswith(".enc")]
        self.assertEqual(len(env_dateien), 1)
        self.assertTrue(env_dateien[0].startswith(ROOT + "\\env-"))
        self.assertNotIn(b"geheim", fake.dateien[env_dateien[0]][0])

    def test_fehlende_env_ist_kein_fehler(self):
        m = _m()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "docker-compose.yml").write_text("services: {}\n")
            (Path(tmp) / "ev1").mkdir()
            (Path(tmp) / "ev1" / ".env").write_text("x=1\n")
            ordner = Path(_sichern(m, tmp))
            (Path(tmp) / "ev1" / ".env").unlink()
            for f in ordner.glob("env-*"):
                f.unlink()
            betrieb._env_mitsichern(m, ordner)
            self.assertEqual(list(ordner.glob("env-*")), [])
        m.refresh_from_db()
        self.assertIn("Keine .env gefunden", m.protokoll)

    def test_letzte_sicherung_vor_dem_endgueltigen_loeschen_enthaelt_die_env_auf_der_freigabe(self):
        _ziel()
        m = _m()
        fake = FakeSMB()
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp, FEUERIX_REPO_PFAD=tmp):
            _vorbereiten(tmp, m)
            with patch("apps.mandanten.betrieb.basis.docker", side_effect=_docker), \
                 patch("apps.mandanten.backupziel._smb", return_value=fake), \
                 patch("apps.mandanten.endsicherung.provisioning.stack_steuern"), \
                 patch("apps.mandanten.endsicherung.provisioning.auf_bereitschaft_warten"):
                endsicherung.abschlusssicherung(m)
        self.assertTrue(any("\\env-" in n and n.endswith(".enc") for n in fake.dateien))
        self.assertTrue(any(n.endswith("db-20261010-020000.sql.gz") for n in fake.dateien))


class EntschluesselnBefehlTests(TestCase):
    def _datei(self, tmp, inhalt=b"A=1\nB=2\n"):
        datei = Path(tmp) / "env-abc.enc"
        datei.write_bytes(fields.verschluesseln(inhalt))
        return datei

    def test_ausgabe_nach_stdout_und_in_datei_ohne_ueberschreiben(self):
        with tempfile.TemporaryDirectory() as tmp:
            datei = self._datei(tmp)
            ziel = Path(tmp) / "wiederhergestellt.env"
            call_command("env_entschluesseln", str(datei), "--ausgabe", str(ziel), stdout=io.StringIO())
            self.assertEqual(ziel.read_bytes(), b"A=1\nB=2\n")
            with self.assertRaises(CommandError):
                call_command("env_entschluesseln", str(datei), "--ausgabe", str(ziel), stdout=io.StringIO())
            with patch("sys.stdout", new_callable=io.StringIO) as ausgabe:
                call_command("env_entschluesseln", str(datei))
            self.assertEqual(ausgabe.getvalue(), "A=1\nB=2\n")

    def test_falscher_schluessel_und_fehlende_datei(self):
        with tempfile.TemporaryDirectory() as tmp:
            datei = Path(tmp) / "kaputt.enc"
            datei.write_bytes(b"das ist nicht verschluesselt")
            with self.assertRaises(CommandError):
                call_command("env_entschluesseln", str(datei), stdout=io.StringIO())
            with self.assertRaises(CommandError):
                call_command("env_entschluesseln", str(Path(tmp) / "gibt-es-nicht"), stdout=io.StringIO())


def _celery(aktiv):
    app = MagicMock()
    app.control.inspect.return_value.active.return_value = aktiv
    return patch("config.celery.app", app)


class TatsaechlicherStatusTests(TestCase):
    def _status(self, m, laufend="", vorhanden="", feuerix_ok=True):
        antworten = {"running": laufend, "-a": vorhanden}

        def docker(befehl, *a, **k):
            return antworten["running"] if "running" in befehl else antworten["-a"]

        with patch("apps.mandanten.betrieb.provisioning._compose_basis", return_value=["docker", "compose"]), \
             patch("apps.mandanten.betrieb.basis.docker", side_effect=docker), \
             patch("apps.mandanten.betrieb.gesundheit", return_value={"feuerix": feuerix_ok}):
            return betrieb.tatsaechlicher_status(m)

    def test_alle_zustaende(self):
        self.assertEqual(self._status(_m(), laufend="abc\ndef\n", vorhanden="abc\ndef\n"), "laeuft")
        self.assertEqual(self._status(_m("ev2"), laufend="abc\n", vorhanden="abc\n", feuerix_ok=False), "fehlgeschlagen")
        self.assertEqual(self._status(_m("ev3"), vorhanden="abc\ndef\n"), "gestoppt")
        self.assertEqual(self._status(_m("ev4", feuerix_version="1.58.0")), "pausiert")
        self.assertEqual(self._status(_m("ev5")), "neu")

    def test_ohne_env_nie_bereitgestellt(self):
        with patch("apps.mandanten.betrieb.provisioning._compose_basis", side_effect=BereitstellungsFehler("keine .env")):
            self.assertEqual(betrieb.tatsaechlicher_status(_m()), "neu")


class HaengendeZuruecksetzenTests(TestCase):
    def _alt(self, m, minuten=30):
        MandantInstanz.objects.filter(pk=m.pk).update(aktualisiert_am=timezone.now() - timedelta(minutes=minuten),
                                                      status="wird_bereitgestellt", schritt="Update: bauen", fortschritt=40)
        m.refresh_from_db()

    def test_fortschritt_haelt_den_aktivitaetszeitpunkt_aktuell(self):
        m = _m()
        MandantInstanz.objects.filter(pk=m.pk).update(aktualisiert_am=timezone.now() - timedelta(hours=1))
        m.refresh_from_db()
        fortschritt(m, 50, "Schritt")
        m.refresh_from_db()
        self.assertLess(timezone.now() - m.aktualisiert_am, timedelta(minutes=1))

    def test_zuruecksetzen_ermittelt_den_echten_zustand_und_protokolliert(self):
        m = _m()
        self._alt(m)
        with _celery({"worker": []}), \
             patch("apps.mandanten.betrieb.tatsaechlicher_status", return_value="laeuft"), \
             patch("apps.mandanten.betrieb.provisioning.proxy_nachziehen_best_effort") as proxy:
            neu = betrieb.haengende_zuruecksetzen(m)
        self.assertEqual(neu, "laeuft")
        m.refresh_from_db()
        self.assertEqual((m.status, m.fortschritt, m.schritt), ("laeuft", 0, ""))
        self.assertIn("zurückgesetzt", m.protokoll)
        proxy.assert_called_once()

    def test_verweigert_wenn_nicht_in_arbeit_zu_frisch_oder_auftrag_aktiv(self):
        m = _m()
        with self.assertRaises(BereitstellungsFehler):
            betrieb.haengende_zuruecksetzen(m)                               # nicht "In Arbeit"
        self._alt(m, minuten=3)
        with self.assertRaises(BereitstellungsFehler) as ctx:
            betrieb.haengende_zuruecksetzen(m)
        self.assertIn("läuft vermutlich noch", str(ctx.exception))
        self._alt(m, minuten=30)
        with _celery({"worker": [{"name": "x", "args": [m.pk]}]}):
            with self.assertRaises(BereitstellungsFehler) as ctx:
                betrieb.haengende_zuruecksetzen(m)
        self.assertIn("noch einen Auftrag", str(ctx.exception))
        m.refresh_from_db()
        self.assertEqual(m.status, "wird_bereitgestellt")

    def test_aktiver_auftrag_erkennt_die_instanz_und_toleriert_fehlenden_worker(self):
        with _celery({"w1": [{"args": [7]}], "w2": [{"args": ["9", "x"]}]}):
            self.assertTrue(betrieb.aktiver_auftrag(7))
            self.assertTrue(betrieb.aktiver_auftrag(9))
            self.assertFalse(betrieb.aktiver_auftrag(8))
        with _celery(None):
            self.assertIsNone(betrieb.aktiver_auftrag(7))
        app = MagicMock()
        app.control.inspect.side_effect = OSError("Broker weg")
        with patch("config.celery.app", app):
            self.assertIsNone(betrieb.aktiver_auftrag(7))

    def test_admin_aktion_nur_fuer_superuser(self):
        m = _m()
        self._alt(m)
        admin = site._registry[MandantInstanz]
        request = RequestFactory().post("/")
        request._messages = MagicMock()
        request.user = get_user_model().objects.create_user("normal", is_staff=True)
        admin.haengende_zuruecksetzen(request, MandantInstanz.objects.filter(pk=m.pk))
        m.refresh_from_db()
        self.assertEqual(m.status, "wird_bereitgestellt")
        request.user = get_user_model().objects.create_superuser("chef", "c@example.org", "pw")
        with _celery({"w": []}), patch("apps.mandanten.betrieb.tatsaechlicher_status", return_value="gestoppt"), \
             patch("apps.mandanten.betrieb.provisioning.proxy_nachziehen_best_effort"):
            admin.haengende_zuruecksetzen(request, MandantInstanz.objects.filter(pk=m.pk))
        m.refresh_from_db()
        self.assertEqual(m.status, "gestoppt")
        self.assertTrue(LogEintrag.objects.filter(text__contains="Hängenden Vorgang zurückgesetzt", benutzer="chef").exists())


class RolloutSperreTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("rl", "r@example.org", "pw"))
        self.url = "/admin/mandanten/mandantinstanz/update/sperre-aufheben/"

    def _sperre(self, minuten):
        UpdateZustand.objects.update_or_create(pk=1, defaults=dict(
            rollout_laeuft=True, rollout_text="Instanz 1 von 2",
            rollout_gestartet_am=timezone.now() - timedelta(minutes=minuten)))

    def test_nur_bei_altem_rollout_wird_der_knopf_gezeigt(self):
        self._sperre(5)
        self.assertNotContains(self.client.get("/admin/"), "Sperre aufheben")
        self._sperre(30)
        self.assertContains(self.client.get("/admin/"), "Sperre aufheben")

    def test_sperre_wird_aufgehoben_wenn_kein_rollout_laeuft(self):
        self._sperre(30)
        with patch("apps.mandanten.betrieb.aktiver_rollout", return_value=False):
            self.assertEqual(self.client.post(self.url).status_code, 302)
        z = UpdateZustand.holen()
        self.assertFalse(z.rollout_laeuft)
        self.assertIn("Sperre von Hand aufgehoben", z.rollout_ergebnis)

    def test_sperre_bleibt_bei_aktivem_rollout_frischer_sperre_oder_ohne_recht(self):
        self._sperre(30)
        with patch("apps.mandanten.betrieb.aktiver_rollout", return_value=True):
            self.client.post(self.url)
        self.assertTrue(UpdateZustand.holen().rollout_laeuft)
        self._sperre(3)
        with patch("apps.mandanten.betrieb.aktiver_rollout", return_value=False):
            self.client.post(self.url)
        self.assertTrue(UpdateZustand.holen().rollout_laeuft)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.client.force_login(get_user_model().objects.create_user("ro3", is_staff=True))
        self.assertEqual(self.client.post(self.url).status_code, 403)

    def test_aktiver_rollout_erkennt_den_auftrag(self):
        with _celery({"w": [{"name": "apps.mandanten.tasks.update_ausrollen_task", "args": []}]}):
            self.assertTrue(betrieb.aktiver_rollout())
        with _celery({"w": [{"name": "apps.mandanten.tasks.mandant_backup_task", "args": [1]}]}):
            self.assertFalse(betrieb.aktiver_rollout())
        with _celery(None):
            self.assertIsNone(betrieb.aktiver_rollout())
