from django.db import models
from django.utils import timezone

from .fields import VerschluesseltesTextField
from .validatoren import gueltige_domain, keine_env_sonderzeichen

STATUS = [
    ("neu", "Neu (noch nicht bereitgestellt)"),
    ("wird_bereitgestellt", "In Arbeit"),
    ("laeuft", "Läuft"),
    ("fehlgeschlagen", "Fehlgeschlagen"),
    ("gestoppt", "Gestoppt (Container angehalten)"),
    ("pausiert", "Stillgelegt (Container entfernt, Daten bleiben)"),
    ("geloescht", "Gelöscht (Daten entfernt)"),
]


class MandantInstanz(models.Model):
    """Eine vollstaendig isolierte Feuerix-Instanz fuer einen Verein (eigene Datenbank, eigener
    Docker-Compose-Stack) - der Kern der Portalverwaltung.

    Ausbaustufe 1 (aktuell): nur Feuerix selbst wird automatisiert bereitgestellt. Reverse-Proxy-Eintrag
    (Ausbaustufe 1b) und OpenSlides/Paperless (Ausbaustufe 2) folgen separat, siehe README."""
    kuerzel = models.SlugField(
        "Kürzel", max_length=50, unique=True,
        help_text="Nur Kleinbuchstaben/Ziffern/Bindestrich - wird für den Docker-Compose-Projektnamen, das "
                  "Instanzverzeichnis und den Datenbanknamen verwendet. Nachträglich nicht mehr änderbar.")
    verein_name = models.CharField("Vereinsname", max_length=200, validators=[keine_env_sonderzeichen])
    domain = models.CharField("Domain", max_length=200, validators=[gueltige_domain],
                              help_text="z. B. verein-x.beispiel.de (für VEREIN_DOMAIN in der generierten "
                                        ".env und den Reverse-Proxy-Eintrag, sofern CADDY_ADMIN_URL gesetzt "
                                        "ist). DNS muss auf den Server zeigen (Wildcard-DNS empfohlen).")
    admin_benutzername = models.CharField("Admin-Benutzername", max_length=150, default="admin",
                                          validators=[keine_env_sonderzeichen])
    admin_email = models.EmailField("Admin-E-Mail", blank=True, validators=[keine_env_sonderzeichen])

    web_port = models.PositiveIntegerField("Zugewiesener Web-Port", null=True, blank=True, editable=False)

    # Optionale Zusatzdienste (Ausbaustufe 2): leere Domain = Dienst aus.
    paperless_domain = models.CharField(
        "Paperless-Domain", max_length=200, blank=True, validators=[gueltige_domain],
        help_text="Nur ausfüllen, wenn Paperless unter einer eigenen Adresse laufen soll (z. B. "
                  "dokumente.verein.de) oder du einen anderen Namen als den Vorschlag möchtest. Wird Paperless "
                  "über den Haken „Paperless-ngx installieren“ installiert, bleibt das Feld leer - es wird dann "
                  "automatisch paperless.<Domain> verwendet. Die Installation, den Reverse-Proxy-Eintrag und die "
                  "Anbindung in Feuerix übernimmt das Portal.")
    paperless_port = models.PositiveIntegerField("Paperless-Port", null=True, blank=True, editable=False)
    paperless_secret_key = VerschluesseltesTextField("Paperless SECRET_KEY", blank=True, editable=False)
    paperless_postgres_passwort = VerschluesseltesTextField("Paperless Postgres-Passwort", blank=True,
                                                            editable=False)
    paperless_admin_passwort = VerschluesseltesTextField("Paperless Admin-Passwort", blank=True, editable=False)

    openslides_domain = models.CharField(
        "OpenSlides-Domain", max_length=200, blank=True, validators=[gueltige_domain],
        help_text="Nur ausfüllen, wenn OpenSlides unter einer eigenen Adresse laufen soll (z. B. "
                  "versammlung.verein.de) oder du einen anderen Namen als den Vorschlag möchtest. Wird OpenSlides "
                  "über den Haken „OpenSlides installieren“ installiert, bleibt das Feld leer - es wird dann "
                  "automatisch openslides.<Domain> verwendet. Die Installation, den Reverse-Proxy-Eintrag und die "
                  "Anbindung in Feuerix übernimmt das Portal.")
    openslides_port = models.PositiveIntegerField("OpenSlides-Port", null=True, blank=True, editable=False)
    openslides_superadmin_passwort = VerschluesseltesTextField("OpenSlides Superadmin-Passwort", blank=True,
                                                               editable=False)
    openslides_initialisiert = models.BooleanField("OpenSlides initialisiert", default=False, editable=False)

    secret_key = VerschluesseltesTextField("SECRET_KEY", blank=True, editable=False)
    field_encryption_key = VerschluesseltesTextField("FIELD_ENCRYPTION_KEY", blank=True, editable=False)
    postgres_passwort = VerschluesseltesTextField("Postgres-Passwort", blank=True, editable=False)
    admin_passwort = VerschluesseltesTextField("Admin-Passwort", blank=True, editable=False)

    status = models.CharField("Status", max_length=20, choices=STATUS, default="neu")
    fortschritt = models.PositiveSmallIntegerField("Fortschritt (%)", default=0, editable=False)
    schritt = models.CharField("Aktueller Schritt", max_length=200, blank=True, editable=False)
    protokoll = models.TextField("Bereitstellungs-Protokoll", blank=True, editable=False)
    feuerix_version = models.CharField("Bereitgestellte Feuerix-Version", max_length=20, blank=True,
                                       editable=False)

    erstellt_am = models.DateTimeField("Erstellt am", auto_now_add=True)
    aktualisiert_am = models.DateTimeField("Zuletzt geändert", auto_now=True)

    class Meta:
        verbose_name = "Mandanten-Instanz"
        verbose_name_plural = "Mandanten-Instanzen"
        ordering = ["kuerzel"]

    def __str__(self):
        return f"{self.verein_name} ({self.kuerzel})"

    @property
    def docker_projekt(self):
        return f"mandant-{self.kuerzel}"

    @property
    def letzte_sicherung(self):
        """Zeitpunkt der neuesten Sicherungsdatei in INSTANZEN_VERZEICHNIS/<kuerzel>/backups (oder None)."""
        from datetime import datetime, timezone as tz
        from pathlib import Path

        from django.conf import settings
        verz = Path(settings.INSTANZEN_VERZEICHNIS) / self.kuerzel / "backups"
        try:
            zeiten = [f.stat().st_mtime for f in verz.iterdir() if f.is_file()]
        except OSError:
            return None
        return datetime.fromtimestamp(max(zeiten), tz=tz.utc) if zeiten else None

    @property
    def sicherung_aktuell(self):
        from datetime import timedelta

        from django.conf import settings
        letzte = self.letzte_sicherung
        return bool(letzte and timezone.now() - letzte <= timedelta(hours=settings.UPDATE_BACKUP_MAX_ALTER_STUNDEN))

    def protokollieren(self, text):
        zeile = f"[{timezone.now():%d.%m.%Y %H:%M:%S}] {text}"
        self.protokoll = (self.protokoll + "\n" + zeile).strip()
        self.save(update_fields=["protokoll", "aktualisiert_am"])


class UpdateZustand(models.Model):
    """Einzeiliger Zustand (pk=1) der Update-Pruefung fuer Feuerix und des laufenden Rollouts."""
    geprueft_am = models.DateTimeField("Zuletzt geprüft", null=True, blank=True)
    angefordert_am = models.DateTimeField("Automatische Prüfung angestoßen", null=True, blank=True)
    fehler = models.TextField("Fehler der letzten Prüfung", blank=True)
    lokale_version = models.CharField("Installierte Feuerix-Version (Checkout)", max_length=20, blank=True)
    remote_version = models.CharField("Verfügbare Feuerix-Version", max_length=20, blank=True)
    aenderungen = models.TextField("Änderungen seit der installierten Version", blank=True)
    rollout_laeuft = models.BooleanField("Rollout läuft", default=False)
    rollout_gestartet_am = models.DateTimeField(null=True, blank=True)
    rollout_text = models.CharField("Rollout-Schritt", max_length=200, blank=True)
    rollout_ergebnis = models.TextField("Ergebnis des letzten Rollouts", blank=True)
    rollout_beendet_am = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Update-Zustand"
        verbose_name_plural = "Update-Zustand"

    @classmethod
    def holen(cls):
        return cls.objects.get_or_create(pk=1)[0]

    @property
    def update_verfuegbar(self):
        from .updates import versionstupel
        return bool(self.remote_version and self.lokale_version
                    and versionstupel(self.remote_version) > versionstupel(self.lokale_version))

    @property
    def ziel_version(self):
        return self.remote_version if self.update_verfuegbar else self.lokale_version

    @property
    def veraltete_instanzen(self):
        """Laufende Instanzen, die nicht auf der Zielversion sind."""
        if not self.ziel_version:
            return []
        return list(MandantInstanz.objects.filter(status="laeuft").exclude(feuerix_version=self.ziel_version))

    @property
    def rollout_empfohlen(self):
        return self.update_verfuegbar or bool(self.veraltete_instanzen)
