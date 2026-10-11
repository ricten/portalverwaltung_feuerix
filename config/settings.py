import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

from . import envdatei  # noqa: E402

envdatei.laden()


def _bool(name, default="0"):
    return os.environ.get(name, default).lower() in ("1", "true", "yes", "ja")


SECRET_KEY = os.environ["SECRET_KEY"]
DEBUG = _bool("DEBUG")
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

# Schluessel fuer die verschluesselt gespeicherten Geheimnisse (SECRET_KEY/FIELD_ENCRYPTION_KEY/Passwoerter)
# jeder verwalteten Mandanten-Instanz - NICHT zu verwechseln mit deren eigenem FIELD_ENCRYPTION_KEY.
FIELD_ENCRYPTION_KEY = os.environ.get("FIELD_ENCRYPTION_KEY", "")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.mandanten",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.mandanten.context.portal_version",
                "apps.mandanten.context.update_info",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "portalverwaltung"),
        "USER": os.environ.get("POSTGRES_USER", "portal"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Meldungen (z. B. angezeigte Zugangsdaten) in der serverseitigen Sitzung halten, nicht in einem Cookie im Browser.
MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"
# Anmeldung der Weboberflaeche laeuft nach so vielen Stunden ab (Standard 12).
SESSION_COOKIE_AGE = int(os.environ.get("SESSION_DAUER_STUNDEN", "12")) * 3600

LANGUAGE_CODE = "de"
TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "admin:login"
LOGIN_REDIRECT_URL = "admin:index"

HTTPS = _bool("HTTPS")
if HTTPS:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_SSL_REDIRECT = True

# --- Portalverwaltung-spezifisch ---
# Eigene Version (Fusszeile der Weboberflaeche): steht fett in VERSION.md, z. B. **0.9.0**.
def _portal_version():
    try:
        treffer = re.search(r"\*\*(\d+\.\d+\.\d+)\*\*", (BASE_DIR / "VERSION.md").read_text(encoding="utf-8"))
    except OSError:
        treffer = None
    return treffer.group(1) if treffer else "unbekannt"


PORTAL_VERSION = _portal_version()
# Pfad zum lokalen Checkout des Feuerix-Quellcodes ("vereinsverwaltung") - von dort wird die dortige
# docker-compose.yml direkt fuer jede neue Instanz verwendet (kein Kopieren, damit Aenderungen daran
# automatisch fuer neue Bereitstellungen gelten).
FEUERIX_REPO_PFAD = os.environ.get("FEUERIX_REPO_PFAD", str(BASE_DIR.parent / "vereinsverwaltung"))
# Wo die generierten .env-Dateien je Mandant abgelegt werden.
INSTANZEN_VERZEICHNIS = os.environ.get("INSTANZEN_VERZEICHNIS", str(BASE_DIR / "instanzen"))
# Erster zu vergebender Web-Port - jede neue Instanz bekommt automatisch den naechsten freien (siehe
# apps/mandanten/provisioning.py::naechster_freier_port).
WEB_PORT_BASIS = int(os.environ.get("WEB_PORT_BASIS", "8100"))
# Analog fuer die optionalen Zusatzdienste (jeder Mandant bekommt je Dienst einen eigenen freien Port).
PAPERLESS_PORT_BASIS = int(os.environ.get("PAPERLESS_PORT_BASIS", "10100"))
OPENSLIDES_PORT_BASIS = int(os.environ.get("OPENSLIDES_PORT_BASIS", "9100"))
# Vor der Vergabe eines Ports pruefen, ob er auf dem Server wirklich frei ist (Bind-Versuch auf 127.0.0.1).
PORT_PRUEFUNG = _bool("PORT_PRUEFUNG", "1")
# Host-Port des OpenSlides-Verwaltungsdienstes (backendManage) je Instanz, ab diesem Wert der naechste freie.
OPENSLIDES_MANAGE_PORT_BASIS = int(os.environ.get("OPENSLIDES_MANAGE_PORT_BASIS", "9200"))
# Reverse-Proxy-Automatisierung (Caddy-Admin-API, z. B. http://127.0.0.1:2019). Leer = aus, dann Caddy von
# Hand pflegen. Die Admin-API gibt vollen Zugriff auf Caddy - deshalb nur auf localhost erreichbar lassen.
CADDY_ADMIN_URL = os.environ.get("CADDY_ADMIN_URL", "")
CADDY_ACME_EMAIL = os.environ.get("CADDY_ACME_EMAIL", "")
# Eigene Domain der Portalverwaltung (Weboberflaeche /admin/) - wird mit in den Reverse Proxy eingetragen.
PORTAL_DOMAIN = os.environ.get("PORTAL_DOMAIN", "")
PORTAL_PORT = int(os.environ.get("PORTAL_PORT", "8000"))
# Update-Pruefung fuer Feuerix: so oft (Stunden) prueft das Portal im Hintergrund auf eine neuere Version,
# sobald jemand die Weboberflaeche oeffnet (0 = aus). Den Rollout startet immer ein Mensch.
UPDATE_PRUEFUNG_STUNDEN = int(os.environ.get("UPDATE_PRUEFUNG_STUNDEN", "6"))
# Vor einem Update muss eine Sicherung der Instanz juenger als so viele Stunden sein - sonst legt das Portal
# automatisch eine an (schlaegt sie fehl, wird die Instanz nicht aktualisiert).
UPDATE_BACKUP_MAX_ALTER_STUNDEN = int(os.environ.get("UPDATE_BACKUP_MAX_ALTER_STUNDEN", "24"))
# Gesamtlog: Eintraege aelter als so viele Tage werden automatisch geloescht (0 = nie).
LOG_AUFBEWAHRUNG_TAGE = int(os.environ.get("LOG_AUFBEWAHRUNG_TAGE", "90"))
# Auch Paperless-ngx und OpenSlides: laufende Versionen erfassen und auf GitHub nach neuen Releases schauen (nur Hinweis).
UPDATE_DIENSTE_PRUEFEN = _bool("UPDATE_DIENSTE_PRUEFEN", "1")
# Pause zwischen den Instanzen beim Rollout (Sekunden), damit nicht alle gleichzeitig neu bauen.
UPDATE_PAUSE_SEKUNDEN = int(os.environ.get("UPDATE_PAUSE_SEKUNDEN", "10"))
# Nur fuer Tests/lokal: reines HTTP ohne Zertifikate (siehe proxy.py).
PROXY_NUR_HTTP = _bool("PROXY_NUR_HTTP")

# --- Celery ---
# WICHTIG (siehe README "Sicherheit"): der Celery-Worker fuehrt echte docker-Befehle auf dem Host aus und
# sollte deshalb NICHT selbst in einem Container mit gemountetem Docker-Socket laufen, sondern direkt auf
# dem Host als eigener Prozess.
CELERY_BROKER_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/1")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TASK_ALWAYS_EAGER = _bool("CELERY_EAGER")
CELERY_TASK_EAGER_PROPAGATES = _bool("CELERY_EAGER")
