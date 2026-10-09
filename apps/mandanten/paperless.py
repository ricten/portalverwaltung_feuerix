"""Paperless-ngx je Mandant (Ausbaustufe 2): eigener Docker-Compose-Stack (`<projekt>-paperless`) auf Basis von
`paperless/docker-compose.yml` aus dem Feuerix-Checkout, mit eigener Datenbank, eigenen Geheimnissen und eigenem
Port. Nach dem Start wird ueber die Paperless-API ein Token geholt und in der Feuerix-Instanz des Mandanten als
Paperless-Anbindung hinterlegt (Verwaltung > Paperless-Anbindung), sodass der Verein nichts mehr einrichten muss.
"""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings

from . import basis
from .basis import BereitstellungsFehler


def aktiv(mandant):
    return bool(mandant.paperless_domain)


def _verzeichnis(mandant):
    return basis.instanz_verzeichnis(mandant, "paperless")


def projektname(mandant):
    return f"{mandant.docker_projekt}-paperless"


def url(mandant):
    return f"{basis.url_schema()}://{mandant.paperless_domain}"


_ENV = """\
PAPERLESS_SECRET_KEY={secret_key}
POSTGRES_PASSWORD={postgres_passwort}
PAPERLESS_PORT={port}
PAPERLESS_URL={url}
PAPERLESS_TIME_ZONE=Europe/Berlin
PAPERLESS_OCR_LANGUAGE=deu
PAPERLESS_ADMIN_USER={admin_benutzername}
PAPERLESS_ADMIN_PASSWORD={admin_passwort}
PAPERLESS_ADMIN_MAIL={admin_email}
"""


def env_erzeugen(mandant):
    """Geheimnisse einmalig erzeugen (bei erneutem Aufruf wiederverwenden - die Paperless-Datenbank waere
    sonst mit einer neuen .env nicht mehr kompatibel) und paperless/.env schreiben."""
    geaendert = []
    if not mandant.paperless_secret_key:
        mandant.paperless_secret_key = basis.zufallswert(50)
        geaendert.append("paperless_secret_key")
    if not mandant.paperless_postgres_passwort:
        mandant.paperless_postgres_passwort = basis.zufallswert(32)
        geaendert.append("paperless_postgres_passwort")
    if not mandant.paperless_admin_passwort:
        mandant.paperless_admin_passwort = basis.zufallswert(16)
        geaendert.append("paperless_admin_passwort")
    if not mandant.paperless_port:
        mandant.paperless_port = basis.naechster_freier_port("paperless_port", settings.PAPERLESS_PORT_BASIS)
        geaendert.append("paperless_port")
    if geaendert:
        mandant.save(update_fields=geaendert)
    pfad = _verzeichnis(mandant) / ".env"
    pfad.write_text(_ENV.format(
        secret_key=mandant.paperless_secret_key, postgres_passwort=mandant.paperless_postgres_passwort,
        port=mandant.paperless_port, url=url(mandant), admin_benutzername=mandant.admin_benutzername,
        admin_passwort=mandant.paperless_admin_passwort, admin_email=mandant.admin_email), encoding="utf-8")
    mandant.protokollieren("Paperless: .env erzeugt.")
    return pfad


def _compose_basis(mandant):
    verz = _verzeichnis(mandant)
    env_pfad = verz / ".env"
    if not env_pfad.exists():
        raise BereitstellungsFehler("Paperless: keine .env vorhanden - zuerst env_erzeugen() aufrufen.")
    compose_datei = Path(settings.FEUERIX_REPO_PFAD) / "paperless" / "docker-compose.yml"
    if not compose_datei.exists():
        raise BereitstellungsFehler(f"paperless/docker-compose.yml nicht gefunden unter {compose_datei} - "
                                    "FEUERIX_REPO_PFAD prüfen.")
    (verz / "export").mkdir(exist_ok=True)
    (verz / "consume").mkdir(exist_ok=True)
    # Die Original-Datei bindet ./export und ./consume relativ zum Feuerix-Checkout ein - das waere fuer alle
    # Mandanten dasselbe Verzeichnis. Compose fuehrt Volumes ueber den Ziel-Pfad zusammen, der spaetere Eintrag
    # gewinnt.
    override = verz / "compose.mandant.yml"
    export_pfad = (verz / "export").resolve().as_posix()
    consume_pfad = (verz / "consume").resolve().as_posix()
    override.write_text(
        "services:\n  webserver:\n    volumes:\n"
        f'      - "{export_pfad}:/usr/src/paperless/export"\n'
        f'      - "{consume_pfad}:/usr/src/paperless/consume"\n', encoding="utf-8")
    return ["docker", "compose", "-p", projektname(mandant), "--env-file", str(env_pfad),
            "-f", str(compose_datei), "-f", str(override)], compose_datei.parent


def hochfahren(mandant):
    befehl, cwd = _compose_basis(mandant)
    basis.docker(befehl + ["up", "-d"], "Paperless: docker compose up", cwd=cwd)
    mandant.protokollieren(f"Paperless-Stack '{projektname(mandant)}' gestartet.")


_STEUERN = {"stop": ["stop"], "start": ["up", "-d"], "restart": ["restart"]}


def steuern(mandant, aktion):
    if not aktiv(mandant) or not (_verzeichnis(mandant) / ".env").exists():
        return
    befehl, cwd = _compose_basis(mandant)
    basis.docker(befehl + _STEUERN[aktion], f"Paperless: docker compose {aktion}", timeout=300, cwd=cwd)
    mandant.protokollieren(f"Paperless-Stack '{projektname(mandant)}': {aktion}.")


def abbauen(mandant, daten_loeschen=False):
    if not aktiv(mandant) or not (_verzeichnis(mandant) / ".env").exists():
        return
    befehl, cwd = _compose_basis(mandant)
    basis.docker(befehl + ["down"] + (["-v"] if daten_loeschen else []), "Paperless: docker compose down",
                 timeout=300, cwd=cwd)
    mandant.protokollieren("Paperless-Stack abgebaut" + (" (inkl. Daten)." if daten_loeschen else "."))


def _anfrage(mandant, pfad, **kw):
    kw.setdefault("headers", {})["Host"] = mandant.paperless_domain
    return urllib.request.Request(f"http://127.0.0.1:{mandant.paperless_port}{pfad}", **kw)


def auf_bereitschaft_warten(mandant, timeout_sekunden=420, intervall_sekunden=5):
    """Paperless braucht beim ersten Start (Migrationen, Datenbank, Admin-Anlage) einige Minuten."""
    ablauf = time.monotonic() + timeout_sekunden
    letzter_fehler = None
    while time.monotonic() < ablauf:
        try:
            with urllib.request.urlopen(_anfrage(mandant, "/accounts/login/"), timeout=5) as antwort:
                if antwort.status == 200:
                    mandant.protokollieren("Paperless antwortet.")
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            letzter_fehler = e
        time.sleep(intervall_sekunden)
    raise BereitstellungsFehler(f"Paperless: Zeitüberschreitung nach {timeout_sekunden}s ({letzter_fehler}).")


def token_holen(mandant):
    """API-Token des Paperless-Admins (Paperless liefert bei erneutem Aufruf denselben Token)."""
    daten = json.dumps({"username": mandant.admin_benutzername,
                        "password": mandant.paperless_admin_passwort}).encode("utf-8")
    anfrage = _anfrage(mandant, "/api/token/", data=daten, method="POST",
                       headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(anfrage, timeout=20) as antwort:
            token = json.load(antwort).get("token")
    except (urllib.error.URLError, ConnectionError, TimeoutError, ValueError) as e:
        raise BereitstellungsFehler(f"Paperless: API-Token konnte nicht geholt werden ({e}).")
    if not token:
        raise BereitstellungsFehler("Paperless: Antwort ohne Token.")
    return token


def in_feuerix_eintragen(mandant, token, feuerix_compose_basis):
    """Traegt die Paperless-Anbindung in der Feuerix-Instanz des Mandanten ein (Skript ueber stdin, damit der
    Token nicht in der Prozessliste auftaucht)."""
    skript = (
        "from apps.core.models import Verein\n"
        "from apps.paperless.models import PaperlessVerbindung\n"
        "v = Verein.objects.order_by('pk').first()\n"
        "PaperlessVerbindung.objects.update_or_create(verein=v, defaults=dict("
        f"url={url(mandant)!r}, api_token={token!r}, aktiv=True, tls_pruefen={not settings.PROXY_NUR_HTTP!r}))\n")
    befehl = feuerix_compose_basis + [
        "exec", "-T", "web", "python", "-c",
        "import os, django; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings'); "
        "django.setup(); exec(open(0).read())"]
    basis.docker(befehl, "Paperless: Anbindung in Feuerix eintragen", timeout=120, eingabe=skript)
    mandant.protokollieren("Paperless-Anbindung in Feuerix hinterlegt.")
