"""Reverse-Proxy-Automatisierung (Ausbaustufe 1b): pflegt ein Caddy-Caddyfile mit einem Site-Block je
laufender Mandanten-Instanz und laedt es ueber die Caddy-Admin-API (POST /load) ohne Neustart/Downtime nach.

Jede Aenderung erzeugt die KOMPLETTE Konfiguration aus dem Datenbankstand neu: Instanzen mit Status "laeuft"
werden durchgereicht, Instanzen mit Domain aber ohne laufenden Dienst (neu, gestoppt, pausiert, fehlgeschlagen,
wird bereitgestellt) bekommen eine Wartungsseite (HTTP 503). Faellt ein "laufender" Dienst unerwartet aus
(Upstream 502/503/504), zeigt Caddy dieselbe Wartungsseite - das ist idempotent und kann nicht "auseinanderlaufen", auch wenn zwischendurch etwas von Hand
geloescht wurde. Ohne CADDY_ADMIN_URL ist die Automatisierung aus (dann Reverse Proxy weiter von Hand pflegen).
"""
import urllib.error
import urllib.request

from django.core.exceptions import ValidationError
from django.conf import settings

from .models import MandantInstanz
from .validatoren import gueltige_domain


class ProxyFehler(Exception):
    pass


# Status, in denen die Domains einer Instanz eine Wartungsseite statt des Dienstes ausliefern.
WARTUNGS_STATUS = ("neu", "gestoppt", "pausiert", "fehlgeschlagen", "wird_bereitgestellt")

# Einzeilig, ohne Backtick und ohne geschweifte Klammern (Caddy-Raw-String; "{...}" wuerde Caddy als
# Platzhalter lesen und leeren) - deshalb nur Inline-Styles.
WARTUNGSSEITE = (
    '<!doctype html><html lang="de"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    '<meta name="robots" content="noindex"><title>Wartungsarbeiten</title></head>'
    '<body style="margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;'
    'font-family:system-ui,sans-serif;background:#f4f5f7;color:#222">'
    '<main style="max-width:30rem;margin:1rem;padding:2rem;background:#fff;border-radius:12px;'
    'box-shadow:0 2px 12px rgba(0,0,0,.1);text-align:center">'
    '<h1 style="margin-top:0;font-size:1.5rem">&#128295; Wartungsarbeiten</h1>'
    '<p style="line-height:1.5;color:#555">Dieser Dienst ist vor&uuml;bergehend nicht erreichbar, weil er '
    'gewartet wird oder gerade neu startet.</p>'
    '<p style="line-height:1.5;color:#555">Bitte versuche es in einigen Minuten erneut.</p>'
    '</main></body></html>')

_AUSFALL_SNIPPET = [
    "(ausfall) {",
    "	handle_errors {",
    "		@ausfall expression {http.error.status_code} in [502, 503, 504]",
    "		handle @ausfall {",
    '			header Content-Type "text/html; charset=utf-8"',
    '			header Retry-After "300"',
    f"			respond `{WARTUNGSSEITE}` 503",
    "		}",
    "	}",
    "}",
]


def caddyfile_erzeugen(mandanten):
    """Erzeugt das Caddyfile. Standard: Site-Block ohne Schema -> Caddy holt automatisch ein Let's-Encrypt-
    Zertifikat und leitet HTTP auf HTTPS um. PROXY_NUR_HTTP=1 (nur fuer Tests/lokal): reines HTTP ohne
    Zertifikate, dabei wird X-Forwarded-Proto: https gesetzt, damit Feuerix (SECURE_SSL_REDIRECT) nicht
    endlos umleitet."""
    zeilen = ["{"]
    if settings.CADDY_ACME_EMAIL:
        zeilen.append(f"\temail {settings.CADDY_ACME_EMAIL}")
    if settings.PROXY_NUR_HTTP:
        zeilen.append("\tauto_https off")
    zeilen.append("}")
    zeilen += _AUSFALL_SNIPPET
    if settings.PORTAL_DOMAIN:
        try:
            gueltige_domain(settings.PORTAL_DOMAIN)
        except ValidationError:
            raise ProxyFehler(f"Ungültige PORTAL_DOMAIN: {settings.PORTAL_DOMAIN!r}")
        zeilen += _site(settings.PORTAL_DOMAIN, settings.PORTAL_PORT, [], gzip=not settings.PROXY_NUR_HTTP)
    for m in sorted(mandanten, key=lambda x: x.kuerzel):
        for domain in (m.domain, m.paperless_domain, m.openslides_domain):
            if domain:
                try:
                    gueltige_domain(domain)
                except ValidationError:
                    raise ProxyFehler(f"Ungültige Domain bei Instanz '{m.kuerzel}': {domain!r}")
        if m.status in WARTUNGS_STATUS:
            for domain in (m.domain, m.paperless_domain, m.openslides_domain):
                if domain:
                    zeilen += _wartung_site(domain)
            continue
        if m.web_port:
            if settings.PROXY_NUR_HTTP:
                # Feuerix (SECURE_SSL_REDIRECT) erwartet den Proxy als HTTPS-Terminierung
                zeilen += _site(m.domain, m.web_port, ["header_up X-Forwarded-Proto https"])
            else:
                zeilen += _site(m.domain, m.web_port, [], gzip=True)
        if m.paperless_domain and m.paperless_port:
            zeilen += _site(m.paperless_domain, m.paperless_port, [], gzip=not settings.PROXY_NUR_HTTP)
        if m.openslides_domain and m.openslides_port:
            # OpenSlides nutzt Streaming (Autoupdate) - deshalb kein Puffern
            zeilen += _site(m.openslides_domain, m.openslides_port, ["flush_interval -1"])
    return "\n".join(zeilen) + "\n"


def _kopf(domain):
    return f"http://{domain} {{" if settings.PROXY_NUR_HTTP else f"{domain} {{"


def _wartung_site(domain):
    return [_kopf(domain), '	header Content-Type "text/html; charset=utf-8"', '	header Retry-After "300"',
            f"	respond `{WARTUNGSSEITE}` 503", "}"]


def _site(domain, port, unteranweisungen, gzip=False):
    zeilen = [_kopf(domain), "	import ausfall"]
    if gzip:
        zeilen.append("\tencode gzip")
    if unteranweisungen:
        zeilen.append(f"\treverse_proxy 127.0.0.1:{int(port)} {{")
        zeilen += [f"\t\t{a}" for a in unteranweisungen]
        zeilen.append("\t}")
    else:
        zeilen.append(f"\treverse_proxy 127.0.0.1:{int(port)}")
    zeilen.append("}")
    return zeilen


def proxy_aktualisieren():
    """Laedt die aktuelle Konfiguration in Caddy. -> True wenn gesendet, False wenn die Automatisierung aus
    ist (CADDY_ADMIN_URL leer)."""
    if not settings.CADDY_ADMIN_URL:
        return False
    text = caddyfile_erzeugen(MandantInstanz.objects.filter(status__in=("laeuft",) + WARTUNGS_STATUS))
    anfrage = urllib.request.Request(settings.CADDY_ADMIN_URL.rstrip("/") + "/load", data=text.encode("utf-8"),
                                     method="POST", headers={"Content-Type": "text/caddyfile"})
    try:
        with urllib.request.urlopen(anfrage, timeout=15):
            return True
    except urllib.error.HTTPError as e:
        raise ProxyFehler(f"Caddy hat die Konfiguration abgelehnt (HTTP {e.code}): "
                          f"{e.read().decode('utf-8', 'replace')[:500]}")
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise ProxyFehler(f"Caddy-Admin-API unter {settings.CADDY_ADMIN_URL} nicht erreichbar: {e}")
