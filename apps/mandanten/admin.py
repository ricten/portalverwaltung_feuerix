from django.contrib import admin, messages

from django.utils.html import format_html

from . import betrieb, tasks
from .models import MandantInstanz


@admin.register(MandantInstanz)
class MandantInstanzAdmin(admin.ModelAdmin):
    list_display = ("kuerzel", "verein_name", "domain", "status", "erreichbarkeit", "feuerix_version",
                    "erstellt_am")
    list_filter = ("status",)
    search_fields = ("kuerzel", "verein_name", "domain", "paperless_domain", "openslides_domain")
    readonly_fields = ("web_port", "paperless_port", "openslides_port", "status", "protokoll",
                       "feuerix_version", "erstellt_am", "aktualisiert_am")
    actions = ["bereitstellen", "pausieren", "sichern", "aktualisieren"]

    @admin.display(description="Erreichbarkeit (live)")
    def erreichbarkeit(self, obj):
        if obj.status != "laeuft":
            return "-"
        dienste = betrieb.gesundheit(obj, timeout=2)
        return format_html(" ".join(
            '<span style="color:{}">{} {}</span>' for _ in dienste
        ), *[x for d, ok in dienste.items() for x in ("green" if ok else "red", "●" if ok else "✖", d)])

    def get_fields(self, request, obj=None):
        pflichtfelder = ["kuerzel", "verein_name", "domain", "admin_benutzername", "admin_email",
                         "paperless_domain", "openslides_domain"]
        if obj is None:
            return pflichtfelder
        return pflichtfelder + ["web_port", "paperless_port", "openslides_port", "status", "protokoll",
                                "feuerix_version", "erstellt_am", "aktualisiert_am"]

    def _starten(self, request, queryset, task, text):
        for m in queryset:
            task.delay(m.pk)
        self.message_user(request, f"{text} für {queryset.count()} Instanz(en) gestartet - Ergebnis im "
                                   "Protokoll der Instanz.", messages.INFO)

    @admin.action(description="Bereitstellen / erneut bereitstellen")
    def bereitstellen(self, request, queryset):
        self._starten(request, queryset, tasks.mandant_bereitstellen_task, "Bereitstellung")

    @admin.action(description="Pausieren (Daten bleiben erhalten)")
    def pausieren(self, request, queryset):
        self._starten(request, queryset, tasks.mandant_pausieren_task, "Pausieren")

    @admin.action(description="Datenbank und Medien sichern")
    def sichern(self, request, queryset):
        self._starten(request, queryset, tasks.mandant_backup_task, "Sicherung")

    @admin.action(description="Auf aktuellen Feuerix-Code aktualisieren")
    def aktualisieren(self, request, queryset):
        self._starten(request, queryset, tasks.mandant_update_task, "Update")
