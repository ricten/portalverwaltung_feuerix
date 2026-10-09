# Portalverwaltung für Feuerix

Automatisierte Bereitstellung vollständig isolierter [Feuerix](https://github.com/ricten/Feuerix)-Instanzen
(je Verein eine eigene Datenbank, ein eigener Docker-Compose-Stack) – die Grundlage für echtes
Mehrmandanten-Hosting, im Unterschied zur bereits in Feuerix selbst eingebauten (aber pro Instanz auf einen
Verein gesperrten) Mandantenfähigkeit.

**Aktuelle Version:** siehe [VERSION.md](VERSION.md) (auch in der Fußzeile der Weboberfläche) ·
[Änderungsprotokoll](CHANGELOG.md) · **[Installationsanleitung](INSTALL.md)**

## Stand: Ausbaustufe 1, 1b und 2 (Betrieb)

Aktuell automatisiert:

- **.env-Generierung** je Mandant (frische Geheimnisse: `SECRET_KEY`, `FIELD_ENCRYPTION_KEY`,
  Postgres-Passwort, Admin-Passwort; verschlüsselt in der Portalverwaltungs-Datenbank gespeichert)
- **Port-Vergabe** (nächster freier Port ab `WEB_PORT_BASIS`; geprüft wird sowohl gegen alle Ports anderer Instanzen als auch per Bind-Versuch, ob der Port auf dem Server wirklich frei ist; OpenSlides bekommt je Instanz zusätzlich einen eigenen Verwaltungsport ab `OPENSLIDES_MANAGE_PORT_BASIS`)
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
  aus dem Reverse Proxy (Status „Stillgelegt“; mit `--daten-loeschen` werden auch Datenbank und Medien
  unwiderruflich entfernt, Status „gelöscht“).
- **Eingabe-Härtung**: Vereinsname, Admin-Daten und Domain landen in `.env` bzw. Caddyfile und werden
  deshalb streng geprüft (keine Zeilenumbrüche/Steuerzeichen, gültiges Domain-Format) – auch wenn der
  Datensatz ohne Formular angelegt wurde.
- **Fehlerbehandlung**: fehlendes `docker`, Zeitüberschreitungen und Proxy-Fehler enden im Status
  „fehlgeschlagen“ (mit Protokoll) statt in „wird bereitgestellt“ hängen zu bleiben.

- **Paperless-ngx je Mandant (Ausbaustufe 2)**: Ist im Datensatz eine `paperless_domain` gesetzt, startet die
  Portalverwaltung einen eigenen Compose-Stack `mandant-<kürzel>-paperless` (Vorlage `paperless/docker-compose.yml`
  aus Feuerix, eigene Datenbank/Geheimnisse/Port ab `PAPERLESS_PORT_BASIS`, `export`/`consume` je Mandant),
  trägt die Domain im Reverse Proxy ein, holt per API einen Token und hinterlegt die Anbindung in der
  Feuerix-Instanz (Verwaltung › Paperless-Anbindung) – der Verein muss nichts einrichten.
- **OpenSlides je Mandant (Ausbaustufe 2)**: Ist eine `openslides_domain` gesetzt, wird mit `osmanage`
  (einmal geladen, in `INSTANZEN_VERZEICHNIS/_werkzeuge` zwischengespeichert) ein eigener OpenSlides-4-Stack
  `mandant-<kürzel>-openslides` erzeugt (Tag aus Feuerix' `openslides/config.yml`, Port ab
  `OPENSLIDES_PORT_BASIS`), gestartet, einmalig mit Startdaten initialisiert und als Anbindung
  (Benutzer `superadmin`, Passwort verschlüsselt gespeichert) in Feuerix hinterlegt.
- **Betrieb**:
  - **Backup-Ziel** (Admin › Backup-Ziel): Sicherungen zusätzlich auf eine SMB-/Windows-/NAS-Freigabe hochladen
    (Server, Freigabe, Unterordner, Benutzer, Passwort verschlüsselt; Aufbewahrung in Tagen; optional lokale Kopie löschen;
    „Speichern und Verbindung testen“). Mit aktivem Ziel zählt eine Sicherung erst nach erfolgreichem Upload als aktuell.
  - **E-Mail-Benachrichtigung** (Admin › Benachrichtigungen): SMTP-Daten und Empfänger einstellen, Test-Mail senden. Mails
    nur bei Ereignissen: Ausfall/Wiederkehr (erst nach einstellbarer Dauer, Standard 10 Min.), fehlgeschlagene Sicherung,
    neue Feuerix-Version.
  - **Gesamtlog** (Admin › Gesamtlog): zentrale, nur lesbare Liste aller Ereignisse (Instanz-Protokolle, Aktionen der Benutzer mit
    Namen, Update-Prüfung und Rollout, Backup-Ziel, Mails, Einstellungsänderungen) mit Filter nach Ebene, Quelle und Kategorie
    sowie Suche; Aufbewahrung `LOG_AUFBEWAHRUNG_TAGE` (Standard 90 Tage).
  - **Versionen von Paperless-ngx und OpenSlides**: Die laufenden Versionen stehen in der Spalte „Version“. Die
    Update-Prüfung schaut auf GitHub nach neuen Releases und meldet sie (Hinweis im Admin, Gesamtlog, Mail), aktualisiert
    aber nichts automatisch. Das Update startest du je Instanz mit dem Pfeil-Knopf in der Spalte „Version“: Paperless
    macht vorher einen Export (`document_exporter`), OpenSlides einen Datenbank-Dump; schlägt das fehl, wird nichts
    geändert. Kein Hauptversionswechsel bei OpenSlides (von Hand). Abschalten der Prüfung: `UPDATE_DIENSTE_PRUEFEN=0`.
  - **Online-Check**: `python manage.py mandant_online_check` (vom Installer als systemd-Timer `portal-check.timer`, alle
    5 Minuten) prüft Instanzen und Backup-Ziel, speichert „offline seit“, protokolliert Wechsel und zeigt Ausfälle als roten
    Hinweis im Admin.
  - `python manage.py mandant_status` – Status/Erreichbarkeit aller Instanzen (Fehlercode bei Ausfall, gut für Monitoring/Cron)
  - `python manage.py mandant_backup [kürzel]` – Feuerix' `backup.sh` im Container, Ergebnis wird nach
    `INSTANZEN_VERZEICHNIS/<kürzel>/backups` kopiert (Paperless/OpenSlides-Daten: Volumes separat sichern)
  - **Update-Hinweis und Rollout**: Das Portal prüft im Hintergrund (`UPDATE_PRUEFUNG_STUNDEN`, Standard 6, 0 = aus), ob
    der Feuerix-Checkout hinter dem Remote zurückliegt, und zeigt dann auf der Startseite und in der Instanzliste die neue
    Version, die Änderungen und die veralteten Instanzen. **„Update jetzt ausrollen“** (nur auf Klick) macht `git pull
    --ff-only` und aktualisiert alle laufenden, noch nicht aktuellen Instanzen nacheinander (Pause
    `UPDATE_PAUSE_SEKUNDEN`); ein Fehler bei einer Instanz stoppt die übrigen nicht. Kommandozeile:
    `python manage.py mandant_update_pruefen [--ausrollen]`. **Vor jedem Update** wird geprüft, ob die letzte Sicherung der
    Instanz jünger als `UPDATE_BACKUP_MAX_ALTER_STUNDEN` (Standard 24) ist - sonst legt das Portal automatisch eine an
    (schlägt das fehl, wird nicht aktualisiert). Bei einer veralteten Instanz zeigt die Spalte „Version“ einen eigenen
    Update-Knopf. **Gestoppte Instanzen** zählen mit (ihre Version bleibt gespeichert): Das Update baut für sie nur das
    neue Image und lässt sie gestoppt; stillgelegte werden nicht beachtet. Das Portal selbst aktualisiert sich nicht über die Weboberfläche (`git pull` und Neustart bleiben Handarbeit,
    siehe INSTALL.md).
  - `python manage.py mandant_update [--git-pull] [--pause 10]` – rollt den aktuellen Feuerix-Code gestaffelt auf
    alle laufenden Instanzen aus (Migrationen laufen beim Start; ein Fehler stoppt die anderen nicht)
  - `python manage.py mandant_dienst <kürzel> stop|start|restart` – Instanz samt Paperless/OpenSlides anhalten,
    wieder starten oder neu starten (Container und Daten bleiben, kein Neubau; gestoppte Instanzen verschwinden
    aus dem Reverse Proxy und kommen beim Starten wieder hinein)
  - Django-Admin: Aktionen „Bereitstellen“, „Starten“, „Stoppen“, „Neustarten“, „Instanz stilllegen“, „Sichern“,
    „Aktualisieren“ (laufen über Celery). **Stoppen** hält die Container nur an (Status „Gestoppt“), **Instanz stilllegen**
    entfernt die Container (Status „Stillgelegt“, Datenbank und Medien bleiben); **Starten** geht aus beiden Zuständen. Start, Stopp und Neustart gibt es zusätzlich als Schaltflächen direkt
    in der Zeile jeder Instanz (nicht passende sind ausgegraut).

**Bewusst nicht enthalten**: Abrechnung/Tarife, Self-Service-Anmeldung für Vereine, automatische
Zertifikats-/Backup-Rotation über 14 Sicherungen hinaus (das übernimmt Feuerix' `backup.sh` je Instanz).

## Installation (interaktiv) und Weboberfläche

Ausführliche Schritt-für-Schritt-Anleitung samt Fehlerbehebung: **[INSTALL.md](INSTALL.md)**. Kurzfassung – auf einem
Linux-Server mit Docker (Repo vollständig klonen, ohne `sudo`): `./install.sh`. Der Assistent fragt, welche Komponenten installiert werden
sollen (PostgreSQL + Redis als Container, Caddy als Reverse Proxy, Celery-Worker und Weboberfläche als
systemd-Dienste, eigene Domain der Portalverwaltung), erzeugt `.env` mit Zufallsgeheimnissen, migriert und
legt den Administrator an. OpenSlides und Paperless wählst du je Verein über die Domain-Felder.

Im Instanzformular gibt es Haken „Paperless-ngx installieren“ und „OpenSlides installieren“ (ohne eigene Domain
wird `paperless.<domain>` bzw. `openslides.<domain>` genommen – DNS bzw. Wildcard-DNS nötig). Bei einer bereits
laufenden Instanz wird der Dienst nach dem Speichern automatisch nachinstalliert. Fehlt der Feuerix-Checkout, klont
der Installer ihn auf Wunsch selbst.

Die **Weboberfläche** ist das Django-Admin (`/admin/`, bei gesetzter `PORTAL_DOMAIN` automatisch über Caddy mit
HTTPS): Die Instanzliste zeigt Status, Version und eine **Live-Erreichbarkeit je Dienst** (grün/rot), die **Version der Portalverwaltung in der Fußzeile**, dazu
Aktionen zum Bereitstellen, Stilllegen, Sichern und Aktualisieren, das Protokoll je Instanz, einen
**Fortschrittsbalken mit aktuellem Schritt** während der Bereitstellung (aktualisiert sich ohne Neuladen) und die
Aktionen **„Zugangsdaten des Instanz-Administrators anzeigen“** (nur Superuser; zeigt das Startpasswort) und
**„Admin-Passwort der Instanz zurücksetzen“** (neues Passwort wird einmalig angezeigt). Die Startseite `/` leitet
auf `/admin/` um.

**Wartungsseite:** Hat eine Instanz eine Domain, aber keinen laufenden Dienst (neu angelegt, gestoppt, stillgelegt,
fehlgeschlagen, in Bereitstellung) – oder fällt ein laufender Dienst aus –, zeigt Caddy unter dieser Domain (auch
bei Paperless/OpenSlides) die Seite „Wartungsarbeiten“ mit HTTP 503 statt eines Browserfehlers. Voraussetzung:
Reverse-Proxy-Automatisierung (`CADDY_ADMIN_URL`) und DNS der Domain auf den Server.

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

## Produktivbetrieb (Beispiel systemd)

Der Celery-Worker läuft direkt auf dem Host (Docker-Zugriff, siehe oben). Beispiel
`/etc/systemd/system/portal-worker.service`:

```ini
[Unit]
Description=Portalverwaltung Celery-Worker
After=network.target docker.service redis.service

[Service]
User=portal
Group=docker
WorkingDirectory=/opt/portalverwaltung_feuerix
EnvironmentFile=/opt/portalverwaltung_feuerix/.env
ExecStart=/opt/portalverwaltung_feuerix/venv/bin/celery -A config worker -l info --concurrency 2
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Caddy läuft als Dienst/Container mit `--network host` bzw. Admin-API nur auf `127.0.0.1:2019`. Für die
Überwachung genügt ein Cron/Timer mit `python manage.py mandant_status`, für Sicherungen ein nächtlicher
`python manage.py mandant_backup`. Auf dem Server müssen die Ports `PAPERLESS_PORT_BASIS`ff. und
`OPENSLIDES_PORT_BASIS`ff. nur lokal (127.0.0.1) gebunden sein – der öffentliche Zugriff läuft ausschließlich
über den Reverse Proxy.

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
  sowie manuell auslösbar): stellt **echt** eine Feuerix-Instanz (inkl. Paperless; ein zweiter Job OpenSlides) per `docker compose` bereit (Checkout von
  Feuerix + Portalverwaltung, kein Mock), prüft die Login-Seite inhaltlich und – als Regressionsschutz für
  den bei Feuerix selbst gefundenen `DEBUG=True`-Vorfall – dass eine bereitgestellte Instanz tatsächlich mit
  `DEBUG=0` läuft. Baut den Stack am Ende wieder ab (`docker compose down -v`), auch bei einem Fehlschlag.
  Braucht keine GitHub Secrets, da alle Geheimnisse automatisch erzeugt werden.
