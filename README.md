# Portalverwaltung für Feuerix

Automatisierte Bereitstellung vollständig isolierter [Feuerix](https://github.com/ricten/Feuerix)-Instanzen
(je Verein eine eigene Datenbank, ein eigener Docker-Compose-Stack) – die Grundlage für echtes
Mehrmandanten-Hosting, im Unterschied zur bereits in Feuerix selbst eingebauten (aber pro Instanz auf einen
Verein gesperrten) Mandantenfähigkeit.

## Stand: Ausbaustufe 1

Aktuell automatisiert:

- **.env-Generierung** je Mandant (frische Geheimnisse: `SECRET_KEY`, `FIELD_ENCRYPTION_KEY`,
  Postgres-Passwort, Admin-Passwort; verschlüsselt in der Portalverwaltungs-Datenbank gespeichert)
- **Port-Vergabe** (nächster freier Port ab `WEB_PORT_BASIS`)
- **Start des Feuerix-Docker-Compose-Stacks** unter einem eigenen Projektnamen (`mandant-<kürzel>`), direkt
  über die `docker-compose.yml` aus dem lokalen Feuerix-Checkout (`FEUERIX_REPO_PFAD`) – kein Kopieren, damit
  Änderungen daran automatisch für neue Bereitstellungen gelten
- **Bereitschaftsprüfung**: wartet, bis die Login-Seite antwortet (= Migration + automatische
  Ersteinrichtung durch Feuerix' eigenes `entrypoint.sh` sind durchgelaufen)

**Noch nicht automatisiert** (bewusst zurückgestellt, siehe Ausbaustufen unten):

- **Ausbaustufe 1b**: Reverse-Proxy-Eintrag (Caddy) für die neue Domain – muss aktuell noch von Hand
  angelegt werden.
- **Ausbaustufe 2**: OpenSlides/Paperless als optionale Zusatzdienste je Mandant.

## Ablauf

```bash
python manage.py migrate
python manage.py shell -c "
from apps.mandanten.models import MandantInstanz
MandantInstanz.objects.create(kuerzel='ff-beispiel', verein_name='Freiwillige Feuerwehr Beispiel e.V.',
                              domain='ff-beispiel.example.org', admin_email='admin@example.org')
"
python manage.py mandant_bereitstellen ff-beispiel
```

Das Kommando durchläuft `apps/mandanten/provisioning.py::bereitstellen()`: `.env` erzeugen → Docker-Compose-
Stack starten → auf Bereitschaft warten. Jeder Schritt ist auch einzeln aufrufbar (z. B. um nach einem
Fehlschlag ohne doppelte Geheimniserzeugung erneut zu versuchen) und wird im `protokoll`-Feld der Instanz
mitgeschrieben (im Admin unter `/admin/` einsehbar).

Für asynchrone Bereitstellung (statt der blockierenden Management-Command-Variante) existiert
`apps/mandanten/tasks.py::mandant_bereitstellen_task` als Celery-Task.

## Sicherheit – wichtig für den Betrieb

Der Celery-Worker führt echte `docker compose`-Befehle auf dem Host aus – das setzt Zugriff auf den
Docker-Daemon voraus, was praktisch **root-Äquivalent** auf dem Host ist. Der Worker-Prozess sollte deshalb
**direkt auf dem Host laufen**, nicht selbst in einem Container mit gemountetem Docker-Socket. Diese
Portalverwaltung selbst wird deshalb (noch) nicht containerisiert ausgeliefert.

Die Geheimnisse jeder verwalteten Instanz (`SECRET_KEY`, `FIELD_ENCRYPTION_KEY`, Passwörter) liegen
verschlüsselt in der Portalverwaltungs-Datenbank (`FIELD_ENCRYPTION_KEY` dieser Anwendung selbst –
unbedingt sichern, ohne ihn sind sie nicht mehr lesbar) und werden bei einem erneuten Bereitstellungs-Versuch
wiederverwendet, nie überschrieben.

## Lokale Entwicklung

```bash
cp .env.example .env   # Werte setzen
pip install -r requirements.txt
python manage.py migrate --settings=config.settings_sqlite   # ohne echten Postgres/Redis
python manage.py test --settings=config.settings_sqlite
```

`FEUERIX_REPO_PFAD` muss auf einen lokalen Checkout von
[Feuerix](https://github.com/ricten/Feuerix) zeigen (Standard: `../vereinsverwaltung`, passend zu einer
Ordnerstruktur, in der beide Repos nebeneinander liegen).
