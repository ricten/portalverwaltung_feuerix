"""Gesamtlog: zentrale Liste aller Ereignisse (Instanz-Protokolle, Aktionen der Benutzer im Admin, Update-Pruefung und
Rollout, Online-Check, Backup-Ziel, Mails). Schreiben darf nie den eigentlichen Vorgang stoeren."""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

_FEHLER = ("fehler", "fehlgeschlagen", "abgebrochen", "exception", "traceback")
_WARNUNG = ("nicht erreichbar", "offline", "keine aktuelle sicherung", "nicht möglich", "nicht moeglich", "bitte ")


def ebene_erkennen(text):
    klein = text.lower()
    if any(w in klein for w in _FEHLER):
        return "fehler"
    if any(w in klein for w in _WARNUNG):
        return "warnung"
    return "info"


def log(text, quelle="Portal", ebene=None, kategorie="", benutzer=""):
    """Schreibt einen Eintrag in den Gesamtlog (stilles Scheitern, damit der aufrufende Vorgang weiterlaeuft)."""
    from .models import LogEintrag
    try:
        LogEintrag.objects.create(text=str(text)[:4000], quelle=str(quelle)[:40], kategorie=kategorie[:30],
                                  ebene=ebene or ebene_erkennen(str(text)), benutzer=str(benutzer)[:150])
    except Exception:
        pass


def aufraeumen():
    """Loescht Eintraege, die aelter als LOG_AUFBEWAHRUNG_TAGE sind (0 = nie loeschen). -> Anzahl."""
    from .models import LogEintrag
    tage = settings.LOG_AUFBEWAHRUNG_TAGE
    if tage <= 0:
        return 0
    return LogEintrag.objects.filter(zeit__lt=timezone.now() - timedelta(days=tage)).delete()[0]
