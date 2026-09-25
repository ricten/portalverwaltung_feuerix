"""Lokale Testumgebung ohne Postgres/Redis (nur fuer manage.py test/makemigrations auf diesem Rechner)."""
import os

os.environ.setdefault("SECRET_KEY", "lokal-test-secret")
os.environ.setdefault("FIELD_ENCRYPTION_KEY", "PVjt1u2VJDDwB2vjXAFxCEXsGXTTPuf6xSHZ_Z2Pv-o=")

from config.settings import *  # noqa

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
