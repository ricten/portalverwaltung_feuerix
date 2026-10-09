"""Betrieb laufender Mandanten-Instanzen: Sicherung, Gesundheitspruefung und Update-Rollout."""
import time
import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings

from . import basis, openslides, paperless, provisioning
from .basis import BereitstellungsFehler
from .models import MandantInstanz


def _ausgangsstatus(mandant):
    """Status, in dem die Instanz vor dem Update war ("laeuft"/"gestoppt"), auch wenn sie fuer das Update schon
    als "in der Warteschlange" vorgemerkt ist; sonst None (Update nicht moeglich)."""
    if mandant.status in ("laeuft", "gestoppt"):
        return mandant.status
    if mandant.status == "wird_bereitgestellt":
        if mandant.schritt == basis.WARTESCHLANGE_UPDATE:
            return "laeuft"
        if mandant.schritt == basis.WARTESCHLANGE_UPDATE_GESTOPPT:
            return "gestoppt"
    return None


def backup(mandant, _pruefen=True):
    """Sichert Datenbank und Medien ueber Feuerix' scripts/backup.sh und kopiert die Sicherungen aus dem
    Container nach `<instanz>/backups` (eine Sicherung im selben Volume waere bei Datenverlust mit weg).
    Der FIELD_ENCRYPTION_KEY liegt verschluesselt in der Portalverwaltungs-Datenbank - deren Sicherung (samt
    FIELD_ENCRYPTION_KEY der Portalverwaltung) bleibt Sache des Betreibers."""
    if _pruefen and mandant.status != "laeuft":
        raise BereitstellungsFehler(f"Sicherung nur fuer laufende Instanzen moeglich (Status: {mandant.status}).")
    compose = provisioning._compose_basis(mandant)
    basis.docker(compose + ["exec", "-T", "web", "sh", "/app/scripts/backup.sh"], "Sicherung", timeout=3600)
    ziel = basis.instanz_verzeichnis(mandant, "backups")
    basis.docker(compose + ["cp", "web:/data/backups/.", str(ziel)], "Sicherung herauskopieren", timeout=3600)
    mandant.protokollieren(f"Sicherung erstellt und nach {ziel} kopiert.")
    return ziel


def _antwortet(port, host, pfad, https_header=False, timeout=5):
    kopf = {"Host": host}
    if https_header:
        kopf["X-Forwarded-Proto"] = "https"
    anfrage = urllib.request.Request(f"http://127.0.0.1:{port}{pfad}", headers=kopf)
    try:
        with urllib.request.urlopen(anfrage, timeout=timeout) as antwort:
            return antwort.status == 200
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        return False


def gesundheit(mandant, timeout=5):
    """-> Dict {Dienst: True/False} fuer alle konfigurierten Dienste der Instanz."""
    ergebnis = {}
    if mandant.web_port:
        ergebnis["feuerix"] = _antwortet(mandant.web_port, mandant.domain, "/login/", https_header=True,
                                       timeout=timeout)
    if mandant.paperless_domain and mandant.paperless_port:
        ergebnis["paperless"] = _antwortet(mandant.paperless_port, mandant.paperless_domain, "/accounts/login/",
                                          timeout=timeout)
    if mandant.openslides_domain and mandant.openslides_port:
        ergebnis["openslides"] = _antwortet(mandant.openslides_port, mandant.openslides_domain, "/", timeout=timeout)
    return ergebnis


def update_ausrollen(mandant, _pruefen=True):
    """Baut die Feuerix-Instanz mit dem aktuellen Code des Checkouts neu (Migrationen laufen beim Start
    automatisch) und wartet, bis sie wieder antwortet. Meldet den Fortschritt (Spalte "Fortschritt")."""
    if _pruefen and mandant.status != "laeuft":
        raise BereitstellungsFehler(f"Update nur fuer laufende Instanzen moeglich (Status: {mandant.status}).")
    basis.fortschritt(mandant, 40, "Update: Feuerix bauen und starten (kann einige Minuten dauern)")
    provisioning.stack_hochfahren(mandant)
    basis.fortschritt(mandant, 75, "Update: warten auf Migration und Neustart")
    provisioning.auf_bereitschaft_warten(mandant)
    mandant.feuerix_version = provisioning.feuerix_version()
    mandant.save(update_fields=["feuerix_version"])
    mandant.protokollieren(f"Update auf Feuerix {mandant.feuerix_version or '?'} ausgerollt.")
    basis.fortschritt(mandant, 100, "Fertig")


def stoppen(mandant):
    """Haelt alle Container der Instanz (Feuerix, Paperless, OpenSlides) an - Daten und Container bleiben
    erhalten - und nimmt sie aus dem Reverse Proxy. Schneller als Stilllegen (Abbau) und mit `starten` sofort
    wieder da. Meldet den Fortschritt (Spalte "Fortschritt")."""
    mandant.status = "wird_bereitgestellt"
    mandant.save(update_fields=["status"])
    try:
        if openslides.aktiv(mandant):
            basis.fortschritt(mandant, 10, "OpenSlides stoppen")
            openslides.steuern(mandant, "stop")
        if paperless.aktiv(mandant):
            basis.fortschritt(mandant, 40, "Paperless stoppen")
            paperless.steuern(mandant, "stop")
        basis.fortschritt(mandant, 70, "Feuerix stoppen")
        provisioning.stack_steuern(mandant, "stop")
    except BereitstellungsFehler as e:
        # Teilweise gestoppt: Zustand unklar, deshalb "fehlgeschlagen" (Wartungsseite) statt "laeuft"
        mandant.status = "fehlgeschlagen"
        mandant.schritt = f"Stoppen fehlgeschlagen bei: {mandant.schritt}"[:200]
        mandant.save(update_fields=["status", "schritt"])
        mandant.protokollieren(f"Fehler beim Stoppen: {e}")
        provisioning.proxy_nachziehen_best_effort(mandant)
        raise
    basis.fortschritt(mandant, 95, "Reverse Proxy aktualisieren")
    mandant.status = "gestoppt"
    mandant.fortschritt, mandant.schritt = 0, ""
    mandant.save(update_fields=["status", "fortschritt", "schritt"])
    provisioning._proxy_nachziehen(mandant)


def _hochfahren(mandant, aktion, text):
    """Gemeinsamer Ablauf fuer Starten/Neustarten: Stacks steuern, auf Bereitschaft warten, Proxy nachziehen."""
    mandant.status = "wird_bereitgestellt"
    mandant.save(update_fields=["status"])
    try:
        basis.fortschritt(mandant, 10, f"Feuerix: {text}")
        provisioning.stack_steuern(mandant, aktion)
        basis.fortschritt(mandant, 30, "Feuerix: warten, bis es antwortet")
        provisioning.auf_bereitschaft_warten(mandant)
        if paperless.aktiv(mandant):
            basis.fortschritt(mandant, 55, f"Paperless: {text}")
            paperless.steuern(mandant, aktion)
            paperless.auf_bereitschaft_warten(mandant)
        if openslides.aktiv(mandant):
            basis.fortschritt(mandant, 80, f"OpenSlides: {text}")
            openslides.steuern(mandant, aktion)
            openslides.auf_bereitschaft_warten(mandant)
        basis.fortschritt(mandant, 97, "Reverse Proxy eintragen")
        mandant.status = "laeuft"
        mandant.save(update_fields=["status"])
        provisioning._proxy_nachziehen(mandant)
        basis.fortschritt(mandant, 100, "Fertig")
    except BereitstellungsFehler as e:
        mandant.status = "fehlgeschlagen"
        mandant.schritt = f"Fehlgeschlagen bei: {mandant.schritt}"[:200]
        mandant.save(update_fields=["status", "schritt"])
        mandant.protokollieren(f"Fehler: {e}")
        provisioning.proxy_nachziehen_best_effort(mandant)
        raise


def starten(mandant):
    """Startet eine gestoppte oder stillgelegte Instanz (ohne Neubau) und traegt sie wieder im Proxy ein."""
    _hochfahren(mandant, "start", "starten")


def neustarten(mandant):
    """Startet alle Container der Instanz neu (ohne Neubau)."""
    _hochfahren(mandant, "restart", "neu starten")


def update_mit_sicherung(mandant, vorher=None):
    """Aktualisiert eine Instanz mit sichtbarem Fortschritt (Status "wird_bereitgestellt" = In Arbeit).
    Laufende Instanz: prueft zuerst, ob eine aktuelle Sicherung existiert (UPDATE_BACKUP_MAX_ALTER_STUNDEN), legt bei
    Bedarf eine an und baut/startet dann neu; schlaegt die Sicherung fehl, wird NICHT aktualisiert.
    Gestoppte Instanz: baut nur das Image neu und bleibt gestoppt (kein Datenzugriff, deshalb ohne Sicherung).
    `vorher` (optional) wird zuerst aufgerufen, z. B. um den Feuerix-Code zu ziehen."""
    ausgang = _ausgangsstatus(mandant)
    if ausgang is None:
        raise BereitstellungsFehler(f"Update nur fuer laufende oder gestoppte Instanzen moeglich "
                                    f"(Status: {mandant.status}).")
    mandant.status = "wird_bereitgestellt"
    mandant.save(update_fields=["status"])
    try:
        if vorher:
            basis.fortschritt(mandant, 3, "Update: neuen Code holen (git pull)")
            vorher()
        if ausgang == "gestoppt":
            basis.fortschritt(mandant, 40, "Update: Image neu bauen (Instanz bleibt gestoppt)")
            provisioning.stack_bauen(mandant)
            mandant.feuerix_version = provisioning.feuerix_version()
            mandant.save(update_fields=["feuerix_version"])
            mandant.protokollieren(f"Update auf Feuerix {mandant.feuerix_version or '?'} vorbereitet "
                                   "(Image gebaut, Instanz bleibt gestoppt).")
        else:
            basis.fortschritt(mandant, 5, "Update: Sicherung prüfen")
            if not mandant.sicherung_aktuell:
                basis.fortschritt(mandant, 10, "Update: Sicherung anlegen (keine aktuelle vorhanden)")
                mandant.protokollieren("Keine aktuelle Sicherung vorhanden - lege vor dem Update eine an.")
                try:
                    backup(mandant, _pruefen=False)
                except BereitstellungsFehler as e:
                    mandant.protokollieren(f"Update abgebrochen: Sicherung fehlgeschlagen ({e})")
                    raise BereitstellungsFehler(f"Sicherung vor dem Update fehlgeschlagen: {e}")
            update_ausrollen(mandant, _pruefen=False)
    except BereitstellungsFehler:
        # Laufende Instanz: ab Fortschritt 75 lief der Neustart schon und sie antwortet moeglicherweise nicht mehr;
        # davor laeuft noch der alte Container weiter. Gestoppte Instanz: bleibt gestoppt.
        spaet = ausgang == "laeuft" and mandant.fortschritt >= 75
        mandant.status = "fehlgeschlagen" if spaet else ausgang
        mandant.schritt = f"Update fehlgeschlagen bei: {mandant.schritt}"[:200]
        mandant.save(update_fields=["status", "schritt"])
        if spaet:
            provisioning.proxy_nachziehen_best_effort(mandant)
        raise
    mandant.status = ausgang
    if ausgang == "gestoppt":
        mandant.fortschritt, mandant.schritt = 0, ""
        mandant.save(update_fields=["status", "fortschritt", "schritt"])
    else:
        mandant.save(update_fields=["status"])


def alle_aktualisieren(pause_sekunden=10, git_pull=False):
    """Rollt das Update nacheinander auf alle laufenden Instanzen aus (gestaffelt, ein Fehler stoppt die
    anderen nicht). -> (erfolgreich, {kuerzel: Fehlertext})"""
    if git_pull:
        basis.docker(["git", "pull", "--ff-only"], "git pull im Feuerix-Checkout", timeout=300,
                     cwd=Path(settings.FEUERIX_REPO_PFAD))
    ok, fehler = [], {}
    for m in MandantInstanz.objects.filter(status="laeuft"):
        try:
            update_ausrollen(m)
            ok.append(m.kuerzel)
        except BereitstellungsFehler as e:
            fehler[m.kuerzel] = str(e)
            m.protokollieren(f"Update fehlgeschlagen: {e}")
        time.sleep(pause_sekunden)
    return ok, fehler


def admin_passwort_zuruecksetzen(mandant):
    """Setzt das Passwort des Instanz-Administrators in der laufenden Feuerix-Instanz neu (legt den Benutzer bei
    Bedarf an) und speichert es verschluesselt. -> das neue Passwort (wird nur einmal angezeigt)."""
    if mandant.status != "laeuft":
        raise BereitstellungsFehler(f"Passwort-Reset nur fuer laufende Instanzen moeglich (Status: {mandant.status}).")
    neu = basis.zufallswert(16)
    skript = "\n".join([
        "from django.contrib.auth import get_user_model",
        "U = get_user_model()",
        f"u = U.objects.filter(username={mandant.admin_benutzername!r}).first()",
        "if u is None:",
        f"    u = U.objects.create_superuser({mandant.admin_benutzername!r}, {mandant.admin_email!r}, {neu!r})",
        "else:",
        f"    u.set_password({neu!r}); u.is_active = True; u.save()",
        ""])
    befehl = provisioning._compose_basis(mandant) + [
        "exec", "-T", "web", "python", "-c",
        "import os, django; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings'); "
        "django.setup(); exec(open(0).read())"]
    basis.docker(befehl, "Admin-Passwort zuruecksetzen", timeout=120, eingabe=skript)
    mandant.admin_passwort = neu
    mandant.save(update_fields=["admin_passwort"])
    mandant.protokollieren("Passwort des Instanz-Administrators zurückgesetzt.")
    return neu
