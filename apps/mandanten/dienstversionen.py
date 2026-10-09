"""Versionen der Zusatzdienste: erfasst, welche Paperless-ngx- und OpenSlides-Version je Instanz laeuft, und fragt die
oeffentlichen GitHub-Releases nach den neuesten Versionen. Das Portal MELDET nur (Hinweis im Admin, Gesamtlog, Mail) -
aktualisiert wird nichts automatisch."""
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings

from . import benachrichtigung, gesamtlog, paperless
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


def paperless_laufend(mandant, timeout=8):
    """Version einer laufenden Paperless-Instanz aus dem Antwort-Header `X-Version` (Paperless-ngx setzt ihn auf API-
    Antworten, auch auf 401/403). Leer, wenn nicht erreichbar oder der Header fehlt."""
    if not (mandant.paperless_domain and mandant.paperless_port):
        return ""
    try:
        with urllib.request.urlopen(paperless._anfrage(mandant, "/api/"), timeout=timeout) as antwort:
            kopf = antwort.headers
    except urllib.error.HTTPError as e:
        kopf = e.headers
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return ""
    return (kopf.get("X-Version") or "").lstrip("vV").strip()[:30]


def openslides_konfiguriert(mandant):
    """OpenSlides-Version aus dem `tag:` der erzeugten config.yml der Instanz (leer, wenn nicht vorhanden)."""
    pfad = Path(settings.INSTANZEN_VERZEICHNIS) / mandant.kuerzel / "openslides" / "config.yml"
    try:
        treffer = re.search(r"^\s*tag:\s*['\"]?([^\s'\"#]+)", pfad.read_text(encoding="utf-8"), re.MULTILINE)
    except OSError:
        return ""
    return treffer.group(1).lstrip("vV")[:30] if treffer else ""


def instanzen_erfassen():
    """Aktualisiert paperless_version/openslides_version der Instanzen (nur wenn der Dienst eingerichtet ist)."""
    for m in MandantInstanz.objects.exclude(status__in=("neu", "geloescht")):
        geaendert = []
        if m.paperless_domain and m.status == "laeuft":
            v = paperless_laufend(m)
            if v and v != m.paperless_version:
                m.paperless_version = v
                geaendert.append("paperless_version")
        if m.openslides_domain:
            v = openslides_konfiguriert(m)
            if v and v != m.openslides_version:
                m.openslides_version = v
                geaendert.append("openslides_version")
        if geaendert:
            m.save(update_fields=geaendert)


def pruefen(zustand):
    """Erfasst die laufenden Versionen, holt die neuesten von GitHub und meldet Neues (Gesamtlog, Mail - je Version
    einmal). Aendert nur `zustand` (der Aufrufer speichert) und die Instanzfelder."""
    instanzen_erfassen()
    fehler = []
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
