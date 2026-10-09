"""Tests fuer den Gesamtlog."""
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from . import gesamtlog, onlinecheck
from .models import LogEintrag, MandantInstanz
from .test_backupziel import FakeSMB, _ziel


def _m(kuerzel="g1", **kw):
    daten = dict(kuerzel=kuerzel, verein_name="Verein G", domain=f"{kuerzel}.example.org", status="laeuft",
                 web_port=8100)
    daten.update(kw)
    return MandantInstanz.objects.create(**daten)


class LogTests(TestCase):
    def test_ebene_wird_erkannt(self):
        self.assertEqual(gesamtlog.ebene_erkennen("Fehler: kaputt"), "fehler")
        self.assertEqual(gesamtlog.ebene_erkennen("Update fehlgeschlagen bei: bauen"), "fehler")
        self.assertEqual(gesamtlog.ebene_erkennen("Nicht erreichbar: feuerix."), "warnung")
        self.assertEqual(gesamtlog.ebene_erkennen("Stack gestartet."), "info")

    def test_instanzprotokoll_landet_auch_im_gesamtlog(self):
        m = _m()
        m.protokollieren("Stack 'mandant-g1' gestartet.")
        m.protokollieren("Fehler: Platte voll")
        eintraege = list(LogEintrag.objects.order_by("id"))
        self.assertEqual([(e.quelle, e.ebene, e.kategorie) for e in eintraege],
                         [("g1", "info", "Instanz"), ("g1", "fehler", "Instanz")])
        self.assertIn("Platte voll", m.protokoll)

    def test_fehler_beim_schreiben_stoert_nicht(self):
        with patch("apps.mandanten.models.LogEintrag.objects") as objekte:
            objekte.create.side_effect = RuntimeError("DB weg")
            gesamtlog.log("egal")
            _m().protokollieren("trotzdem")          # darf nicht werfen

    def test_aufraeumen_loescht_nur_alte_eintraege(self):
        gesamtlog.log("alt")
        gesamtlog.log("neu")
        LogEintrag.objects.filter(text="alt").update(zeit=timezone.now() - timedelta(days=100))
        self.assertEqual(gesamtlog.aufraeumen(), 1)
        self.assertEqual(list(LogEintrag.objects.values_list("text", flat=True)), ["neu"])
        with override_settings(LOG_AUFBEWAHRUNG_TAGE=0):
            LogEintrag.objects.update(zeit=timezone.now() - timedelta(days=999))
            self.assertEqual(gesamtlog.aufraeumen(), 0)

    def test_backupziel_wechsel_wird_geloggt(self):
        _ziel()
        with patch("apps.mandanten.backupziel._smb", return_value=FakeSMB(fehler="Host weg")):
            onlinecheck.backupziel_pruefen()
            onlinecheck.backupziel_pruefen()
        with patch("apps.mandanten.backupziel._smb", return_value=FakeSMB()):
            onlinecheck.backupziel_pruefen()
        texte = [e.text for e in LogEintrag.objects.filter(kategorie="Backup-Ziel").order_by("id")]
        self.assertEqual(len(texte), 2)          # nur die Wechsel, nicht jeder Check
        self.assertIn("nicht erreichbar", texte[0])
        self.assertIn("wieder erreichbar", texte[1])


class AdminAktionenTests(TestCase):
    def _request(self):
        request = RequestFactory().post("/")
        request.user = get_user_model().objects.get_or_create(
            username="logger", defaults=dict(is_superuser=True, is_staff=True))[0]
        request._messages = MagicMock()
        return request

    def test_aktion_wird_mit_benutzername_geloggt(self):
        m = _m()
        admin = site._registry[MandantInstanz]
        with patch("apps.mandanten.tasks.mandant_neustarten_task.delay"):
            admin.neustarten(self._request(), MandantInstanz.objects.filter(pk=m.pk))
        eintrag = LogEintrag.objects.get(kategorie="Instanz")
        self.assertEqual(eintrag.benutzer, "logger")
        self.assertEqual(eintrag.quelle, "g1")
        self.assertIn("Neustarten gestartet", eintrag.text)
        m.refresh_from_db()
        self.assertIn("Benutzer: logger", m.protokoll)

    def test_update_und_stilllegen_werden_geloggt(self):
        m1, m2 = _m("u1"), _m("u2")
        admin = site._registry[MandantInstanz]
        with patch("apps.mandanten.tasks.mandant_update_task.delay"), \
             patch("apps.mandanten.tasks.mandant_stilllegen_task.delay"):
            admin.aktualisieren(self._request(), MandantInstanz.objects.filter(pk=m1.pk))
            admin.stilllegen(self._request(), MandantInstanz.objects.filter(pk=m2.pk))
        texte = sorted(LogEintrag.objects.values_list("text", flat=True))
        self.assertTrue(any("Update gestartet" in t for t in texte))
        self.assertTrue(any("Stilllegen gestartet" in t for t in texte))


class OberflaecheTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("lg", "l@example.org", "pw"))

    def test_liste_zeigt_eintraege_mit_filter_und_suche(self):
        gesamtlog.log("Stack gestartet.", quelle="a1")
        gesamtlog.log("Fehler: kaputt", quelle="b1", benutzer="rico")
        antwort = self.client.get("/admin/mandanten/logeintrag/")
        self.assertContains(antwort, "Stack gestartet.")
        self.assertContains(antwort, "Fehler: kaputt")
        nur_fehler = self.client.get("/admin/mandanten/logeintrag/?ebene__exact=fehler")
        self.assertContains(nur_fehler, "Fehler: kaputt")
        self.assertNotContains(nur_fehler, "Stack gestartet.")
        such = self.client.get("/admin/mandanten/logeintrag/?q=rico")
        self.assertContains(such, "Fehler: kaputt")
        self.assertNotContains(such, "Stack gestartet.")

    def test_log_ist_nur_lesbar(self):
        gesamtlog.log("x")
        eintrag = LogEintrag.objects.get()
        self.assertEqual(self.client.get("/admin/mandanten/logeintrag/add/").status_code, 403)
        self.assertEqual(self.client.post(f"/admin/mandanten/logeintrag/{eintrag.pk}/change/", {"text": "y"}).status_code, 403)
        eintrag.refresh_from_db()
        self.assertEqual(eintrag.text, "x")

    def test_loeschen_nur_fuer_superuser(self):
        gesamtlog.log("x")
        eintrag = LogEintrag.objects.get()
        self.client.force_login(get_user_model().objects.create_user("normal", is_staff=True))
        self.assertEqual(self.client.post(f"/admin/mandanten/logeintrag/{eintrag.pk}/delete/", {"post": "yes"}).status_code, 403)
        self.assertTrue(LogEintrag.objects.exists())
