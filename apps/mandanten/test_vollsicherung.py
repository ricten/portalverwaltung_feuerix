"""Tests: Die Aktion "Sichern" sichert Feuerix UND die eingerichteten Zusatzdienste."""
import io
from unittest.mock import patch

from django.core import mail
from django.core.mail import get_connection
from django.core.management import call_command
from django.test import TestCase

from . import betrieb, tasks
from .basis import BereitstellungsFehler
from .models import Benachrichtigung, MandantInstanz


def _m(kuerzel="vs1", **kw):
    daten = dict(kuerzel=kuerzel, verein_name="Verein VS", domain=f"{kuerzel}.example.org", status="laeuft",
                 web_port=8100, paperless_domain=f"p.{kuerzel}.example.org", paperless_port=10100,
                 openslides_domain=f"o.{kuerzel}.example.org", openslides_port=9100)
    daten.update(kw)
    return MandantInstanz.objects.create(**daten)


class _Dienste:
    def __init__(self, test, paperless=True, openslides=True, fehler=()):
        self.ereignisse, self.fehler = [], set(fehler)
        for p in (
            patch("apps.mandanten.endsicherung._paperless_vorhanden", return_value=paperless),
            patch("apps.mandanten.endsicherung._openslides_vorhanden", return_value=openslides),
            patch("apps.mandanten.betrieb._backup", side_effect=self._feuerix),
            patch("apps.mandanten.dienstupdate.paperless_export", side_effect=self._export),
            patch("apps.mandanten.dienstupdate.openslides_dump", side_effect=self._dump),
            patch("apps.mandanten.dienstversionen.openslides_konfiguriert", return_value="4.3.5"),
        ):
            p.start()
            test.addCleanup(p.stop)

    def _feuerix(self, m):
        if "feuerix" in self.fehler:
            raise BereitstellungsFehler("Platte voll")
        self.ereignisse.append("feuerix")
        return "/pfad/backups"

    def _export(self, m, *a, **k):
        if "paperless" in self.fehler:
            raise BereitstellungsFehler("Export kaputt\nDetails")
        self.ereignisse.append("paperless")

    def _dump(self, m, *a, **k):
        if "openslides" in self.fehler:
            raise BereitstellungsFehler("Dump kaputt")
        self.ereignisse.append("openslides")


class VollsicherungTests(TestCase):
    def test_feuerix_paperless_und_openslides_in_dieser_reihenfolge(self):
        dienste = _Dienste(self)
        self.assertEqual(betrieb.vollsicherung(_m()), "/pfad/backups")
        self.assertEqual(dienste.ereignisse, ["feuerix", "paperless", "openslides"])

    def test_instanz_ohne_zusatzdienste_sichert_nur_feuerix(self):
        dienste = _Dienste(self, paperless=False, openslides=False)
        betrieb.vollsicherung(_m(paperless_domain="", paperless_port=None, openslides_domain="", openslides_port=None))
        self.assertEqual(dienste.ereignisse, ["feuerix"])

    def test_immer_frisch_auch_wenn_die_letzte_sicherung_jung_ist(self):
        from datetime import timedelta

        from django.utils import timezone
        m = _m(paperless_sicherung_am=timezone.now() - timedelta(minutes=5),
               openslides_sicherung_am=timezone.now() - timedelta(minutes=5))
        dienste = _Dienste(self)
        betrieb.vollsicherung(m)
        self.assertEqual(dienste.ereignisse, ["feuerix", "paperless", "openslides"])

    def test_fehler_bei_paperless_verhindert_openslides_nicht_und_wird_gemeldet(self):
        m = _m()
        dienste = _Dienste(self, fehler={"paperless"})
        with self.assertRaises(BereitstellungsFehler) as ctx:
            betrieb.vollsicherung(m)
        self.assertEqual(dienste.ereignisse, ["feuerix", "openslides"])
        self.assertIn("Feuerix gesichert, aber: Paperless: Export kaputt", str(ctx.exception))
        self.assertNotIn("Details", str(ctx.exception))
        m.refresh_from_db()
        self.assertIn("Sicherung der Zusatzdienste unvollständig", m.protokoll)

    def test_beide_zusatzdienste_fehlerhaft_werden_zusammen_gemeldet(self):
        _Dienste(self, fehler={"paperless", "openslides"})
        with self.assertRaises(BereitstellungsFehler) as ctx:
            betrieb.vollsicherung(_m())
        self.assertIn("Paperless:", str(ctx.exception))
        self.assertIn("OpenSlides:", str(ctx.exception))

    def test_fehler_bei_feuerix_bricht_ab_ohne_zusatzdienste(self):
        dienste = _Dienste(self, fehler={"feuerix"})
        with self.assertRaises(BereitstellungsFehler):
            betrieb.vollsicherung(_m())
        self.assertEqual(dienste.ereignisse, [])

    def test_nicht_laufende_instanz_wird_nicht_gesichert(self):
        _Dienste(self)
        with self.assertRaises(BereitstellungsFehler):
            betrieb.vollsicherung(_m(status="gestoppt"))

    def test_mail_bei_fehlgeschlagener_zusatzsicherung(self):
        Benachrichtigung.objects.update_or_create(pk=1, defaults=dict(
            aktiv=True, empfaenger="a@example.org", absender="p@example.org", smtp_host="h"))
        _Dienste(self, fehler={"openslides"})
        with patch("apps.mandanten.benachrichtigung._verbindung",
                   side_effect=lambda c: get_connection("django.core.mail.backends.locmem.EmailBackend")):
            with self.assertRaises(BereitstellungsFehler):
                betrieb.vollsicherung(_m())
        betreffe = [n.subject for n in mail.outbox]
        self.assertTrue(any("Sicherung der Zusatzdienste von vs1 fehlgeschlagen" in b for b in betreffe))


class EinstiegspunkteTests(TestCase):
    def test_task_der_admin_aktion_nutzt_die_vollsicherung(self):
        m = _m()
        with patch("apps.mandanten.betrieb.vollsicherung") as voll, patch("apps.mandanten.betrieb.backup") as nur:
            tasks.mandant_backup_task(m.pk)
        voll.assert_called_once()
        nur.assert_not_called()

    def test_befehl_nutzt_die_vollsicherung(self):
        _m()
        out = io.StringIO()
        with patch("apps.mandanten.betrieb.vollsicherung", return_value="/ziel") as voll:
            call_command("mandant_backup", "vs1", stdout=out)
        voll.assert_called_once()
        self.assertIn("gesichert nach /ziel", out.getvalue())

    def test_aktion_heisst_sichern_mit_allen_diensten(self):
        from django.contrib.admin.sites import site
        beschreibung = site._registry[MandantInstanz].sichern.short_description
        self.assertIn("Paperless", beschreibung)
        self.assertIn("OpenSlides", beschreibung)
