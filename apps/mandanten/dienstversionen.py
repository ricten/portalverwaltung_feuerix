"""Versionen der Zusatzdienste: erfasst, welche Paperless-ngx- und OpenSlides-Version je Instanz laeuft, und fragt die
oeffentlichen GitHub-Releases nach den neuesten Versionen. Das Portal MELDET nur (Hinweis im Admin, Gesamtlog, Mail) -
aktualisiert wird nichts automatisch."""
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings

from . import basis, benachrichtigung, gesamtlog, paperless
from .basis import BereitstellungsFehler
from .models import MandantInstanz

RELEASES = {
    "paperless": "https://api.github.com/repos/paperless-ngx/paperless-ngx/releases/latest",
    "openslides": "https://api.github.com/repos/OpenSlides/OpenSlides/releases/latest",
}
NAMEN = {"paperless": "Paperless-ngx", "openslides": "OpenSlides"}


def _json_holen(url, timeout=10):
    anfrage = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "portalverwaltung-feuerix"})
    with urllib.request.urlopen(anfrage, timeout=timeout) as antwort:
        return json.load(antwort)


def neueste_version(dienst):
    """Neueste stabile Release-Version (ohne fuehrendes "v") oder leer."""
    tag = _json_holen(RELEASES[dienst]).get("tag_name", "")
    return tag.lstrip("vV").strip()[:30]


_PAPERLESS_VERSION = "from paperless import version; print(version.__full_version_str__)"


def paperless_laufend(mandant, timeout=60):
    """Version der laufenden Paperless-Instanz, direkt aus dem Container gelesen (`paperless.version`). Der Header
    `X-Version` taugt nicht: Paperless sendet ihn nur an angemeldete Benutzer. Wirft BereitstellungsFehler, wenn die
    Version nicht ermittelt werden kann (Container laeuft nicht, Ausgabe unerwartet)."""
    befehl, cwd = paperless._compose_basis(mandant)
    ausgabe = basis.docker(befehl + ["exec", "-T", "webserver", "python3", "-c", _PAPERLESS_VERSION],
                           "Paperless: Version lesen", timeout=timeout, cwd=cwd)
    zeilen = [z.strip() for z in ausgabe.splitlines() if z.strip()]
    treffer = re.fullmatch(r"v?(\d+\.\d+\.\d+)", zeilen[-1]) if zeilen else None
    if not treffer:
        raise BereitstellungsFehler(f"Paperless: unerwartete Versionsausgabe: {ausgabe.strip()[:100]!r}")
    return treffer.group(1)


def openslides_konfiguriert(mandant):
    """OpenSlides-Version der Instanz: aus den OpenSlides-Images der erzeugten docker-compose.yml (das ist, was wirklich
    laeuft), sonst aus dem `tag:` der config.yml. Leer, wenn beides fehlt."""
    verz = Path(settings.INSTANZEN_VERZEICHNIS) / mandant.kuerzel / "openslides"
    try:
        treffer = re.search(r"(?m)^\s*image:\s*\S*openslides\S*:v?(\d+\.\d+\.\d+)\s*$",
                            (verz / "docker-compose.yml").read_text(encoding="utf-8"))
        if treffer:
            return treffer.group(1)
    except OSError:
        pass
    try:
        treffer = re.search(r"^\s*tag:\s*['\"]?([^\s'\"#]+)", (verz / "config.yml").read_text(encoding="utf-8"),
                            re.MULTILINE)
    except OSError:
        return ""
    return treffer.group(1).lstrip("vV")[:30] if treffer else ""


def erfassen(mandant, fehler=None):
    """Aktualisiert paperless_version/openslides_version einer Instanz (nur wenn der Dienst eingerichtet ist).
    Gruende, warum eine Version nicht ermittelt werden konnte, werden an die Liste `fehler` angehaengt."""
    geaendert = []
    if mandant.paperless_domain and mandant.status in ("laeuft", "wird_bereitgestellt"):
        try:
            v = paperless_laufend(mandant)
        except BereitstellungsFehler as e:
            v = ""
            if fehler is not None:
                fehler.append(f"{mandant.kuerzel}: {str(e).splitlines()[0][:120]}")
        if v and v != mandant.paperless_version:
            mandant.paperless_version = v
            geaendert.append("paperless_version")
    if mandant.openslides_domain:
        v = openslides_konfiguriert(mandant)
        if v and v != mandant.openslides_version:
            mandant.openslides_version = v
            geaendert.append("openslides_version")
    if geaendert:
        mandant.save(update_fields=geaendert)


def erfassen_sicher(mandant):
    """Wie `erfassen`, aber nie mit Ausnahme: fuer das Ende von Bereitstellung/Start, wo es nur ein Zusatz ist."""
    try:
        erfassen(mandant)
    except Exception:
        pass


def instanzen_erfassen(fehler=None):
    for m in MandantInstanz.objects.exclude(status__in=("neu", "geloescht")):
        erfassen(m, fehler)


def pruefen(zustand):
    """Erfasst die laufenden Versionen, holt die neuesten von GitHub und meldet Neues (Gesamtlog, Mail - je Version
    einmal). Aendert nur `zustand` (der Aufrufer speichert) und die Instanzfelder."""
    fehler = []
    instanzen_erfassen(fehler)
    for dienst in ("paperless", "openslides"):
        feld = f"{dienst}_neueste"
        try:
            neu = neueste_version(dienst)
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError, ValueError) as e:
            fehler.append(f"{NAMEN[dienst]}: {e}")
            continue
        if neu and neu != getattr(zustand, feld):
            setattr(zustand, feld, neu)
    zustand.dienste_fehler = "; ".join(fehler)[:500]
    for hinweis in zustand.dienste_hinweise:
        dienst = hinweis["dienst"]
        mailfeld = f"{dienst}_mail_version"
        if getattr(zustand, mailfeld) == hinweis["neu"]:
            continue
        liste = ", ".join(f"{k} ({v})" for k, v in hinweis["instanzen"])
        gesamtlog.log(f"{hinweis['name']} {hinweis['neu']} verfügbar - veraltet: {liste}.", kategorie="Update")
        benachrichtigung.senden(
            "update", f"{hinweis['name']} {hinweis['neu']} verfügbar",
            f"Es gibt eine neue Version von {hinweis['name']}: {hinweis['neu']}.\n\nInstanzen mit älterer Version: "
            f"{liste}.\n\nAktualisiert wird nicht automatisch: Das Update starten Sie je Instanz im Admin mit dem Pfeil-Knopf in der Spalte „Version“.")
        setattr(zustand, mailfeld, hinweis["neu"])
    return zustand
