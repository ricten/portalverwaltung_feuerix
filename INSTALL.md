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
./install.sh        # ohne sudo – das Skript ruft sudo selbst auf, wo nötig
```

Der Assistent fragt nacheinander:

1. **Pfad zum Feuerix-Checkout** – Standard `../Feuerix` (neben dem Portal). Fehlt er, bietet der Installer an, ihn von
   `https://github.com/ricten/Feuerix` zu klonen. Daraus wird die `docker-compose.yml` jeder Instanz genommen.
2. **PostgreSQL + Redis als Docker-Container** mitinstallieren (sonst werden vorhandene verwendet).
3. **Caddy** (Reverse Proxy mit automatischem HTTPS) installieren und von der Portalverwaltung steuern lassen.
4. **Celery-Worker** als systemd-Dienst (nötig für „Bereitstellen“ im Admin).
5. **Weboberfläche** (Gunicorn) als systemd-Dienst.
6. **Domain der Portalverwaltung** (leer = nur lokal unter `127.0.0.1:8000`) und **E-Mail für Let's Encrypt**.

Danach erzeugt er `.env` mit Zufallsgeheimnissen, richtet die Datenbank ein, führt die Migrationen aus und fragt nach
Benutzername und Passwort des Portal-Administrators. Mehrfaches Ausführen ist möglich; eine vorhandene `.env` wird nicht
überschrieben.

> **`FIELD_ENCRYPTION_KEY` aus der `.env` sichern.** Ohne ihn sind alle gespeicherten Geheimnisse der Vereins-Instanzen
> unlesbar.

## 3. Anmelden und ersten Verein anlegen

1. `https://<portal-domain>/` öffnen (leitet auf `/admin/`) und mit dem Portal-Administrator anmelden. Die installierte
   **Version** steht in der Fußzeile.
2. **Mandanten-Instanzen → Hinzufügen**: Kürzel, Vereinsname, Domain, Administrator-Benutzername und -E-Mail ausfüllen.
   Optional die Haken **Paperless-ngx** / **OpenSlides** setzen.
3. Instanz in der Liste markieren → Aktion **Bereitstellen**. Der Fortschrittsbalken zeigt die Schritte; der erste Lauf
   baut das Feuerix-Image und dauert einige Minuten.
4. Im Betrieb: Aktionen **Stoppen**, **Starten** und **Neustarten** (auf der Kommandozeile
   `manage.py mandant_dienst <kürzel> stop|start|restart`). Datenbank und Medien bleiben dabei erhalten.
5. Aktion **Zugangsdaten des Instanz-Administrators anzeigen** zeigt Adresse, Benutzer und Startpasswort der neuen
   Instanz (nur für Superuser).

## Aktualisieren

```bash
cd /opt/verein
git pull
./venv/bin/pip install -q -r requirements.txt
./venv/bin/python manage.py migrate
./venv/bin/python manage.py collectstatic --noinput
sudo systemctl restart portal-web portal-worker
```

Den laufenden Vereins-Instanzen den neuen Feuerix-Code ausrollen: Aktion **Auf aktuellen Feuerix-Code aktualisieren**
im Admin oder `./venv/bin/python manage.py mandant_update --git-pull`.

## Fehlerbehebung

| Meldung / Symptom | Ursache und Lösung |
|---|---|
| `docker-compose.yml nicht gefunden` | kein Feuerix-Checkout am angegebenen Pfad – den Klon-Vorschlag des Installers bestätigen |
| `ensurepip is not available` | `sudo apt install python3-venv` (bzw. `python3.13-venv`), dann `rm -rf venv` und Installer erneut starten |
| `requirements.txt` nicht gefunden / „Verzeichnis ist unvollständig“ | Verzeichnis ist kein vollständiger Klon – siehe Schritt 1 |
| `permission denied` bei Docker | Benutzer nicht in der Gruppe `docker` (Voraussetzungen) |
| Seite zeigt „Not Found“ | Version < 0.7.0 oder Pfad falsch – `/admin/` aufrufen, `git pull` und Dienst neu starten |
| Bereitstellung hängt bei 35 % und schlägt fehl | Logs des Web-Containers: `docker logs --tail 50 mandant-<kürzel>-web-1`; Protokoll der Instanz im Admin lesen |
| `password authentication failed for user "mandant_…"` | Fehler in Version < 0.7.1. Nach `git pull` und Neustart der Dienste: `docker compose -p mandant-<kürzel> down -v`, dann erneut **Bereitstellen** |
| Admin-Passwort der Instanz unbekannt | Aktion **Zugangsdaten anzeigen** bzw. **Admin-Passwort zurücksetzen** |
| Nach `git pull` keine Wirkung | Dienste neu starten: `sudo systemctl restart portal-web portal-worker` (der Worker lädt Code nicht automatisch nach) |

Dienste prüfen: `systemctl status portal-web portal-worker`, Logs: `journalctl -u portal-worker -e`.
Status aller Instanzen: `./venv/bin/python manage.py mandant_status`.

## Manuelle Installation (ohne Installer)

```bash
git clone https://github.com/ricten/portalverwaltung_feuerix /opt/verein && cd /opt/verein
git clone https://github.com/ricten/Feuerix ../Feuerix
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
cp .env.example .env     # alle Werte setzen, FEUERIX_REPO_PFAD=/opt/Feuerix, FIELD_ENCRYPTION_KEY erzeugen
set -a; . ./.env; set +a
./venv/bin/python manage.py migrate
./venv/bin/python manage.py createsuperuser
./venv/bin/celery -A config worker -l info --concurrency 2      # eigener Prozess/Dienst
./venv/bin/gunicorn config.wsgi --bind 127.0.0.1:8000           # eigener Prozess/Dienst
```

Beispiel für eine systemd-Unit des Workers: [README.md](README.md#produktivbetrieb-beispiel-systemd). Postgres, Redis und
Caddy müssen dann selbst bereitgestellt werden (`CADDY_ADMIN_URL` leer lassen, wenn der Proxy von Hand gepflegt wird).

## Deinstallation

```bash
sudo systemctl disable --now portal-web portal-worker && sudo rm /etc/systemd/system/portal-{web,worker}.service
docker rm -f portal-caddy portal-postgres portal-redis        # Daten bleiben in den Volumes portal-*
```

Vereins-Instanzen vorher im Admin pausieren oder mit `manage.py mandant_abbauen <kürzel> [--daten-loeschen]` abbauen.
