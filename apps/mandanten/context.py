from django.conf import settings


def portal_version(request):
    return {"portal_version": settings.PORTAL_VERSION}
