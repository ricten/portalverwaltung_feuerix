"""Validierung von Werten, die in generierte Konfigurationsdateien fliessen (.env, Caddyfile).

Ohne diese Pruefung waere z. B. ein Zeilenumbruch im Vereinsnamen eine Injection in die .env der Instanz
(zusaetzliche Variablen) bzw. eine Domain mit Leerzeichen/Klammern eine Injection in das Caddyfile."""
import re

from django.core.exceptions import ValidationError

_DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_VERBOTEN = re.compile(r"[\x00-\x1f\x7f#$\"'`\\]")


def gueltige_domain(wert):
    if not _DOMAIN.match(wert or ""):
        raise ValidationError("Ungültige Domain - nur Kleinbuchstaben, Ziffern, Punkte und Bindestriche "
                              "(z. B. verein-x.beispiel.de).")


def keine_env_sonderzeichen(wert):
    if _VERBOTEN.search(wert or ""):
        raise ValidationError("Enthält unzulässige Zeichen (Zeilenumbruch/Steuerzeichen, # $ \" ' ` \\) - "
                              "diese würden die generierte .env-Datei verfälschen.")
