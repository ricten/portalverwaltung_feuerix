"""Regelmaessiger Online-Check: prueft, ob die laufenden Instanzen (Feuerix, Paperless, OpenSlides) und das Backup-Ziel
erreichbar sind, speichert das Ergebnis samt "offline seit" und protokolliert Wechsel. Aufgerufen wird er von einem
systemd-Timer (`manage.py mandant_online_check`, vom Installer eingerichtet) oder per Hand."""
import ntpath
from datetime import timedelta

from django.utils import timezone

from . import backupziel, benachrichtigung, betrieb, gesamtlog
from .basis import BereitstellungsFehler
from .models import BackupZiel, MandantInstanz


def _zeit(dt):
    return dt.strftime("%d.%m.%Y %H:%M") if dt else "?"


def _ausfall_mail_faellig(cfg, offline_seit, schon_gesendet, jetzt):
    return bool(cfg and offline_seit and not schon_gesendet
                and jetzt - offline_seit >= timedelta(minutes=cfg.ausfall_minuten))


def instanz_pruefen(mandant, timeout=5):
    """-> (online, [ausgefallene Dienste]) und aktualisiert Zustand/Protokoll der Instanz; verschickt (falls
    eingerichtet) eine Mail, wenn die Instanz laenger als `ausfall_minuten` offline ist, und eine bei Wiederkehr."""
    jetzt = timezone.now()
    dienste = betrieb.gesundheit(mandant, timeout=timeout)
    ausgefallen = sorted(d for d, ok in dienste.items() if not ok)
    online = bool(dienste) and not ausgefallen
    vorher = mandant.online
    cfg = benachrichtigung.konfig("ausfall")
    if online:
        if vorher is False:
            mandant.protokollieren(f"Wieder erreichbar (war offline seit {_zeit(mandant.offline_seit)}).")
        if mandant.offline_mail_gesendet:
            benachrichtigung.senden(
                "ausfall", f"{mandant.kuerzel} ist wieder erreichbar",
                f"Die Instanz {mandant.verein_name} ({mandant.kuerzel}, {mandant.domain}) ist wieder erreichbar "
                f"(war offline seit {_zeit(mandant.offline_seit)}).")
            mandant.offline_mail_gesendet = False
        mandant.offline_seit = None
    else:
        if vorher is not False:
            mandant.offline_seit = jetzt
            mandant.protokollieren("Nicht erreichbar: " + (", ".join(ausgefallen) or "kein Dienst konfiguriert") + ".")
        if _ausfall_mail_faellig(cfg, mandant.offline_seit, mandant.offline_mail_gesendet, jetzt):
            if benachrichtigung.senden(
                    "ausfall", f"{mandant.kuerzel} ist nicht erreichbar",
                    f"Die Instanz {mandant.verein_name} ({mandant.kuerzel}, {mandant.domain}) ist seit "
                    f"{_zeit(mandant.offline_seit)} nicht erreichbar. Betroffen: "
                    f"{', '.join(ausgefallen) or 'kein Dienst konfiguriert'}."):
                mandant.offline_mail_gesendet = True
    mandant.online = online
    mandant.online_geprueft_am = jetzt
    mandant.save(update_fields=["online", "online_geprueft_am", "offline_seit", "offline_mail_gesendet"])
    return online, ausgefallen


def backupziel_pruefen():
    """Prueft nur die Erreichbarkeit (anmelden, Freigabe lesen) - ohne zu schreiben. -> True/False/None (nicht aktiv)."""
    ziel = BackupZiel.objects.filter(pk=1).first()
    if ziel is None:
        return None
    if not ziel.aktiv:
        BackupZiel.objects.filter(pk=1).update(online=None, offline_seit=None, offline_mail_gesendet=False)
        return None
    jetzt = timezone.now()
    try:
        smb = backupziel._smb()
        backupziel._verbinden(ziel, smb)
        list(smb.scandir(ntpath.join(f"\\\\{ziel.server}", ziel.freigabe)))
        online, fehler = True, ""
    except Exception as e:
        online, fehler = False, f"{type(e).__name__}: {e}"[:500]
    name = f"\\\\{ziel.server}\\{ziel.freigabe}"
    offline_seit, gesendet = ziel.offline_seit, ziel.offline_mail_gesendet
    cfg = benachrichtigung.konfig("ausfall")
    if online:
        if gesendet:
            benachrichtigung.senden("ausfall", "Backup-Ziel ist wieder erreichbar",
                                    f"Das Backup-Ziel {name} ist wieder erreichbar (war offline seit {_zeit(offline_seit)}).")
        offline_seit, gesendet = None, False
    else:
        if ziel.online is not False or offline_seit is None:
            offline_seit = jetzt
        if _ausfall_mail_faellig(cfg, offline_seit, gesendet, jetzt):
            gesendet = benachrichtigung.senden(
                "ausfall", "Backup-Ziel ist nicht erreichbar",
                f"Das Backup-Ziel {name} ist seit {_zeit(offline_seit)} nicht erreichbar: {fehler} "
                "Sicherungen können so nicht abgelegt werden und Updates werden nicht durchgeführt.") or gesendet
    if online and ziel.online is False:
        gesamtlog.log(f"Backup-Ziel {name} wieder erreichbar.", kategorie="Backup-Ziel")
    elif not online and ziel.online is not False:
        gesamtlog.log(f"Backup-Ziel {name} nicht erreichbar: {fehler}", kategorie="Backup-Ziel", ebene="fehler")
    BackupZiel.objects.filter(pk=1).update(online=online, online_geprueft_am=jetzt, online_fehler=fehler,
                                           offline_seit=offline_seit, offline_mail_gesendet=gesendet)
    return online


def alle_pruefen(timeout=5):
    """-> {"instanzen": {kuerzel: (online, ausgefallen)}, "backupziel": True/False/None}"""
    ergebnis = {}
    for m in MandantInstanz.objects.filter(status="laeuft"):
        ergebnis[m.kuerzel] = instanz_pruefen(m, timeout=timeout)
    # Instanzen, die nicht laufen, haben keinen Online-Zustand
    MandantInstanz.objects.exclude(status="laeuft").exclude(online__isnull=True).update(
        online=None, offline_seit=None, offline_mail_gesendet=False)
    gesamtlog.aufraeumen()
    return {"instanzen": ergebnis, "backupziel": backupziel_pruefen()}
