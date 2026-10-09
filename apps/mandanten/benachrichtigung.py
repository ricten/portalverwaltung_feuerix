"""E-Mail-Benachrichtigungen (im Admin unter "Benachrichtigungen" eingestellt: SMTP-Server, Empfaenger, Ereignisse).
Gesendet wird nur bei Ereignissen, nie bei jedem Check: Ausfall/Wiederkehr von Instanzen und Backup-Ziel, Sicherungsfehler
und neue Feuerix-Version. Ein Fehler beim Versand darf nie den eigentlichen Vorgang stoeren - er wird nur gespeichert."""
from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.utils import timezone

from . import gesamtlog
from .models import Benachrichtigung

EREIGNISSE = {"ausfall": "bei_ausfall", "backup": "bei_backup_fehler", "update": "bei_update"}


def konfig(ereignis=None):
    """Die aktive Konfiguration (mit eingeschaltetem Ereignis) oder None."""
    cfg = Benachrichtigung.objects.filter(pk=1, aktiv=True).first()
    if cfg is None or (ereignis and not getattr(cfg, EREIGNISSE[ereignis])):
        return None
    return cfg


def _verbindung(cfg):
    return get_connection(
        backend="django.core.mail.backends.smtp.EmailBackend", host=cfg.smtp_host, port=cfg.smtp_port,
        username=cfg.smtp_benutzer or None, password=cfg.smtp_passwort or None,
        use_tls=cfg.smtp_sicherheit == "starttls", use_ssl=cfg.smtp_sicherheit == "ssl", timeout=15,
        fail_silently=False)


def _portal_link():
    return f"https://{settings.PORTAL_DOMAIN}/admin/" if settings.PORTAL_DOMAIN else ""


def _verschicken(cfg, betreff, text):
    link = _portal_link()
    body = text + (f"\n\nPortalverwaltung: {link}" if link else "") + "\n\n-- \nAutomatische Nachricht der Portalverwaltung."
    EmailMessage(f"[Portalverwaltung] {betreff}", body, from_email=cfg.absender, to=cfg.empfaenger_liste(),
                 connection=_verbindung(cfg)).send()


def senden(ereignis, betreff, text):
    """Sendet eine Benachrichtigung, falls aktiv und fuer dieses Ereignis eingeschaltet. -> True, wenn versendet."""
    cfg = konfig(ereignis)
    if cfg is None:
        return False
    try:
        _verschicken(cfg, betreff, text)
    except Exception as e:
        Benachrichtigung.objects.filter(pk=1).update(letzter_fehler=f"{type(e).__name__}: {e}"[:500])
        gesamtlog.log(f"E-Mail „{betreff}“ konnte nicht gesendet werden: {type(e).__name__}: {e}",
                      kategorie="E-Mail", ebene="fehler")
        return False
    Benachrichtigung.objects.filter(pk=1).update(letzter_fehler="", letzte_mail_am=timezone.now())
    gesamtlog.log(f"E-Mail „{betreff}“ an {', '.join(cfg.empfaenger_liste())} gesendet.", kategorie="E-Mail")
    return True


def testmail(cfg):
    """Fuer den Admin-Knopf: schickt eine Testnachricht und wirft bei Fehlern die Ausnahme weiter."""
    _verschicken(cfg, "Testnachricht", "Die E-Mail-Benachrichtigung der Portalverwaltung funktioniert.")
    Benachrichtigung.objects.filter(pk=cfg.pk).update(letzter_fehler="", letzte_mail_am=timezone.now())
