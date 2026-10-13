# Installation

Anleitung für einen Linux-Server (getestet mit Debian 13). Überblick über Funktionen und Betrieb: [README.md](README.md).

## Voraussetzungen

| Was | Warum |
|---|---|
| Linux-Server mit **Docker** (inkl. Compose-Plugin ≥ 2.24) | jede Vereins-Instanz läuft als eigener Compose-Stack |
| `git`, `python3`, `python3-venv` | Debian/Ubuntu: `sudo apt install git python3 python3-venv` |
| Ein normaler Benutzer **in der Gruppe `docker`** | `sudo usermod -aG docker $USER`, danach neu einloggen |
| `sudo`-Rechte für diesen Benutzer | der Installer legt systemd-Dienste an |
| Offene Ports **80** und **443** | Caddy holt das Let's-Encrypt-Zertifikat |
| DNS-Eintrag für die Portal-Domain (und je Verein, am besten Wildcard `*.deine-domain`) | zeigt auf die Server-IP |
| Internetzugang zu GitHub und den Docker-Registries | Feuerix-Checkout, Updates, `osmanage`, Images (GitHub-API für die Versionsprüfung von Paperless/OpenSlides) |

Optional: eine SMB-Freigabe (Windows, NAS, Samba) für Sicherungen und ein SMTP-Server für E-Mail-Benachrichtigungen.

## 1. Portalverwaltung holen

```bash
sudo mkdir -p /opt/verein && sudo chown $USER: /opt/verein
git clone https://github.com/ricten/portalverwaltung_feuerix /opt/verein
cd /opt/verein
```

Wichtig: das **ganze Repository** holen (nicht nur einzelne Dateien) – der Installer prüft, ob `requirements.txt`,
`manage.py` und `config/` vorhanden sind.

## 2. Installer starten

```bash
./install.sh        # ohne sudo – das Skript bricht ab, wenn es als root läuft, und ruft sudo selbst auf, wo nötig
```

Der Assistent fragt nacheinander:

1. **Pfad zum Feuerix-Checkout** – Standard `../Feuerix` (neben dem Portal). Fehlt er, bietet der Installer an, ihn von
   `https://github.com/ricten/Feuerix` zu klonen. Daraus wird die `docker-compose.yml` jeder Instanz genommen und später auch das
   Feuerix-Update gezogen. Gehört der Ordner einem anderen Benutzer, bietet der Installer an, den Besitzer zu ändern.
2. **PostgreSQL + Redis als Docker-Container** mitinstallieren (sonst werden vorhandene verwendet).
3. **Caddy** (Reverse Proxy mit automatischem HTTPS) installieren und von der Portalverwaltung steuern lassen.
4. **Celery-Worker** als systemd-Dienst (nötig für „Bereitstellen“ und alle längeren Aktionen im Admin).
5. **Weboberfläche** (Gunicorn) als systemd-Dienst.
6. **Domain der Portalverwaltung** (leer = nur lokal unter `127.0.0.1:8000`) und **E-Mail für Let's Encrypt**.
7. Am Ende: **Online-Check** (alle 5 Minuten, systemd-Timer) einrichten.

Dazwischen erzeugt er `.env` mit Zufallsgeheimnissen, richtet die Datenbank ein, führt die Migrationen aus und fragt nach
Benutzername und Passwort des Portal-Administrators. Mehrfaches Ausführen ist möglich; eine vorhandene `.env` wird nicht
überschrieben.

> **`FIELD_ENCRYPTION_KEY` aus der `.env` sichern.** Ohne ihn sind alle gespeicherten Geheimnisse (der Instanzen, des Backup-Ziels
> und des Mailservers) unlesbar.

## 3. Anmelden und ersten Verein anlegen

1. `https://<portal-domain>/` öffnen (leitet auf `/admin/`) und mit dem Portal-Administrator anmelden. Die installierte
   **Version** steht in der Fußzeile.
2. **Mandanten-Instanzen → Hinzufügen**: Kürzel, Vereinsname, Domain, Administrator-Benutzername und -E-Mail ausfüllen.
   Optional die Haken **Paperless-ngx** / **OpenSlides** setzen (das Domain-Feld nur ausfüllen, wenn die Dienste unter einer
   eigenen Adresse laufen sollen; sonst wird `paperless.<domain>` bzw. `openslides.<domain>` genommen).
   **Demo- oder Testinstanz?** Haken **„Demo-Instanz (mit Beispieldaten)“** setzen: Das Portal füllt die Instanz bei der Bereitstellung mit
   fiktiven Mitgliedern, Rechnungen, Zahlungen (auch Mahnungen) und einem Beispiel-Inventar (Anzahl einstellbar, Standard 40). Nie bei echten
   Vereinen verwenden. Eine schon laufende Instanz kannst du nachträglich zur Demo machen: Haken setzen, speichern, Aktion
   **Beispieldaten anlegen (nur Demo-Instanzen)**. Danach merkt sich das Portal den Stand als **Demo-Standard**; mit **Demo auf Standard
   zurücksetzen** (oder dem lila Knopf in der Zeile) stellst du ihn wieder her, wenn andere Daten eingegeben haben. Das ersetzt alle Daten der
   Instanz.
3. Instanz in der Liste markieren → Aktion **Bereitstellen**. Der Fortschrittsbalken zeigt die Schritte; der erste Lauf
   baut das Feuerix-Image und dauert einige Minuten.
4. Aktion **Zugangsdaten des Instanz-Administrators anzeigen** zeigt Adresse, Benutzer und Startpasswort der neuen
   Instanz (nur für Superuser).

Im Betrieb: **Starten**, **Stoppen**, **Neustarten** (auch als runde Knöpfe in der Zeile), **Instanz stilllegen** (Container
entfernen, Daten bleiben), **Sichern**, **Aus Sicherung wiederherstellen** und **Instanz endgültig löschen** – Einzelheiten im
[README](README.md#funktionen-im-überblick).

## 4. Empfohlen direkt danach

1. **Backup-Ziel** einrichten (Abschnitt unten), damit Sicherungen nicht nur auf dem Server liegen.
2. **Benachrichtigungen** einrichten (Abschnitt unten), damit Ausfälle und fehlgeschlagene Sicherungen per Mail ankommen.
3. Prüfen, dass der **Online-Check-Timer** läuft: `systemctl list-timers portal-check.timer` (sonst siehe „Online-Check
   nachträglich einrichten“).
4. Im Update-Hinweis auf der Startseite einmal **„Erneut prüfen“** klicken: Das erfasst die Versionen von Paperless und
   OpenSlides und zeigt, ob es etwas Neues gibt.
5. Eine **Test-Sicherung** (Aktion „Sichern“) und – an einer Test-Instanz – eine **Wiederherstellung** ausprobieren, bevor du sie
   im Ernstfall brauchst.

## Aktualisieren

**Die Portalverwaltung selbst** aktualisierst du von Hand:

```bash
cd /opt/verein
git pull
./venv/bin/pip install -q -r requirements.txt
./venv/bin/python manage.py migrate
./venv/bin/python manage.py collectstatic --noinput
sudo systemctl restart portal-web portal-worker
```

Starte die Dienste nicht neu, solange der Admin eine Instanz „In Arbeit“ zeigt: Der Worker bricht dabei laufende Aufträge ab
(der Status lässt sich danach mit „Hängende Instanz zurücksetzen“ bereinigen, siehe Fehlerbehebung).

**Feuerix für die Vereins-Instanzen:** Das Portal prüft selbst, ob es eine neuere Feuerix-Version gibt, und zeigt auf der
Startseite einen Hinweis. Mit **Update jetzt ausrollen** wird der Code gezogen (`git pull`) und alle laufenden Instanzen werden
nacheinander aktualisiert; einzelne Instanzen aktualisierst du mit dem Pfeil-Knopf in der Spalte „Version“. Vor jedem Update legt das
Portal bei Bedarf eine frische Sicherung an (Feuerix, Paperless, OpenSlides) und aktualisiert nicht, wenn das fehlschlägt. Ohne Admin:
`./venv/bin/python manage.py mandant_update_pruefen --ausrollen`. Voraussetzung: Der Feuerix-Checkout hat einen Upstream-Zweig (ein
normaler `git clone`), gehört dem Dienstbenutzer, und der Server erreicht GitHub. Die Prüfung lässt sich mit
`UPDATE_PRUEFUNG_STUNDEN=0` abschalten.

**Paperless und OpenSlides:** Neue Versionen werden angezeigt und gemeldet, aber nie von selbst installiert. Das Update startest du
mit dem Pfeil-Knopf in der Spalte „Version“ (OpenSlides nur für Patch-Versionen, z. B. 4.3.0 → 4.3.5; größere Sprünge bitte nach der
Anleitung von OpenSlides von Hand).

## Sicherheit nach einem Update auf 0.31.0 (bestehende Installation)

1. **Dateirechte** korrigieren (neue Installationen sind schon richtig): `./venv/bin/python manage.py rechte_haerten`. Meldet der Befehl
   einen Fehler bei einem Ordner, gehört er vermutlich `root` (nach einem Installer-Lauf mit `sudo`): `sudo chown -R $USER: /opt/verein/instanzen`
   und den Befehl wiederholen.
2. **Redis mit Passwort** (neue Installationen haben es): Passwort erzeugen, den Container neu anlegen und die `.env` anpassen:
   ```bash
   PW=$(python3 -c "import secrets,string;print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(32)))")
   docker rm -f portal-redis
   docker run -d --name portal-redis --restart unless-stopped -p 127.0.0.1:6379:6379 redis:7-alpine redis-server --requirepass "$PW"
   sed -i "s#^REDIS_URL=.*#REDIS_URL=redis://:$PW@127.0.0.1:6379/1#" /opt/verein/.env
   sudo systemctl restart portal-web portal-worker
   ```
   Laufende Aufträge gehen dabei verloren; also nur ausführen, wenn keine Instanz „In Arbeit“ ist.
3. Superuser-Rechte nur an Personen vergeben, die Zugangsdaten und Sicherungsziele verwalten dürfen. Normale Benutzer brauchen nur
   Änderungsrechte für „Mandanten-Instanzen“.

## Update auf 0.33.0 (Zwei-Faktor-Anmeldung)

`git pull`, dann `./venv/bin/pip install -r requirements.txt`, `./venv/bin/python manage.py migrate` und
`sudo systemctl restart portal-web portal-worker`. **Superuser werden beim nächsten Login zur Einrichtung geführt**: Authenticator-App auf dem
Handy bereithalten, QR-Code scannen, Code eingeben und die Notfallcodes speichern. Zum Abschalten `ZWEI_FAKTOR=aus` in die `.env`.
Ausgesperrt (Handy weg, keine Codes): `./venv/bin/python manage.py zwei_faktor_zuruecksetzen BENUTZERNAME`.
Die Uhr des Servers und des Handys muss stimmen, sonst passen die Codes nicht.

## Update auf 0.32.0

`git pull`, dann `./venv/bin/python manage.py migrate` (neue Tabelle für die Anmelde-Sperre, neue Option im Backup-Ziel) und
`sudo systemctl restart portal-web portal-worker`. Wenn das Backup-Ziel danach nicht mehr erreichbar ist, kann der Server kein SMB 3 mit
Verschlüsselung: im Admin unter **Backup-Ziel** die Option „Übertragung verschlüsseln“ abschalten (nur im vertrauenswürdigen LAN).
Hast du dich durch Fehlversuche ausgesperrt: `./venv/bin/python manage.py login_entsperren`.

## Online-Check nachträglich einrichten

Der Installer richtet den Check (alle 5 Minuten) auf Wunsch ein. Bei einer bestehenden Installation:

```bash
sudo tee /etc/systemd/system/portal-check.service >/dev/null <<EOF
[Unit]
Description=Portalverwaltung Online-Check
After=network.target docker.service

[Service]
Type=oneshot
User=$USER
WorkingDirectory=/opt/verein
EnvironmentFile=/opt/verein/.env
ExecStart=/opt/verein/venv/bin/python manage.py mandant_online_check
EOF
sudo tee /etc/systemd/system/portal-check.timer >/dev/null <<EOF
[Unit]
Description=Portalverwaltung Online-Check alle 5 Minuten

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now portal-check.timer
```

Status: `systemctl list-timers portal-check.timer`, Ergebnis: `./venv/bin/python manage.py mandant_online_check`.

## Backup-Ziel (Netzwerkfreigabe)

Im Admin unter **Backup-Ziel**: Server (Hostname/IP), Freigabe, optionaler Unterordner, Benutzer und Passwort eintragen, **Haken „aktiv“
setzen** und mit **Speichern und Verbindung testen** prüfen. Der Verbindungstest klappt auch bei inaktivem Ziel – hochgeladen wird erst,
wenn „aktiv“ gesetzt ist. Die Zeile **Status** oben zeigt, ob wirklich hochgeladen wird, ob die Freigabe erreichbar ist und wann zuletzt
etwas hochgeladen wurde.

Unterstützt wird SMB/CIFS (Windows-Freigabe, NAS, Samba), kein NFS. Das Portal schreibt direkt per SMB (nichts wird gemountet, kein Root
nötig, Port 445 muss erreichbar sein); der Benutzer braucht Schreibrechte auf die Freigabe. Je Instanz entsteht ein Ordner mit dem Kürzel
unter dem eingestellten Unterordner: `…\<kürzel>\` (Feuerix-Sicherungen), `…\<kürzel>\openslides\` (Dumps), `…\<kürzel>\paperless\`
(Export). Die Aktion **Sichern** legt alle drei an. Mit jeder Sicherung liegt außerdem die **`.env`** der Instanz als verschlüsselte
`env-….enc` im Ordner `…\<kürzel>\` (so auch bei der letzten Sicherung vor dem endgültigen Löschen); sie enthält den `FIELD_ENCRYPTION_KEY`, ohne den
die Sicherung auf einem neuen Server nicht vollständig lesbar wäre. Entschlüsseln: `./venv/bin/python manage.py env_entschluesseln <datei> --ausgabe .env`
(braucht den `FIELD_ENCRYPTION_KEY` der Portalverwaltung). Nach einem `git pull` einmal `pip install -r requirements.txt` und `migrate`.

## E-Mail-Benachrichtigung

Im Admin unter **Benachrichtigungen**: SMTP-Server, Port, Verschlüsselung (STARTTLS 587, SSL 465), Benutzer und Passwort, Absender
und Empfänger eintragen, **aktiv** anhaken und mit **Speichern und Test-Mail senden** prüfen. Mails gehen nur bei Ereignissen raus:
Ausfall und Wiederkehr (nach `Ausfall erst melden nach` Minuten), fehlgeschlagene Sicherung, fehlgeschlagenes Update von Paperless oder
OpenSlides, neue Version von Feuerix, Paperless oder OpenSlides. Ohne den Online-Check-Timer gibt es keine Ausfallmeldungen. Wenn die Mail
nicht ankommt: unter „Status“ steht der letzte Versandfehler.

## Sicherung wiederherstellen und Instanz löschen

- **Wiederherstellen:** Instanz markieren → Aktion **Aus Sicherung wiederherstellen …** (nur Superuser). Dienst und Sicherung wählen,
  „Vorher den jetzigen Stand sichern“ angehakt lassen, Kürzel eintippen. Die Sicherungen kommen vom Server oder von der Freigabe. Die
  Wiederherstellung von Paperless ist experimentell – bitte zuerst an einer Test-Instanz probieren.
- **Endgültig löschen:** Instanz markieren → **Instanz ENDGÜLTIG löschen …**, Kürzel eintippen. Das Portal sichert vorher alle Dienste und
  löscht nichts, wenn die Sicherung fehlschlägt. Der Instanzordner (mit `.env`) bleibt in `instanzen/_geloescht/` liegen.

## Fehlerbehebung

| Meldung / Symptom | Ursache und Lösung |
|---|---|
| `docker-compose.yml nicht gefunden` | kein Feuerix-Checkout am angegebenen Pfad – den Klon-Vorschlag des Installers bestätigen |
| `ensurepip is not available` | `sudo apt install python3-venv` (bzw. `python3.13-venv`), dann `rm -rf venv` und Installer erneut starten |
| `requirements.txt` nicht gefunden / „Verzeichnis ist unvollständig“ | Verzeichnis ist kein vollständiger Klon – siehe Schritt 1 |
| `permission denied` bei Docker | Benutzer nicht in der Gruppe `docker` (Voraussetzungen) |
| Anmeldung: „CSRF-Verifizierung fehlgeschlagen“ (403) | meist veraltete Cookies/Cache im Browser (z. B. nach einem Update): Seite im privaten Fenster öffnen oder Cookies der Domain löschen |
| Update-Prüfung: „dubiose Besitzverhältnisse im Repository“ | Der Feuerix-Ordner gehört einem anderen Benutzer als die Portal-Dienste (meist root nach einem Installer-Lauf mit `sudo`): `sudo chown -R $USER: /opt/Feuerix`, dann „Erneut prüfen“ |
| OpenSlides: „Startdaten konnten nicht angelegt werden … 401“ oder „Bind for 127.0.0.1:9002 failed: port is already allocated“ | Version < 0.23.1: mehrere OpenSlides-Instanzen teilten sich den Verwaltungsport 9002. Nach `git pull`, `migrate` und Neustart der Dienste bei der betroffenen (noch nicht initialisierten) Instanz erneut **Bereitstellen** – das Portal erzeugt den Stack dann mit eigenem Port neu. Bereits eingerichtete Instanzen laufen unverändert weiter. |
| „Kein freier Port ab … gefunden“ | Das Portal prüft vor der Vergabe, ob ein Port auf dem Server frei ist. Mit `ss -ltnp` siehst du, was belegt ist; die Basis-Ports stehen in der `.env` (`WEB_PORT_BASIS`, `PAPERLESS_PORT_BASIS`, `OPENSLIDES_PORT_BASIS`, `OPENSLIDES_MANAGE_PORT_BASIS`). |
| Seite zeigt „Not Found“ | Version < 0.7.0 oder Pfad falsch – `/admin/` aufrufen, `git pull` und Dienst neu starten |
| Bereitstellung hängt bei 35 % und schlägt fehl | Logs des Web-Containers: `docker logs --tail 50 mandant-<kürzel>-web-1`; Protokoll der Instanz im Admin lesen |
| `password authentication failed for user "mandant_…"` | Fehler in Version < 0.7.1. Nach `git pull` und Neustart der Dienste: `docker compose -p mandant-<kürzel> down -v`, dann erneut **Bereitstellen** |
| „Beispieldaten“ schlagen fehl oder fehlen | Der Befehl `beispieldaten` gehört zu Feuerix: Der Feuerix-Checkout muss aktuell sein (Update-Hinweis) und die Instanz laufen. Das Protokoll der Instanz nennt den Fehler; sie werden nur einmal angelegt und bei einem Fehler beim nächsten „Bereitstellen“ bzw. mit der Aktion erneut versucht |
| Admin-Passwort der Instanz (Feuerix, Paperless, OpenSlides) unbekannt | Aktion **Zugangsdaten anzeigen** (zeigt alle drei) bzw. **… Passwort zurücksetzen** (je Dienst eine eigene Aktion) |
| Nach `git pull` keine Wirkung | Dienste neu starten: `sudo systemctl restart portal-web portal-worker` (der Worker lädt Code nicht automatisch nach) |
| Backup-Ziel zeigt „NICHT AKTIV“ / es landet nichts auf der Freigabe | Haken „aktiv“ im Backup-Ziel setzen und speichern (der Verbindungstest klappt auch ohne). Gesucht wird unter `<Freigabe>\<Unterordner>\<Kürzel>\`; `openslides` und `paperless` liegen darin in eigenen Ordnern |
| „Upload zur Netzwerkfreigabe fehlgeschlagen“ | Status im Backup-Ziel lesen, „Speichern und Verbindung testen“ ausführen; prüfen: Server erreichbar (Port 445), Benutzer/Passwort, Schreibrecht auf der Freigabe, Platz. Solange das Ziel aktiv ist und der Upload scheitert, gilt die Sicherung nicht als aktuell und es wird nicht aktualisiert oder gelöscht |
| „Update abgebrochen: Sicherung … fehlgeschlagen“ | Das Portal sichert vor dem Update auch Paperless und OpenSlides; deren Container müssen laufen. Den Grund nennt das Protokoll der Instanz. Starten bzw. reparieren, dann das Update wiederholen |
| Paperless-Version „nicht ermittelt“ | Der Paperless-Container der Instanz läuft nicht oder antwortet nicht auf `docker compose exec`. Der Update-Hinweis nennt den Grund; nach dem Beheben „Erneut prüfen“ |
| OpenSlides-Sicherung/-Update schlägt fehl | Das Portal erwartet im OpenSlides-Stack den Dienst `postgres` mit Benutzer und Datenbank `openslides` (Standard der Vorlage). Weicht deine Konfiguration ab, bitte von Hand sichern |
| Instanz bleibt auf „In Arbeit“ stehen | Meist wurde der Worker währenddessen neu gestartet oder abgebrochen. Instanz markieren → Aktion **Hängende Instanz zurücksetzen („In Arbeit“ beenden)**: Das Portal prüft über Docker, was wirklich läuft, und setzt den Status. Es verweigert das, solange der Vorgang noch aktiv aussieht (weniger als 15 Minuten ohne Fortschritt oder Auftrag beim Worker aktiv). Bei einem hängenden **Rollout** erscheint im Update-Hinweis nach 15 Minuten der Knopf „Sperre aufheben“ |

Dienste prüfen: `systemctl status portal-web portal-worker`, Logs: `journalctl -u portal-worker -e`.
Status aller Instanzen: `./venv/bin/python manage.py mandant_status`. Alle Ereignisse mit Zeit und Benutzer: Admin › **Gesamtlog**.

## Manuelle Installation (ohne Installer)

```bash
git clone https://github.com/ricten/portalverwaltung_feuerix /opt/verein && cd /opt/verein
git clone https://github.com/ricten/Feuerix ../Feuerix
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
cp .env.example .env     # alle Werte setzen, FEUERIX_REPO_PFAD=/opt/Feuerix, FIELD_ENCRYPTION_KEY erzeugen
./venv/bin/python manage.py migrate              # manage.py liest die .env selbst
./venv/bin/python manage.py collectstatic --noinput
./venv/bin/python manage.py createsuperuser
./venv/bin/celery -A config worker -l info --concurrency 2      # eigener Prozess/Dienst
./venv/bin/gunicorn config.wsgi --bind 127.0.0.1:8000           # eigener Prozess/Dienst
```

Beispiel für eine systemd-Unit des Workers: [README.md](README.md#produktivbetrieb). Postgres, Redis und Caddy müssen dann selbst
bereitgestellt werden (`CADDY_ADMIN_URL` leer lassen, wenn der Proxy von Hand gepflegt wird). Den Online-Check (Abschnitt oben) bei Bedarf
selbst als Timer oder Cron einrichten.

## Deinstallation

```bash
sudo systemctl disable --now portal-check.timer portal-web portal-worker
sudo rm -f /etc/systemd/system/portal-{web,worker}.service /etc/systemd/system/portal-check.{service,timer}
sudo systemctl daemon-reload
docker rm -f portal-caddy portal-postgres portal-redis        # Daten bleiben in den Volumes portal-*
```

Vereins-Instanzen vorher im Admin stilllegen oder mit **Instanz ENDGÜLTIG löschen** (samt Abschlusssicherung) entfernen, oder auf der
Kommandozeile mit `manage.py mandant_abbauen <kürzel> [--daten-loeschen]`. Archivierte gelöschte Instanzen liegen in
`instanzen/_geloescht/` und werden nie automatisch entfernt.
