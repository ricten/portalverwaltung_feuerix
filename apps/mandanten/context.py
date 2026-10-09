from django.conf import settings
from django.urls import reverse


def portal_version(request):
    return {"portal_version": settings.PORTAL_VERSION}


def update_info(request):
    """Update-Zustand fuer den Hinweis auf Startseite und Instanzliste des Admins (nur dort, nur fuer Staff)."""
    benutzer = getattr(request, "user", None)
    if not (benutzer and benutzer.is_authenticated and benutzer.is_staff):
        return {}
    if request.path not in (reverse("admin:index"), reverse("admin:mandanten_mandantinstanz_changelist")):
        return {}
    from . import updates
    from .models import BackupZiel, MandantInstanz
    offline = list(MandantInstanz.objects.filter(status="laeuft", online=False))
    ziel = BackupZiel.objects.filter(pk=1, aktiv=True).first()
    zeiten = [m.online_geprueft_am for m in offline if m.online_geprueft_am]
    if ziel and ziel.online_geprueft_am:
        zeiten.append(ziel.online_geprueft_am)
    online_info = {"offline": offline, "backupziel_offline": bool(ziel and ziel.online is False),
                   "backupziel_fehler": ziel.online_fehler if ziel else "", "zuletzt": max(zeiten, default=None)}
    return {"update": updates.fuer_anzeige(), "online_info": online_info}
