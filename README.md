# Portalverwaltung für Feuerix

Automatisierte Bereitstellung vollständig isolierter [Feuerix](https://github.com/ricten/Feuerix)-Instanzen
(je Verein eine eigene Datenbank, ein eigener Docker-Compose-Stack) – die Grundlage für echtes
Mehrmandanten-Hosting, im Unterschied zur bereits in Feuerix selbst eingebauten (aber pro Instanz auf einen
Verein gesperrten) Mandantenfähigkeit.

## Stand: Ausbaustufe 1 + 1b

Aktuell automatisiert:

- **.env-Generierung** je Mandant (frische Geheimnisse: `SECRET_KEY`, `FIELD_ENCRYPTION_KEY`,
  Postgres-Passwort, Admin-Passwort; verschlüsselt in der Portalverwaltungs-Datenbank gespeichert)
- **Port-Vergabe** (nächster freier Port ab `WEB_PORT_BASIS`)
- **Start des Feuerix-Docker-Compose-Stacks** unter einem eigenen Projektnamen (`mandant-<kürzel>`), direkt
  über die `docker-compose.yml` aus dem lokalen Feuerix-Checkout (`FEUERIX_REPO_PFAD`) – kein Kopieren, damit
  Änderungen daran automatisch für neue Bereitstellungen gelten
- **Bereitschaftsprüfung**: wartet, bis die Login-Seite antwortet (= Migration + automatische
  Ersteinrichtung durch Feuerix' eigenes `entrypoint.sh` sind durchgelaufen)
- **Reverse Proxy (Ausbaustufe 1b)**: Sobald `CADDY_ADMIN_URL` gesetzt ist (z. B. `http://127.0.0.1:2019`),
  erzeugt die Portalverwaltung aus dem Datenbestand ein Caddyfile mit einem Site-Block je *laufender*
  Instanz und lädt es über die Caddy-Admin-API ohne Neustart nach (`apps/mandanten/proxy.py`). Caddy holt das
  Let's-Encrypt-Zertifikat selbst. Die Konfiguration wird jedes Mal komplett neu erzeugt (idempotent). Ohne
  `CADDY_ADMIN_URL` bleibt die Automatisierung aus. Die Admin-API gibt vollen Zugriff auf Caddy – nur auf
  localhost erreichbar lassen. DNS muss auf den Server zeigen (Wildcard-DNS `*.domain` empfohlen).
- **Abbau**: `python manage.py mandant_abbauen <kürzel> [--daten-loeschen]` stoppt die Instanz und nimmt sie
  aus dem Reverse Proxy (Status „pausiert“; mit `--daten-loeschen` werden auch Datenbank und Medien
  unwiderruflich entfernt, Status „gelöscht“).
- **Eingabe-Härtung**: Vereinsname, Admin-Daten und Domain landen in `.env` bzw. Caddyfile und werden
  deshalb streng geprüft (keine Zeilenumbrüche/Steuerzeichen, gültiges Domain-Format) – auch wenn der
  Datensatz ohne Formular angelegt wurde.
- **Fehlerbehandlung**: fehlendes `docker`, Zeitüberschreitungen und Proxy-Fehler enden im Status
  „fehlgeschlagen“ (mit Protokoll) statt in „wird bereitgestellt“ hängen zu bleiben.

**Noch nicht automatisiert** (bewusst zurückgestellt):

- **Ausbaustufe 2**: OpenSlides/Paperless als optionale Zusatzdienste je Mandant.
- Backups je Mandant, Update-Rollout über alle Instanzen, Web-Oberfläche über den Django-Admin hinaus.

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

## Tests

Zwei Ebenen:

- **Unit-Tests** (`apps/mandanten/tests.py`, `python manage.py test --settings=config.settings_sqlite`):
  schnell, mocken `docker compose` und die HTTP-Bereitschaftsprüfung – prüfen die eigentliche Logik
  (Geheimniserzeugung, Port-Vergabe, `.env`-Inhalt, Status-Zustandsautomat).
- **Integrationstest** (`.github/workflows/integrationstest.yml`, läuft bei jedem Push nach `master`/`main`
  sowie manuell auslösbar): stellt **echt** eine Feuerix-Instanz per `docker compose` bereit (Checkout von
  Feuerix + Portalverwaltung, kein Mock), prüft die Login-Seite inhaltlich und – als Regressionsschutz für
  den bei Feuerix selbst gefundenen `DEBUG=True`-Vorfall – dass eine bereitgestellte Instanz tatsächlich mit
  `DEBUG=0` läuft. Baut den Stack am Ende wieder ab (`docker compose down -v`), auch bei einem Fehlschlag.
  Braucht keine GitHub Secrets, da alle Geheimnisse automatisch erzeugt werden.
