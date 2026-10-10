# Portalverwaltung für Feuerix

Automatisierte Bereitstellung und Verwaltung vollständig isolierter [Feuerix](https://github.com/ricten/Feuerix)-Instanzen
(je Verein eine eigene Datenbank, ein eigener Docker-Compose-Stack, optional mit Paperless-ngx und OpenSlides) – die
Grundlage für echtes Mehrmandanten-Hosting, im Unterschied zur in Feuerix selbst eingebauten (aber pro Instanz auf einen
Verein gesperrten) Mandantenfähigkeit.

**Aktuelle Version:** siehe [VERSION.md](VERSION.md) (steht auch in der Fußzeile der Weboberfläche) ·
[Änderungsprotokoll](CHANGELOG.md) · **[Installationsanleitung](INSTALL.md)**

## Inhalt

1. [Funktionen im Überblick](#funktionen-im-überblick)
2. [Weboberfläche](#weboberfläche)
3. [Installation](#installation)
4. [Einstellungen (`.env`)](#einstellungen-env)
5. [Kommandozeile](#kommandozeile)
6. [Sicherung und Wiederherstellung](#sicherung-und-wiederherstellung)
7. [Updates](#updates)
8. [Überwachung, Benachrichtigung, Gesamtlog](#überwachung-benachrichtigung-gesamtlog)
9. [Sicherheit](#sicherheit)
10. [Produktivbetrieb](#produktivbetrieb)
11. [Entwicklung und Tests](#entwicklung-und-tests)
12. [Grenzen](#grenzen)

## Funktionen im Überblick

**Bereitstellung**
- **`.env`-Generierung** je Mandant mit frischen Geheimnissen (`SECRET_KEY`, `FIELD_ENCRYPTION_KEY`, Postgres- und
  Admin-Passwort), verschlüsselt in der Datenbank der Portalverwaltung gespeichert; Eingaben (Vereinsname, Domain, Admin-Daten)
  werden streng geprüft, weil sie in `.env` und Caddyfile landen.
- **Feuerix-Stack** unter eigenem Projektnamen (`mandant-<kürzel>`), direkt über die `docker-compose.yml` aus dem lokalen
  Feuerix-Checkout (`FEUERIX_REPO_PFAD`), Bereitschaftsprüfung (Login-Seite antwortet = Migration und Ersteinrichtung
  durch).
- **Paperless-ngx** und **OpenSlides 4** je Mandant als eigene Stacks (`mandant-<kürzel>-paperless`, `…-openslides`), per
  Haken im Instanzformular oder über die Domain-Felder; die Anbindung in Feuerix (Token bzw. Superadmin) wird automatisch
  hinterlegt. Ohne eigene Domain wird `paperless.<domain>` bzw. `openslides.<domain>` genommen.
- **Port-Vergabe**: nächster freier Port ab `WEB_PORT_BASIS` usw. Frei heißt: von keiner Instanz in irgendeinem Port-Feld
  belegt **und** auf dem Server tatsächlich nicht gebunden (Bind-Versuch). OpenSlides bekommt je Instanz einen eigenen
  Verwaltungsport (`OPENSLIDES_MANAGE_PORT_BASIS`), damit sich mehrere Instanzen nicht den Standard-Port 9002 teilen.
- **Reverse Proxy (Caddy)**: Mit `CADDY_ADMIN_URL` erzeugt das Portal aus dem Datenbestand das komplette Caddyfile (ein
  Site-Block je laufender Instanz und Dienst) und lädt es über die Admin-API ohne Neustart nach; Let's-Encrypt-Zertifikate holt
  Caddy selbst. DNS muss auf den Server zeigen (Wildcard-DNS `*.domain` empfohlen).
- **Wartungsseite**: Hat eine Instanz eine Domain, aber keinen laufenden Dienst (neu, gestoppt, stillgelegt, fehlgeschlagen,
  in Arbeit) oder fällt ein laufender Dienst aus, zeigt Caddy unter dieser Domain eine Seite „Wartungsarbeiten“ (HTTP 503)
  statt eines Browserfehlers.

**Betrieb einer Instanz** (Admin-Aktionen und Schaltflächen in der Instanzzeile)
- **Starten / Stoppen / Neustarten** (Container werden nur angehalten bzw. neu gestartet, nichts wird neu gebaut).
- **Instanz stilllegen**: Container und Netzwerk werden entfernt, Datenbank und Medien bleiben in den Volumes
  (Status „Stillgelegt“); **Starten** geht aus „Gestoppt“ und „Stillgelegt“.
- **Zugangsdaten anzeigen** (Superuser; zeigt das Startpasswort) und **Admin-Passwort der Instanz zurücksetzen**.
- **Instanz endgültig löschen** mit Abschlusssicherung (siehe unten).
- **Hängende Instanz zurücksetzen**: Bleibt eine Instanz auf „In Arbeit“ stehen (z. B. Worker währenddessen neu gestartet), ermittelt das Portal den
  echten Zustand über Docker und setzt den Status – nur, wenn der Vorgang nicht mehr aktiv aussieht (15 Minuten ohne Fortschritt, kein
  Auftrag beim Worker). Ein verwaister Rollout lässt sich im Update-Hinweis mit „Sperre aufheben“ freigeben.
- Jede länger laufende Aktion zeigt einen **Fortschrittsbalken mit aktuellem Schritt** (aktualisiert sich ohne Neuladen);
  scheitert ein Schritt, nennt der Balken, wo es hing.

**Weiteres**: Sicherung/Wiederherstellung, Updates, Überwachung, Benachrichtigung und Gesamtlog – jeweils in eigenen
Abschnitten unten.

### Status einer Instanz

| Status | Bedeutung |
|---|---|
| Neu | angelegt, noch nicht bereitgestellt |
| In Arbeit | Bereitstellung, Start, Stopp, Update, Sicherung, Wiederherstellung o. Ä. läuft gerade |
| Läuft | alles in Ordnung |
| Gestoppt | Container angehalten (Daten und Container bleiben) |
| Stillgelegt | Container entfernt, Daten bleiben |
| Fehlgeschlagen | ein Schritt ist gescheitert (Schritt im Balken, Grund im Protokoll) |
| Gelöscht | Daten entfernt (Instanzordner liegt in `instanzen/_geloescht/`) |

## Weboberfläche

Die Weboberfläche ist das Django-Admin (`/admin/`, bei gesetzter `PORTAL_DOMAIN` automatisch über Caddy mit HTTPS; die
Startseite `/` leitet dorthin um).

- **Mandanten-Instanzen**: Status, Fortschritt, Live-Erreichbarkeit je Dienst, **Version** (Feuerix, Paperless, OpenSlides – mit
  Pfeil-Knopf, wenn ein Update bereitsteht), Steuerung (Start/Stopp/Neustart als runde Symbol-Knöpfe) und das Protokoll je Instanz.
- **Hinweise oben** auf Startseite und Instanzliste: verfügbares Feuerix-Update samt Änderungen und veralteten Instanzen (mit
  „Update jetzt ausrollen“), neue Versionen von Paperless/OpenSlides, nicht erreichbare Instanzen oder Backup-Ziele sowie
  „Updates gerade nicht möglich“, solange Instanzen in Arbeit sind.
- **Backup-Ziel**, **Benachrichtigungen** und **Gesamtlog** als eigene Seiten.
- Die **Version der Portalverwaltung** steht in der Fußzeile.

Admin-Aktionen (Sammelaktionen): Bereitstellen, Starten, Stoppen, Neustarten, Instanz stilllegen, Aus Sicherung
wiederherstellen, Instanz endgültig löschen, Sichern (Feuerix, Paperless, OpenSlides), Auf aktuellen Feuerix-Code
aktualisieren, Zugangsdaten anzeigen, Admin-Passwort zurücksetzen. Alles Länger­laufende läuft über Celery im Hintergrund.

## Installation

Ausführliche Schritt-für-Schritt-Anleitung samt Fehlerbehebung: **[INSTALL.md](INSTALL.md)**. Kurzfassung: Auf einem
Linux-Server mit Docker das Repo **vollständig klonen** und `./install.sh` **ohne `sudo`** starten. Der Assistent fragt, welche
Komponenten installiert werden (PostgreSQL + Redis als Container, Caddy, Celery-Worker und Weboberfläche als systemd-Dienste,
eigene Domain der Portalverwaltung, Online-Check-Timer), klont bei Bedarf den Feuerix-Checkout, erzeugt `.env` mit
Zufallsgeheimnissen, migriert und legt den Administrator an. Danach: **Mandanten-Instanzen → Hinzufügen**, dann Aktion
„Bereitstellen“.

## Einstellungen (`.env`)

Alle Werte stehen mit Erklärung in [.env.example](.env.example); der Installer erzeugt die Datei. `manage.py` liest die `.env`
im Projektverzeichnis selbst, bereits gesetzte Umgebungsvariablen (systemd) haben Vorrang.

| Variable | Standard | Wirkung |
|---|---|---|
| `SECRET_KEY`, `FIELD_ENCRYPTION_KEY` | – (Pflicht) | Django-Schlüssel; der Fernet-Schlüssel verschlüsselt alle Instanz-Geheimnisse – **sichern** |
| `POSTGRES_*`, `REDIS_URL` | – | Datenbank und Broker der Portalverwaltung |
| `DEBUG`, `ALLOWED_HOSTS`, `HTTPS` | 0 / lokal / 0 | Betriebsmodus der Weboberfläche |
| `FEUERIX_REPO_PFAD` | `../vereinsverwaltung` | lokaler Feuerix-Checkout (Compose-Dateien, Update-Quelle) |
| `INSTANZEN_VERZEICHNIS` | `./instanzen` | `.env`, Sicherungen und Dienstordner je Instanz; `_geloescht/` für Archive |
| `WEB_PORT_BASIS`, `PAPERLESS_PORT_BASIS`, `OPENSLIDES_PORT_BASIS`, `OPENSLIDES_MANAGE_PORT_BASIS` | 8100 / 10100 / 9100 / 9200 | erster zu vergebender Port je Dienst |
| `PORT_PRUEFUNG` | 1 | Ports vor der Vergabe auf Belegung prüfen |
| `CADDY_ADMIN_URL`, `CADDY_ACME_EMAIL` | leer | Reverse-Proxy-Automatisierung (leer = aus) und Let's-Encrypt-Adresse |
| `PORTAL_DOMAIN`, `PORTAL_PORT` | leer / 8000 | eigene Domain der Weboberfläche im Reverse Proxy |
| `UPDATE_PRUEFUNG_STUNDEN` | 6 | Intervall der Update-Prüfung (0 = aus) |
| `UPDATE_PAUSE_SEKUNDEN` | 10 | Pause zwischen den Instanzen beim Rollout |
| `UPDATE_DIENSTE_PRUEFEN` | 1 | Versionen von Paperless/OpenSlides erfassen und auf GitHub nach neuen Releases schauen |
| `UPDATE_BACKUP_MAX_ALTER_STUNDEN` | 24 | höchstes Alter einer Sicherung, ab dem vor einem Update neu gesichert wird |
| `LOG_AUFBEWAHRUNG_TAGE` | 90 | Aufbewahrung des Gesamtlogs (0 = nie löschen) |
| `PROXY_NUR_HTTP` | 0 | nur für Tests/lokal: Caddy ohne Zertifikate |

## Kommandozeile

Alles Wichtige geht im Admin; für Skripte, Cron und Notfälle gibt es `./venv/bin/python manage.py …`:

| Befehl | Zweck |
|---|---|
| `mandant_bereitstellen <kürzel>` | Instanz bereitstellen (blockierend; im Admin läuft es über Celery) |
| `mandant_status` | Status und Erreichbarkeit aller Instanzen (Fehlercode bei Ausfall, gut für Monitoring) |
| `mandant_online_check [--streng]` | Online-Check: Instanzen und Backup-Ziel prüfen und den Zustand speichern |
| `mandant_dienst <kürzel> stop\|start\|restart` | Instanz samt Paperless/OpenSlides anhalten, starten, neu starten |
| `env_entschluesseln <datei> [--ausgabe .env]` | die mit der Sicherung abgelegte, verschlüsselte `.env` wieder lesbar machen |
| `mandant_backup [kürzel]` | vollständige Sicherung (Feuerix, Paperless-Export, OpenSlides-Dump) |
| `mandant_update_pruefen [--ausrollen]` | auf ein neues Feuerix prüfen, auf Wunsch sofort ausrollen (mit Sicherung vorab) |
| `mandant_update [--git-pull] [--pause N]` | Feuerix-Code gestaffelt auf alle laufenden Instanzen ausrollen (ohne Sicherungsprüfung) |
| `mandant_abbauen <kürzel> [--daten-loeschen [--ohne-sicherung]]` | stilllegen bzw. mit Abschlusssicherung endgültig löschen |

## Sicherung und Wiederherstellung

**Sichern** (Admin-Aktion „Sichern“ bzw. `mandant_backup`) sichert alle Dienste einer laufenden Instanz:
- **Feuerix**: Datenbank und Medien über `scripts/backup.sh` im Container, Kopie nach `instanzen/<kürzel>/backups`.
- **Paperless-ngx** (falls eingerichtet): Export mit `document_exporter` nach `instanzen/<kürzel>/paperless/export`.
- **OpenSlides** (falls eingerichtet): SQL-Dump nach `instanzen/<kürzel>/openslides/backups`.

Die Docker-Volumes selbst werden nicht kopiert. Ein Fehler bei einem Zusatzdienst verhindert die anderen nicht; am Ende werden
alle Fehler gemeldet (Protokoll, auf Wunsch Mail).

**Backup-Ziel (Netzwerkfreigabe)**: Im Admin unter **Backup-Ziel** (Server, Freigabe, Unterordner, Benutzer, Passwort
verschlüsselt gespeichert; **Haken „aktiv“ nicht vergessen** – die Seite zeigt oben, ob wirklich hochgeladen wird, wann zuletzt
und ob die Freigabe erreichbar ist). Unterstützt wird SMB/CIFS (Windows-Freigabe, NAS, Samba); das Portal schreibt direkt per
SMB, ohne etwas zu mounten oder Root-Rechte. Hochgeladen wird nach `<Freigabe>\<Unterordner>\<kürzel>\` (Feuerix),
`…\openslides\` und `…\paperless\`; die Dateigröße wird geprüft. Optional: Aufbewahrung in Tagen (gilt für Feuerix und OpenSlides,
nicht für den Paperless-Export, der als Abbild gespiegelt wird) und „lokale Kopie nach Upload löschen“. Mit aktivem Ziel gilt eine
Sicherung erst nach erfolgreichem Upload als aktuell; schlägt der Upload fehl, wird nicht aktualisiert oder gelöscht.

**Aus Sicherung wiederherstellen** (Admin-Aktion, nur Superuser; Kürzel zur Bestätigung eintippen; vorher wird standardmäßig der
jetzige Stand gesichert; die anderen Dienste der Instanz bleiben unberührt):
- **Feuerix**: Datenbank leeren, Datenbank und optional Medien mit Feuerix' `restore.sh` zurückspielen; Sicherungen lokal oder von
  der Freigabe (wird heruntergeladen).
- **OpenSlides**: nach der Anleitung des Herstellers – herunterfahren, nur die Datenbank starten, leeren, Dump einspielen,
  starten.
- **Paperless-ngx (experimentell)**: Datenbank leeren und den Export mit dem Importer von Paperless in einem einmaligen Container
  einspielen; die Feuerix-Anbindung bekommt danach einen neuen Token. Angeboten werden der letzte Export und die Exporte
  `_vorher-…` vom Stand vor einer Wiederherstellung (Paperless überschreibt den Ordner `export` bei jedem Export).
- Bricht es ab, nachdem die Datenbank geleert wurde, nennt das Protokoll, wo der Stand davor liegt.

**`.env` der Instanz**: Jede Sicherung legt die `.env` (mit `FIELD_ENCRYPTION_KEY`, ohne den die verschlüsselten Felder einer Sicherung
auf einem neuen Server unlesbar wären) **verschlüsselt** als `env-<hash>.enc` mit ab – lokal und auf der Freigabe, also auch in der letzten
Sicherung vor dem endgültigen Löschen. Verschlüsselt wird mit dem `FIELD_ENCRYPTION_KEY` der Portalverwaltung (den du ohnehin sichern musst);
Entschlüsseln: `./venv/bin/python manage.py env_entschluesseln <datei> [--ausgabe .env]`.

**Endgültig löschen** (Admin-Aktion „Instanz ENDGÜLTIG löschen …“ bzw. `mandant_abbauen --daten-loeschen`): Das Portal startet die
Instanz bei Bedarf und legt eine **Abschlusssicherung aller Dienste** an. **Schlägt sie fehl, wird nichts gelöscht.** Danach
werden Container und Volumes entfernt und der Instanzordner (mit `.env` und den Schlüsseln, Sicherungen und Exporten) nach
`instanzen/_geloescht/<kürzel>-<Zeit>` verschoben – er wird nie automatisch entfernt. Der normale „Löschen“-Knopf löscht nur
Datensätze von nie bereitgestellten oder bereits gelöschten Instanzen.

## Updates

- **Feuerix**: Das Portal prüft im Hintergrund (`UPDATE_PRUEFUNG_STUNDEN`) per `git fetch`, ob der Checkout hinter dem Remote
  liegt, und zeigt Version, Änderungen und veraltete Instanzen. **„Update jetzt ausrollen“** (nur auf Klick) macht
  `git pull --ff-only` und aktualisiert alle laufenden, noch nicht aktuellen Instanzen nacheinander; ein Fehler bei einer
  Instanz stoppt die übrigen nicht. Einzelne Instanzen aktualisierst du mit dem Pfeil-Knopf in der Spalte „Version“ (auch dabei
  wird zuerst der Code gezogen). **Gestoppte Instanzen** zählen mit: Für sie wird nur das Image gebaut, sie bleiben gestoppt.
  Stillgelegte werden nicht beachtet. Während Instanzen „In Arbeit“ sind, sind keine Updates möglich.
- **Sicherung vor jedem Update**: Ist die letzte Sicherung einer laufenden Instanz (Feuerix, Paperless-Export, OpenSlides-Dump)
  älter als `UPDATE_BACKUP_MAX_ALTER_STUNDEN`, legt das Portal vorher eine neue an; schlägt das fehl, wird nicht aktualisiert.
- **Paperless-ngx und OpenSlides**: Die laufenden Versionen stehen in der Spalte „Version“ (Paperless wird direkt aus dem
  Container gelesen, OpenSlides aus der Compose-Datei). Das Portal fragt auf GitHub nach neuen Releases und meldet sie
  (Hinweis, Gesamtlog, Mail), aktualisiert aber nichts von selbst. Das Update startest du mit dem Pfeil-Knopf: Paperless macht
  vorher einen Export und holt das neue Image; OpenSlides sichert vorher die Datenbank und aktualisiert **nur Patch-Versionen**
  (z. B. 4.3.0 → 4.3.5) nach der offiziellen Anleitung. Minor- und Major-Sprünge (Migrationen, PostgreSQL-Wechsel) bitte von
  Hand. Neue OpenSlides-Instanzen bekommen die in Feuerix' `openslides/config.yml` festgelegte Version.
- **Die Portalverwaltung selbst** aktualisierst du von Hand (`git pull`, `pip install -r requirements.txt`, `manage.py migrate`,
  Dienste neu starten) – siehe INSTALL.md.

## Überwachung, Benachrichtigung, Gesamtlog

- **Online-Check** (`mandant_online_check`, vom Installer als systemd-Timer `portal-check.timer` alle 5 Minuten eingerichtet):
  prüft alle Dienste laufender Instanzen und das Backup-Ziel, merkt sich „offline seit“, schreibt Wechsel ins Protokoll und
  zeigt Ausfälle als roten Hinweis im Admin. Ohne den Timer gibt es keine Ausfallmeldungen.
- **E-Mail-Benachrichtigung** (Admin › Benachrichtigungen: SMTP-Server, Verschlüsselung, Benutzer, Passwort verschlüsselt, Absender,
  Empfänger, „Speichern und Test-Mail senden“). Mails gibt es nur bei Ereignissen, jedes einzeln abschaltbar: Instanz oder Backup-Ziel
  länger als einstellbar (Standard 10 Min.) offline und wieder erreichbar, fehlgeschlagene Sicherung (auch Upload, Zusatzdienste),
  fehlgeschlagenes Dienst-Update, neue Version von Feuerix, Paperless oder OpenSlides (einmal je Version). Ein Versandfehler stört nie
  den eigentlichen Vorgang.
- **Gesamtlog** (Admin › Gesamtlog): zentrale, nur lesbare Liste aller Ereignisse – Instanz-Protokolle, Aktionen der Benutzer mit
  Namen, Update-Prüfung und Rollout, Backup-Ziel, Mails, Einstellungsänderungen (ohne Passwörter) – mit Filter nach Ebene, Quelle,
  Kategorie, Suche und Datumsgliederung; Aufbewahrung `LOG_AUFBEWAHRUNG_TAGE`.

## Sicherheit

- Der Celery-Worker führt echte `docker compose`-Befehle aus – Zugriff auf den Docker-Daemon ist praktisch **root-äquivalent**. Der
  Worker läuft deshalb **direkt auf dem Host** (nicht in einem Container mit gemountetem Docker-Socket); die Portalverwaltung selbst
  wird (noch) nicht containerisiert ausgeliefert. Den Installer **nicht mit `sudo`** starten (er ruft `sudo` selbst auf, wo nötig).
- Die Geheimnisse jeder Instanz und das Passwort des Backup-Ziels und des Mailservers liegen verschlüsselt in der Datenbank der
  Portalverwaltung (Fernet, `FIELD_ENCRYPTION_KEY` – **unbedingt sichern**, ohne ihn sind sie unlesbar) und werden bei erneutem
  Bereitstellen wiederverwendet, nie überschrieben. Passwörter werden in der Oberfläche nie wieder angezeigt (Ausnahme: die
  ausdrückliche Aktion „Zugangsdaten anzeigen“ für Superuser, im Gesamtlog vermerkt).
- Die Admin-API von Caddy gibt vollen Zugriff auf Caddy – nur auf localhost erreichbar lassen. Die Ports von Instanzen und
  Zusatzdiensten sind nur an `127.0.0.1` gebunden; der öffentliche Zugriff läuft ausschließlich über den Reverse Proxy.
- Sicherungen enthalten alle Daten der Instanzen. Das Portal setzt auf diese Dateien keine besonderen Dateirechte – den
  Zugriff auf `instanzen/` bitte über die Rechte des Servers einschränken.

## Produktivbetrieb

Der Installer richtet auf Wunsch die systemd-Dienste `portal-web` (Gunicorn) und `portal-worker` (Celery) sowie den Timer
`portal-check.timer` ein; Beispiele und das nachträgliche Einrichten des Timers stehen in [INSTALL.md](INSTALL.md). Caddy läuft als
Dienst/Container mit `--network host`, die Admin-API nur auf `127.0.0.1:2019`. Beispiel `portal-worker.service`:

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

Der Dienstbenutzer muss `instanzen/` und den Feuerix-Checkout besitzen (Schreibrechte für `git pull`) und in der Gruppe
`docker` sein. Nach jedem `git pull` der Portalverwaltung: `pip install -r requirements.txt`, `manage.py migrate`,
`manage.py collectstatic`, Dienste neu starten.

## Entwicklung und Tests

```bash
cp .env.example .env   # Werte setzen
pip install -r requirements.txt
python manage.py migrate --settings=config.settings_sqlite   # ohne echten Postgres/Redis
python manage.py test --settings=config.settings_sqlite
```

`FEUERIX_REPO_PFAD` muss auf einen lokalen Checkout von [Feuerix](https://github.com/ricten/Feuerix) zeigen (Standard
`../vereinsverwaltung`, passend zu einer Ordnerstruktur, in der beide Repos nebeneinander liegen).

Zwei Ebenen von Tests:

- **Unit-Tests** (`apps/mandanten/test*.py`, rund 340 Tests, laufen in etwa einer Minute): mocken `docker compose`, HTTP, SMB,
  SMTP und GitHub – prüfen die Logik (Geheimniserzeugung, Port-Vergabe, Zustandsautomat, Sicherung, Wiederherstellung,
  Updates, Benachrichtigung, Admin-Ansichten).
- **Integrationstest** (`.github/workflows/integrationstest.yml`, bei jedem Push nach `master`/`main` und manuell): stellt **echt**
  eine Feuerix-Instanz (inkl. Paperless; ein zweiter Job OpenSlides) per `docker compose` bereit, mit echtem Caddy, prüft die
  Login-Seite und – als Regressionsschutz – dass eine bereitgestellte Instanz mit `DEBUG=0` läuft, und baut alles wieder ab.

Wichtig: Sicherung, Wiederherstellung, Backup-Ziel, Mailversand und die Dienst-Updates sind durch Tests mit nachgebildeten
Systemen abgesichert, nicht durch den Integrationstest. Neue Funktionen bitte zuerst an einer Test-Instanz ausprobieren.

## Grenzen

Bewusst nicht enthalten: Abrechnung/Tarife, Self-Service-Anmeldung für Vereine, ein Update der Portalverwaltung über die
Weboberfläche, Wiederherstellung einer bereits **gelöschten** Instanz aus dem Archiv.

Zu beachten:
- Das Backup-Ziel unterstützt SMB/CIFS, kein NFS.
- Lokale Sicherungen unter `instanzen/<kürzel>/backups` werden vom Portal nicht rotiert (es sei denn, „lokale Kopie nach Upload
  löschen“ ist aktiv); Feuerix' `backup.sh` behält nur die letzten 14 Sicherungen im Container.
- Die Wiederherstellung von Paperless ist experimentell; die von OpenSlides und Paperless ersetzt jeweils die komplette Datenbank
  des Dienstes.
- OpenSlides-Updates aus dem Portal gibt es nur für Patch-Versionen; die Paperless-Version folgt dem Image-Tag `latest`.
