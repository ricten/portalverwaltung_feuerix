from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseNotAllowed, HttpResponseRedirect, JsonResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from . import backupziel, benachrichtigung, betrieb, gesamtlog, provisioning, tasks, updates
from .basis import vormerken, WARTESCHLANGE_DIENST, WARTESCHLANGE_UPDATE, WARTESCHLANGE_UPDATE_GESTOPPT, BereitstellungsFehler
from .models import BackupZiel, Benachrichtigung, LogEintrag, MandantInstanz, UpdateZustand


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
                    "steuerung", "version_spalte", "erstellt_am")
    list_filter = ("status",)
    search_fields = ("kuerzel", "verein_name", "domain", "paperless_domain", "openslides_domain")
    readonly_fields = ("web_port", "paperless_port", "openslides_port", "status", "fortschrittsbalken",
                       "protokoll", "feuerix_version", "paperless_version", "openslides_version",
                       "erstellt_am", "aktualisiert_am")
    form = MandantForm
    actions = ["bereitstellen", "starten", "stoppen", "neustarten", "stilllegen", "wiederherstellen", "endgueltig_loeschen", "sichern", "aktualisieren",
               "zugangsdaten_anzeigen", "admin_passwort_zuruecksetzen"]

    # Schaltflaechen in der Zeile: aktion -> (erlaubte Status, Task, Text, SVG-Pfad, Farbklasse, Rueckfrage)
    DIENSTE = {
        "starten": (("gestoppt", "pausiert", "fehlgeschlagen"), tasks.mandant_starten_task, "Starten", "M8 5v14l11-7z",
                    "pv-start", ""),
        "stoppen": (("laeuft", "fehlgeschlagen"), tasks.mandant_stoppen_task, "Stoppen", "M6 6h12v12H6z", "pv-stopp",
                    "Instanz wirklich stoppen? Sie ist danach nicht erreichbar."),
        "neustarten": (("laeuft", "fehlgeschlagen"), tasks.mandant_neustarten_task, "Neustarten", "M17.65 6.35A7.96 7.96 0 0 0 12 4a8 8 0 1 0 7.73 10h-2.08A6 6 0 1 1 12 6c1.66 0 3.14.69 4.22 1.78L13 11h7V4z",
                       "pv-neustart", "Instanz neu starten? Sie ist kurz nicht erreichbar."),
    }

    class Media:
        css = {"all": ("mandanten/fortschritt.css",)}
        js = ("mandanten/fortschritt.js",)

    def get_urls(self):
        eigene = [path("<int:pk>/status/", self.admin_site.admin_view(self.status_json), name="mandanten_status"),
                  path("<int:pk>/wiederherstellen/", self.admin_site.admin_view(self.wiederherstellen_view),
                       name="mandanten_wiederherstellen"),
                  path("<int:pk>/endgueltig-loeschen/", self.admin_site.admin_view(self.loeschen_view),
                       name="mandanten_loeschen"),
                  path("update/pruefen/", self.admin_site.admin_view(self.update_pruefen_view),
                       name="mandanten_update_pruefen"),
                  path("update/ausrollen/", self.admin_site.admin_view(self.update_ausrollen_view),
                       name="mandanten_update_ausrollen"),
                  path("update/status/", self.admin_site.admin_view(self.update_status_view),
                       name="mandanten_update_status"),
                  path("<int:pk>/dienst/<str:aktion>/", self.admin_site.admin_view(self.dienst_view),
                       name="mandanten_dienst")]
        return eigene + super().get_urls()

    def _log(self, request, m, text):
        """Aktion eines Benutzers im Protokoll der Instanz und im Gesamtlog festhalten."""
        benutzer = request.user.get_username()
        m.protokollieren(f"{text} (Benutzer: {benutzer})", benutzer=benutzer)

    def _dienst_update(self, request, pk, dienst):
        """Update von Paperless/OpenSlides einer Instanz (nur auf Klick)."""
        from .dienstversionen import NAMEN
        m = MandantInstanz.objects.filter(pk=pk).first()
        zustand = UpdateZustand.holen()
        liste = HttpResponseRedirect(reverse("admin:mandanten_mandantinstanz_changelist"))
        if m is None or m.status != "laeuft":
            self.message_user(request, "Dienst-Update nur für laufende Instanzen möglich.", messages.WARNING)
        elif zustand.rollout_laeuft:
            self.message_user(request, "Während eines Rollouts ist kein Dienst-Update möglich.", messages.WARNING)
        elif not getattr(zustand, f"{dienst}_neueste"):
            self.message_user(request, "Die neueste Version ist unbekannt - bitte zuerst „Erneut prüfen“.",
                              messages.WARNING)
        else:
            m.status, m.fortschritt, m.schritt = "wird_bereitgestellt", 0, WARTESCHLANGE_DIENST
            m.save(update_fields=["status", "fortschritt", "schritt"])
            self._log(request, m, f"Update von {NAMEN[dienst]} gestartet")
            tasks.dienst_update_task.delay(m.pk, dienst)
            self.message_user(request, f"Update von {NAMEN[dienst]} für {m.kuerzel} gestartet - Fortschritt siehe "
                                       "Spalte „Fortschritt“.", messages.INFO)
        return liste

    def _update_vormerken(self, m):
        """Sofort sichtbar machen (Balken), bevor der Worker anfaengt."""
        marker = WARTESCHLANGE_UPDATE_GESTOPPT if m.status == "gestoppt" else WARTESCHLANGE_UPDATE
        m.status, m.fortschritt, m.schritt = "wird_bereitgestellt", 0, marker
        m.save(update_fields=["status", "fortschritt", "schritt"])

    def has_delete_permission(self, request, obj=None):
        """Den Datensatz einer Instanz loescht man nur, wenn nichts mehr dranhaengt (nie bereitgestellt oder bereits
        endgueltig geloescht). Alles andere laeuft ueber "Instanz endgueltig loeschen" mit Abschlusssicherung."""
        if obj is not None and obj.status not in ("neu", "geloescht"):
            return False
        return super().has_delete_permission(request, obj)

    def wiederherstellen_view(self, request, pk):
        """Auswahl einer Sicherung und Bestaetigung der Wiederherstellung (nur Superuser)."""
        from django.http import Http404
        from django.template.response import TemplateResponse
        from . import wiederherstellung
        if not request.user.is_superuser:
            raise PermissionDenied
        m = MandantInstanz.objects.filter(pk=pk).first()
        if m is None:
            raise Http404
        liste = HttpResponseRedirect(reverse("admin:mandanten_mandantinstanz_changelist"))
        if m.status not in ("laeuft", "gestoppt", "fehlgeschlagen"):
            self.message_user(request, f"{m.kuerzel}: Wiederherstellen nicht möglich im Status "
                                       f"„{m.get_status_display()}“.", messages.WARNING)
            return liste
        angebote, freigabe_fehler = wiederherstellung.angebote(m)
        angebote_os, os_fehler = wiederherstellung.angebote_openslides(m) if m.openslides_domain else ([], "")
        angebote_pl = wiederherstellung.angebote_paperless(m) if m.paperless_domain else []
        if request.method == "POST":
            dienst = request.POST.get("dienst", "feuerix")
            auswahl = request.POST.get("stamp", "")
            gueltig = {"feuerix": {a["stamp"] for a in angebote},
                       "openslides": {a["name"] for a in angebote_os},
                       "paperless": {a["name"] for a in angebote_pl}}.get(dienst, set())
            if request.POST.get("kuerzel", "").strip() != m.kuerzel:
                self.message_user(request, "Das Kürzel stimmt nicht - es wurde nichts wiederhergestellt.", messages.ERROR)
            elif auswahl not in gueltig:
                self.message_user(request, "Bitte eine der angezeigten Sicherungen wählen.", messages.ERROR)
            else:
                mit_medien = "mit_medien" in request.POST
                vorher_sichern = "vorher_sichern" in request.POST
                vormerken(m, "Wiederherstellung: in der Warteschlange")
                self._log(request, m, f"Wiederherstellung ({dienst}) aus {auswahl} gestartet "
                                      f"({'ohne' if not vorher_sichern else 'mit'} Sicherung des jetzigen Stands)")
                tasks.mandant_wiederherstellen_task.delay(m.pk, auswahl, mit_medien, vorher_sichern, dienst)
                self.message_user(request, f"{m.kuerzel}: Wiederherstellung ({dienst}) gestartet - Fortschritt siehe Spalte "
                                           "„Fortschritt“.", messages.INFO)
                return liste
        kontext = dict(self.admin_site.each_context(request), title="Aus Sicherung wiederherstellen", instanz=m,
                       opts=self.model._meta, angebote=angebote, freigabe_fehler=freigabe_fehler,
                       angebote_os=angebote_os, os_fehler=os_fehler, angebote_pl=angebote_pl)
        return TemplateResponse(request, "admin/mandanten/wiederherstellen.html", kontext)

    def loeschen_view(self, request, pk):
        """Bestaetigungsseite fuer das endgueltige Loeschen (nur Superuser): vorher Abschlusssicherung."""
        from django.http import Http404
        from django.template.response import TemplateResponse
        if not request.user.is_superuser:
            raise PermissionDenied
        m = MandantInstanz.objects.filter(pk=pk).first()
        if m is None:
            raise Http404
        liste = HttpResponseRedirect(reverse("admin:mandanten_mandantinstanz_changelist"))
        if m.status not in ("laeuft", "gestoppt", "pausiert", "fehlgeschlagen"):
            self.message_user(request, f"{m.kuerzel}: Löschen nicht möglich im Status „{m.get_status_display()}“.",
                              messages.WARNING)
            return liste
        if request.method == "POST":
            if request.POST.get("kuerzel", "").strip() == m.kuerzel:
                vormerken(m, "Löschen: in der Warteschlange")
                self._log(request, m, "Endgültiges Löschen gestartet (mit Abschlusssicherung)")
                tasks.mandant_loeschen_task.delay(m.pk)
                self.message_user(request, f"{m.kuerzel}: Abschlusssicherung und Löschen gestartet - Fortschritt siehe "
                                           "Spalte „Fortschritt“.", messages.INFO)
                return liste
            self.message_user(request, "Das Kürzel stimmt nicht - es wurde nichts gelöscht.", messages.ERROR)
        from . import endsicherung
        kontext = dict(self.admin_site.each_context(request), title="Instanz endgültig löschen", instanz=m,
                       opts=self.model._meta, backupziel_aktiv=backupziel.aktuell() is not None,
                       archiv=endsicherung.archiv_verzeichnis())
        return TemplateResponse(request, "admin/mandanten/endgueltig_loeschen.html", kontext)

    def _zurueck(self, request):
        return HttpResponseRedirect(request.META.get("HTTP_REFERER") or reverse("admin:index"))

    def update_pruefen_view(self, request):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        if not self.has_change_permission(request):
            raise PermissionDenied
        z = updates.pruefen()
        if z.fehler:
            self.message_user(request, f"Prüfung fehlgeschlagen: {z.fehler}", messages.ERROR)
        elif z.rollout_empfohlen:
            self.message_user(request, f"Feuerix {z.ziel_version} ist das Ziel - Rollout empfohlen.", messages.WARNING)
        else:
            self.message_user(request, f"Feuerix ist aktuell ({z.lokale_version}).", messages.SUCCESS)
        if z.dienste_fehler:
            self.message_user(request, f"Zusatzdienste: {z.dienste_fehler}", messages.WARNING)
        for name, kuerzel in z.dienste_unbekannt:
            self.message_user(request, f"{name}: Version bei {', '.join(kuerzel)} nicht ermittelt.", messages.WARNING)
        return self._zurueck(request)

    def update_ausrollen_view(self, request):
        """Startet den Rollout (git pull + alle veralteten Instanzen nacheinander) - nur auf ausdruecklichen Klick."""
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        if not self.has_change_permission(request):
            raise PermissionDenied
        zustand = UpdateZustand.holen()
        if zustand.rollout_laeuft:
            self.message_user(request, "Es läuft bereits ein Rollout.", messages.WARNING)
        elif zustand.in_arbeit_instanzen:
            self.message_user(request, "Während Instanzen in Arbeit sind ("
                              + ", ".join(m.kuerzel for m in zustand.in_arbeit_instanzen)
                              + "), sind keine Updates möglich. Bitte warten, bis der Vorgang beendet ist.",
                              messages.WARNING)
        else:
            gesamtlog.log("Rollout per Klick gestartet.", kategorie="Update", benutzer=request.user.get_username())
            tasks.update_ausrollen_task.delay()
            self.message_user(request, "Rollout gestartet - Fortschritt siehe Hinweis oben.", messages.INFO)
        return self._zurueck(request)

    def update_status_view(self, request):
        if not self.has_view_permission(request):
            return JsonResponse({}, status=403)
        z = UpdateZustand.holen()
        return JsonResponse({"laeuft": z.rollout_laeuft, "text": z.rollout_text})

    def dienst_view(self, request, pk, aktion):
        """Ziel der Schaltflaechen in der Instanzliste (nur POST, braucht Aenderungsrecht)."""
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        dienst_update = {"paperless_update": "paperless", "openslides_update": "openslides"}
        if not self.has_change_permission(request) or (
                aktion not in self.DIENSTE and aktion != "aktualisieren" and aktion not in dienst_update):
            raise PermissionDenied
        if aktion in dienst_update:
            return self._dienst_update(request, pk, dienst_update[aktion])
        if aktion == "aktualisieren":
            m = MandantInstanz.objects.filter(pk=pk).first()
            if m is None or m.status not in ("laeuft", "gestoppt"):
                self.message_user(request, "Update nur für laufende oder gestoppte Instanzen möglich.",
                                  messages.WARNING)
            else:
                self._update_vormerken(m)
                self._log(request, m, "Update gestartet")
                tasks.mandant_update_task.delay(m.pk)
                self.message_user(request, f"Update für {m.kuerzel} gestartet (bei Bedarf mit vorheriger Sicherung) - "
                                           "Fortschritt siehe Spalte „Fortschritt“.", messages.INFO)
            return HttpResponseRedirect(reverse("admin:mandanten_mandantinstanz_changelist"))
        erlaubt, task, text = self.DIENSTE[aktion][:3]
        self._steuern(request, MandantInstanz.objects.filter(pk=pk), erlaubt, task, text)
        return HttpResponseRedirect(reverse("admin:mandanten_mandantinstanz_changelist"))

    def _mit_diensten(self, obj, zustand, text):
        """Feuerix-Version plus (kleiner) Paperless-/OpenSlides-Version; veraltete mit Hinweis auf die neue."""
        from .updates import versionstupel
        zeilen = []
        for dienst, name in (("paperless", "Paperless"), ("openslides", "OpenSlides")):
            v, neu = getattr(obj, f"{dienst}_version"), getattr(zustand, f"{dienst}_neueste")
            if not v:
                continue
            alt = bool(versionstupel(v) and versionstupel(neu) and versionstupel(v) < versionstupel(neu))
            knopf = ""
            nur_manuell = bool(alt and dienst == "openslides" and versionstupel(v)[:2] != versionstupel(neu)[:2])
            if alt and not nur_manuell and obj.status == "laeuft" and not zustand.rollout_laeuft:
                if dienst == "paperless":
                    frage = (f"Paperless-ngx von {v} auf {neu} aktualisieren? Vorher wird ein Export der Dokumente "
                             "angelegt (Ordner export der Instanz); schlägt er fehl, wird nichts geändert. Paperless "
                             "ist kurz nicht erreichbar, Feuerix läuft weiter. Bei Paperless entscheidet der Hersteller, "
                             "welche Datenbank-Migrationen mit der neuen Version laufen.")
                else:
                    frage = (f"OpenSlides von {v} auf {neu} aktualisieren (Patch-Update)? Vorher wird ein "
                             "Datenbank-Dump gesichert; schlägt er fehl, wird nichts geändert. Die Compose-Datei wird "
                             "nach der OpenSlides-Anleitung neu erzeugt (osmanage config --force). OpenSlides ist kurz "
                             "nicht erreichbar.")
                knopf = format_html(
                    ' <button type="button" class="pv-knopf pv-update-einzeln" data-url="{}" data-frage="{}" '
                    'title="{} auf {} aktualisieren" aria-label="{} aktualisieren"><svg viewBox="0 0 24 24" width="14" '
                    'height="14" aria-hidden="true"><path fill="currentColor" d="M12 3l-8 8h5v9h6v-9h5z"/></svg></button>',
                    reverse("admin:mandanten_dienst", args=[obj.pk, f"{dienst}_update"]), frage, name, neu, name)
            zeilen.append(format_html('<small{}>{} {}{}</small>{}', mark_safe(' style="color:#ef6c00"') if alt else "",
                                      name, v, (f" → {neu} verfügbar" + (" (nur von Hand, siehe OpenSlides-Anleitung)" if nur_manuell else ""))
                                      if alt else "", knopf))
        if not zeilen:
            return text
        return format_html("{}<br>{}", text, mark_safe("<br>".join(str(z) for z in zeilen)))

    @admin.display(description="Version")
    def version_spalte(self, obj):
        """Bereitgestellte Version; bei verfuegbarem Update zusaetzlich ein Knopf zum Aktualisieren dieser Instanz."""
        text = obj.feuerix_version or "-"
        zustand = UpdateZustand.holen()
        ziel = zustand.ziel_version
        text = self._mit_diensten(obj, zustand, text)
        if obj.status not in ("laeuft", "gestoppt") or not ziel or obj.feuerix_version == ziel:
            return text
        frage = f"{obj.kuerzel} auf Feuerix {ziel} aktualisieren? Die Instanz ist dabei kurz nicht erreichbar."
        if obj.status == "gestoppt":
            frage = (f"{obj.kuerzel} ist gestoppt. Auf Feuerix {ziel} aktualisieren? Es wird nur das Image gebaut, "
                     "die Instanz bleibt gestoppt und wird beim nächsten Start aktualisiert.")
        else:
            frage += (" Alle Sicherungen (Feuerix und Zusatzdienste) sind aktuell." if obj.alle_sicherungen_aktuell
                      else " Fehlende oder veraltete Sicherungen (Feuerix, Paperless, OpenSlides) werden vorher "
                           "automatisch angelegt.")
        return format_html(
            '<span>{}</span> <button type="button" class="pv-knopf pv-update-einzeln" data-url="{}" '
            'data-frage="{}" title="Auf {} aktualisieren" aria-label="Aktualisieren">'
            '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="currentColor" '
            'd="M12 3l-8 8h5v9h6v-9h5z"/></svg></button> <small>→ {}</small>',
            text, reverse("admin:mandanten_dienst", args=[obj.pk, "aktualisieren"]), frage, ziel, ziel)

    @admin.display(description="Steuerung")
    def steuerung(self, obj):
        if obj.status in ("neu", "geloescht"):
            return "-"
        knoepfe = []
        for aktion, (erlaubt, _task, text, symbol, klasse, frage) in self.DIENSTE.items():
            knoepfe.append(format_html(
                '<button type="button" class="pv-knopf {}" data-url="{}" data-frage="{}" title="{}" '
                'aria-label="{}"{}><svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">'
                '<path fill="currentColor" d="{}"/></svg></button>',
                klasse, reverse("admin:mandanten_dienst", args=[obj.pk, aktion]), frage, text, text,
                "" if obj.status in erlaubt else mark_safe(" disabled"), symbol))
        return format_html('<div class="pv-knoepfe">{}</div>', mark_safe("".join(knoepfe)))

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
        text = f"{obj.get_status_display()}" + (f" - {obj.schritt}" if obj.schritt else "")
        if obj.status not in ("gestoppt", "pausiert"):
            text += f" ({obj.fortschritt} %)"
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
                                "protokoll", "feuerix_version", "paperless_version", "openslides_version",
                                "erstellt_am", "aktualisiert_am"]

    def save_model(self, request, obj, form, change):
        alt = MandantInstanz.objects.filter(pk=obj.pk).first() if change else None
        super().save_model(request, obj, form, change)
        if obj.status != "laeuft":
            # Domain sofort mit Wartungsseite belegen (statt Zertifikats-/Verbindungsfehler im Browser)
            provisioning.proxy_nachziehen_best_effort(obj)
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
            self._log(request, m, f"{text} gestartet")
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

    def _steuern(self, request, queryset, erlaubt, task, text):
        gestartet = []
        for m in queryset:
            if m.status not in erlaubt:
                self.message_user(request, f"{m.kuerzel}: {text} nicht möglich im Status „{m.get_status_display()}“.",
                                  messages.WARNING)
                continue
            # sofort sichtbar machen, damit die Oberflaeche den Fortschritt live anzeigen kann
            m.status, m.fortschritt, m.schritt = "wird_bereitgestellt", 0, "In der Warteschlange"
            m.save(update_fields=["status", "fortschritt", "schritt"])
            self._log(request, m, f"{text} gestartet")
            task.delay(m.pk)
            gestartet.append(m.kuerzel)
        if gestartet:
            self.message_user(request, f"{text} gestartet für: {', '.join(gestartet)} - Fortschritt siehe Spalte "
                                       "„Fortschritt“.", messages.INFO)

    @admin.action(description="Starten (gestoppte/stillgelegte Instanz)")
    def starten(self, request, queryset):
        self._steuern(request, queryset, *self.DIENSTE["starten"][:3])

    @admin.action(description="Stoppen (Container anhalten, Daten bleiben)")
    def stoppen(self, request, queryset):
        self._steuern(request, queryset, *self.DIENSTE["stoppen"][:3])

    @admin.action(description="Neustarten")
    def neustarten(self, request, queryset):
        self._steuern(request, queryset, *self.DIENSTE["neustarten"][:3])

    @admin.action(description="Instanz stilllegen (Container entfernen, Daten behalten)")
    def stilllegen(self, request, queryset):
        gestartet = []
        for m in queryset:
            if m.status in ("neu", "geloescht", "pausiert", "wird_bereitgestellt"):
                self.message_user(request, f"{m.kuerzel}: Stilllegen nicht möglich im Status "
                                           f"„{m.get_status_display()}“.", messages.WARNING)
                continue
            vormerken(m, "Stilllegen: in der Warteschlange")
            self._log(request, m, "Stilllegen gestartet")
            tasks.mandant_stilllegen_task.delay(m.pk)
            gestartet.append(m.kuerzel)
        if gestartet:
            self.message_user(request, f"Stilllegen gestartet für: {', '.join(gestartet)} - Fortschritt siehe Spalte "
                                       "„Fortschritt“.", messages.INFO)

    @admin.action(description="Aus Sicherung wiederherstellen ...")
    def wiederherstellen(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Bitte genau eine Instanz zum Wiederherstellen auswählen.", messages.WARNING)
            return None
        return HttpResponseRedirect(reverse("admin:mandanten_wiederherstellen", args=[queryset.first().pk]))

    @admin.action(description="Instanz ENDGÜLTIG löschen (mit Abschlusssicherung) ...")
    def endgueltig_loeschen(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Bitte genau eine Instanz zum Löschen auswählen.", messages.WARNING)
            return None
        return HttpResponseRedirect(reverse("admin:mandanten_loeschen", args=[queryset.first().pk]))

    @admin.action(description="Sichern (Feuerix, Paperless, OpenSlides)")
    def sichern(self, request, queryset):
        self._starten(request, queryset, tasks.mandant_backup_task, "Sicherung")

    @admin.action(description="Auf aktuellen Feuerix-Code aktualisieren")
    def aktualisieren(self, request, queryset):
        gestartet = []
        for m in queryset:
            if m.status not in ("laeuft", "gestoppt"):
                self.message_user(request, f"{m.kuerzel}: Update nur für laufende oder gestoppte Instanzen möglich "
                                           f"(Status: {m.get_status_display()}).", messages.WARNING)
                continue
            self._update_vormerken(m)
            self._log(request, m, "Update gestartet")
            tasks.mandant_update_task.delay(m.pk)
            gestartet.append(m.kuerzel)
        if gestartet:
            self.message_user(request, f"Update gestartet für: {', '.join(gestartet)} - Fortschritt siehe Spalte "
                                       "„Fortschritt“.", messages.INFO)

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


class BackupZielForm(forms.ModelForm):
    passwort_neu = forms.CharField(
        label="Passwort", required=False, widget=forms.PasswordInput(render_value=False),
        help_text="Leer lassen, um das gespeicherte Passwort zu behalten. Es wird verschlüsselt gespeichert.")

    class Meta:
        model = BackupZiel
        fields = ["aktiv", "server", "freigabe", "unterordner", "benutzer", "aufbewahrung_tage", "lokal_loeschen"]

    def clean(self):
        daten = super().clean()
        if daten.get("aktiv") and not (daten.get("passwort_neu") or self.instance.passwort):
            self.add_error("passwort_neu", "Ein Passwort ist nötig, wenn das Ziel aktiv ist.")
        return daten


@admin.register(BackupZiel)
class BackupZielAdmin(admin.ModelAdmin):
    """Eine einzige Einstellung: die Liste fuehrt direkt zum Formular."""
    form = BackupZielForm
    fields = ["status_anzeige", "aktiv", "server", "freigabe", "unterordner", "benutzer", "passwort_neu",
              "aufbewahrung_tage", "lokal_loeschen"]
    readonly_fields = ["status_anzeige"]

    @admin.display(description="Status")
    def status_anzeige(self, obj):
        """Sagt klar, ob Sicherungen wirklich hochgeladen werden - und wann zuletzt."""
        if not obj or not obj.server:
            return "Noch nicht eingerichtet."
        if not obj.aktiv:
            return mark_safe('<strong style="color:#c62828">NICHT AKTIV - es wird nichts hochgeladen.</strong> '
                             "Haken „Sicherungen auf Netzwerkfreigabe ablegen“ setzen und speichern.")
        zeilen = [format_html('<strong style="color:#2e7d32">Aktiv.</strong> Neue Sicherungen werden nach '
                              "<code>\\\\{}\\{}</code> hochgeladen.", obj.server, obj.freigabe)]
        if obj.online is False:
            zeilen.append(format_html('<span style="color:#c62828">Nicht erreichbar ({}): {}</span>',
                                      obj.online_geprueft_am.strftime("%d.%m. %H:%M") if obj.online_geprueft_am else "?",
                                      obj.online_fehler))
        elif obj.online:
            zeilen.append(format_html("Erreichbar (geprüft {}).", obj.online_geprueft_am.strftime("%d.%m. %H:%M")))
        zeilen.append(format_html("Letzter Upload: {}.", f"{obj.letzter_upload_am:%d.%m.%Y %H:%M} - {obj.letzter_upload_text}"
                                  if obj.letzter_upload_am else "noch keiner"))
        return format_html("<br>".join("{}" for _ in zeilen), *zeilen)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        ziel = BackupZiel.holen()
        return HttpResponseRedirect(reverse("admin:mandanten_backupziel_change", args=[ziel.pk]))

    def save_model(self, request, obj, form, change):
        if form.cleaned_data.get("passwort_neu"):
            obj.passwort = form.cleaned_data["passwort_neu"]
        super().save_model(request, obj, form, change)
        gesamtlog.log(f"Backup-Ziel gespeichert ({obj}, {'aktiv' if obj.aktiv else 'inaktiv'}"
                      f"{', Passwort geändert' if form.cleaned_data.get('passwort_neu') else ''}).",
                      kategorie="Einstellungen", benutzer=request.user.get_username())

    def response_change(self, request, obj):
        if "_testen" in request.POST:
            try:
                self.message_user(request, backupziel.testen(obj), messages.SUCCESS)
            except BereitstellungsFehler as e:
                self.message_user(request, f"Test fehlgeschlagen - {e}", messages.ERROR)
            return HttpResponseRedirect(request.path)
        return super().response_change(request, obj)


class BenachrichtigungForm(forms.ModelForm):
    smtp_passwort_neu = forms.CharField(
        label="Passwort", required=False, widget=forms.PasswordInput(render_value=False),
        help_text="Leer lassen, um das gespeicherte Passwort zu behalten. Es wird verschlüsselt gespeichert.")

    class Meta:
        model = Benachrichtigung
        fields = ["aktiv", "empfaenger", "absender", "smtp_host", "smtp_port", "smtp_sicherheit", "smtp_benutzer",
                  "bei_ausfall", "ausfall_minuten", "bei_backup_fehler", "bei_update"]


@admin.register(Benachrichtigung)
class BenachrichtigungAdmin(admin.ModelAdmin):
    """Eine einzige Einstellung: die Liste fuehrt direkt zum Formular."""
    form = BenachrichtigungForm
    fieldsets = [
        (None, {"fields": ["aktiv", "empfaenger", "absender"]}),
        ("SMTP-Server", {"fields": ["smtp_host", "smtp_port", "smtp_sicherheit", "smtp_benutzer",
                                    "smtp_passwort_neu"]}),
        ("Wann benachrichtigen?", {"fields": ["bei_ausfall", "ausfall_minuten", "bei_backup_fehler", "bei_update"]}),
        ("Status", {"fields": ["letzte_mail_am", "letzter_fehler"]}),
    ]
    readonly_fields = ["letzte_mail_am", "letzter_fehler"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        return HttpResponseRedirect(reverse("admin:mandanten_benachrichtigung_change", args=[Benachrichtigung.holen().pk]))

    def save_model(self, request, obj, form, change):
        if form.cleaned_data.get("smtp_passwort_neu"):
            obj.smtp_passwort = form.cleaned_data["smtp_passwort_neu"]
        super().save_model(request, obj, form, change)
        gesamtlog.log(f"E-Mail-Benachrichtigung gespeichert ({'aktiv' if obj.aktiv else 'inaktiv'}"
                      f"{', Passwort geändert' if form.cleaned_data.get('smtp_passwort_neu') else ''}).",
                      kategorie="Einstellungen", benutzer=request.user.get_username())

    def response_change(self, request, obj):
        if "_testmail" in request.POST:
            try:
                if not obj.empfaenger_liste() or not obj.smtp_host or not obj.absender:
                    raise ValueError("Empfänger, Absender und SMTP-Server müssen ausgefüllt sein.")
                benachrichtigung.testmail(obj)
                self.message_user(request, f"Test-Mail an {', '.join(obj.empfaenger_liste())} gesendet.",
                                  messages.SUCCESS)
            except Exception as e:
                self.message_user(request, f"Test-Mail fehlgeschlagen: {type(e).__name__}: {e}", messages.ERROR)
            return HttpResponseRedirect(request.path)
        return super().response_change(request, obj)


@admin.register(LogEintrag)
class LogEintragAdmin(admin.ModelAdmin):
    """Gesamtlog: nur lesen, filtern und durchsuchen (Loeschen nur fuer Superuser)."""
    list_display = ("zeit", "ebene_farbig", "quelle", "kategorie", "kurztext", "benutzer")
    list_filter = ("ebene", "quelle", "kategorie")
    search_fields = ("text", "quelle", "benutzer")
    date_hierarchy = "zeit"
    list_per_page = 100
    readonly_fields = ("zeit", "ebene", "quelle", "kategorie", "text", "benutzer")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    @admin.display(description="Ebene", ordering="ebene")
    def ebene_farbig(self, obj):
        farbe = {"info": "#2e7d32", "warnung": "#ef6c00", "fehler": "#c62828"}[obj.ebene]
        return format_html('<strong style="color:{}">{}</strong>', farbe, obj.get_ebene_display())

    @admin.display(description="Ereignis")
    def kurztext(self, obj):
        text = obj.text.replace("\n", " ")
        return text if len(text) <= 160 else text[:157] + "…"
