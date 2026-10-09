"""Update-Pruefung und Rollout fuer Feuerix: vergleicht den Checkout (FEUERIX_REPO_PFAD) mit dem Remote-Stand,
schlaegt bei einer neueren Version den Rollout vor und rollt ihn auf Wunsch nacheinander auf alle laufenden
Instanzen aus (git pull, dann je Instanz Neubau mit Migrationen). Gestartet wird der Rollout nie von allein -
nur die Pruefung laeuft automatisch."""
import re
import time
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from . import basis, benachrichtigung, betrieb, provisioning
from .basis import BereitstellungsFehler
from .models import MandantInstanz, UpdateZustand

MAX_ABSCHNITTE = 12      # so viele Changelog-Abschnitte werden maximal angezeigt
ROLLOUT_VERFALL = timedelta(hours=2)   # ein haengengebliebener Rollout-Lock wird danach ignoriert


def versionstupel(version):
    try:
        return tuple(int(teil) for teil in version.strip().split("."))
    except ValueError:
        return ()


def _git(argumente, was, timeout=90):
    return basis.docker(["git"] + argumente, was, timeout=timeout, cwd=Path(settings.FEUERIX_REPO_PFAD))


def aenderungen_auszug(changelog, lokale_version):
    """Die Changelog-Abschnitte (`## x.y.z`), die neuer als `lokale_version` sind."""
    lokal = versionstupel(lokale_version)
    abschnitte, aktuell = [], None
    for zeile in changelog.splitlines():
        treffer = re.match(r"##\s+(\d+(?:\.\d+)*)", zeile)
        if treffer:
            aktuell = [zeile.strip()] if versionstupel(treffer.group(1)) > lokal else None
            if aktuell is not None:
                abschnitte.append(aktuell)
        elif aktuell is not None and zeile.strip():
            aktuell.append(zeile.rstrip())
    return "\n\n".join("\n".join(a) for a in abschnitte[:MAX_ABSCHNITTE])


def pruefen():
    """Holt den Remote-Stand (git fetch) und speichert Versionen und Aenderungen. Fehler landen im Zustand."""
    zustand = UpdateZustand.holen()
    zustand.lokale_version = provisioning.feuerix_version()
    try:
        _git(["fetch", "--quiet"], "git fetch im Feuerix-Checkout")
        upstream = _git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
                        "Upstream-Zweig ermitteln").strip()
        zustand.remote_version = _git(["show", f"{upstream}:VERSION"], "Remote-Version lesen").strip()[:20]
        try:
            changelog = _git(["show", f"{upstream}:CHANGELOG.md"], "Remote-Changelog lesen")
        except BereitstellungsFehler:
            changelog = ""
        zustand.aenderungen = (aenderungen_auszug(changelog, zustand.lokale_version)
                               if zustand.update_verfuegbar else "")
        zustand.fehler = ""
    except BereitstellungsFehler as e:
        zustand.fehler = str(e)[:1000]
    zustand.geprueft_am = timezone.now()
    if (zustand.update_verfuegbar and not zustand.fehler and zustand.remote_version != zustand.update_mail_version
            and benachrichtigung.senden(
                "update", f"Feuerix {zustand.remote_version} verfügbar",
                f"Es gibt eine neue Feuerix-Version: {zustand.remote_version} (installiert: {zustand.lokale_version}).\n\n"
                f"{zustand.aenderungen[:1500]}\n\nDas Update starten Sie im Admin mit \"Update jetzt ausrollen\".")):
        zustand.update_mail_version = zustand.remote_version
    zustand.save()
    return zustand


def fuer_anzeige(anfordern=True):
    """Zustand fuer die Weboberflaeche; stoesst bei veralteter Pruefung (UPDATE_PRUEFUNG_STUNDEN) im Hintergrund
    eine neue an. Ein nicht erreichbarer Task-Broker darf die Oberflaeche nie beeintraechtigen."""
    zustand = UpdateZustand.holen()
    stunden = settings.UPDATE_PRUEFUNG_STUNDEN
    if anfordern and stunden > 0 and not zustand.rollout_laeuft:
        jetzt = timezone.now()
        veraltet = zustand.geprueft_am is None or jetzt - zustand.geprueft_am > timedelta(hours=stunden)
        kuerzlich = zustand.angefordert_am and jetzt - zustand.angefordert_am < timedelta(minutes=10)
        if veraltet and not kuerzlich:
            UpdateZustand.objects.filter(pk=1).update(angefordert_am=jetzt)
            try:
                from . import tasks
                tasks.update_pruefen_task.delay()
            except Exception:
                pass
    return zustand


def checkout_aktualisieren():
    """Fuer das Update einer einzelnen Instanz: holt den neuen Feuerix-Code (git pull --ff-only), falls der Remote
    neuer ist - sonst wuerde der Neubau nur die alte Version erzeugen. Laeuft gerade ein Gesamt-Rollout, zieht der
    selbst. Die uebrigen Instanzen bleiben dabei auf der alten Version und erscheinen weiter im Update-Hinweis."""
    if UpdateZustand.holen().rollout_laeuft:
        return
    zustand = pruefen()
    if zustand.fehler:
        raise BereitstellungsFehler(f"Prüfung fehlgeschlagen: {zustand.fehler}")
    if zustand.update_verfuegbar:
        _git(["pull", "--ff-only"], "git pull im Feuerix-Checkout", timeout=300)
        pruefen()


def ausrollen(pause_sekunden=None):
    """Zieht den neuen Feuerix-Code (git pull --ff-only, falls verfuegbar) und aktualisiert danach nacheinander
    alle laufenden Instanzen, die noch nicht auf der Zielversion sind. Ein Fehler bei einer Instanz stoppt die
    anderen nicht."""
    pause = settings.UPDATE_PAUSE_SEKUNDEN if pause_sekunden is None else pause_sekunden
    UpdateZustand.holen()
    jetzt = timezone.now()
    beansprucht = UpdateZustand.objects.filter(pk=1).filter(
        Q(rollout_laeuft=False) | Q(rollout_gestartet_am__lt=jetzt - ROLLOUT_VERFALL)
    ).update(rollout_laeuft=True, rollout_gestartet_am=jetzt, rollout_text="Vorbereitung", rollout_ergebnis="")
    if not beansprucht:
        raise BereitstellungsFehler("Es läuft bereits ein Rollout.")

    def schritt(text):
        UpdateZustand.objects.filter(pk=1).update(rollout_text=text[:200])

    ergebnis = ""
    try:
        zustand = pruefen()
        if zustand.fehler:
            raise BereitstellungsFehler(f"Prüfung fehlgeschlagen: {zustand.fehler}")
        if zustand.update_verfuegbar:
            schritt("Feuerix-Code aktualisieren (git pull)")
            _git(["pull", "--ff-only"], "git pull im Feuerix-Checkout", timeout=300)
        version = provisioning.feuerix_version()
        ziele = list(MandantInstanz.objects.filter(status__in=("laeuft", "gestoppt")).exclude(feuerix_version=version))
        ok, fehler = [], {}
        for nr, mandant in enumerate(ziele, 1):
            schritt(f"Instanz {nr} von {len(ziele)}: {mandant.kuerzel}"
                   + (" (gestoppt: nur Image bauen)" if mandant.status == "gestoppt"
                      else "" if mandant.sicherung_aktuell else " (Sicherung, dann Update)"))
            try:
                betrieb.update_mit_sicherung(mandant)
                ok.append(mandant.kuerzel)
            except BereitstellungsFehler as e:
                fehler[mandant.kuerzel] = str(e).splitlines()[0][:200]
                mandant.protokollieren(f"Update fehlgeschlagen: {e}")
            if nr < len(ziele) and pause:
                time.sleep(pause)
        ergebnis = f"Feuerix {version}: {len(ok)} von {len(ziele)} Instanz(en) aktualisiert."
        if not ziele:
            ergebnis = f"Feuerix {version}: keine Instanz musste aktualisiert werden."
        if fehler:
            ergebnis += " Fehlgeschlagen: " + "; ".join(f"{k} ({v})" for k, v in fehler.items())
    except BereitstellungsFehler as e:
        ergebnis = f"Abgebrochen: {str(e)[:500]}"
    finally:
        UpdateZustand.objects.filter(pk=1).update(rollout_laeuft=False, rollout_text="", rollout_ergebnis=ergebnis,
                                                  rollout_beendet_am=timezone.now())
        pruefen()
    return ergebnis
