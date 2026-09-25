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


def _antwortet(port, host, pfad, https_header=False):
    kopf = {"Host": host}
    if https_header:
        kopf["X-Forwarded-Proto"] = "https"
    anfrage = urllib.request.Request(f"http://127.0.0.1:{port}{pfad}", headers=kopf)
    try:
        with urllib.request.urlopen(anfrage, timeout=5) as antwort:
            return antwort.status == 200
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        return False


def gesundheit(mandant):
    """-> Dict {Dienst: True/False} fuer alle konfigurierten Dienste der Instanz."""
    ergebnis = {}
    if mandant.web_port:
        ergebnis["feuerix"] = _antwortet(mandant.web_port, mandant.domain, "/login/", https_header=True)
    if mandant.paperless_domain and mandant.paperless_port:
        ergebnis["paperless"] = _antwortet(mandant.paperless_port, mandant.paperless_domain, "/accounts/login/")
    if mandant.openslides_domain and mandant.openslides_port:
        ergebnis["openslides"] = _antwortet(mandant.openslides_port, mandant.openslides_domain, "/")
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
