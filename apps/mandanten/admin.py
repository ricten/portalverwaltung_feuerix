from django import forms
from django.contrib import admin, messages
from django.http import JsonResponse
from django.urls import path, reverse
from django.utils.html import format_html

from . import betrieb, tasks
from .basis import BereitstellungsFehler
from .models import MandantInstanz


class MandantForm(forms.ModelForm):
    """Haken fuer Zusatzdienste: ohne eigene Domain wird `paperless.<domain>` bzw. `openslides.<domain>` genommen."""
    paperless_installieren = forms.BooleanField(
        label="Paperless-ngx installieren", required=False,
        help_text="Dokumentenmanagement. Bei bereits laufender Instanz wird es nachinstalliert. Entfernen "
                  "geschieht nicht automatisch (Daten bleiben erhalten).")
    openslides_installieren = forms.BooleanField(
        label="OpenSlides installieren", required=False,
        help_text="Digitale Versammlungen/Wahlen. Bei bereits laufender Instanz wird es nachinstalliert.")

    class Meta:
        model = MandantInstanz
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["paperless_installieren"].initial = bool(self.instance.paperless_domain)
            self.fields["openslides_installieren"].initial = bool(self.instance.openslides_domain)

    def clean(self):
        daten = super().clean()
        domain = daten.get("domain")
        for dienst in ("paperless", "openslides"):
            if daten.get(f"{dienst}_installieren") and domain and not daten.get(f"{dienst}_domain"):
                daten[f"{dienst}_domain"] = f"{dienst}.{domain}"
        return daten


@admin.register(MandantInstanz)
class MandantInstanzAdmin(admin.ModelAdmin):
    list_display = ("kuerzel", "verein_name", "domain", "status", "fortschrittsbalken", "erreichbarkeit",
                    "feuerix_version", "erstellt_am")
    list_filter = ("status",)
    search_fields = ("kuerzel", "verein_name", "domain", "paperless_domain", "openslides_domain")
    readonly_fields = ("web_port", "paperless_port", "openslides_port", "status", "fortschrittsbalken",
                       "protokoll", "feuerix_version", "erstellt_am", "aktualisiert_am")
    form = MandantForm
    actions = ["bereitstellen", "pausieren", "sichern", "aktualisieren", "zugangsdaten_anzeigen",
               "admin_passwort_zuruecksetzen"]

    class Media:
        css = {"all": ("mandanten/fortschritt.css",)}
        js = ("mandanten/fortschritt.js",)

    def get_urls(self):
        eigene = [path("<int:pk>/status/", self.admin_site.admin_view(self.status_json), name="mandanten_status")]
        return eigene + super().get_urls()

    def status_json(self, request, pk):
        if not self.has_view_permission(request):
            return JsonResponse({}, status=403)
        m = MandantInstanz.objects.filter(pk=pk).first()
        if m is None:
            return JsonResponse({}, status=404)
        return JsonResponse({"status": m.status, "status_text": m.get_status_display(),
                             "fortschritt": m.fortschritt, "schritt": m.schritt})

    @admin.display(description="Fortschritt")
    def fortschrittsbalken(self, obj):
        if not obj.pk or obj.status in ("neu", "geloescht"):
            return "-"
        url = reverse("admin:mandanten_status", args=[obj.pk])
        text = f"{obj.get_status_display()}" + (f" - {obj.schritt}" if obj.schritt else "") \
            + f" ({obj.fortschritt} %)"
        laeuft = obj.status == "wird_bereitgestellt"
        return format_html(
            '<div class="pv-fortschritt" {}><div class="pv-balken"><div style="width:{}%"></div></div>'
            '<span class="pv-text">{}</span></div>',
            format_html('data-url="{}"', url) if laeuft else "", obj.fortschritt, text)

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
                         "paperless_installieren", "paperless_domain", "openslides_installieren",
                         "openslides_domain"]
        if obj is None:
            return pflichtfelder
        return pflichtfelder + ["web_port", "paperless_port", "openslides_port", "status", "fortschrittsbalken",
                                "protokoll", "feuerix_version", "erstellt_am", "aktualisiert_am"]

    def save_model(self, request, obj, form, change):
        alt = MandantInstanz.objects.filter(pk=obj.pk).first() if change else None
        super().save_model(request, obj, form, change)
        neu = alt is not None and obj.status == "laeuft" and (
            (obj.paperless_domain and not alt.paperless_domain)
            or (obj.openslides_domain and not alt.openslides_domain))
        if neu:
            # laufende Instanz: Zusatzdienst nachinstallieren (Bereitstellen ist idempotent)
            obj.status, obj.fortschritt, obj.schritt = "wird_bereitgestellt", 0, "In der Warteschlange"
            obj.save(update_fields=["status", "fortschritt", "schritt"])
            tasks.mandant_bereitstellen_task.delay(obj.pk)
            self.message_user(request, "Zusatzdienst wird nachinstalliert - Fortschritt siehe Spalte „Fortschritt“.",
                              messages.INFO)

    def _starten(self, request, queryset, task, text):
        for m in queryset:
            task.delay(m.pk)
        self.message_user(request, f"{text} für {queryset.count()} Instanz(en) gestartet - Fortschritt siehe "
                                   "Spalte „Fortschritt“ und Protokoll der Instanz.", messages.INFO)

    @admin.action(description="Bereitstellen / erneut bereitstellen")
    def bereitstellen(self, request, queryset):
        for m in queryset:
            # sofort sichtbar machen, damit die Oberflaeche den Fortschritt live anzeigen kann
            m.status, m.fortschritt, m.schritt = "wird_bereitgestellt", 0, "In der Warteschlange"
            m.save(update_fields=["status", "fortschritt", "schritt"])
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

    @admin.action(description="Zugangsdaten des Instanz-Administrators anzeigen")
    def zugangsdaten_anzeigen(self, request, queryset):
        if not request.user.is_superuser:
            self.message_user(request, "Nur Superuser dürfen Zugangsdaten einsehen.", messages.ERROR)
            return
        for m in queryset:
            if not m.admin_passwort:
                self.message_user(request, f"{m.kuerzel}: noch kein Passwort erzeugt - zuerst bereitstellen.",
                                  messages.WARNING)
                continue
            m.protokollieren(f"Zugangsdaten angezeigt für {request.user.get_username()}.")
            self.message_user(
                request, f"{m.kuerzel}: https://{m.domain}/ - Benutzer „{m.admin_benutzername}“, Passwort: "
                         f"{m.admin_passwort} (Stand der Bereitstellung; nach einer Änderung in Feuerix selbst "
                         "bitte „zurücksetzen“ nutzen).", messages.WARNING)

    @admin.action(description="Admin-Passwort der Instanz zurücksetzen")
    def admin_passwort_zuruecksetzen(self, request, queryset):
        for m in queryset:
            try:
                neu = betrieb.admin_passwort_zuruecksetzen(m)
            except BereitstellungsFehler as e:
                self.message_user(request, f"{m.kuerzel}: {e}", messages.ERROR)
                continue
            self.message_user(
                request, f"{m.kuerzel}: neues Passwort für Benutzer „{m.admin_benutzername}“: {neu} - "
                         "wird nur jetzt angezeigt, bitte sicher weitergeben.", messages.WARNING)
