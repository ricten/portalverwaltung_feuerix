from django.db import models
from django.utils import timezone

from .fields import VerschluesseltesTextField
from .validatoren import gueltige_domain, keine_env_sonderzeichen

STATUS = [
    ("neu", "Neu (noch nicht bereitgestellt)"),
    ("wird_bereitgestellt", "Wird bereitgestellt"),
    ("laeuft", "Läuft"),
    ("fehlgeschlagen", "Fehlgeschlagen"),
    ("pausiert", "Pausiert (gestoppt, Daten erhalten)"),
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
        help_text="Leer = kein Paperless-ngx. Sonst eigener Compose-Stack je Mandant, automatisch im Reverse "
                  "Proxy eingetragen und in Feuerix (Verwaltung › Paperless-Anbindung) hinterlegt.")
    paperless_port = models.PositiveIntegerField("Paperless-Port", null=True, blank=True, editable=False)
    paperless_secret_key = VerschluesseltesTextField("Paperless SECRET_KEY", blank=True, editable=False)
    paperless_postgres_passwort = VerschluesseltesTextField("Paperless Postgres-Passwort", blank=True,
                                                            editable=False)
    paperless_admin_passwort = VerschluesseltesTextField("Paperless Admin-Passwort", blank=True, editable=False)

    openslides_domain = models.CharField(
        "OpenSlides-Domain", max_length=200, blank=True, validators=[gueltige_domain],
        help_text="Leer = kein OpenSlides. Sonst eigener OpenSlides-4-Stack je Mandant, automatisch im "
                  "Reverse Proxy eingetragen und in Feuerix (Verwaltung › OpenSlides-Anbindung) hinterlegt.")
    openslides_port = models.PositiveIntegerField("OpenSlides-Port", null=True, blank=True, editable=False)
    openslides_superadmin_passwort = VerschluesseltesTextField("OpenSlides Superadmin-Passwort", blank=True,
                                                               editable=False)
    openslides_initialisiert = models.BooleanField("OpenSlides initialisiert", default=False, editable=False)

    secret_key = VerschluesseltesTextField("SECRET_KEY", blank=True, editable=False)
    field_encryption_key = VerschluesseltesTextField("FIELD_ENCRYPTION_KEY", blank=True, editable=False)
    postgres_passwort = VerschluesseltesTextField("Postgres-Passwort", blank=True, editable=False)
    admin_passwort = VerschluesseltesTextField("Admin-Passwort", blank=True, editable=False)

    status = models.CharField("Status", max_length=20, choices=STATUS, default="neu")
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

    def protokollieren(self, text):
        zeile = f"[{timezone.now():%d.%m.%Y %H:%M:%S}] {text}"
        self.protokoll = (self.protokoll + "\n" + zeile).strip()
        self.save(update_fields=["protokoll", "aktualisiert_am"])
