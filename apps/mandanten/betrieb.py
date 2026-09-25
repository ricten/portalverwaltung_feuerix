"""Betrieb laufender Mandanten-Instanzen: Sicherung, Gesundheitspruefung und Update-Rollout."""
import time
import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings

from . import basis, provisioning
from .basis import BereitstellungsFehler
from .models import MandantInstanz


def backup(mandant):
    """Sichert Datenbank und Medien ueber Feuerix' scripts/backup.sh und kopiert die Sicherungen aus dem
    Container nach `<instanz>/backups` (eine Sicherung im selben Volume waere bei Datenverlust mit weg).
    Der FIELD_ENCRYPTION_KEY liegt verschluesselt in der Portalverwaltungs-Datenbank - deren Sicherung (samt
    FIELD_ENCRYPTION_KEY der Portalverwaltung) bleibt Sache des Betreibers."""
    if mandant.status != "laeuft":
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


def update_ausrollen(mandant):
    """Baut die Feuerix-Instanz mit dem aktuellen Code des Checkouts neu (Migrationen laufen beim Start
    automatisch) und wartet, bis sie wieder antwortet."""
    if mandant.status != "laeuft":
        raise BereitstellungsFehler(f"Update nur fuer laufende Instanzen moeglich (Status: {mandant.status}).")
    provisioning.stack_hochfahren(mandant)
    provisioning.auf_bereitschaft_warten(mandant)
    mandant.feuerix_version = provisioning.feuerix_version()
    mandant.save(update_fields=["feuerix_version"])
    mandant.protokollieren(f"Update auf Feuerix {mandant.feuerix_version or '?'} ausgerollt.")


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
