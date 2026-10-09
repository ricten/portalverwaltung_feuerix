"""Lokale Testumgebung ohne Postgres/Redis (fuer manage.py test/makemigrations sowie fuer den
CI-Integrationstest, siehe .github/workflows/integrationstest.yml).

Datei-basiertes SQLite statt ":memory:" - Letzteres lebt nur innerhalb eines einzelnen Prozesses und wuerde
bei mehreren aufeinanderfolgenden manage.py-Aufrufen (migrate, dann shell, dann mandant_bereitstellen - je
ein eigener Prozess) bei jedem Aufruf wieder leer sein. manage.py test legt trotzdem automatisch eine eigene,
fluechtige Test-Datenbank an (Praefix "test_"), das Verhalten dort aendert sich also nicht."""
import os

os.environ.setdefault("SECRET_KEY", "lokal-test-secret")
os.environ.setdefault("FIELD_ENCRYPTION_KEY", "PVjt1u2VJDDwB2vjXAFxCEXsGXTTPuf6xSHZ_Z2Pv-o=")

from config.settings import *  # noqa

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
UPDATE_PRUEFUNG_STUNDEN = 0   # Tests: keine automatische Pruefung
UPDATE_PAUSE_SEKUNDEN = 0
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
