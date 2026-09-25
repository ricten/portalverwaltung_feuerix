from django.db import models
from django.utils import timezone

from .fields import VerschluesseltesTextField

STATUS = [
    ("neu", "Neu (noch nicht bereitgestellt)"),
    ("wird_bereitgestellt", "Wird bereitgestellt"),
    ("laeuft", "Läuft"),
    ("fehlgeschlagen", "Fehlgeschlagen"),
    ("pausiert", "Pausiert"),
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
    verein_name = models.CharField("Vereinsname", max_length=200)
    domain = models.CharField("Domain", max_length=200,
                              help_text="z. B. verein-x.beispiel.de (für VEREIN_DOMAIN in der generierten "
                                        ".env) - der Reverse-Proxy-Eintrag muss aktuell noch von Hand "
                                        "angelegt werden, siehe README Ausbaustufe 1b.")
    admin_benutzername = models.CharField("Admin-Benutzername", max_length=150, default="admin")
    admin_email = models.EmailField("Admin-E-Mail", blank=True)

    web_port = models.PositiveIntegerField("Zugewiesener Web-Port", null=True, blank=True, editable=False)

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
