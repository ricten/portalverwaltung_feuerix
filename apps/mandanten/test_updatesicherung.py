"""Tests: Jedes Instanz-Update sichert auch Paperless und OpenSlides (Docker gemockt)."""
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import PropertyMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from . import betrieb, dienstupdate, endsicherung
from .basis import BereitstellungsFehler
from .models import MandantInstanz


def _m(kuerzel="us1", **kw):
    daten = dict(kuerzel=kuerzel, verein_name="Verein US", domain=f"{kuerzel}.example.org", status="laeuft",
                 web_port=8100, paperless_domain=f"p.{kuerzel}.example.org", paperless_port=10100,
                 openslides_domain=f"o.{kuerzel}.example.org", openslides_port=9100,
                 sicherung_am=timezone.now())
    daten.update(kw)
    return MandantInstanz.objects.create(**daten)


class _Dienste:
    """Ersetzt die echten Sicherungen/Updates und merkt sich die Reihenfolge."""

    def __init__(self, test, vorhanden=True, feuerix_aktuell=True, **fehler):
        self.ereignisse = []
        self.fehler = fehler
        pfade = [
            patch.object(MandantInstanz, "sicherung_aktuell", new_callable=PropertyMock, return_value=feuerix_aktuell),
            patch("apps.mandanten.endsicherung._paperless_vorhanden", return_value=vorhanden),
            patch("apps.mandanten.endsicherung._openslides_vorhanden", return_value=vorhanden),
            patch("apps.mandanten.endsicherung.dienstupdate.paperless_export", side_effect=self._export),
            patch("apps.mandanten.endsicherung.dienstupdate.openslides_dump", side_effect=self._dump),
            patch("apps.mandanten.betrieb.backup", side_effect=lambda *a, **k: self.ereignisse.append("feuerix")),
            patch("apps.mandanten.betrieb.update_ausrollen", side_effect=lambda *a, **k: self.ereignisse.append("update")),
        ]
        for p in pfade:
            p.start()
            test.addCleanup(p.stop)

    def _export(self, mandant, *a, **k):
        if "paperless" in self.fehler:
            raise BereitstellungsFehler("Export kaputt")
        self.ereignisse.append("paperless")
        mandant.paperless_sicherung_am = timezone.now()
        mandant.save(update_fields=["paperless_sicherung_am"])

    def _dump(self, mandant, *a, **k):
        if "openslides" in self.fehler:
            raise BereitstellungsFehler("Dump kaputt")
        self.ereignisse.append("openslides")
        mandant.openslides_sicherung_am = timezone.now()
        mandant.save(update_fields=["openslides_sicherung_am"])


class UpdateSicherungTests(TestCase):
    def test_veraltete_zusatzsicherungen_werden_vor_dem_update_angelegt(self):
        m = _m()
        dienste = _Dienste(self)
        with patch("apps.mandanten.endsicherung.dienstversionen.openslides_konfiguriert", return_value="4.3.5"):
            betrieb.update_mit_sicherung(m)
        self.assertEqual(dienste.ereignisse, ["paperless", "openslides", "update"])   # Feuerix war aktuell

    def test_aktuelle_zusatzsicherungen_werden_nicht_wiederholt(self):
        m = _m(paperless_sicherung_am=timezone.now() - timedelta(hours=2),
               openslides_sicherung_am=timezone.now() - timedelta(hours=2))
        dienste = _Dienste(self)
        betrieb.update_mit_sicherung(m)
        self.assertEqual(dienste.ereignisse, ["update"])

    def test_nach_24_stunden_wird_neu_gesichert(self):
        m = _m(paperless_sicherung_am=timezone.now() - timedelta(hours=30),
               openslides_sicherung_am=timezone.now() - timedelta(hours=2))
        dienste = _Dienste(self)
        betrieb.update_mit_sicherung(m)
        self.assertEqual(dienste.ereignisse, ["paperless", "update"])

    def test_feuerix_zuerst_dann_paperless_dann_openslides_dann_update(self):
        m = _m()
        dienste = _Dienste(self, feuerix_aktuell=False)
        with patch("apps.mandanten.endsicherung.dienstversionen.openslides_konfiguriert", return_value="4.3.5"):
            betrieb.update_mit_sicherung(m)
        self.assertEqual(dienste.ereignisse, ["feuerix", "paperless", "openslides", "update"])

    def test_fehlgeschlagene_zusatzsicherung_bricht_das_update_ab(self):
        m = _m()
        dienste = _Dienste(self, paperless=True)
        with self.assertRaises(BereitstellungsFehler) as ctx:
            betrieb.update_mit_sicherung(m)
        self.assertIn("Sicherung der Zusatzdienste", str(ctx.exception))
        self.assertNotIn("update", dienste.ereignisse)
        m.refresh_from_db()
        self.assertEqual(m.status, "laeuft")
        self.assertIn("Update abgebrochen", m.protokoll)

    def test_instanz_ohne_zusatzdienste_wird_wie_bisher_aktualisiert(self):
        m = _m(paperless_domain="", paperless_port=None, openslides_domain="", openslides_port=None)
        dienste = _Dienste(self, vorhanden=False)
        betrieb.update_mit_sicherung(m)
        self.assertEqual(dienste.ereignisse, ["update"])

    def test_gestoppte_instanz_braucht_keine_sicherung(self):
        m = _m(status="gestoppt")
        dienste = _Dienste(self, feuerix_aktuell=False)
        with patch("apps.mandanten.betrieb.provisioning.stack_bauen"), \
             patch("apps.mandanten.betrieb.provisioning.feuerix_version", return_value="1.59.0"):
            betrieb.update_mit_sicherung(m)
        self.assertEqual(dienste.ereignisse, [])


class ZeitpunktTests(TestCase):
    def test_export_und_dump_setzen_den_zeitpunkt_nach_dem_upload(self):
        m = _m(paperless_sicherung_am=None, openslides_sicherung_am=None)
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp), \
             patch("apps.mandanten.dienstupdate.paperless._compose_basis", return_value=(["docker"], Path(tmp))), \
             patch("apps.mandanten.dienstupdate.paperless._verzeichnis", return_value=Path(tmp) / "us1" / "paperless"), \
             patch("apps.mandanten.dienstupdate.basis.docker", return_value="-- dump --\n"):
            (Path(tmp) / "us1" / "openslides").mkdir(parents=True)
            dienstupdate.paperless_export(m)
            dienstupdate.openslides_dump(m, "4.3.5")
        m.refresh_from_db()
        self.assertIsNotNone(m.paperless_sicherung_am)
        self.assertIsNotNone(m.openslides_sicherung_am)

    def test_bei_fehlgeschlagenem_upload_bleibt_der_zeitpunkt_leer(self):
        m = _m(paperless_sicherung_am=None, openslides_sicherung_am=None)
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp), \
             patch("apps.mandanten.dienstupdate.basis.docker", return_value="-- dump --\n"), \
             patch("apps.mandanten.dienstupdate.backupziel.aktuell", return_value=True), \
             patch("apps.mandanten.dienstupdate.backupziel.hochladen", side_effect=BereitstellungsFehler("Netz weg")):
            (Path(tmp) / "us1" / "openslides").mkdir(parents=True)
            with self.assertRaises(BereitstellungsFehler):
                dienstupdate.openslides_dump(m, "4.3.5")
        m.refresh_from_db()
        self.assertIsNone(m.openslides_sicherung_am)

    @patch.object(MandantInstanz, "sicherung_aktuell", new_callable=PropertyMock, return_value=True)
    def test_eigenschaften_der_aktualitaet(self, _):
        jetzt = timezone.now()
        voll = _m(paperless_sicherung_am=jetzt, openslides_sicherung_am=jetzt)
        self.assertTrue(voll.zusatz_sicherungen_aktuell)
        self.assertTrue(voll.alle_sicherungen_aktuell)
        halb = _m("us2", paperless_sicherung_am=jetzt, openslides_sicherung_am=None)
        self.assertFalse(halb.zusatz_sicherungen_aktuell)
        self.assertFalse(halb.alle_sicherungen_aktuell)
        ohne = _m("us3", paperless_domain="", paperless_port=None, openslides_domain="", openslides_port=None)
        self.assertTrue(ohne.zusatz_sicherungen_aktuell)
        veraltet = _m("us4", paperless_sicherung_am=jetzt - timedelta(hours=30), openslides_sicherung_am=jetzt)
        self.assertFalse(veraltet.zusatz_sicherungen_aktuell)
