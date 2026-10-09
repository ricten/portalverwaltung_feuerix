"""Tests fuer Sicherung, Gesundheitspruefung, Update-Rollout, Befehle und Admin-Aktionen (Docker gemockt)."""
import io
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, TestCase, override_settings

from . import betrieb
from .basis import BereitstellungsFehler
from .models import MandantInstanz


def _m(kuerzel="b1", status="laeuft", **kw):
    daten = dict(kuerzel=kuerzel, verein_name="B e.V.", domain=f"{kuerzel}.example.org", status=status,
                 web_port=8100)
    daten.update(kw)
    return MandantInstanz.objects.create(**daten)


def _feuerix(tmp, m):
    (Path(tmp) / "docker-compose.yml").write_text("services: {}\n")
    (Path(tmp) / m.kuerzel).mkdir(exist_ok=True)
    (Path(tmp) / m.kuerzel / ".env").write_text("A=b\n")


def _verz(tmp):
    return override_settings(INSTANZEN_VERZEICHNIS=tmp, FEUERIX_REPO_PFAD=tmp)


class BackupTests(TestCase):
    def test_backup_fuehrt_script_aus_und_kopiert_heraus(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            m = _m()
            _feuerix(tmp, m)
            with patch("apps.mandanten.basis.subprocess.run",
                       return_value=MagicMock(returncode=0, stdout="", stderr="")) as run:
                ziel = betrieb.backup(m)
            befehle = [c.args[0] for c in run.call_args_list]
            self.assertIn("/app/scripts/backup.sh", befehle[0])
            self.assertEqual(befehle[1][-3:-1], ["cp", "web:/data/backups/."])
            self.assertEqual(Path(ziel), Path(tmp) / "b1" / "backups")

    def test_nur_laufende_instanzen(self):
        with self.assertRaises(BereitstellungsFehler):
            betrieb.backup(_m(status="pausiert"))


class GesundheitTests(TestCase):
    def test_meldet_je_dienst(self):
        m = _m(paperless_domain="p.example.org", paperless_port=10100)
        with patch("apps.mandanten.betrieb._antwortet", side_effect=[True, False]):
            self.assertEqual(betrieb.gesundheit(m), {"feuerix": True, "paperless": False})

    def test_status_befehl_meldet_ausfall_per_fehlercode(self):
        _m()
        out = io.StringIO()
        with patch("apps.mandanten.betrieb._antwortet", return_value=False):
            with self.assertRaises(CommandError):
                call_command("mandant_status", stdout=out)
        self.assertIn("AUSFALL", out.getvalue())

    def test_status_befehl_ok(self):
        _m()
        out = io.StringIO()
        with patch("apps.mandanten.betrieb._antwortet", return_value=True):
            call_command("mandant_status", stdout=out)
        self.assertIn("feuerix=ok", out.getvalue())


class UpdateTests(TestCase):
    def test_update_setzt_version(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            m = _m()
            _feuerix(tmp, m)
            (Path(tmp) / "VERSION").write_text("9.9.9\n")
            with patch("apps.mandanten.betrieb.provisioning.stack_hochfahren") as hoch, \
                 patch("apps.mandanten.betrieb.provisioning.auf_bereitschaft_warten") as warten:
                betrieb.update_ausrollen(m)
            hoch.assert_called_once()
            warten.assert_called_once()
            m.refresh_from_db()
            self.assertEqual(m.feuerix_version, "9.9.9")

    def test_fehler_bei_einer_instanz_stoppt_die_anderen_nicht(self):
        a, b = _m("a"), _m("b", web_port=8101)
        _m("c", status="pausiert")

        def fake(mandant):
            if mandant.kuerzel == "a":
                raise BereitstellungsFehler("kaputt")
        with patch("apps.mandanten.betrieb.update_ausrollen", side_effect=fake):
            ok, fehler = betrieb.alle_aktualisieren(pause_sekunden=0)
        self.assertEqual(ok, ["b"])
        self.assertEqual(list(fehler), ["a"])

    def test_befehl_endet_bei_fehler_mit_fehlercode(self):
        _m()
        with patch("apps.mandanten.betrieb.alle_aktualisieren", return_value=([], {"b1": "x"})):
            with self.assertRaises(CommandError):
                call_command("mandant_update", stderr=io.StringIO())


class AdminAktionTests(TestCase):
    def test_aktionen_starten_die_tasks(self):
        m = _m()
        admin = site._registry[MandantInstanz]
        request = RequestFactory().post("/")
        request.user = get_user_model().objects.create_superuser("a", "a@example.org", "x")
        request._messages = MagicMock()
        with patch("apps.mandanten.tasks.mandant_backup_task.delay") as backup, \
             patch("apps.mandanten.tasks.mandant_bereitstellen_task.delay") as bereit:
            admin.sichern(request, MandantInstanz.objects.all())
            admin.bereitstellen(request, MandantInstanz.objects.all())
        backup.assert_called_once_with(m.pk)
        bereit.assert_called_once_with(m.pk)
        self.assertEqual({"bereitstellen", "starten", "stoppen", "neustarten", "pausieren", "sichern",
                          "aktualisieren", "zugangsdaten_anzeigen", "admin_passwort_zuruecksetzen"},
                         set(admin.actions))


class StartenStoppenTests(TestCase):
    def _lauf(self, funktion, status="laeuft", **kw):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            m = _m(status=status, **kw)
            _feuerix(tmp, m)
            with patch("apps.mandanten.basis.subprocess.run",
                       return_value=MagicMock(returncode=0, stdout="", stderr="")) as run,                  patch("apps.mandanten.provisioning.auf_bereitschaft_warten"),                  patch("apps.mandanten.provisioning.proxy.proxy_aktualisieren", return_value=False):
                funktion(m)
            m.refresh_from_db()
            return m, [c.args[0] for c in run.call_args_list]

    def test_stoppen_haelt_container_an_und_setzt_status(self):
        m, befehle = self._lauf(betrieb.stoppen)
        self.assertEqual(m.status, "gestoppt")
        self.assertEqual(befehle[0][-1], "stop")
        self.assertNotIn("down", befehle[0])

    def test_starten_fuehrt_up_ohne_build_aus_und_laeuft_danach(self):
        m, befehle = self._lauf(betrieb.starten, status="gestoppt")
        self.assertEqual(m.status, "laeuft")
        self.assertEqual(befehle[0][-2:], ["up", "-d"])
        self.assertNotIn("--build", befehle[0])
        self.assertEqual(m.fortschritt, 100)

    def test_neustarten(self):
        m, befehle = self._lauf(betrieb.neustarten)
        self.assertEqual(m.status, "laeuft")
        self.assertEqual(befehle[0][-1], "restart")

    def test_fehler_beim_starten_setzt_fehlgeschlagen(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            m = _m(status="gestoppt")
            _feuerix(tmp, m)
            with patch("apps.mandanten.basis.subprocess.run",
                       return_value=MagicMock(returncode=1, stdout="", stderr="kaputt")):
                with self.assertRaises(BereitstellungsFehler):
                    betrieb.starten(m)
            m.refresh_from_db()
            self.assertEqual(m.status, "fehlgeschlagen")

    def test_admin_aktion_prueft_den_status(self):
        gestoppt = _m("g1", status="gestoppt")
        laeuft = _m("l1", status="laeuft")
        admin = site._registry[MandantInstanz]
        request = RequestFactory().post("/")
        request.user = get_user_model().objects.create_superuser("a2", "a@example.org", "x")
        request._messages = MagicMock()
        with patch("apps.mandanten.tasks.mandant_stoppen_task.delay") as stoppen:
            admin.stoppen(request, MandantInstanz.objects.all())
        stoppen.assert_called_once_with(laeuft.pk)
        with patch("apps.mandanten.tasks.mandant_starten_task.delay") as starten:
            admin.starten(request, MandantInstanz.objects.all())
        starten.assert_called_once_with(gestoppt.pk)
        gestoppt.refresh_from_db()
        self.assertEqual(gestoppt.status, "wird_bereitgestellt")

    def test_befehl(self):
        _m(status="laeuft")
        with patch("apps.mandanten.betrieb.stoppen") as stoppen,              patch.dict("apps.mandanten.management.commands.mandant_dienst.AKTIONEN", {"stop": stoppen}):
            call_command("mandant_dienst", "b1", "stop", stdout=io.StringIO())
        stoppen.assert_called_once()


@override_settings(PORTAL_DOMAIN="portal.example.org", PORTAL_PORT=8000, PROXY_NUR_HTTP=False)
class PortalOberflaecheTests(TestCase):
    def test_portal_domain_kommt_in_den_reverse_proxy(self):
        from . import proxy
        text = proxy.caddyfile_erzeugen([])
        self.assertIn("portal.example.org {", text)
        self.assertIn("reverse_proxy 127.0.0.1:8000", text)

    @override_settings(PORTAL_DOMAIN="x.org {\n respond\n}")
    def test_ungueltige_portal_domain_wird_abgelehnt(self):
        from . import proxy
        with self.assertRaises(proxy.ProxyFehler):
            proxy.caddyfile_erzeugen([])

    def test_admin_spalte_zeigt_live_status(self):
        admin = site._registry[MandantInstanz]
        with patch("apps.mandanten.betrieb._antwortet", return_value=False):
            html = str(admin.erreichbarkeit(_m()))
        self.assertIn("red", html)
        self.assertIn("feuerix", html)
        self.assertEqual(admin.erreichbarkeit(_m("z", status="pausiert")), "-")


class FortschrittUndPasswortTests(TestCase):
    def _admin_request(self):
        request = RequestFactory().post("/")
        request.user = get_user_model().objects.create_superuser("adm", "a@example.org", "x")
        request._messages = MagicMock()
        return request

    def test_bereitstellen_meldet_fortschritt_bis_100(self):
        from . import provisioning
        m = _m(status="neu", web_port=None)
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp), \
             patch("apps.mandanten.provisioning.stack_hochfahren"), \
             patch("apps.mandanten.provisioning.auf_bereitschaft_warten"), \
             patch("apps.mandanten.provisioning._proxy_nachziehen"):
            provisioning.bereitstellen(m)
        m.refresh_from_db()
        self.assertEqual((m.status, m.fortschritt, m.schritt), ("laeuft", 100, "Fertig"))

    def test_fehlschlag_merkt_den_schritt(self):
        from . import provisioning
        m = _m(status="neu", web_port=None)
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp), \
             patch("apps.mandanten.provisioning.stack_hochfahren", side_effect=BereitstellungsFehler("x")):
            with self.assertRaises(BereitstellungsFehler):
                provisioning.bereitstellen(m)
        m.refresh_from_db()
        self.assertEqual(m.status, "fehlgeschlagen")
        self.assertIn("Fehlgeschlagen bei: Feuerix bauen", m.schritt)

    def test_status_json_endpunkt(self):
        m = _m(status="wird_bereitgestellt", fortschritt=40, schritt="Test")
        self.client.force_login(get_user_model().objects.create_superuser("adm", "a@example.org", "x"))
        antwort = self.client.get(f"/admin/mandanten/mandantinstanz/{m.pk}/status/")
        self.assertEqual(antwort.json()["fortschritt"], 40)
        self.assertEqual(antwort.json()["status"], "wird_bereitgestellt")

    def test_status_json_nur_fuer_angemeldete(self):
        m = _m()
        self.assertEqual(self.client.get(f"/admin/mandanten/mandantinstanz/{m.pk}/status/").status_code, 302)

    def test_liste_und_detailseite_rendern_mit_balken(self):
        m = _m(status="wird_bereitgestellt", fortschritt=40, schritt="Test")
        self.client.force_login(get_user_model().objects.create_superuser("adm", "a@example.org", "x"))
        self.assertContains(self.client.get("/admin/mandanten/mandantinstanz/"), "pv-balken")
        self.assertContains(self.client.get(f"/admin/mandanten/mandantinstanz/{m.pk}/change/"), "data-url")

    def test_passwort_reset_setzt_in_instanz_und_speichert_neu(self):
        with tempfile.TemporaryDirectory() as tmp, _verz(tmp):
            m = _m(admin_benutzername="chef", admin_email="c@example.org", admin_passwort="alt")
            _feuerix(tmp, m)
            with patch("apps.mandanten.basis.subprocess.run",
                       return_value=MagicMock(returncode=0, stdout="", stderr="")) as run:
                neu = betrieb.admin_passwort_zuruecksetzen(m)
            self.assertNotIn(neu, " ".join(run.call_args.args[0]))   # nicht in der Prozessliste
            self.assertIn(neu, run.call_args.kwargs["input"])
            self.assertIn("'chef'", run.call_args.kwargs["input"])
            m.refresh_from_db()
            self.assertEqual(m.admin_passwort, neu)
            self.assertNotEqual(neu, "alt")

    def test_passwort_reset_nur_fuer_laufende(self):
        with self.assertRaises(BereitstellungsFehler):
            betrieb.admin_passwort_zuruecksetzen(_m(status="pausiert"))

    def test_admin_aktion_zeigt_passwort_einmalig(self):
        m = _m()
        admin = site._registry[MandantInstanz]
        request = self._admin_request()
        with patch("apps.mandanten.betrieb.admin_passwort_zuruecksetzen", return_value="NEUESPW"):
            admin.admin_passwort_zuruecksetzen(request, MandantInstanz.objects.all())
        self.assertIn("NEUESPW", request._messages.add.call_args.args[1])


class NachinstallationTests(TestCase):
    def setUp(self):
        self.admin_user = get_user_model().objects.create_superuser("adm", "a@example.org", "x")
        self.client.force_login(self.admin_user)

    def _post(self, m, **extra):
        daten = dict(kuerzel=m.kuerzel, verein_name=m.verein_name, domain=m.domain, admin_benutzername="admin",
                     admin_email="")
        daten.update(extra)
        return self.client.post(f"/admin/mandanten/mandantinstanz/{m.pk}/change/", daten)

    def test_haken_fuellt_domain_und_installiert_nach(self):
        m = _m()
        with patch("apps.mandanten.tasks.mandant_bereitstellen_task.delay") as delay:
            antwort = self._post(m, paperless_installieren="on")
        self.assertEqual(antwort.status_code, 302)
        m.refresh_from_db()
        self.assertEqual(m.paperless_domain, "paperless.b1.example.org")
        self.assertEqual(m.status, "wird_bereitgestellt")
        delay.assert_called_once_with(m.pk)

    def test_ohne_neuen_dienst_keine_bereitstellung(self):
        m = _m()
        with patch("apps.mandanten.tasks.mandant_bereitstellen_task.delay") as delay:
            self._post(m)
        delay.assert_not_called()
        m.refresh_from_db()
        self.assertEqual(m.status, "laeuft")

    def test_eigene_domain_hat_vorrang(self):
        m = _m()
        with patch("apps.mandanten.tasks.mandant_bereitstellen_task.delay"):
            self._post(m, openslides_installieren="on", openslides_domain="versammlung.example.org")
        m.refresh_from_db()
        self.assertEqual(m.openslides_domain, "versammlung.example.org")

    def test_bei_neuer_instanz_nur_speichern(self):
        with patch("apps.mandanten.tasks.mandant_bereitstellen_task.delay") as delay:
            antwort = self.client.post("/admin/mandanten/mandantinstanz/add/", dict(
                kuerzel="neu", verein_name="Neu e.V.", domain="neu.example.org", admin_benutzername="admin",
                admin_email="", openslides_installieren="on"))
        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(MandantInstanz.objects.get(kuerzel="neu").openslides_domain, "openslides.neu.example.org")
        delay.assert_not_called()   # Bereitstellung startet der Admin bewusst per Aktion


class ZeilenknoepfeTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("zk", "z@example.org", "pw"))

    def test_liste_zeigt_drei_knoepfe_mit_passendem_zustand(self):
        _m("k1", status="laeuft")
        _m("k2", status="gestoppt")
        antwort = self.client.get("/admin/mandanten/mandantinstanz/")
        self.assertContains(antwort, "pv-knopf pv-start", count=2)
        self.assertContains(antwort, "pv-knopf pv-stopp", count=2)
        self.assertContains(antwort, "pv-knopf pv-neustart", count=2)
        # laeuft: Start gesperrt; gestoppt: Stopp und Neustart gesperrt
        self.assertEqual(antwort.content.decode().count('pv-knopf pv-start" data-url') , 2)
        self.assertEqual(antwort.content.decode().count(" disabled>"), 3)

    def test_knopf_loest_task_aus_nur_per_post(self):
        m = _m("k3", status="laeuft")
        url = f"/admin/mandanten/mandantinstanz/{m.pk}/dienst/neustarten/"
        self.assertEqual(self.client.get(url).status_code, 405)
        with patch("apps.mandanten.tasks.mandant_neustarten_task.delay") as delay:
            antwort = self.client.post(url)
        self.assertEqual(antwort.status_code, 302)
        delay.assert_called_once_with(m.pk)

    def test_unbekannte_aktion_und_fehlende_rechte(self):
        m = _m("k4", status="laeuft")
        self.assertEqual(self.client.post(f"/admin/mandanten/mandantinstanz/{m.pk}/dienst/loeschen/").status_code, 403)
        self.client.force_login(get_user_model().objects.create_user("ro", is_staff=True))
        self.assertEqual(self.client.post(f"/admin/mandanten/mandantinstanz/{m.pk}/dienst/stoppen/").status_code, 403)
