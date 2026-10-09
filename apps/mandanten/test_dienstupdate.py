"""Tests fuer das Update von Paperless-ngx und OpenSlides aus dem Portal (Docker gemockt)."""
import re
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from . import dienstupdate
from .basis import WARTESCHLANGE_DIENST, BereitstellungsFehler
from .models import MandantInstanz, UpdateZustand

COMPOSE = """services:
  backend:
    image: ghcr.io/openslides/openslides/openslides-backend:4.2.0
  client:
    image: ghcr.io/openslides/openslides/openslides-client:4.2.0
  postgres:
    image: postgres:15
"""
CONFIG = "port: 9100\ndefaults:\n  containerRegistry: ghcr.io/openslides/openslides\n  tag: 4.2.0   # fest\n"


def _m(kuerzel="du1", **kw):
    daten = dict(kuerzel=kuerzel, verein_name="Verein DU", domain=f"{kuerzel}.example.org", status="laeuft",
                 web_port=8100, paperless_domain=f"p.{kuerzel}.example.org", paperless_port=10100,
                 paperless_version="2.14.0", openslides_domain=f"o.{kuerzel}.example.org", openslides_port=9100,
                 openslides_version="4.2.0")
    daten.update(kw)
    return MandantInstanz.objects.create(**daten)


def _zustand(**kw):
    daten = dict(paperless_neueste="2.15.0", openslides_neueste="4.2.1")
    daten.update(kw)
    return UpdateZustand.objects.update_or_create(pk=1, defaults=daten)[0]


class _Umgebung:
    """Temporaerer Instanzordner mit OpenSlides-Dateien und aufgezeichneten Docker-Aufrufen."""

    def __init__(self, test, dump="-- dump --\nCREATE TABLE x();\n", fehler_bei=None):
        self.befehle, self.dump, self.fehler_bei = [], dump, fehler_bei
        self.tmp = tempfile.TemporaryDirectory()
        test.addCleanup(self.tmp.cleanup)
        self.pfad = Path(self.tmp.name)
        verz = self.pfad / "du1" / "openslides"
        verz.mkdir(parents=True)
        (verz / "docker-compose.yml").write_text(COMPOSE)
        (verz / "config.yml").write_text(CONFIG)
        self.os_verz = verz

    def docker(self, befehl, was, timeout=900, cwd=None, eingabe=None, env=None):
        self.befehle.append((befehl, was))
        if self.fehler_bei and self.fehler_bei in was:
            raise BereitstellungsFehler(f"{was} fehlgeschlagen:\nkaputt")
        if len(befehl) > 1 and befehl[1] == "config":
            # simuliert `osmanage config --force`: Compose-Datei mit dem Tag der config.yml neu erzeugen
            tag = re.search(r"tag:\s*(\S+)", (self.os_verz / "config.yml").read_text()).group(1)
            (self.os_verz / "docker-compose.yml").write_text(re.sub(r"(openslides[^\s]*:)\d+\.\d+\.\d+",
                                                                   rf"\g<1>{tag}", COMPOSE))
        return self.dump if "pg_dump" in befehl else ""

    def patchen(self):
        return [
            override_settings(INSTANZEN_VERZEICHNIS=str(self.pfad)),
            patch("apps.mandanten.dienstupdate.basis.docker", side_effect=self.docker),
            patch("apps.mandanten.dienstupdate.paperless._compose_basis",
                  return_value=(["docker", "compose", "-p", "x"], self.pfad)),
            patch("apps.mandanten.dienstupdate.paperless._verzeichnis", return_value=self.pfad / "du1" / "paperless"),
            patch("apps.mandanten.dienstupdate.paperless.auf_bereitschaft_warten"),
            patch("apps.mandanten.dienstupdate.openslides.auf_bereitschaft_warten"),
            patch("apps.mandanten.dienstupdate.openslides.werkzeuge_bereitstellen",
                  return_value=(Path("osmanage"), Path("vorlage.tmpl"))),
            patch("apps.mandanten.dienstupdate.dienstversionen.paperless_laufend", return_value="2.15.0"),
        ]


def _mit(umgebung, funktion, *args):
    from contextlib import ExitStack
    with ExitStack() as stack:
        for p in umgebung.patchen():
            stack.enter_context(p)
        return funktion(*args)


class PaperlessUpdateTests(TestCase):
    def test_export_pull_up_in_der_reihenfolge_und_neue_version(self):
        m, _ = _m(), _zustand()
        env = _Umgebung(self)
        _mit(env, dienstupdate.dienst_aktualisieren, m, "paperless")
        reihenfolge = [("exec" if "exec" in b else "pull" if "pull" in b else "up") for b, _ in env.befehle]
        self.assertEqual(reihenfolge, ["exec", "pull", "up"])
        self.assertIn("document_exporter", env.befehle[0][0])
        m.refresh_from_db()
        self.assertEqual((m.status, m.paperless_version, m.fortschritt), ("laeuft", "2.15.0", 100))
        self.assertIn("Update von Paperless-ngx abgeschlossen", m.protokoll)

    def test_fehlgeschlagener_export_aendert_nichts(self):
        m, _ = _m(), _zustand()
        env = _Umgebung(self, fehler_bei="Export")
        with self.assertRaises(BereitstellungsFehler):
            _mit(env, dienstupdate.dienst_aktualisieren, m, "paperless")
        self.assertEqual(len(env.befehle), 1)                      # weder pull noch up
        m.refresh_from_db()
        self.assertEqual((m.status, m.paperless_version), ("laeuft", "2.14.0"))
        self.assertIn("Update von Paperless-ngx fehlgeschlagen bei", m.schritt)


class OpenSlidesUpdateTests(TestCase):
    def test_dump_dann_config_neu_dann_pull_up(self):
        m, _ = _m(), _zustand()
        env = _Umgebung(self)
        _mit(env, dienstupdate.dienst_aktualisieren, m, "openslides")
        beschreibungen = [was for _, was in env.befehle]
        self.assertEqual(beschreibungen, ["OpenSlides: Datenbank-Dump vor dem Update",
                                          "OpenSlides: Compose-Datei neu erzeugen",
                                          "OpenSlides: docker compose pull", "OpenSlides: docker compose up"])
        self.assertIn("--force", env.befehle[1][0])
        compose = (env.os_verz / "docker-compose.yml").read_text()
        self.assertEqual(compose.count(":4.2.1"), 2)
        self.assertIn("postgres:15", compose)                       # Fremd-Images bleiben unberuehrt
        self.assertIn("tag: 4.2.1", (env.os_verz / "config.yml").read_text())
        dumps = list((env.os_verz / "backups").glob("openslides-4.2.0-*.sql"))
        self.assertEqual(len(dumps), 1)
        self.assertIn("CREATE TABLE", dumps[0].read_text())
        m.refresh_from_db()
        self.assertEqual((m.status, m.openslides_version), ("laeuft", "4.2.1"))

    def test_leerer_dump_bricht_ab_ohne_aenderung(self):
        m, _ = _m(), _zustand()
        env = _Umgebung(self, dump="   ")
        with self.assertRaises(BereitstellungsFehler):
            _mit(env, dienstupdate.dienst_aktualisieren, m, "openslides")
        self.assertEqual((env.os_verz / "docker-compose.yml").read_text(), COMPOSE)
        self.assertEqual(len(env.befehle), 1)
        m.refresh_from_db()
        self.assertEqual((m.status, m.openslides_version), ("laeuft", "4.2.0"))

    def test_fehler_beim_pull_stellt_die_dateien_wieder_her(self):
        m, _ = _m(), _zustand()
        env = _Umgebung(self, fehler_bei="pull")
        with self.assertRaises(BereitstellungsFehler):
            _mit(env, dienstupdate.dienst_aktualisieren, m, "openslides")
        self.assertEqual((env.os_verz / "docker-compose.yml").read_text(), COMPOSE)
        self.assertEqual((env.os_verz / "config.yml").read_text(), CONFIG)
        m.refresh_from_db()
        self.assertEqual(m.openslides_version, "4.2.0")

    def test_nur_patch_updates_minor_und_major_sprung_werden_abgelehnt(self):
        m = _m()
        for neueste in ("4.3.0", "5.0.0"):
            _zustand(openslides_neueste=neueste)
            env = _Umgebung(self)
            with self.assertRaises(BereitstellungsFehler) as ctx:
                _mit(env, dienstupdate.dienst_aktualisieren, m, "openslides")
            self.assertIn("kein Patch-Update", str(ctx.exception))
            self.assertEqual(env.befehle, [])           # nicht einmal ein Dump

    def test_fehlt_die_neue_version_in_der_compose_datei_wird_zurueckgesetzt(self):
        m, _ = _m(), _zustand()
        env = _Umgebung(self)
        with patch("apps.mandanten.dienstupdate.dienstversionen.openslides_konfiguriert", side_effect=["4.2.0", "4.2.0"]):
            with self.assertRaises(BereitstellungsFehler):
                _mit(env, dienstupdate.dienst_aktualisieren, m, "openslides")
        self.assertEqual((env.os_verz / "config.yml").read_text(), CONFIG)
        self.assertEqual((env.os_verz / "docker-compose.yml").read_text(), COMPOSE)


class VoraussetzungenTests(TestCase):
    def test_nur_laufende_instanz_ohne_rollout_mit_bekannter_version(self):
        _zustand()
        with self.assertRaises(BereitstellungsFehler):
            dienstupdate.dienst_aktualisieren(_m(status="gestoppt"), "paperless")
        with self.assertRaises(BereitstellungsFehler):
            dienstupdate.dienst_aktualisieren(_m("du2", paperless_domain=""), "paperless")
        _zustand(rollout_laeuft=True)
        with self.assertRaises(BereitstellungsFehler):
            dienstupdate.dienst_aktualisieren(_m("du3"), "paperless")
        _zustand(rollout_laeuft=False, paperless_neueste="")
        with self.assertRaises(BereitstellungsFehler):
            dienstupdate.dienst_aktualisieren(_m("du4"), "paperless")
        with self.assertRaises(BereitstellungsFehler):
            dienstupdate.dienst_aktualisieren(_m("du5"), "unbekannt")

    def test_vorgemerkte_instanz_wird_angenommen(self):
        _zustand()
        m = _m(status="wird_bereitgestellt", schritt=WARTESCHLANGE_DIENST)
        env = _Umgebung(self)
        _mit(env, dienstupdate.dienst_aktualisieren, m, "paperless")
        m.refresh_from_db()
        self.assertEqual(m.status, "laeuft")


class OberflaecheTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("dup", "d@example.org", "pw"))

    def test_knopf_nur_bei_veralteter_version_und_laufender_instanz(self):
        _zustand(openslides_neueste="4.2.0")                       # OpenSlides aktuell, Paperless veraltet
        m = _m()
        html = self.client.get("/admin/mandanten/mandantinstanz/").content.decode()
        self.assertIn(f"/{m.pk}/dienst/paperless_update/", html)
        self.assertNotIn("openslides_update", html)
        MandantInstanz.objects.filter(pk=m.pk).update(status="gestoppt")
        self.assertNotIn("paperless_update", self.client.get("/admin/mandanten/mandantinstanz/").content.decode())

    def test_klick_merkt_vor_und_startet_task(self):
        _zustand()
        m = _m()
        with patch("apps.mandanten.tasks.dienst_update_task.delay") as delay:
            antwort = self.client.post(f"/admin/mandanten/mandantinstanz/{m.pk}/dienst/openslides_update/")
        self.assertEqual(antwort.status_code, 302)
        delay.assert_called_once_with(m.pk, "openslides")
        m.refresh_from_db()
        self.assertEqual((m.status, m.schritt), ("wird_bereitgestellt", WARTESCHLANGE_DIENST))

    def test_klick_ohne_bekannte_version_oder_waehrend_rollout_tut_nichts(self):
        m = _m()
        _zustand(paperless_neueste="")
        with patch("apps.mandanten.tasks.dienst_update_task.delay") as delay:
            self.client.post(f"/admin/mandanten/mandantinstanz/{m.pk}/dienst/paperless_update/")
            _zustand(rollout_laeuft=True)
            self.client.post(f"/admin/mandanten/mandantinstanz/{m.pk}/dienst/paperless_update/")
        delay.assert_not_called()

    def test_nur_per_post_und_mit_recht(self):
        _zustand()
        m = _m()
        url = f"/admin/mandanten/mandantinstanz/{m.pk}/dienst/paperless_update/"
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.force_login(get_user_model().objects.create_user("ro2", is_staff=True))
        self.assertEqual(self.client.post(url).status_code, 403)
