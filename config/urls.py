from django.contrib import admin
from django.urls import path

from apps.mandanten import zweifaktor
from django.views.generic import RedirectView

urlpatterns = [
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
    path("zwei-faktor/", zweifaktor.verwalten, name="zweifaktor_status"),
    path("zwei-faktor/pruefen/", zweifaktor.pruefen, name="zweifaktor_pruefen"),
    path("zwei-faktor/einrichten/", zweifaktor.einrichten, name="zweifaktor_einrichten"),
    path("admin/", admin.site.urls),
]
