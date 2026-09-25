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
        try:
            gueltige_domain(m.domain)
        except ValidationError:
            raise ProxyFehler(f"Ungültige Domain bei Instanz '{m.kuerzel}': {m.domain!r}")
        if not m.web_port:
            continue
        if settings.PROXY_NUR_HTTP:
            zeilen += [f"http://{m.domain} {{",
                       f"\treverse_proxy 127.0.0.1:{int(m.web_port)} {{",
                       "\t\theader_up X-Forwarded-Proto https",
                       "\t}", "}"]
        else:
            zeilen += [f"{m.domain} {{", "\tencode gzip",
                       f"\treverse_proxy 127.0.0.1:{int(m.web_port)}", "}"]
    return "\n".join(zeilen) + "\n"


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
