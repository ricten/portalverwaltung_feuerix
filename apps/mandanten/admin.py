from django.contrib import admin

from .models import MandantInstanz


@admin.register(MandantInstanz)
class MandantInstanzAdmin(admin.ModelAdmin):
    list_display = ("kuerzel", "verein_name", "domain", "status", "web_port", "erstellt_am")
    list_filter = ("status",)
    search_fields = ("kuerzel", "verein_name", "domain")
    readonly_fields = ("web_port", "status", "protokoll", "feuerix_version", "erstellt_am", "aktualisiert_am")

    def get_fields(self, request, obj=None):
        pflichtfelder = ["kuerzel", "verein_name", "domain", "admin_benutzername", "admin_email"]
        if obj is None:
            return pflichtfelder
        return pflichtfelder + ["web_port", "status", "protokoll", "feuerix_version", "erstellt_am",
                                "aktualisiert_am"]
