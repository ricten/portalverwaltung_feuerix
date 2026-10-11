import re

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
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
    # Verwaltungsport (backendManage) des OpenSlides-Stacks auf dem Host. Der Standard 9002 kollidiert bei mehreren
    # Instanzen und fuehrt bei `osmanage initial-data` zu "401". None = alte Instanz mit dem Standard 9002.
    openslides_manage_port = models.PositiveIntegerField("OpenSlides-Verwaltungsport", null=True, blank=True,
                                                         editable=False)
    openslides_superadmin_passwort = VerschluesseltesTextField("OpenSlides Superadmin-Passwort", blank=True,
                                                               editable=False)
    openslides_initialisiert = models.BooleanField("OpenSlides initialisiert", default=False, editable=False)

    demo = models.BooleanField(
        "Demo-Instanz (mit Beispieldaten)", default=False,
        help_text="Legt bei der Bereitstellung fiktive Mitglieder mit Beitragsrechnungen, Zahlungen und Mahnungen sowie ein "
                  "Beispiel-Inventar an (Feuerix-Befehl „beispieldaten“). Nur für Vorführ- und Testinstanzen, nie für echte Vereine. "
                  "Bei einer schon laufenden Instanz legt die Aktion „Beispieldaten anlegen“ sie nachträglich an.")
    demo_anzahl = models.PositiveSmallIntegerField(
        "Anzahl Beispiel-Mitglieder", default=40, validators=[MinValueValidator(1), MaxValueValidator(500)])
    demo_inventar = models.BooleanField("Beispiel-Inventar anlegen", default=True)
    beispieldaten_angelegt = models.BooleanField("Beispieldaten angelegt", default=False, editable=False)
    demo_standard_am = models.DateTimeField("Demo-Standard gespeichert am", null=True, blank=True, editable=False)

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

    sicherung_am = models.DateTimeField("Letzte erfolgreiche Sicherung", null=True, blank=True, editable=False)
    paperless_sicherung_am = models.DateTimeField("Letzte Paperless-Sicherung", null=True, blank=True, editable=False)
    openslides_sicherung_am = models.DateTimeField("Letzte OpenSlides-Sicherung", null=True, blank=True, editable=False)
    paperless_version = models.CharField("Paperless-ngx-Version", max_length=30, blank=True, editable=False)
    openslides_version = models.CharField("OpenSlides-Version", max_length=30, blank=True, editable=False)

    online = models.BooleanField("Erreichbar (letzter Check)", null=True, blank=True, editable=False)
    online_geprueft_am = models.DateTimeField("Zuletzt geprüft", null=True, blank=True, editable=False)
    offline_seit = models.DateTimeField("Offline seit", null=True, blank=True, editable=False)
    offline_mail_gesendet = models.BooleanField(default=False, editable=False)

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
        """Zeitpunkt der letzten Sicherung (oder None). Mit aktivem Backup-Ziel zaehlt nur eine erfolgreich auf die
        Netzwerkfreigabe hochgeladene Sicherung; sonst die neueste Datei in INSTANZEN_VERZEICHNIS/<kuerzel>/backups."""
        from datetime import datetime, timezone as tz
        from pathlib import Path

        from django.conf import settings
        if BackupZiel.objects.filter(pk=1, aktiv=True).exists():
            return self.sicherung_am
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

    @property
    def zusatz_sicherungen_aktuell(self):
        """True, wenn fuer jeden eingerichteten Zusatzdienst (Paperless, OpenSlides) eine Sicherung juenger als
        UPDATE_BACKUP_MAX_ALTER_STUNDEN existiert (Export bzw. Dump, bei aktivem Backup-Ziel erst nach dem Upload)."""
        from datetime import timedelta

        from django.conf import settings
        grenze = timezone.now() - timedelta(hours=settings.UPDATE_BACKUP_MAX_ALTER_STUNDEN)
        for domain, port, zeit in ((self.paperless_domain, self.paperless_port, self.paperless_sicherung_am),
                                   (self.openslides_domain, self.openslides_port, self.openslides_sicherung_am)):
            if domain and port and not (zeit and zeit >= grenze):
                return False
        return True

    @property
    def alle_sicherungen_aktuell(self):
        return self.sicherung_aktuell and self.zusatz_sicherungen_aktuell

    def protokollieren(self, text, benutzer=""):
        """Haengt eine Zeile an das Protokoll der Instanz an und schreibt sie zusaetzlich in den Gesamtlog."""
        from . import gesamtlog
        zeile = f"[{timezone.now():%d.%m.%Y %H:%M:%S}] {text}"
        self.protokoll = (self.protokoll + "\n" + zeile).strip()
        self.save(update_fields=["protokoll", "aktualisiert_am"])
        gesamtlog.log(text, quelle=self.kuerzel, kategorie="Instanz", benutzer=benutzer)


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
    update_mail_version = models.CharField("Version, zu der zuletzt eine Mail ging", max_length=20, blank=True,
                                           editable=False)

    # Zusatzdienste: neueste Versionen laut GitHub (nur Hinweis, kein automatisches Update)
    paperless_neueste = models.CharField(max_length=30, blank=True, editable=False)
    openslides_neueste = models.CharField(max_length=30, blank=True, editable=False)
    dienste_fehler = models.CharField(max_length=500, blank=True, editable=False)
    paperless_mail_version = models.CharField(max_length=30, blank=True, editable=False)
    openslides_mail_version = models.CharField(max_length=30, blank=True, editable=False)

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
        """Laufende und gestoppte Instanzen, die nicht auf der Zielversion sind (die gespeicherte Version bleibt
        beim Stoppen erhalten; stillgelegte Instanzen werden bewusst nicht beachtet)."""
        if not self.ziel_version:
            return []
        return list(MandantInstanz.objects.filter(status__in=("laeuft", "gestoppt"))
                    .exclude(feuerix_version=self.ziel_version))

    @property
    def dienste_hinweise(self):
        """Zusatzdienste mit neuerer Version als auf mindestens einer Instanz:
        [{"dienst", "name", "neu", "instanzen": [(kuerzel, version), ...]}]"""
        from .dienstversionen import NAMEN
        from .updates import versionstupel
        hinweise = []
        for dienst in ("paperless", "openslides"):
            neu = getattr(self, f"{dienst}_neueste")
            if not versionstupel(neu):
                continue
            alt = []
            for m in MandantInstanz.objects.exclude(status__in=("neu", "geloescht")):
                v = getattr(m, f"{dienst}_version")
                if versionstupel(v) and versionstupel(v) < versionstupel(neu):
                    alt.append((m.kuerzel, v))
            if alt:
                hinweise.append({"dienst": dienst, "name": NAMEN[dienst], "neu": neu, "instanzen": alt})
        return hinweise

    @property
    def dienste_unbekannt(self):
        """Laufende Instanzen mit Paperless/OpenSlides, deren Version (noch) nicht ermittelt werden konnte:
        [(Dienstname, [kuerzel, ...])] - Hinweis fuer die Anzeige, damit eine fehlende Version nicht stumm bleibt."""
        ergebnis = []
        for name, domain, version in (("Paperless-ngx", "paperless_domain", "paperless_version"),
                                      ("OpenSlides", "openslides_domain", "openslides_version")):
            kuerzel = [m.kuerzel for m in MandantInstanz.objects.filter(status="laeuft").exclude(**{domain: ""})
                       if not getattr(m, version)]
            if kuerzel:
                ergebnis.append((name, kuerzel))
        return ergebnis

    @property
    def rollout_verdaechtig(self):
        """Ein Rollout gilt seit mehr als HAENGT_MINUTEN als laufend - moeglicherweise wurde der Worker dabei beendet."""
        from datetime import timedelta
        return bool(self.rollout_laeuft and self.rollout_gestartet_am
                    and timezone.now() - self.rollout_gestartet_am > timedelta(minutes=15))

    @property
    def in_arbeit_instanzen(self):
        """Instanzen, die gerade starten, stoppen, aktualisiert oder bereitgestellt werden - solange sind Updates
        nicht moeglich und der Update-Status nicht verlaesslich."""
        return list(MandantInstanz.objects.filter(status="wird_bereitgestellt"))

    @property
    def rollout_empfohlen(self):
        return self.update_verfuegbar or bool(self.veraltete_instanzen)


_SERVER = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.\-]*[A-Za-z0-9])?$")
_FREIGABE = re.compile(r'^[^\\/:*?"<>|\x00-\x1f]+$')


class LoginSperre(models.Model):
    """Fehlversuche bei der Anmeldung je Adresse bzw. Benutzername; nach zu vielen ist die Anmeldung kurz gesperrt."""
    schluessel = models.CharField(max_length=300, unique=True)
    fehlversuche = models.PositiveIntegerField(default=0)
    letzter_versuch = models.DateTimeField(auto_now=True)
    gesperrt_bis = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Anmelde-Sperre"
        verbose_name_plural = "Anmelde-Sperren"


class BackupZiel(models.Model):
    """Einzeilige Einstellung (pk=1): Sicherungen zusaetzlich auf einer SMB-Netzwerkfreigabe ablegen."""
    aktiv = models.BooleanField("Sicherungen auf Netzwerkfreigabe ablegen", default=False,
                                help_text="Neue Sicherungen werden nach dem Anlegen zusätzlich dorthin hochgeladen. "
                                          "Ist das Ziel aktiv, gilt eine Sicherung erst als aktuell, wenn der Upload "
                                          "geklappt hat - schlägt er fehl, wird kein Update durchgeführt.")
    server = models.CharField("Server", max_length=200, blank=True,
                              help_text="Hostname oder IP-Adresse des SMB-Servers, z. B. nas.local oder 192.168.1.20.")
    freigabe = models.CharField("Freigabe", max_length=100, blank=True,
                                help_text="Name der Freigabe (Share), z. B. backups.")
    unterordner = models.CharField("Unterordner", max_length=200, blank=True,
                                   help_text="Optional, z. B. feuerix/portal. Darin legt das Portal je Instanz einen "
                                             "Ordner mit dem Kürzel an.")
    benutzer = models.CharField("Benutzer", max_length=200, blank=True,
                                help_text="Benutzername, mit Domäne z. B. DOMÄNE\\benutzer.")
    passwort = VerschluesseltesTextField("Passwort", blank=True, editable=False)
    verschluesselung = models.BooleanField(
        "Übertragung verschlüsseln (SMB 3)", default=True,
        help_text="Erzwingt eine verschlüsselte Verbindung (SMB 3 mit AES). Nur abschalten, wenn der Server das nicht "
                  "kann (sehr alte NAS/Samba-Versionen) und das Netz vertrauenswürdig ist.")
    aufbewahrung_tage = models.PositiveIntegerField(
        "Aufbewahrung (Tage)", default=0,
        help_text="Sicherungen auf der Freigabe, die älter sind, werden gelöscht. 0 = nie löschen.")
    lokal_loeschen = models.BooleanField(
        "Lokale Kopie nach erfolgreichem Upload löschen", default=False,
        help_text="Spart Speicherplatz auf dem Server; die Sicherungen liegen dann nur noch auf der Freigabe.")

    online = models.BooleanField("Erreichbar (letzter Check)", null=True, blank=True, editable=False)
    online_geprueft_am = models.DateTimeField("Zuletzt geprüft", null=True, blank=True, editable=False)
    online_fehler = models.CharField("Fehler des letzten Checks", max_length=500, blank=True, editable=False)
    letzter_upload_am = models.DateTimeField("Letzter Upload", null=True, blank=True, editable=False)
    letzter_upload_text = models.CharField("Letzter Upload (Details)", max_length=255, blank=True, editable=False)
    offline_seit = models.DateTimeField(null=True, blank=True, editable=False)
    offline_mail_gesendet = models.BooleanField(default=False, editable=False)

    class Meta:
        verbose_name = "Backup-Ziel"
        verbose_name_plural = "Backup-Ziel"

    def __str__(self):
        return f"\\\\{self.server}\\{self.freigabe}" if self.server else "Kein Backup-Ziel eingerichtet"

    @classmethod
    def holen(cls):
        return cls.objects.get_or_create(pk=1)[0]

    def unterordner_teile(self):
        return [t for t in re.split(r"[\\/]+", self.unterordner.strip()) if t]

    def clean(self):
        fehler = {}
        if self.server and not _SERVER.match(self.server.strip()):
            fehler["server"] = "Nur Hostname oder IP-Adresse (Buchstaben, Ziffern, Punkt, Bindestrich)."
        if self.freigabe and not _FREIGABE.match(self.freigabe.strip()):
            fehler["freigabe"] = "Nur der Name der Freigabe, ohne Schrägstriche oder Sonderzeichen."
        if any(t == ".." or t == "." or not _FREIGABE.match(t) for t in self.unterordner_teile()):
            fehler["unterordner"] = "Ungültiger Ordnerpfad (kein „..“, keine Sonderzeichen)."
        if self.aktiv:
            for feld, text in (("server", "Server"), ("freigabe", "Freigabe"), ("benutzer", "Benutzer")):
                if not getattr(self, feld).strip():
                    fehler.setdefault(feld, f"{text} ist nötig, wenn das Ziel aktiv ist.")
        if fehler:
            raise ValidationError(fehler)


class Benachrichtigung(models.Model):
    """Einzeilige Einstellung (pk=1): E-Mail-Benachrichtigung ueber Ausfaelle, Sicherungsfehler und Updates."""
    SICHERHEIT = [("starttls", "STARTTLS (Port 587)"), ("ssl", "SSL/TLS (Port 465)"), ("keine", "Keine (nur im LAN!)")]

    aktiv = models.BooleanField("E-Mail-Benachrichtigung aktiv", default=False)
    empfaenger = models.TextField("Empfänger", blank=True,
                                  help_text="Eine oder mehrere E-Mail-Adressen, getrennt durch Komma oder Zeilenumbruch.")
    absender = models.EmailField("Absender", blank=True, help_text="z. B. portal@verein.de")
    smtp_host = models.CharField("SMTP-Server", max_length=200, blank=True)
    smtp_port = models.PositiveIntegerField("Port", default=587)
    smtp_sicherheit = models.CharField("Verschlüsselung", max_length=10, choices=SICHERHEIT, default="starttls")
    smtp_benutzer = models.CharField("Benutzer", max_length=200, blank=True)
    smtp_passwort = VerschluesseltesTextField("Passwort", blank=True, editable=False)

    bei_ausfall = models.BooleanField("Bei Ausfall und Wiederkehr von Instanzen und Backup-Ziel", default=True)
    ausfall_minuten = models.PositiveIntegerField(
        "Ausfall erst melden nach (Minuten)", default=10,
        help_text="Verhindert Fehlalarme bei kurzen Neustarts; die Mail kommt, wenn etwas so lange durchgehend offline ist.")
    bei_backup_fehler = models.BooleanField("Bei fehlgeschlagener Sicherung (auch Upload auf die Freigabe)", default=True)
    bei_update = models.BooleanField("Wenn eine neue Feuerix-Version verfügbar ist", default=True)

    letzter_fehler = models.CharField("Letzter Versandfehler", max_length=500, blank=True, editable=False)
    letzte_mail_am = models.DateTimeField("Letzte Mail gesendet", null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "Benachrichtigung"
        verbose_name_plural = "Benachrichtigungen"

    def __str__(self):
        return "E-Mail-Benachrichtigung"

    @classmethod
    def holen(cls):
        return cls.objects.get_or_create(pk=1)[0]

    def empfaenger_liste(self):
        return [a.strip() for a in re.split(r"[,;\s]+", self.empfaenger) if a.strip()]

    def clean(self):
        from django.core.validators import validate_email
        fehler = {}
        for adresse in self.empfaenger_liste():
            try:
                validate_email(adresse)
            except ValidationError:
                fehler["empfaenger"] = f"„{adresse}“ ist keine gültige E-Mail-Adresse."
                break
        if self.aktiv:
            if not self.smtp_host.strip():
                fehler["smtp_host"] = "SMTP-Server ist nötig, wenn die Benachrichtigung aktiv ist."
            if not self.absender:
                fehler["absender"] = "Absender ist nötig, wenn die Benachrichtigung aktiv ist."
            if not self.empfaenger_liste():
                fehler.setdefault("empfaenger", "Mindestens ein Empfänger ist nötig.")
        if fehler:
            raise ValidationError(fehler)


class LogEintrag(models.Model):
    """Ein Ereignis im Gesamtlog (nur lesbar im Admin)."""
    EBENEN = [("info", "Info"), ("warnung", "Warnung"), ("fehler", "Fehler")]

    zeit = models.DateTimeField("Zeit", auto_now_add=True, db_index=True)
    ebene = models.CharField("Ebene", max_length=10, choices=EBENEN, default="info", db_index=True)
    quelle = models.CharField("Quelle", max_length=40, blank=True, db_index=True,
                              help_text="Kürzel der Instanz oder „Portal“.")
    kategorie = models.CharField("Kategorie", max_length=30, blank=True)
    text = models.TextField("Ereignis")
    benutzer = models.CharField("Benutzer", max_length=150, blank=True)

    class Meta:
        verbose_name = "Log-Eintrag"
        verbose_name_plural = "Gesamtlog"
        ordering = ["-zeit", "-id"]

    def __str__(self):
        return f"{self.zeit:%d.%m.%Y %H:%M} {self.quelle}: {self.text[:60]}"
