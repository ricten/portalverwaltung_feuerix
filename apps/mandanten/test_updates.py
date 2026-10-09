"""Tests fuer Update-Pruefung und Rollout (git und Docker gemockt)."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from . import updates
from .basis import BereitstellungsFehler
from .models import MandantInstanz, UpdateZustand

CHANGELOG = """# Changelog

Einleitung.

## 1.59.0
- Neu: Etwas Neues.

## 1.58.1
- Fix: Etwas Kleines.

## 1.58.0
- Altes.
"""


def _m(kuerzel, version, status="laeuft", **kw):
    return MandantInstanz.objects.create(kuerzel=kuerzel, verein_name=kuerzel, domain=f"{kuerzel}.example.org",
                                         status=status, web_port=8100, feuerix_version=version, **kw)


def _git_antworten(remote="1.59.0", changelog=CHANGELOG):
    def antwort(argumente, was, timeout=90):
        if argumente[0] == "rev-parse":
            return "origin/main\n"
        if argumente[0] == "show" and argumente[1].endswith(":VERSION"):
            return remote + "\n"
        if argumente[0] == "show":
            return changelog
        return ""
    return antwort


class HilfsfunktionTests(TestCase):
    def test_versionsvergleich_ist_numerisch(self):
        self.assertGreater(updates.versionstupel("1.10.0"), updates.versionstupel("1.9.9"))
        self.assertEqual(updates.versionstupel("kaputt"), ())

    def test_auszug_enthaelt_nur_neuere_abschnitte(self):
        text = updates.aenderungen_auszug(CHANGELOG, "1.58.0")
        self.assertIn("1.59.0", text)
        self.assertIn("1.58.1", text)
        self.assertNotIn("Altes", text)
        self.assertNotIn("Einleitung", text)


class PruefenTests(TestCase):
    def test_neuere_version_wird_erkannt_und_instanzen_sind_veraltet(self):
        _m("a", "1.58.0")
        _m("b", "1.59.0")
        with patch("apps.mandanten.updates._git", side_effect=_git_antworten()), \
             patch("apps.mandanten.provisioning.feuerix_version", return_value="1.58.0"):
            z = updates.pruefen()
        self.assertTrue(z.update_verfuegbar)
        self.assertEqual(z.ziel_version, "1.59.0")
        self.assertEqual([m.kuerzel for m in z.veraltete_instanzen], ["a"])
        self.assertIn("Etwas Neues", z.aenderungen)
        self.assertEqual(z.fehler, "")
        self.assertTrue(z.rollout_empfohlen)

    def test_aktuell_keine_empfehlung(self):
        _m("a", "1.58.0")
        with patch("apps.mandanten.updates._git", side_effect=_git_antworten(remote="1.58.0")), \
             patch("apps.mandanten.provisioning.feuerix_version", return_value="1.58.0"):
            z = updates.pruefen()
        self.assertFalse(z.update_verfuegbar)
        self.assertFalse(z.rollout_empfohlen)

    def test_instanz_auf_alter_version_nach_manuellem_pull_wird_empfohlen(self):
        _m("a", "1.57.0")
        with patch("apps.mandanten.updates._git", side_effect=_git_antworten(remote="1.58.0")), \
             patch("apps.mandanten.provisioning.feuerix_version", return_value="1.58.0"):
            z = updates.pruefen()
        self.assertFalse(z.update_verfuegbar)
        self.assertTrue(z.rollout_empfohlen)

    def test_fehler_wird_gespeichert(self):
        with patch("apps.mandanten.updates._git", side_effect=BereitstellungsFehler("git fetch fehlgeschlagen")):
            z = updates.pruefen()
        self.assertIn("git fetch fehlgeschlagen", z.fehler)


class RolloutTests(TestCase):
    def _lauf(self, **kw):
        """Fuehrt einen Rollout aus; die Checkout-Version springt mit dem `git pull` von 1.58.0 auf 1.59.0."""
        befehle, version = [], {"wert": "1.58.0"}

        def git(argumente, was, timeout=90):
            befehle.append(argumente)
            if argumente[0] == "pull":
                version["wert"] = "1.59.0"
            return _git_antworten()(argumente, was, timeout)

        with patch("apps.mandanten.updates._git", side_effect=git), \
             patch("apps.mandanten.provisioning.feuerix_version", side_effect=lambda: version["wert"]), \
             patch("apps.mandanten.updates.betrieb.update_mit_sicherung", **kw) as ausrollen:
            ergebnis = updates.ausrollen(pause_sekunden=0)
        return ergebnis, ausrollen, befehle

    def test_pull_und_nur_veraltete_laufende_instanzen(self):
        a = _m("a", "1.58.0")
        _m("b", "1.59.0")
        _m("c", "1.58.0", status="gestoppt")
        ergebnis, ausrollen, _ = self._lauf()
        ausrollen.assert_called_once()
        self.assertEqual(ausrollen.call_args.args[0].pk, a.pk)
        self.assertIn("1 von 1", ergebnis)
        z = UpdateZustand.holen()
        self.assertFalse(z.rollout_laeuft)
        self.assertEqual(z.rollout_ergebnis, ergebnis)

    def test_git_pull_wird_ausgefuehrt(self):
        _m("a", "1.58.0")
        _, _, befehle = self._lauf()
        self.assertIn(["pull", "--ff-only"], befehle)

    def test_ohne_neue_version_kein_pull(self):
        _m("a", "1.57.0")
        befehle = []

        def git(argumente, was, timeout=90):
            befehle.append(argumente)
            return _git_antworten(remote="1.58.0")(argumente, was, timeout)

        with patch("apps.mandanten.updates._git", side_effect=git), \
             patch("apps.mandanten.provisioning.feuerix_version", return_value="1.58.0"), \
             patch("apps.mandanten.updates.betrieb.update_mit_sicherung") as ausrollen:
            updates.ausrollen(pause_sekunden=0)
        self.assertNotIn(["pull", "--ff-only"], befehle)
        ausrollen.assert_called_once()   # die Instanz war trotzdem veraltet

    def test_fehler_bei_einer_instanz_stoppt_die_anderen_nicht(self):
        _m("a", "1.58.0")
        _m("b", "1.58.0")
        ergebnis, ausrollen, _ = self._lauf(side_effect=[BereitstellungsFehler("kaputt\nDetails"), None])
        self.assertEqual(ausrollen.call_count, 2)
        self.assertIn("1 von 2", ergebnis)
        self.assertIn("a (kaputt)", ergebnis)

    def test_laufender_rollout_wird_nicht_doppelt_gestartet(self):
        UpdateZustand.objects.update_or_create(pk=1, defaults=dict(rollout_laeuft=True,
                                                                  rollout_gestartet_am=timezone.now()))
        with self.assertRaises(BereitstellungsFehler):
            updates.ausrollen(pause_sekunden=0)

    def test_haengengebliebener_lock_verfaellt(self):
        UpdateZustand.objects.update_or_create(pk=1, defaults=dict(
            rollout_laeuft=True, rollout_gestartet_am=timezone.now() - timedelta(hours=3)))
        with patch("apps.mandanten.updates._git", side_effect=_git_antworten(remote="1.58.0")), \
             patch("apps.mandanten.provisioning.feuerix_version", return_value="1.58.0"):
            updates.ausrollen(pause_sekunden=0)
        self.assertFalse(UpdateZustand.holen().rollout_laeuft)

    def test_pruefungsfehler_bricht_ohne_pull_ab(self):
        with patch("apps.mandanten.updates._git", side_effect=BereitstellungsFehler("offline")):
            ergebnis = updates.ausrollen(pause_sekunden=0)
        self.assertIn("Abgebrochen", ergebnis)
        self.assertFalse(UpdateZustand.holen().rollout_laeuft)


class OberflaecheTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("u", "u@example.org", "pw"))

    def _zustand(self, **kw):
        UpdateZustand.objects.update_or_create(pk=1, defaults=dict(
            lokale_version="1.58.0", remote_version="1.59.0", geprueft_am=timezone.now(), **kw))

    def test_hinweis_zeigt_neue_version_und_veraltete_instanzen(self):
        _m("a", "1.58.0")
        self._zustand(aenderungen="## 1.59.0\n- Neu: Etwas Neues.")
        antwort = self.client.get("/admin/")
        self.assertContains(antwort, "Neue Feuerix-Version 1.59.0 verfügbar")
        self.assertContains(antwort, "Update jetzt ausrollen")
        self.assertContains(antwort, "Etwas Neues")
        antwort = self.client.get("/admin/mandanten/mandantinstanz/")
        self.assertContains(antwort, "Update jetzt ausrollen")

    def test_hinweis_aktuell(self):
        UpdateZustand.objects.update_or_create(pk=1, defaults=dict(
            lokale_version="1.58.0", remote_version="1.58.0", geprueft_am=timezone.now()))
        self.assertContains(self.client.get("/admin/"), "Feuerix ist aktuell")

    def test_rollout_starten_nur_per_post_und_mit_recht(self):
        url = "/admin/mandanten/mandantinstanz/update/ausrollen/"
        self.assertEqual(self.client.get(url).status_code, 405)
        with patch("apps.mandanten.tasks.update_ausrollen_task.delay") as delay:
            self.assertEqual(self.client.post(url).status_code, 302)
        delay.assert_called_once()
        self.client.force_login(get_user_model().objects.create_user("ro", is_staff=True))
        self.assertEqual(self.client.post(url).status_code, 403)

    def test_status_endpunkt(self):
        UpdateZustand.objects.update_or_create(pk=1, defaults=dict(rollout_laeuft=True, rollout_text="Instanz 1 von 2"))
        antwort = self.client.get("/admin/mandanten/mandantinstanz/update/status/")
        self.assertEqual(antwort.json(), {"laeuft": True, "text": "Instanz 1 von 2"})
        self.assertContains(self.client.get("/admin/"), "Update wird ausgerollt")

    def test_pruefen_per_knopf(self):
        with patch("apps.mandanten.updates._git", side_effect=_git_antworten(remote="1.58.0")), \
             patch("apps.mandanten.provisioning.feuerix_version", return_value="1.58.0"):
            antwort = self.client.post("/admin/mandanten/mandantinstanz/update/pruefen/")
        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(UpdateZustand.holen().remote_version, "1.58.0")


class AutomatischePruefungTests(TestCase):
    @override_settings(UPDATE_PRUEFUNG_STUNDEN=6)
    def test_veraltete_pruefung_wird_im_hintergrund_angestossen_aber_nicht_doppelt(self):
        with patch("apps.mandanten.tasks.update_pruefen_task.delay") as delay:
            updates.fuer_anzeige()
            updates.fuer_anzeige()
        delay.assert_called_once()

    @override_settings(UPDATE_PRUEFUNG_STUNDEN=6)
    def test_frische_pruefung_wird_nicht_wiederholt(self):
        UpdateZustand.objects.update_or_create(pk=1, defaults=dict(geprueft_am=timezone.now()))
        with patch("apps.mandanten.tasks.update_pruefen_task.delay") as delay:
            updates.fuer_anzeige()
        delay.assert_not_called()

    @override_settings(UPDATE_PRUEFUNG_STUNDEN=6)
    def test_nicht_erreichbarer_broker_stoert_nicht(self):
        with patch("apps.mandanten.tasks.update_pruefen_task.delay", side_effect=OSError("kein Redis")):
            updates.fuer_anzeige()

    def test_ausgeschaltet(self):
        with patch("apps.mandanten.tasks.update_pruefen_task.delay") as delay:
            updates.fuer_anzeige()
        delay.assert_not_called()


class SicherungVorUpdateTests(TestCase):
    def _sicherung_anlegen(self, tmp, kuerzel, alter_stunden):
        import os
        import time
        from pathlib import Path
        verz = Path(tmp) / kuerzel / "backups"
        verz.mkdir(parents=True)
        datei = verz / "db.sql"
        datei.write_text("x")
        zeit = time.time() - alter_stunden * 3600
        os.utime(datei, (zeit, zeit))

    def test_letzte_sicherung_und_aktualitaet(self):
        import tempfile
        m = _m("s1", "1.58.0")
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp):
            self.assertIsNone(m.letzte_sicherung)
            self.assertFalse(m.sicherung_aktuell)
            self._sicherung_anlegen(tmp, "s1", alter_stunden=2)
            self.assertTrue(m.sicherung_aktuell)
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp):
            self._sicherung_anlegen(tmp, "s1", alter_stunden=48)
            self.assertFalse(m.sicherung_aktuell)

    def test_ohne_aktuelle_sicherung_wird_erst_gesichert_dann_aktualisiert(self):
        from . import betrieb
        m = _m("s2", "1.58.0")
        reihenfolge = []
        with patch("apps.mandanten.betrieb.backup", side_effect=lambda x, **kw: reihenfolge.append("backup")), \
             patch("apps.mandanten.betrieb.update_ausrollen", side_effect=lambda x, **kw: reihenfolge.append("update")):
            betrieb.update_mit_sicherung(m)
        self.assertEqual(reihenfolge, ["backup", "update"])

    def test_mit_aktueller_sicherung_keine_neue(self):
        import tempfile
        from . import betrieb
        m = _m("s3", "1.58.0")
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp):
            self._sicherung_anlegen(tmp, "s3", alter_stunden=1)
            with patch("apps.mandanten.betrieb.backup") as backup, \
                 patch("apps.mandanten.betrieb.update_ausrollen") as update:
                betrieb.update_mit_sicherung(m)
        backup.assert_not_called()
        update.assert_called_once()

    def test_fehlgeschlagene_sicherung_verhindert_das_update(self):
        from . import betrieb
        m = _m("s4", "1.58.0")
        with patch("apps.mandanten.betrieb.backup", side_effect=BereitstellungsFehler("Platte voll")), \
             patch("apps.mandanten.betrieb.update_ausrollen") as update:
            with self.assertRaises(BereitstellungsFehler):
                betrieb.update_mit_sicherung(m)
        update.assert_not_called()
        m.refresh_from_db()
        self.assertIn("Update abgebrochen", m.protokoll)


class EinzelUpdateKnopfTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("e", "e@example.org", "pw"))

    def test_knopf_nur_bei_veralteter_laufender_instanz(self):
        UpdateZustand.objects.update_or_create(pk=1, defaults=dict(lokale_version="1.58.0", remote_version="1.59.0"))
        veraltet = _m("alt", "1.58.0")
        _m("neu", "1.59.0")
        _m("aus", "1.58.0", status="gestoppt")
        html = self.client.get("/admin/mandanten/mandantinstanz/").content.decode()
        self.assertIn(f"/{veraltet.pk}/dienst/aktualisieren/", html)
        self.assertEqual(html.count("/dienst/aktualisieren/"), 1)
        self.assertIn("→ 1.59.0", html)

    def test_klick_startet_update_task(self):
        m = _m("k", "1.58.0")
        url = f"/admin/mandanten/mandantinstanz/{m.pk}/dienst/aktualisieren/"
        with patch("apps.mandanten.tasks.mandant_update_task.delay") as delay:
            self.assertEqual(self.client.post(url).status_code, 302)
        delay.assert_called_once_with(m.pk)

    def test_klick_bei_nicht_laufender_instanz_tut_nichts(self):
        m = _m("g", "1.58.0", status="gestoppt")
        with patch("apps.mandanten.tasks.mandant_update_task.delay") as delay:
            self.client.post(f"/admin/mandanten/mandantinstanz/{m.pk}/dienst/aktualisieren/")
        delay.assert_not_called()


class UpdateFortschrittTests(TestCase):
    def _ablauf(self, **kw):
        """Fuehrt update_mit_sicherung aus (Docker gemockt) und merkt sich die gemeldeten Schritte."""
        from . import betrieb
        m = _m("f1", "1.58.0", **kw)
        schritte = []
        orig = betrieb.basis.fortschritt

        def merken(mandant, prozent, text):
            schritte.append((mandant.status, prozent, text))
            orig(mandant, prozent, text)

        return m, schritte, merken

    def test_status_und_fortschritt_waehrend_des_updates(self):
        import tempfile
        from . import betrieb
        m, schritte, merken = self._ablauf()
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp), \
             patch("apps.mandanten.betrieb.basis.fortschritt", side_effect=merken), \
             patch("apps.mandanten.betrieb.backup"), \
             patch("apps.mandanten.betrieb.provisioning.stack_hochfahren"), \
             patch("apps.mandanten.betrieb.provisioning.auf_bereitschaft_warten"), \
             patch("apps.mandanten.betrieb.provisioning.feuerix_version", return_value="1.59.0"):
            betrieb.update_mit_sicherung(m)
        self.assertTrue(all(s[0] == "wird_bereitgestellt" for s in schritte))
        self.assertEqual([s[1] for s in schritte], [5, 10, 40, 75, 100])
        m.refresh_from_db()
        self.assertEqual(m.status, "laeuft")
        self.assertEqual(m.feuerix_version, "1.59.0")
        self.assertEqual(m.fortschritt, 100)

    def test_fehler_vor_dem_neustart_laesst_die_instanz_laufen(self):
        from . import betrieb
        m = _m("f2", "1.58.0")
        with patch("apps.mandanten.betrieb.backup", side_effect=BereitstellungsFehler("voll")):
            with self.assertRaises(BereitstellungsFehler):
                betrieb.update_mit_sicherung(m)
        m.refresh_from_db()
        self.assertEqual(m.status, "laeuft")
        self.assertIn("Update fehlgeschlagen bei", m.schritt)

    def test_fehler_nach_dem_neustart_markiert_fehlgeschlagen(self):
        import tempfile
        from . import betrieb
        m = _m("f3", "1.58.0")
        with tempfile.TemporaryDirectory() as tmp, override_settings(INSTANZEN_VERZEICHNIS=tmp), \
             patch("apps.mandanten.betrieb.backup"), \
             patch("apps.mandanten.betrieb.provisioning.stack_hochfahren"), \
             patch("apps.mandanten.betrieb.provisioning.auf_bereitschaft_warten",
                   side_effect=BereitstellungsFehler("Zeitüberschreitung")), \
             patch("apps.mandanten.betrieb.provisioning.proxy_nachziehen_best_effort") as proxy:
            with self.assertRaises(BereitstellungsFehler):
                betrieb.update_mit_sicherung(m)
        m.refresh_from_db()
        self.assertEqual(m.status, "fehlgeschlagen")
        proxy.assert_called_once()

    def test_in_der_warteschlange_vorgemerkte_instanz_wird_angenommen_andere_nicht(self):
        from . import betrieb
        from .basis import WARTESCHLANGE_UPDATE
        vorgemerkt = _m("f4", "1.58.0", status="wird_bereitgestellt", schritt=WARTESCHLANGE_UPDATE)
        with patch("apps.mandanten.betrieb.update_ausrollen"), patch("apps.mandanten.betrieb.backup"):
            betrieb.update_mit_sicherung(vorgemerkt)
        fremd = _m("f5", "1.58.0", status="wird_bereitgestellt", schritt="Feuerix bauen")
        with self.assertRaises(BereitstellungsFehler):
            betrieb.update_mit_sicherung(fremd)

    def test_admin_merkt_update_sofort_vor(self):
        from .basis import WARTESCHLANGE_UPDATE
        self.client.force_login(get_user_model().objects.create_superuser("fx", "f@example.org", "pw"))
        m = _m("f6", "1.58.0")
        with patch("apps.mandanten.tasks.mandant_update_task.delay"):
            self.client.post(f"/admin/mandanten/mandantinstanz/{m.pk}/dienst/aktualisieren/")
        m.refresh_from_db()
        self.assertEqual((m.status, m.schritt), ("wird_bereitgestellt", WARTESCHLANGE_UPDATE))


class EinzelUpdatePullTests(TestCase):
    def test_einzelupdate_zieht_zuerst_den_neuen_code(self):
        from . import tasks
        m = _m("p1", "1.58.0")
        gerufen = []
        version = {"wert": "1.58.0"}

        def git(argumente, was, timeout=90):
            gerufen.append(argumente[0])
            if argumente[0] == "pull":
                version["wert"] = "1.59.0"
            return _git_antworten()(argumente, was, timeout)

        with patch("apps.mandanten.updates._git", side_effect=git), \
             patch("apps.mandanten.provisioning.feuerix_version", side_effect=lambda: version["wert"]), \
             patch("apps.mandanten.betrieb.backup"), \
             patch("apps.mandanten.betrieb.update_ausrollen") as ausrollen:
            tasks.mandant_update_task(m.pk)
        self.assertIn("pull", gerufen)
        ausrollen.assert_called_once()

    def test_ohne_neue_version_kein_pull(self):
        from . import tasks
        m = _m("p2", "1.58.0")
        gerufen = []

        def git(argumente, was, timeout=90):
            gerufen.append(argumente[0])
            return _git_antworten(remote="1.58.0")(argumente, was, timeout)

        with patch("apps.mandanten.updates._git", side_effect=git), \
             patch("apps.mandanten.provisioning.feuerix_version", return_value="1.58.0"), \
             patch("apps.mandanten.betrieb.backup"), patch("apps.mandanten.betrieb.update_ausrollen"):
            tasks.mandant_update_task(m.pk)
        self.assertNotIn("pull", gerufen)

    def test_pull_fehler_bricht_ab_und_laesst_instanz_laufen(self):
        from . import tasks
        m = _m("p3", "1.58.0")
        with patch("apps.mandanten.updates._git", side_effect=BereitstellungsFehler("offline")), \
             patch("apps.mandanten.betrieb.update_ausrollen") as ausrollen:
            tasks.mandant_update_task(m.pk)
        ausrollen.assert_not_called()
        m.refresh_from_db()
        self.assertEqual(m.status, "laeuft")
        self.assertIn("Prüfung fehlgeschlagen", m.protokoll)

    def test_waehrend_eines_rollouts_kein_eigener_pull(self):
        UpdateZustand.objects.update_or_create(pk=1, defaults=dict(rollout_laeuft=True))
        with patch("apps.mandanten.updates._git") as git:
            updates.checkout_aktualisieren()
        git.assert_not_called()
