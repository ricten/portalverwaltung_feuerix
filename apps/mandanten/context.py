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
    return {"update": updates.fuer_anzeige()}
