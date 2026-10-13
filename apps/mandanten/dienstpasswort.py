"""Passwoerter von Paperless-ngx und OpenSlides einer Instanz zuruecksetzen (laufende Dienste).

Das neue Passwort wird nie in einer Befehlszeile uebergeben, wo es der Hersteller-Werkzeugaufruf nicht erzwingt: bei Paperless
laeuft ein Skript ueber stdin. Einzige Ausnahme ist `osmanage set-password`, das nur `--password` kennt (der Aufruf dauert
Sekundenbruchteile; die Prozessliste des Servers sollte ohnehin nur vertrauenswuerdigen Benutzern offenstehen)."""
from . import basis, openslides, paperless, provisioning, rechte
from .basis import BereitstellungsFehler

NAMEN = {"paperless": "Paperless", "openslides": "OpenSlides"}


def _voraussetzungen(mandant, dienst):
    if mandant.status != "laeuft":
        raise BereitstellungsFehler(
            f"Passwort-Reset für {NAMEN[dienst]} nur bei laufenden Instanzen möglich (Status: {mandant.status}).")
    if not getattr(mandant, f"{dienst}_domain"):
        raise BereitstellungsFehler(f"{NAMEN[dienst]} ist bei dieser Instanz nicht eingerichtet.")


def paperless_zuruecksetzen(mandant, passwort=None):
    """Setzt das Passwort des Paperless-Administrators (legt ihn bei Bedarf an). -> neues Passwort."""
    _voraussetzungen(mandant, "paperless")
    neu = passwort or basis.zufallswert(16)
    skript = "\n".join([
        "from django.contrib.auth import get_user_model",
        "U = get_user_model()",
        f"u = U.objects.filter(username={mandant.admin_benutzername!r}).first()",
        "if u is None:",
        f"    U.objects.create_superuser({mandant.admin_benutzername!r}, {mandant.admin_email!r}, {neu!r})",
        "else:",
        f"    u.set_password({neu!r}); u.is_active = True; u.save()",
        ""])
    befehl, cwd = paperless._compose_basis(mandant)
    basis.docker(befehl + ["exec", "-T", "-w", "/usr/src/paperless/src", "webserver", "python3", "manage.py", "shell"],
                 "Paperless: Passwort zurücksetzen", timeout=120, cwd=cwd, eingabe=skript)
    mandant.paperless_admin_passwort = neu
    mandant.save(update_fields=["paperless_admin_passwort"])
    paperless.env_erzeugen(mandant)             # .env bleibt zum gespeicherten Wert passend
    mandant.protokollieren("Paperless: Passwort des Administrators zurückgesetzt.")
    return neu


def openslides_zuruecksetzen(mandant, passwort=None):
    """Setzt das Passwort von `superadmin` (Benutzer 1) mit `osmanage set-password` und zieht Datei `secrets/superadmin`
    und die Anbindung in Feuerix nach. -> neues Passwort."""
    _voraussetzungen(mandant, "openslides")
    verz = openslides._verzeichnis(mandant)
    if not (verz / "docker-compose.yml").exists():
        raise BereitstellungsFehler("OpenSlides: Stack-Konfiguration der Instanz nicht gefunden.")
    neu = passwort or basis.zufallswert(16)
    osmanage = openslides.werkzeuge_bereitstellen()[0]
    befehl = [str(osmanage), "set-password", "--address", f"localhost:{openslides.verwaltungsport(mandant)}"]
    if (verz / "secrets" / "internal_auth_password").exists():
        befehl += ["--password-file", "secrets/internal_auth_password"]
    basis.docker(befehl + ["--user_id", "1", "--password", neu], "OpenSlides: Passwort zurücksetzen", timeout=120,
                 cwd=verz, env=openslides._env(mandant))
    rechte.geheim_schreiben(verz / "secrets" / "superadmin", neu)   # einrichten() liest diese Datei wieder ein
    mandant.openslides_superadmin_passwort = neu
    mandant.save(update_fields=["openslides_superadmin_passwort"])
    mandant.protokollieren("OpenSlides: Passwort von superadmin zurückgesetzt.")
    openslides.in_feuerix_eintragen(mandant, provisioning._compose_basis(mandant))
    return neu
