"""Regelmaessiger Online-Check: prueft, ob die laufenden Instanzen (Feuerix, Paperless, OpenSlides) und das Backup-Ziel
erreichbar sind, speichert das Ergebnis samt "offline seit" und protokolliert Wechsel. Aufgerufen wird er von einem
systemd-Timer (`manage.py mandant_online_check`, vom Installer eingerichtet) oder per Hand."""
import ntpath

from django.utils import timezone

from . import backupziel, betrieb
from .basis import BereitstellungsFehler
from .models import BackupZiel, MandantInstanz


def instanz_pruefen(mandant, timeout=5):
    """-> (online, [ausgefallene Dienste]) und aktualisiert Zustand/Protokoll der Instanz."""
    jetzt = timezone.now()
    dienste = betrieb.gesundheit(mandant, timeout=timeout)
    ausgefallen = sorted(d for d, ok in dienste.items() if not ok)
    online = bool(dienste) and not ausgefallen
    vorher = mandant.online
    if online:
        if vorher is False:
            seit = mandant.offline_seit.strftime("%d.%m.%Y %H:%M") if mandant.offline_seit else "?"
            mandant.protokollieren(f"Wieder erreichbar (war offline seit {seit}).")
        mandant.offline_seit = None
    else:
        if vorher is not False:
            mandant.offline_seit = jetzt
            mandant.protokollieren("Nicht erreichbar: " + (", ".join(ausgefallen) or "kein Dienst konfiguriert") + ".")
    mandant.online = online
    mandant.online_geprueft_am = jetzt
    mandant.save(update_fields=["online", "online_geprueft_am", "offline_seit"])
    return online, ausgefallen


def backupziel_pruefen():
    """Prueft nur die Erreichbarkeit (anmelden, Freigabe lesen) - ohne zu schreiben. -> True/False/None (nicht aktiv)."""
    ziel = BackupZiel.objects.filter(pk=1).first()
    if ziel is None:
        return None
    if not ziel.aktiv:
        BackupZiel.objects.filter(pk=1).update(online=None)
        return None
    try:
        smb = backupziel._smb()
        backupziel._verbinden(ziel, smb)
        list(smb.scandir(ntpath.join(f"\\\\{ziel.server}", ziel.freigabe)))
        online, fehler = True, ""
    except Exception as e:
        online, fehler = False, f"{type(e).__name__}: {e}"[:500]
    BackupZiel.objects.filter(pk=1).update(online=online, online_geprueft_am=timezone.now(), online_fehler=fehler)
    return online


def alle_pruefen(timeout=5):
    """-> {"instanzen": {kuerzel: (online, ausgefallen)}, "backupziel": True/False/None}"""
    ergebnis = {}
    for m in MandantInstanz.objects.filter(status="laeuft"):
        ergebnis[m.kuerzel] = instanz_pruefen(m, timeout=timeout)
    # Instanzen, die nicht laufen, haben keinen Online-Zustand
    MandantInstanz.objects.exclude(status="laeuft").exclude(online__isnull=True).update(
        online=None, offline_seit=None)
    return {"instanzen": ergebnis, "backupziel": backupziel_pruefen()}
