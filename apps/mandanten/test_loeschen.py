"""Tests fuer das endgueltige Loeschen mit Abschlusssicherung, die Vormerkung im Admin und die frueher erfasste
Version der Zusatzdienste (Docker, HTTP und Dateisystem gemockt bzw. temporaer)."""
import io
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, TestCase, override_settings

from . import endsicherung, provisioning
from .basis import BereitstellungsFehler, status_vor_vormerkung, vormerken
from .models import LogEintrag, MandantInstanz, UpdateZustand


def _m(kuerzel="l1", **kw):
    daten = dict(kuerzel=kuerzel, verein_name="Verein L", domain=f"{kuerzel}.example.org", status="laeuft",
                 web_port=8100)
    daten.update(kw)
    return MandantInstanz.objects.create(**daten)


class VormerkenTests(TestCase):
    def test_ausgangsstatus_bleibt_erhalten_auch_bei_mehrfachem_vormerken(self):
        m = _m(status="gestoppt")
        vormerken(m, "Löschen: in der Warteschlange")
        self.assertEqual((m.status, status_vor_vormerkung(m)), ("wird_bereitgestellt", "gestoppt"))
        vormerken(m, "Noch einmal")
        self.assertEqual(status_vor_vormerkung(m), "gestoppt")

    def test_ohne_vormerkung_gilt_der_aktuelle_status(self):
        self.assertEqual(status_vor_vormerkung(_m(status="laeuft")), "laeuft")
        self.assertEqual(status_vor_vormerkung(_m("l2", status="wird_bereitgestellt", schritt="Feuerix bauen")),
                         "wird_bereitgestellt")

    def test_fehlgeschlagenes_stilllegen_aus_dem_admin_laesst_die_instanz_nicht_in_arbeit_haengen(self):
        m = _m()
        vormerken(m, "Stilllegen: in der Warteschlange")
        with patch("apps.mandanten.provisioning.stack_abbauen", side_effect=BereitstellungsFehler("kaputt")):
            with self.assertRaises(BereitstellungsFehler):
                provisioning.abbauen(m)
        m.refresh_from_db()
        self.assertEqual(m.status, "laeuft")


class AbbauenMitSicherungTests(TestCase):
    def _patches(self, **kw):
        return [patch("apps.mandanten.provisioning.stack_abbauen", **kw.get("stack", {})),
                patch("apps.mandanten.provisioning._proxy_nachziehen"),
                patch("apps.mandanten.endsicherung.archivieren")]

    def test_sicherung_kommt_vor_dem_entfernen(self):
        m = _m()
        reihenfolge = []
        with patch("apps.mandanten.endsicherung.abschlusssicherung",
                   side_effect=lambda x: reihenfolge.append("sicherung")), \
             patch("apps.mandanten.provisioning.stack_abbauen",
                   side_effect=lambda *a, **k: reihenfolge.append("entfernen")), \
             patch("apps.mandanten.provisioning._proxy_nachziehen"), \
             patch("apps.mandanten.endsicherung.archivieren",
                   side_effect=lambda x: reihenfolge.append("archiv")):
            provisioning.abbauen(m, daten_loeschen=True)
        self.assertEqual(reihenfolge, ["sicherung", "entfernen", "archiv"])
        m.refresh_from_db()
        self.assertEqual(m.status, "geloescht")

    def test_fehlgeschlagene_sicherung_bricht_ab_ohne_etwas_zu_entfernen(self):
        m = _m(status="gestoppt")
        vormerken(m, "Löschen: in der Warteschlange")
        with patch("apps.mandanten.endsicherung.abschlusssicherung", side_effect=BereitstellungsFehler("Platte voll")), \
             patch("apps.mandanten.provisioning.stack_abbauen") as abbau, \
             patch("apps.mandanten.provisioning.proxy_nachziehen_best_effort"):
            with self.assertRaises(BereitstellungsFehler):
                provisioning.abbauen(m, daten_loeschen=True)
        abbau.assert_not_called()
        m.refresh_from_db()
        self.assertEqual(m.status, "gestoppt")
        self.assertIn("Löschen fehlgeschlagen bei", m.schritt)

    def test_stilllegen_macht_keine_abschlusssicherung(self):
        m = _m()
        with patch("apps.mandanten.endsicherung.abschlusssicherung") as sicherung, \
             patch("apps.mandanten.provisioning.stack_abbauen"), patch("apps.mandanten.provisioning._proxy_nachziehen"):
            provisioning.abbauen(m)
        sicherung.assert_not_called()

    def test_ohne_sicherung_ist_ausdruecklich_und_wird_protokolliert(self):
        m = _m()
        with patch("apps.mandanten.endsicherung.abschlusssicherung") as sicherung, \
             patch("apps.mandanten.provisioning.stack_abbauen"), patch("apps.mandanten.provisioning._proxy_nachziehen"), \
             patch("apps.mandanten.endsicherung.archivieren"):
            provisioning.abbauen(m, daten_loeschen=True, sicherung=False)
        sicherung.assert_not_called()
        m.refresh_from_db()
        self.assertIn("OHNE Abschlusssicherung", m.protokoll)


class AbschlusssicherungTests(TestCase):
    def _ablauf(self, m, tmp, **patches):
        ereignisse = []
        with override_settings(INSTANZEN_VERZEICHNIS=tmp), \
             patch("apps.mandanten.endsicherung.provisioning.stack_steuern", side_effect=lambda *a: ereignisse.append("start")), \
             patch("apps.mandanten.endsicherung.provisioning.auf_bereitschaft_warten"), \
             patch("apps.mandanten.endsicherung.paperless.steuern"), \
             patch("apps.mandanten.endsicherung.openslides.steuern"), \
             patch("apps.mandanten.endsicherung.paperless.auf_bereitschaft_warten"), \
             patch("apps.mandanten.endsicherung.openslides.auf_bereitschaft_warten"), \
             patch("apps.mandanten.endsicherung.betrieb.backup", side_effect=lambda *a, **k: ereignisse.append("feuerix")), \
             patch("apps.mandanten.endsicherung.dienstupdate.paperless_export",
                   side_effect=lambda *a, **k: ereignisse.append("paperless")), \
             patch("apps.mandanten.endsicherung.dienstupdate.openslides_dump",
                   side_effect=lambda *a, **k: ereignisse.append("openslides")):
            endsicherung.abschlusssicherung(m)
        return ereignisse

    def test_alle_dienste_werden_gestartet_und_der_reihe_nach_gesichert(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = _m(paperless_domain="p.l1.example.org", openslides_domain="o.l1.example.org")
            (Path(tmp) / "l1" / "paperless").mkdir(parents=True)
            (Path(tmp) / "l1" / "paperless" / ".env").write_text("X=1")
            (Path(tmp) / "l1" / "openslides").mkdir(parents=True)
            (Path(tmp) / "l1" / "openslides" / "docker-compose.yml").write_text("services: {}")
            with patch("apps.mandanten.endsicherung.paperless._verzeichnis",
                       return_value=Path(tmp) / "l1" / "paperless"), \
                 patch("apps.mandanten.endsicherung.openslides._verzeichnis",
                       return_value=Path(tmp) / "l1" / "openslides"):
                ereignisse = self._ablauf(m, tmp)
        self.assertEqual(ereignisse, ["start", "feuerix", "paperless", "openslides"])
        m.refresh_from_db()
        self.assertIn("Abschlusssicherung vollständig (Feuerix, Paperless, OpenSlides)", m.protokoll)

    def test_nur_feuerix_wenn_keine_zusatzdienste_eingerichtet_sind(self):
        with tempfile.TemporaryDirectory() as tmp:
            ereignisse = self._ablauf(_m(), tmp)
        self.assertEqual(ereignisse, ["start", "feuerix"])

    def test_zusatzdienst_ohne_dateien_wird_nicht_gesichert(self):
        with tempfile.TemporaryDirectory() as tmp:
            ereignisse = self._ablauf(_m(paperless_domain="p.l1.example.org"), tmp)   # Paperless nie installiert
        self.assertEqual(ereignisse, ["start", "feuerix"])


class ArchivierenTests(TestCase):
    def test_instanzordner_wird_mit_env_und_sicherungen_verschoben(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp):
            m = _m()
            quelle = Path(tmp) / "l1"
            (quelle / "backups").mkdir(parents=True)
            (quelle / ".env").write_text("FIELD_ENCRYPTION_KEY=geheim")
            (quelle / "backups" / "db.sql").write_text("daten")
            ziel = endsicherung.archivieren(m)
            self.assertFalse(quelle.exists())
            self.assertEqual(ziel.parent, Path(tmp) / "_geloescht")
            self.assertTrue(ziel.name.startswith("l1-"))
            self.assertEqual((ziel / ".env").read_text(), "FIELD_ENCRYPTION_KEY=geheim")
            self.assertEqual((ziel / "backups" / "db.sql").read_text(), "daten")
        m.refresh_from_db()
        self.assertIn("aufbewahrt in", m.protokoll)

    def test_ohne_ordner_passiert_nichts(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp):
            self.assertIsNone(endsicherung.archivieren(_m()))


class BefehlTests(TestCase):
    def test_standardmaessig_mit_sicherung_und_notfall_ohne(self):
        _m()
        with patch("apps.mandanten.management.commands.mandant_abbauen.abbauen") as abbauen:
            call_command("mandant_abbauen", "l1", "--daten-loeschen", stdout=io.StringIO())
            call_command("mandant_abbauen", "l1", "--daten-loeschen", "--ohne-sicherung", stdout=io.StringIO())
        self.assertEqual([c.kwargs["sicherung"] for c in abbauen.call_args_list], [True, False])

    def test_ohne_sicherung_nur_zusammen_mit_daten_loeschen(self):
        _m()
        with self.assertRaises(CommandError):
            call_command("mandant_abbauen", "l1", "--ohne-sicherung", stdout=io.StringIO())


class AdminLoeschenTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("del", "d@example.org", "pw"))

    def _url(self, m):
        return f"/admin/mandanten/mandantinstanz/{m.pk}/endgueltig-loeschen/"

    def test_bestaetigungsseite_erklaert_die_sicherung(self):
        antwort = self.client.get(self._url(_m(paperless_domain="p.l1.example.org")))
        self.assertContains(antwort, "Abschlusssicherung")
        self.assertContains(antwort, "Paperless-Export")
        self.assertContains(antwort, "Schlägt etwas davon fehl, wird nichts gelöscht")
        self.assertContains(antwort, "_geloescht")

    def test_falsches_kuerzel_loescht_nichts(self):
        m = _m()
        with patch("apps.mandanten.tasks.mandant_loeschen_task.delay") as delay:
            antwort = self.client.post(self._url(m), {"kuerzel": "falsch"})
        delay.assert_not_called()
        self.assertContains(antwort, "Kürzel stimmt nicht")
        m.refresh_from_db()
        self.assertEqual(m.status, "laeuft")

    def test_richtiges_kuerzel_merkt_vor_und_startet_den_auftrag(self):
        m = _m(status="gestoppt")
        with patch("apps.mandanten.tasks.mandant_loeschen_task.delay") as delay:
            antwort = self.client.post(self._url(m), {"kuerzel": "l1"})
        self.assertEqual(antwort.status_code, 302)
        delay.assert_called_once_with(m.pk)
        m.refresh_from_db()
        self.assertEqual(m.status, "wird_bereitgestellt")
        self.assertEqual(status_vor_vormerkung(m), "gestoppt")
        self.assertTrue(LogEintrag.objects.filter(text__contains="Endgültiges Löschen gestartet", benutzer="del").exists())

    def test_nur_superuser_und_nur_geeignete_status(self):
        m = _m()
        self.client.force_login(get_user_model().objects.create_user("st", is_staff=True))
        self.assertEqual(self.client.get(self._url(m)).status_code, 403)
        self.client.force_login(get_user_model().objects.get(username="del"))
        for status in ("neu", "geloescht", "wird_bereitgestellt"):
            MandantInstanz.objects.filter(pk=m.pk).update(status=status)
            with patch("apps.mandanten.tasks.mandant_loeschen_task.delay") as delay:
                antwort = self.client.post(self._url(m), {"kuerzel": "l1"}, follow=True)
            delay.assert_not_called()
            self.assertContains(antwort, "Löschen nicht möglich")

    def test_aktion_fuehrt_zur_bestaetigungsseite_und_verlangt_genau_eine_instanz(self):
        a, b = _m("a1"), _m("b1")
        admin = site._registry[MandantInstanz]
        request = RequestFactory().post("/")
        request.user = get_user_model().objects.get(username="del")
        request._messages = MagicMock()
        antwort = admin.endgueltig_loeschen(request, MandantInstanz.objects.filter(pk=a.pk))
        self.assertEqual(antwort.url, self._url(a))
        self.assertIsNone(admin.endgueltig_loeschen(request, MandantInstanz.objects.all()))

    def test_datensatz_loeschen_nur_bei_neuen_oder_geloeschten_instanzen(self):
        admin = site._registry[MandantInstanz]
        request = RequestFactory().get("/")
        request.user = get_user_model().objects.get(username="del")
        for status, erlaubt in (("neu", True), ("geloescht", True), ("laeuft", False), ("gestoppt", False),
                                ("pausiert", False), ("fehlgeschlagen", False)):
            self.assertEqual(admin.has_delete_permission(request, _m(f"x-{status}", status=status)), erlaubt, status)


class VersionserfassungTests(TestCase):
    def test_versionen_werden_am_ende_von_start_und_bereitstellung_erfasst(self):
        from . import betrieb
        m = _m(paperless_domain="p.l1.example.org", paperless_port=10100)
        with patch("apps.mandanten.betrieb.provisioning.stack_steuern"), \
             patch("apps.mandanten.betrieb.provisioning.auf_bereitschaft_warten"), \
             patch("apps.mandanten.betrieb.paperless.steuern"), \
             patch("apps.mandanten.betrieb.paperless.auf_bereitschaft_warten"), \
             patch("apps.mandanten.betrieb.provisioning._proxy_nachziehen"), \
             patch("apps.mandanten.dienstversionen.paperless_laufend", return_value="2.14.7"):
            betrieb.neustarten(m)
        m.refresh_from_db()
        self.assertEqual(m.paperless_version, "2.14.7")

    def test_nicht_ermittelte_version_wird_angezeigt_und_loest_die_pruefung_aus(self):
        _m(paperless_domain="p.l1.example.org", paperless_port=10100)
        z = UpdateZustand.holen()
        self.assertEqual(z.dienste_unbekannt, [("Paperless-ngx", ["l1"])])
        client = self.client
        client.force_login(get_user_model().objects.create_superuser("vu", "v@example.org", "pw"))
        html = client.get("/admin/").content.decode()
        self.assertIn("Paperless-ngx-Version nicht ermittelt", html)
        with override_settings(UPDATE_PRUEFUNG_STUNDEN=6, UPDATE_DIENSTE_PRUEFEN=True), \
             patch("apps.mandanten.tasks.update_pruefen_task.delay") as delay:
            UpdateZustand.objects.update_or_create(pk=1, defaults=dict(geprueft_am=__import__("django.utils.timezone", fromlist=["now"]).now()))
            from . import updates
            updates.fuer_anzeige()
        delay.assert_called_once()           # obwohl die letzte Pruefung frisch ist: Version fehlt

    def test_dienstefehler_wird_im_hinweis_gezeigt(self):
        self.client.force_login(get_user_model().objects.create_superuser("vf", "v@example.org", "pw"))
        UpdateZustand.objects.update_or_create(pk=1, defaults=dict(dienste_fehler="Paperless-ngx: kein Netz"))
        self.assertIn("kein Netz", self.client.get("/admin/").content.decode())
