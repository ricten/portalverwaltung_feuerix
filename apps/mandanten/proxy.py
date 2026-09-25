"""Reverse-Proxy-Automatisierung (Ausbaustufe 1b): pflegt ein Caddy-Caddyfile mit einem Site-Block je
laufender Mandanten-Instanz und laedt es ueber die Caddy-Admin-API (POST /load) ohne Neustart/Downtime nach.

Jede Aenderung erzeugt die KOMPLETTE Konfiguration aus dem Datenbankstand neu (nur Instanzen mit Status
"laeuft") - das ist idempotent und kann nicht "auseinanderlaufen", auch wenn zwischendurch etwas von Hand
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
    for m in sorted(mandanten, key=lambda x: x.kuerzel):
        for domain in (m.domain, m.paperless_domain, m.openslides_domain):
            if domain:
                try:
                    gueltige_domain(domain)
                except ValidationError:
                    raise ProxyFehler(f"Ungültige Domain bei Instanz '{m.kuerzel}': {domain!r}")
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


def _site(domain, port, unteranweisungen, gzip=False):
    kopf = f"http://{domain} {{" if settings.PROXY_NUR_HTTP else f"{domain} {{"
    zeilen = [kopf]
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
    text = caddyfile_erzeugen(MandantInstanz.objects.filter(status="laeuft", web_port__isnull=False))
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
