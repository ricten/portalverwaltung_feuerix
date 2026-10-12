# Changelog

Alle nennenswerten Änderungen an der Portalverwaltung, neueste zuerst. Versionsnummern folgen Semantic
Versioning (siehe [VERSION.md](VERSION.md)). Die Einträge bis 0.6.0 wurden nachträglich aus der Git-Historie
zusammengestellt.

## 0.33.1 (2026-10-12)
- Versionsspalte: Der Knopf „Feuerix aktualisieren“ mit „→ neue Version“ steht jetzt direkt hinter der Feuerix-Version statt unter den
  Paperless-/OpenSlides-Zeilen, wo er wie deren Update wirkte.

## 0.33.0 (2026-10-12)
- **Zwei-Faktor-Anmeldung (2FA)** für die Weboberfläche: Authenticator-App (TOTP, z. B. Google/Microsoft Authenticator, Aegis, FreeOTP) plus
  10 Einmal-Notfallcodes. **Superuser müssen sie nutzen** (beim nächsten Login: QR-Code scannen, Code bestätigen, Notfallcodes sichern); andere
  Benutzer können sie freiwillig einrichten (Link „Zwei-Faktor“ oben rechts) und müssen den Code dann immer eingeben. Einstellung
  `ZWEI_FAKTOR=superuser|alle|aus`. Handy verloren: `manage.py zwei_faktor_zuruecksetzen BENUTZER`. Falsche Codes werden von django-otp
  mit wachsender Wartezeit gebremst. Neue Pakete `django-otp` und `segno` (`pip install -r requirements.txt`), Migration für die Geräte-Tabellen.

## 0.32.0 (2026-10-12)
Restliche Punkte der Sicherheitsprüfung:
- **Anmelde-Sperre**: Nach 5 Fehlversuchen (je Adresse und je Benutzername, einstellbar mit `LOGIN_MAX_VERSUCHE`) ist die Anmeldung
  15 Minuten gesperrt (`LOGIN_SPERRE_MINUTEN`), auch mit richtigem Passwort; die Anmeldeseite nennt die Uhrzeit. Ein Erfolg setzt die Zähler
  zurück. Aufheben von Hand: `manage.py login_entsperren`. Neue Tabelle (Migration 0019).
- **osmanage mit Prüfsumme**: Der Download wird gegen die von GitHub veröffentlichte SHA-256 des Releases geprüft (ohne Prüfsumme wird nichts
  ausgeführt) und die Summe in `osmanage.sha256` gemerkt; vor jeder Nutzung wird die Datei dagegen geprüft, eine veränderte Datei wird nicht
  ausgeführt. Dasselbe Merken gilt für die Compose-Vorlage. Bestehende Dateien werden beim ersten Aufruf übernommen.
- **SMB verschlüsselt**: Neue Option „Übertragung verschlüsseln (SMB 3)“ im Backup-Ziel, standardmäßig an. Unterstützt der Server das nicht
  (sehr alte NAS), meldet der Verbindungstest einen Fehler; dann die Option abschalten.

## 0.31.0 (2026-10-12)
**Sicherheitsprüfung des Codes - behobene Schwachstellen** (Abhängigkeiten geprüft: keine bekannten Schwachstellen; Eingaben, die in `.env`
und Caddyfile fließen, werden bereits streng validiert; keine Shell-Aufrufe, keine rohen SQL-Abfragen):
- **Hoch: Admin-Aktionen ohne Rechteprüfung.** Jeder Benutzer, der die Instanzliste sehen durfte (auch nur mit Leserecht), konnte Aktionen wie
  Stoppen, Sichern, Aktualisieren ausführen. Alle Aktionen sind jetzt an das Änderungsrecht gebunden; Zugangsdaten anzeigen, Admin-Passwort
  zurücksetzen, Wiederherstellen, endgültig Löschen und „Hängende Instanz zurücksetzen“ nur noch für Superuser (Zugangsdaten und Passwort-Reset
  waren vorher ein Weg, sich Administratorrechte in jeder Instanz zu verschaffen).
- **Hoch: Backup-Ziel und Benachrichtigung** (Ziel aller Sicherungen bzw. Mailserver) konnte jeder Benutzer mit Änderungsrecht umstellen, also
  etwa alle Sicherungen auf einen fremden Server umleiten. Beides ist jetzt nur für Superuser sichtbar und änderbar.
- **Mittel: Geheimnisse auf der Platte.** Die `.env`-Dateien der Instanzen (Schlüssel, Passwörter), Dumps und Sicherungen waren für andere
  Benutzer des Servers lesbar. Instanzordner sind jetzt 0700, `.env`-Dateien und Dumps 0600 (von Anfang an, ohne Zeitfenster mit offenen Rechten).
  Bestehende Installationen werden mit `manage.py rechte_haerten` (läuft auch automatisch beim Online-Check) korrigiert; Paperlesss Ordner
  `export`/`consume` bleiben unverändert, weil dort der Container schreibt.
- **Mittel: Redis ohne Passwort.** Jeder lokale Benutzer hätte Celery-Aufträge einreihen können. Neue Installationen starten Redis mit Passwort
  (in `REDIS_URL`); für bestehende siehe INSTALL.md.
- **Niedrig:** Weiterleitung nach Aktionen folgte dem `Referer` ungeprüft (Open Redirect) - jetzt nur auf die eigene Seite. Meldungen mit
  angezeigten Zugangsdaten lagen in einem Browser-Cookie - jetzt in der serverseitigen Sitzung. Die Anmeldung läuft nach 12 Stunden ab
  (`SESSION_DAUER_STUNDEN`).
- Nicht geändert, aber in der README unter „Sicherheit“ beschrieben: kein Schutz gegen wiederholte Anmeldeversuche (Sperre empfohlen),
  `osmanage` wird ohne Prüfsumme von GitHub geladen, SMB-Übertragung ist signiert, aber nicht verschlüsselt.
- Migration: keine.

## 0.30.0 (2026-10-12)
- Neu: **Demo zurücksetzen.** Direkt nach dem Anlegen der Beispieldaten speichert das Portal den **Demo-Standard** (Datenbank und Medien
  in `instanzen/<kürzel>/demo-standard/`, bei aktivem Backup-Ziel zusätzlich auf der Freigabe). Die Aktion **„Demo auf Standard zurücksetzen“**
  (auch als lila Knopf in der Zeile der Demo-Instanz, mit Rückfrage; `manage.py mandant_demo_zuruecksetzen <kürzel>`, z. B. für Cron) ersetzt
  alle Daten der Instanz durch den Standard, wenn andere eigene Daten eingegeben haben: Datenbank und hochgeladene Medien werden geleert und
  der Standard wird eingespielt; das Admin-Passwort wird danach wieder auf den im Portal gespeicherten Wert gesetzt. Mit **„Aktuellen Stand
  als Demo-Standard speichern“** (bzw. `--standard-speichern`) legst du einen veränderten Zustand als neuen Standard fest.
- Datenbank-Migration 0018.

## 0.29.0 (2026-10-12)
- Neu: **Demo-Instanzen mit Beispieldaten.** Im Instanzformular gibt es den Haken „Demo-Instanz (mit Beispieldaten)“ mit Anzahl der
  Beispiel-Mitglieder (Standard 40) und der Option „Beispiel-Inventar anlegen“. Bei der Bereitstellung führt das Portal in der Instanz
  Feuerix' Befehl `beispieldaten` aus (fiktive Mitglieder mit Beitragsrechnungen, Zahlungen und Mahnungen, Beispiel-Inventar). Das
  geschieht nur einmal je Instanz (der Befehl würde sonst bei jedem Lauf neue Mitglieder anlegen); Demo-Instanzen tragen in der Liste
  ein „DEMO“-Abzeichen und lassen sich filtern. Für eine schon laufende Instanz: Haken setzen, speichern und die Aktion „Beispieldaten
  anlegen (nur Demo-Instanzen)“ bzw. `manage.py mandant_beispieldaten <kürzel> [--anzahl N] [--ohne-inventar]` ausführen.
- Datenbank-Migration 0017.

## 0.28.0 (2026-10-10)
- Neu: **Die `.env` der Instanz wird mit jeder Sicherung mitgesichert** - auch mit der letzten Sicherung vor dem endgültigen Löschen - und
  liegt dann bei bestehendem Backup-Ziel auf der Netzwerkfreigabe (`<kürzel>\env-<hash>.enc`). Sie enthält den `FIELD_ENCRYPTION_KEY`,
  ohne den die verschlüsselten Felder einer Sicherung auf einem neuen Server unlesbar wären. Damit Sicherung und Schlüssel nicht im
  Klartext nebeneinander liegen, ist sie **verschlüsselt** (mit dem `FIELD_ENCRYPTION_KEY` der Portalverwaltung, den du ohnehin sichern
  musst); gleicher Inhalt ergibt denselben Dateinamen, es entstehen keine Duplikate. Entschlüsseln:
  `manage.py env_entschluesseln <datei> [--ausgabe .env]`.
- Neu: **Hängende Instanzen zurücksetzen** (Admin-Aktion „Hängende Instanz zurücksetzen („In Arbeit“ beenden)“, nur Superuser): Bleibt eine
  Instanz auf „In Arbeit“ stehen, z. B. weil der Worker währenddessen neu gestartet wurde, ermittelt das Portal anhand von Docker den
  echten Zustand (Läuft / Fehlgeschlagen / Gestoppt / Stillgelegt / Neu) und setzt den Status. Es verweigert das, solange der Vorgang
  noch aktiv aussieht (letzte Aktivität jünger als 15 Minuten oder Auftrag beim Worker aktiv). Der Fortschritt hält dafür den
  Aktivitätszeitpunkt aktuell. Dasselbe für einen verwaisten **Rollout**: Im Update-Hinweis erscheint nach 15 Minuten ein Knopf
  „Sperre aufheben“ (nur wenn der Worker keinen Rollout mehr ausführt).

## 0.27.0 (2026-10-10)
- Neu: **Wiederherstellung auch für OpenSlides und Paperless-ngx** (Seite „Aus Sicherung wiederherstellen“, je Dienst ein eigener Abschnitt,
  Kürzel-Bestätigung, voreingestellte Sicherung des jetzigen Stands). Feuerix und die jeweils anderen Dienste bleiben unberührt.
  - **OpenSlides** (nach der Anleitung des Herstellers): Dienste herunterfahren, nur die Datenbank starten, alle Schemas leeren, den
    SQL-Dump (lokal oder von der Freigabe) einspielen, alles starten. Vorher wird der jetzige Stand als Dump gesichert.
  - **Paperless-ngx (experimentell, noch nicht gegen eine echte Instanz geprüft)**: Paperless anhalten, Datenbank leeren, in einem einmaligen
    Container ohne den normalen Start (der sonst schon einen Admin anlegt) `migrate` und `document_importer` ausführen, starten und die
    Feuerix-Anbindung mit einem neuen Token versorgen. Angeboten werden der letzte Export (Paperless überschreibt den Ordner `export`
    bei jedem Export, ältere Stände gibt es dort nicht) und die Exporte `_vorher-…` vom Stand vor einer Wiederherstellung - der jetzige
    Stand wird vorher dorthin exportiert, nie in den Ordner, aus dem wiederhergestellt wird.
  - Bricht eine Wiederherstellung ab, nachdem die Datenbank geleert wurde, nennt das Protokoll, wo der Stand davor liegt; der Status der
    Instanz (Feuerix) bleibt dabei unverändert.

## 0.26.1 (2026-10-10)
- Fix: Die Aktion **„Sichern“** (und `manage.py mandant_backup`) sichert jetzt **alle** Dienste der Instanz: Feuerix (Datenbank,
  Medien) plus - falls eingerichtet - den Paperless-Export und den OpenSlides-Datenbank-Dump, bei aktivem Backup-Ziel jeweils auch
  auf der Netzwerkfreigabe (`<kürzel>\`, `<kürzel>\openslides\`, `<kürzel>\paperless\`). Bisher entstanden die beiden nur bei
  Updates und vor dem Löschen. Schlägt ein Zusatzdienst fehl, werden die anderen trotzdem gesichert und alle Fehler gemeldet
  (Protokoll, auf Wunsch Mail).

## 0.26.0 (2026-10-10)
- Neu: **Instanz aus einer Sicherung wiederherstellen** (Admin-Aktion „Aus Sicherung wiederherstellen …“, nur Superuser, Kürzel eintippen).
  Die Seite listet alle Sicherungen der Instanz (lokal und auf der Netzwerkfreigabe, mit Zeitpunkt, Herkunft und Größe). Ablauf: Instanz
  starten, **den jetzigen Stand sichern** (voreingestellt, abwählbar für defekte Instanzen), Sicherung bereitstellen (von der Freigabe
  herunterladen) und in den Container kopieren, Worker anhalten, Datenbank leeren, mit Feuerix' `restore.sh` Datenbank und optional
  Medien zurückspielen, neu starten (Migrationen laufen beim Start). Bricht es ab, nachdem die Datenbank geleert wurde, steht die
  Instanz auf „Fehlgeschlagen“ und die Sicherung vom Stand davor liegt zum Zurückspielen bereit. Aktuell nur Feuerix (Datenbank und
  Medien); Paperless und OpenSlides werden von Hand zurückgespielt.

## 0.25.1 (2026-10-10)
- Das Backup-Ziel zeigt jetzt oben seinen **Status**: „NICHT AKTIV - es wird nichts hochgeladen“ (häufigster Grund, warum nichts auf
  der Freigabe landet: der Haken „aktiv“ fehlt, auch wenn der Verbindungstest klappt), sonst Server/Freigabe, Erreichbarkeit und
  der **letzte Upload** mit Zeit und Ordner. Eine Sicherung bei eingerichtetem, aber inaktivem Ziel schreibt einen Hinweis ins
  Protokoll. Migration 0016.

## 0.25.0 (2026-10-10)
- Neu: **Jedes Instanz-Update sichert jetzt auch die Zusatzdienste.** Neben der Feuerix-Sicherung legt das Portal vor dem Update
  (Einzel-Knopf, Admin-Aktion, Gesamt-Rollout) einen Paperless-Export und einen OpenSlides-Dump an, sofern der Dienst
  eingerichtet ist und seine letzte Sicherung älter als `UPDATE_BACKUP_MAX_ALTER_STUNDEN` (Standard 24) ist. Ein Fehler dabei
  bricht das Update ab (Instanz läuft unverändert weiter). Bei aktivem Backup-Ziel gilt auch das erst nach erfolgreichem
  Upload als aktuell. Der Update-Hinweis zeigt „Sicherungen aktuell“ bzw. „werden vorher angelegt“ für alle Dienste.
- Datenbank-Migration 0015.

## 0.24.2 (2026-10-10)
- Fix: Die **Paperless-Version wurde nie angezeigt** (und der Update-Pfeil fehlte), weil Paperless den Header `X-Version` nur an
  angemeldete Benutzer sendet - die anonyme Abfrage des Portals bekam ihn nie. Die Version wird jetzt direkt aus dem
  Paperless-Container gelesen (`docker compose exec webserver python3 …paperless.version`), ohne Zugangsdaten. Kann sie
  nicht ermittelt werden, nennt der Hinweis im Admin den Grund (z. B. „Container läuft nicht“).

## 0.24.1 (2026-10-10)
- Fix: Mit aktivem Backup-Ziel wurden nur die Feuerix-Sicherungen auf die Netzwerkfreigabe hochgeladen. Jetzt landen auch der
  **OpenSlides-Datenbank-Dump** (`<Freigabe>\<kürzel>\openslides\`) und der **Paperless-Export**
  (`<Freigabe>\<kürzel>\paperless\`, samt Unterordnern gespiegelt) dort - bei Updates dieser Dienste und in der
  Abschlusssicherung vor dem endgültigen Löschen. Schlägt der Upload fehl, wird (wie bei Feuerix) nichts aktualisiert bzw.
  gelöscht; auf Wunsch kommt eine Mail.

## 0.24.0 (2026-10-10)
- Neu: **Endgültiges Löschen einer Instanz mit Abschlusssicherung** (Admin-Aktion „Instanz ENDGÜLTIG löschen …“, nur
  Superuser, Bestätigung durch Eintippen des Kürzels). Vorher startet das Portal die Instanz bei Bedarf und sichert
  Feuerix (Datenbank und Medien, bei aktivem Backup-Ziel auch auf die Netzwerkfreigabe), Paperless (Export) und OpenSlides
  (Datenbank-Dump). **Schlägt irgendein Schritt fehl, wird nichts gelöscht.** Danach werden Container und Volumes entfernt
  und der Instanzordner (mit `.env` und Schlüsseln, Sicherungen, Exporten) nach `instanzen/_geloescht/<kürzel>-<Zeit>`
  verschoben und nie automatisch entfernt. Auch `manage.py mandant_abbauen --daten-loeschen` macht jetzt zuerst die
  Sicherung (`--ohne-sicherung` nur als ausdrücklicher Notfall).
- Der normale „Löschen“-Knopf löscht nur noch Datensätze von Instanzen, die nie bereitgestellt wurden oder schon
  endgültig gelöscht sind (sonst blieben Container und Schlüssel verwaist zurück).
- Fix: Schlug „Stilllegen“ oder „Löschen“ beim ersten Schritt fehl, blieb die Instanz im Status „In Arbeit“ hängen; der
  ursprüngliche Status wird jetzt wiederhergestellt.
- Fix: Die Versionen von Paperless-ngx und OpenSlides wurden nur bei der Update-Prüfung erfasst, daher stand nach dem
  Update des Portals „-“ und der Update-Pfeil fehlte. Sie werden jetzt auch am Ende von Bereitstellung, Start und
  Neustart erfasst; fehlt eine Version, startet das Portal die Prüfung automatisch und zeigt im Hinweis, bei welcher
  Instanz sie nicht ermittelt werden konnte; Fehler der Zusatzdienste-Prüfung (z. B. GitHub nicht erreichbar) stehen
  ebenfalls dort.

## 0.23.1 (2026-10-10)
- Fix: **OpenSlides bei mehreren Instanzen.** Der Verwaltungsdienst (`backendManage`) lag bei jeder Instanz auf dem Host-Port
  9002; bei der zweiten kollidierte das („port is already allocated“), und `osmanage initial-data` landete bei der falschen
  Instanz (HTTP 401, „Startdaten konnten nicht angelegt werden“). Jede neue Instanz bekommt jetzt einen eigenen
  Verwaltungsport (ab `OPENSLIDES_MANAGE_PORT_BASIS`, Standard 9200), `osmanage` wird mit `--address` und `--password-file`
  aufgerufen, und ein noch nicht initialisierter Stack, der mit 9002 erzeugt wurde und mit einer anderen Instanz kollidiert,
  wird beim erneuten Bereitstellen samt (leerer) Datenbank entfernt und mit eigenem Port neu erzeugt. Bereits
  initialisierte Instanzen werden nie angefasst. Migration 0014.
- **Portvergabe prüft jetzt die Belegung auf dem Server**: Ein Port gilt nur als frei, wenn ihn keine Instanz in irgendeinem
  Port-Feld hat und er auf dem Server tatsächlich nicht belegt ist (Bind-Versuch; `PORT_PRUEFUNG=0` schaltet das ab).
- OpenSlides-Update aus dem Portal nur noch für Patch-Versionen (z. B. 4.3.0 → 4.3.1) über den offiziellen Weg
  (`osmanage config --force`); Minor-/Major-Sprünge (Migrationen, PostgreSQL-Wechsel) bitte nach der OpenSlides-Anleitung von Hand.

## 0.23.0 (2026-10-10)
- Neu: **Update von Paperless-ngx und OpenSlides aus dem Portal** (nur auf Klick, nie automatisch): In der Spalte „Version“
  erscheint bei veralteter Version ein Pfeil-Knopf. Paperless: Export der Dokumente (`document_exporter`) → `pull` → `up -d`
  → warten. OpenSlides: Datenbank-Dump → Version in `config.yml`/`docker-compose.yml` ändern → `pull` → `up` → warten.
  Schlägt die Sicherung fehl, wird nichts geändert; bei einem Fehler vor dem Neustart werden die OpenSlides-Dateien
  zurückgesetzt. Feuerix läuft währenddessen weiter. Kein Hauptversionswechsel bei OpenSlides (das bitte von Hand).
  Fortschritt in der Spalte „Fortschritt“, Ergebnis in Protokoll und Gesamtlog, bei Fehlschlag auf Wunsch per Mail.

## 0.22.0 (2026-10-10)
- Neu: **Versionen von Paperless-ngx und OpenSlides** werden je Instanz erfasst (Paperless aus dem `X-Version`-Header der
  laufenden Instanz, OpenSlides aus dem `tag:` der erzeugten `config.yml`) und in der Spalte „Version“ sowie auf der
  Detailseite angezeigt. Bei der Update-Prüfung (alle `UPDATE_PRUEFUNG_STUNDEN`, Knopf „Erneut prüfen“) fragt das Portal
  die neuesten stabilen Releases auf GitHub ab (`api.github.com`; abschaltbar mit `UPDATE_DIENSTE_PRUEFEN=0`).
  Gibt es eine neuere Version, erscheint ein Hinweis im Admin (mit den betroffenen Instanzen), ein Eintrag im Gesamtlog
  und - falls eingerichtet - eine Mail (je Version einmal). **Es wird nichts automatisch aktualisiert.**
- Datenbank-Migration 0013.

## 0.21.0 (2026-10-10)
- Neu: **Gesamtlog** (Admin › Gesamtlog): eine zentrale, nur lesbare Liste aller Ereignisse mit Zeit, Ebene (Info/Warnung/
  Fehler), Quelle (Instanz oder „Portal“), Kategorie, Text und Benutzer; filter- und durchsuchbar, nach Datum gegliedert.
  Jede Zeile aus den Instanz-Protokollen landet automatisch auch dort. Zusätzlich: Aktionen im Admin mit Benutzername
  (Starten, Stoppen, Neustarten, Stilllegen, Update, Sichern, Bereitstellen, Rollout), Update-Prüfung und Rollout,
  Backup-Ziel erreichbar/nicht erreichbar, versendete und fehlgeschlagene Mails, Änderungen an Backup-Ziel und
  Benachrichtigung (ohne Passwörter). Einträge älter als `LOG_AUFBEWAHRUNG_TAGE` (Standard 90) werden automatisch
  gelöscht. Ein Fehler beim Schreiben des Logs stört nie den eigentlichen Vorgang.
- Datenbank-Migration 0012.

## 0.20.1 (2026-10-10)
- Fix: Während eine Instanz startet, stoppt, aktualisiert oder bereitgestellt wird („In Arbeit“), meldete der Update-Hinweis
  fälschlich „Feuerix ist aktuell“. Jetzt steht dort „Updates sind gerade nicht möglich: Instanzen in Arbeit …“, der Knopf
  „Update jetzt ausrollen“ ist in dieser Zeit ausgeblendet und ein Rollout wird abgelehnt.

## 0.20.0 (2026-10-10)
- Neu: **E-Mail-Benachrichtigung** (Admin › Benachrichtigungen: SMTP-Server mit Verschlüsselung, Benutzer, Passwort
  (verschlüsselt gespeichert), Absender, Empfänger, „Speichern und Test-Mail senden“). Mails gibt es nur bei Ereignissen:
  Instanz oder Backup-Ziel länger als `Ausfall erst melden nach` Minuten (Standard 10) offline und wieder erreichbar,
  fehlgeschlagene Sicherung (inkl. Upload auf die Freigabe) und neue Feuerix-Version (einmal je Version). Jedes
  Ereignis lässt sich einzeln abschalten; ein Versandfehler stört nie den eigentlichen Vorgang und wird angezeigt.
- Datenbank-Migration 0011.

## 0.19.0 (2026-10-10)
- Neu: **Backup-Ziel auf einer Netzwerkfreigabe (SMB/CIFS)** mit Benutzer und Passwort (verschlüsselt gespeichert),
  einstellbar im Admin unter „Backup-Ziel“ (Server, Freigabe, Unterordner, Aufbewahrung, Option „lokale Kopie löschen“,
  Knopf „Speichern und Verbindung testen“). Jede Sicherung wird nach dem Anlegen dorthin hochgeladen (Größe wird
  geprüft). Ist das Ziel aktiv, gilt eine Sicherung erst nach erfolgreichem Upload als aktuell; schlägt er fehl, wird
  kein Update durchgeführt. Bibliothek `smbprotocol` (kein Mounten, kein Root; SMB-Signierung aktiv) - bitte
  `pip install -r requirements.txt` ausführen.
- Neu: **Regelmäßiger Online-Check** (`manage.py mandant_online_check`, vom Installer als systemd-Timer alle 5 Minuten
  eingerichtet): prüft laufende Instanzen (alle Dienste) und das Backup-Ziel, merkt sich „offline seit“, protokolliert
  Wechsel je Instanz und zeigt Ausfälle als roten Hinweis auf Startseite und Instanzliste.
- Datenbank-Migration 0010.

## 0.18.0 (2026-10-10)
- **Gestoppte Instanzen** werden jetzt bei der Update-Prüfung mitgezählt (die gespeicherte Feuerix-Version bleibt beim
  Stoppen erhalten): Sie erscheinen im Update-Hinweis, die Versionsspalte zeigt den Update-Knopf, und der Gesamt-Rollout
  nimmt sie mit. Das Update baut für sie nur das neue Image und lässt sie gestoppt (ohne Sicherung, da kein Datenzugriff);
  beim nächsten Start läuft die neue Version samt Migrationen. Stillgelegte Instanzen werden weiterhin nicht beachtet.

## 0.17.0 (2026-10-10)
- „Pausieren“ heißt jetzt **„Instanz stilllegen (Container entfernen, Daten behalten)“**, der Status **„Stillgelegt“**.
  Das Stilllegen zeigt wie Stoppen/Starten einen Fortschritt (OpenSlides, Paperless, Feuerix entfernen, Reverse Proxy
  aktualisieren); scheitert ein Schritt, wird die Instanz „Fehlgeschlagen“. Intern bleibt der Statusschlüssel `pausiert`
  (keine Datenänderung). Migration 0009 (nur die Statusbezeichnung).

## 0.16.0 (2026-10-10)
- Neu: **Stoppen** zeigt jetzt wie Starten und Neustart einen Fortschritt in der Spalte „Fortschritt“ (OpenSlides, Paperless,
  Feuerix stoppen, Reverse Proxy aktualisieren). Scheitert das Stoppen, wird die Instanz „Fehlgeschlagen“.
- Der Zwischenstatus heißt jetzt „In Arbeit“ (statt „Wird bereitgestellt“), weil er auch bei Update, Stoppen, Starten und
  Neustart gilt. Gestoppte/pausierte Instanzen zeigen keine Prozentzahl mehr. Migration 0008 (nur die Statusbezeichnung).

## 0.15.1 (2026-10-10)
- Fix: Das Update einer **einzelnen** Instanz (Knopf in der Versionsspalte, Admin-Aktion) zieht jetzt zuerst den neuen
  Feuerix-Code (`git pull --ff-only`, falls der Remote neuer ist). Bisher wurde nur mit dem vorhandenen Checkout neu
  gebaut, die Instanz blieb daher auf der alten Version. Fehlschläge dabei lassen die Instanz unverändert laufen.

## 0.15.0 (2026-10-10)
- Neu: Ein Instanz-Update zeigt seinen **Fortschritt in der Spalte „Fortschritt“** (Balken mit Schritt: Sicherung prüfen,
  Sicherung anlegen, bauen und starten, auf Migration warten) - beim Einzel-Knopf, der Admin-Aktion und im Gesamt-Rollout.
  Während des Updates steht die Instanz auf „Wird bereitgestellt“ und danach wieder auf „Läuft“. Scheitert das Update vor
  dem Neustart (z. B. die Sicherung), läuft die Instanz unverändert weiter; scheitert es danach, wird sie
  „Fehlgeschlagen“ (Wartungsseite) und der Schritt ist im Balken sichtbar.

## 0.14.1 (2026-10-10)
- Installer: bricht bei Start mit `sudo` ab (sonst gehören Dateien und Dienste root) und bietet an, den Besitzer eines
  fremden Feuerix-Checkouts auf den aktuellen Benutzer zu ändern. Hintergrund: Die Update-Prüfung scheiterte mit
  „dubiose Besitzverhältnisse im Repository“, weil `/opt/Feuerix` root gehörte.

## 0.14.0 (2026-10-10)
- Neu: In der Spalte **Version** der Instanzliste erscheint bei verfügbarem Update ein Knopf (↑ → Zielversion), der
  nur diese Instanz aktualisiert.
- Neu: **Sicherungsprüfung vor jedem Update** (Einzel-Knopf, Admin-Aktion und Gesamt-Rollout): Ist die letzte Sicherung
  der Instanz älter als `UPDATE_BACKUP_MAX_ALTER_STUNDEN` (Standard 24) oder fehlt sie, legt das Portal vorher
  automatisch eine an. Schlägt die Sicherung fehl, wird diese Instanz nicht aktualisiert. Der Update-Hinweis zeigt je
  Instanz, ob die Sicherung aktuell ist.
- Die Kommandos `mandant_update` (ohne Prüfung) und `mandant_update_pruefen --ausrollen` (mit Prüfung) bleiben getrennt.

## 0.13.0 (2026-10-10)
- Neu: **Update-Prüfung für Feuerix.** Das Portal prüft im Hintergrund (alle `UPDATE_PRUEFUNG_STUNDEN`, Standard 6,
  sobald jemand das Admin öffnet; 0 = aus) per `git fetch`, ob der Feuerix-Code eine neuere Version hat, und zeigt auf
  der Startseite und in der Instanzliste einen Hinweis mit den Änderungen und den veralteten Instanzen.
- Neu: **„Update jetzt ausrollen“** (nur auf Klick): zieht den neuen Code per `git pull --ff-only` und aktualisiert
  nacheinander alle laufenden, noch nicht aktuellen Instanzen (Pause `UPDATE_PAUSE_SEKUNDEN` dazwischen, ein Fehler
  stoppt die anderen nicht). Live-Anzeige des Schritts, Ergebnis danach im Hinweis.
- Neu: `manage.py mandant_update_pruefen [--ausrollen]` für Kommandozeile/Cron.
- Datenbank-Migration 0007 (neue Tabelle für den Update-Zustand).

## 0.12.2 (2026-10-09)
- Die Start/Stopp/Neustart-Schaltflächen in der Instanzliste sind jetzt runde Symbol-Knöpfe ohne Text (Beschriftung
  als Tooltip), statt breiter Text-Knöpfe.

## 0.12.1 (2026-10-09)
- `manage.py` liest die `.env` im Projektverzeichnis jetzt selbst. Bisher scheiterten Befehle wie `migrate` oder
  `collectstatic` auf dem Server mit `KeyError: 'SECRET_KEY'`, wenn man die `.env` nicht vorher per
  `set -a; . ./.env` geladen hatte. Bereits gesetzte Umgebungsvariablen (systemd, Installer) haben Vorrang.

## 0.12.0 (2026-10-09)
- Neu: In jeder Zeile der Instanzliste gibt es die Schaltflächen **Starten**, **Stoppen** und **Neustarten**
  (je nach Status aktiv oder ausgegraut; Stoppen/Neustarten fragen vorher nach). Die Sammel-Aktionen bleiben.

## 0.11.0 (2026-10-09)
- Neu: **Wartungsseite**. Hat eine Instanz eine Domain, aber keinen laufenden Dienst (neu angelegt, gestoppt,
  pausiert, fehlgeschlagen oder in Bereitstellung), liefert der Reverse Proxy statt eines Browserfehlers eine
  Seite „Wartungsarbeiten“ (HTTP 503, `Retry-After`). Dasselbe gilt für Paperless/OpenSlides der Instanz und,
  wenn ein „laufender“ Dienst unerwartet ausfällt (Upstream 502/503/504). Der Proxy wird dafür auch beim
  Speichern einer Instanz und nach Fehlschlägen aktualisiert.

## 0.10.1 (2026-10-09)
- Die Hilfetexte der Felder „Paperless-Domain“ und „OpenSlides-Domain“ sagen jetzt klar, dass sie nur für eine
  eigene Adresse bzw. einen abweichenden Namen auszufüllen sind - bei Installation über den Haken bleiben sie leer.

## 0.10.0 (2026-10-09)
- Neu: Admin-Aktionen **Starten**, **Stoppen** und **Neustarten** für Instanzen (inkl. Paperless/OpenSlides), mit
  Fortschrittsbalken. Stoppen hält nur die Container an (neuer Status „Gestoppt“, Instanz verlässt den Reverse
  Proxy); Starten und Neustarten bauen nichts neu. „Pausieren“ bleibt der vollständige Abbau (Container entfernt,
  Daten bleiben).
- Neu: `manage.py mandant_dienst <kürzel> stop|start|restart`.
- Datenbank-Migration 0005 (nur Auswahlliste des Status, keine Datenänderung).

## 0.9.0 (2026-10-09)
- Neu: Die Version der Portalverwaltung steht in der Fußzeile der Weboberfläche (Quelle: `VERSION.md`).
- Neu: `INSTALL.md` mit Voraussetzungen, Installation, Update, Fehlerbehebung und manueller Installation;
  `README.md` verweist darauf und beschreibt die neuen Funktionen.

## 0.8.0 (2026-10-09)
- Neu: Admin-Aktion „Zugangsdaten des Instanz-Administrators anzeigen“ (nur Superuser, wird im Protokoll
  vermerkt). Bisher war das Startpasswort einer neuen Instanz nirgends einsehbar.

## 0.7.1 (2026-10-09)
- Fix: Die Datenbank-Zugangsdaten der Portalverwaltung (`POSTGRES_USER`/`-PASSWORD`/`-DB` aus deren `.env`) wurden
  über die Prozessumgebung an `docker compose` durchgereicht und überschrieben dort die der Mandanten-Instanz. Folge:
  Der Feuerix-Web-Container kam nicht hoch („password authentication failed for user mandant_…“), die
  Bereitstellung endete bei 35 % mit „Fehlgeschlagen“. Docker-Aufrufe bekommen jetzt eine bereinigte Umgebung.

## 0.7.0 (2026-10-09)
- Installer: Fehlt der Feuerix-Checkout, bietet `install.sh` an, ihn automatisch zu klonen (Standardpfad jetzt
  `../Feuerix`), statt mit einer `docker-compose.yml`-Fehlermeldung abzubrechen.
- Installer: erkennt ein unvollständiges Verzeichnis (`requirements.txt`, `manage.py` oder `config` fehlt) und
  nennt den passenden `git clone`-Befehl.
- Die Startseite (`/`) leitet auf `/admin/` um, statt „Not Found“ zu zeigen.
- `.gitattributes`: Shell-Skripte behalten LF-Zeilenenden (sonst bricht `install.sh` unter Linux ab).

## 0.6.0 (2026-09-26)
- Paperless-ngx und OpenSlides lassen sich im Instanzformular per Haken nachinstallieren.

## 0.5.0 (2026-09-26)
- Fortschrittsbalken mit Live-Schritten im Admin während der Bereitstellung.
- Aktion „Admin-Passwort der Instanz zurücksetzen“.

## 0.4.0 (2026-09-26)
- Interaktiver Installer (`install.sh`) und Weboberfläche mit Live-Erreichbarkeit je Dienst.
- Eigene Domain der Portalverwaltung im Reverse Proxy.

## 0.3.0 (2026-09-26)
- Betrieb: Backup, Status, gestaffelter Update-Rollout, Admin-Aktionen, Dokumentation.

## 0.2.0 (2026-09-25)
- Ausbaustufe 2: Paperless-ngx und OpenSlides je Mandant (eigener Stack, Proxy-Eintrag, Anbindung in Feuerix).

## 0.1.0 (2026-09-25)
- Ausbaustufe 1: automatisierte Bereitstellung einer Feuerix-Instanz je Verein (`.env`, Port, Docker-Compose-Stack,
  Bereitschaftsprüfung).
- Ausbaustufe 1b: Reverse-Proxy-Automatisierung (Caddy), Abbau von Instanzen, Eingabe-Härtung.
- Integrationstest per GitHub Actions.
