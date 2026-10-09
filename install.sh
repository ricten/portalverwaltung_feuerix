#!/bin/bash
# Interaktiver Installer der Portalverwaltung (Linux-Server mit Docker). Fragt nach den gewuenschten
# Komponenten, erzeugt .env mit Zufallsgeheimnissen, richtet die Datenbank ein und legt bei Bedarf
# systemd-Dienste an. Mehrfaches Ausfuehren ist moeglich (vorhandene .env wird nicht ueberschrieben).
set -e
cd "$(dirname "$0")"
DIR="$(pwd)"
for f in requirements.txt manage.py config; do
  [ -e "$f" ] || { echo "FEHLER: $DIR ist unvollstaendig ($f fehlt). Das ganze Repo verwenden: git clone https://github.com/ricten/portalverwaltung_feuerix"; exit 1; }
done

frage() { # frage "Text" "standard(j|n)"  -> 0 = ja
  local a; read -r -p "$1 [$2] " a; a="${a:-$2}"; [[ "$a" =~ ^[jJyY] ]]
}
eingabe() { # eingabe "Text" "standard" -> stdout
  local a; read -r -p "$1 [$2] " a; echo "${a:-$2}"
}
zufall() { python3 -c "import secrets,string;print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range($1)))"; }

[ "$(id -u)" != 0 ] || { echo "FEHLER: Bitte ohne sudo starten (./install.sh) - sonst gehoeren Dateien und Dienste root. sudo ruft das Skript selbst auf, wo noetig."; exit 1; }
command -v docker >/dev/null || { echo "FEHLER: Docker fehlt."; exit 1; }
command -v python3 >/dev/null || { echo "FEHLER: python3 fehlt."; exit 1; }

echo "=== Portalverwaltung fuer Feuerix - Installation ==="
FEUERIX_URL="https://github.com/ricten/Feuerix"
FEUERIX=$(eingabe "Pfad zum Feuerix-Checkout (wird bei Bedarf von $FEUERIX_URL geklont)" "$DIR/../Feuerix")
if [ ! -f "$FEUERIX/docker-compose.yml" ]; then
  echo "Kein Feuerix-Checkout (docker-compose.yml) unter $FEUERIX gefunden."
  command -v git >/dev/null || { echo "FEHLER: git fehlt - Feuerix manuell nach $FEUERIX klonen."; exit 1; }
  if [ -e "$FEUERIX" ] && [ -n "$(ls -A "$FEUERIX" 2>/dev/null)" ]; then
    echo "FEHLER: $FEUERIX existiert und ist nicht leer, enthaelt aber kein Feuerix. Anderen Pfad angeben."; exit 1
  fi
  frage "  Feuerix jetzt nach $FEUERIX klonen?" j || { echo "Abbruch."; exit 1; }
  git clone "$FEUERIX_URL" "$FEUERIX" || { echo "FEHLER: Klonen fehlgeschlagen (privates Repo? Zugangsdaten/SSH-Key pruefen)."; exit 1; }
  [ -f "$FEUERIX/docker-compose.yml" ] || { echo "FEHLER: $FEUERIX/docker-compose.yml fehlt auch nach dem Klonen."; exit 1; }
fi
FEUERIX="$(cd "$FEUERIX" && pwd)"
# Die Portal-Dienste laufen als dieser Benutzer und brauchen Schreibrechte im Feuerix-Checkout (git fetch/pull).
if [ "$(stat -c %U "$FEUERIX")" != "$(id -un)" ]; then
  echo "Hinweis: $FEUERIX gehoert $(stat -c %U "$FEUERIX"), die Dienste laufen aber als $(id -un) (git-Update braucht Rechte)."
  frage "  Besitzer auf $(id -un) aendern (sudo chown -R)?" j && sudo chown -R "$(id -un):$(id -gn)" "$FEUERIX"
fi

echo
echo "Komponenten waehlen:"
frage "  PostgreSQL + Redis als Docker-Container mitinstallieren (sonst vorhandene nutzen)?" j && MIT_DB=1 || MIT_DB=0
frage "  Reverse Proxy Caddy (automatisches HTTPS) installieren und von der Portalverwaltung steuern lassen?" j && MIT_CADDY=1 || MIT_CADDY=0
frage "  Celery-Worker als systemd-Dienst (fuer Bereitstellung aus dem Admin-Bereich)?" j && MIT_WORKER=1 || MIT_WORKER=0
frage "  Weboberflaeche (Gunicorn) als systemd-Dienst?" j && MIT_WEB=1 || MIT_WEB=0
PORTAL_DOMAIN=""
if [ "$MIT_WEB" = 1 ] && [ "$MIT_CADDY" = 1 ]; then
  PORTAL_DOMAIN=$(eingabe "  Domain der Portalverwaltung selbst (leer = nur lokal unter 127.0.0.1:8000)" "")
fi
ACME=""
[ "$MIT_CADDY" = 1 ] && ACME=$(eingabe "  E-Mail fuer Let's Encrypt" "")
echo "Hinweis: OpenSlides und Paperless werden je Verein gewaehlt (Domain-Felder im Admin-Formular)."

python3 -m venv venv
./venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
  DBPW=$(zufall 32)
  FEKEY=$(./venv/bin/python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())")
  cat > .env <<EOF
SECRET_KEY=$(zufall 60)
FIELD_ENCRYPTION_KEY=$FEKEY
POSTGRES_DB=portalverwaltung
POSTGRES_USER=portal
POSTGRES_PASSWORD=$DBPW
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
REDIS_URL=redis://127.0.0.1:6379/1
DEBUG=0
ALLOWED_HOSTS=localhost,127.0.0.1${PORTAL_DOMAIN:+,$PORTAL_DOMAIN}
HTTPS=$([ -n "$PORTAL_DOMAIN" ] && echo 1 || echo 0)
FEUERIX_REPO_PFAD=$FEUERIX
INSTANZEN_VERZEICHNIS=$DIR/instanzen
WEB_PORT_BASIS=8100
PAPERLESS_PORT_BASIS=10100
OPENSLIDES_PORT_BASIS=9100
CADDY_ADMIN_URL=$([ "$MIT_CADDY" = 1 ] && echo http://127.0.0.1:2019)
CADDY_ACME_EMAIL=$ACME
PORTAL_DOMAIN=$PORTAL_DOMAIN
PORTAL_PORT=8000
EOF
  chmod 600 .env
  echo ">>> .env erzeugt. WICHTIG: FIELD_ENCRYPTION_KEY sichern - ohne ihn sind die Mandanten-Geheimnisse unlesbar!"
else
  echo ">>> Vorhandene .env bleibt unveraendert."
fi
set -a; . ./.env; set +a

if [ "$MIT_DB" = 1 ]; then
  docker start portal-postgres >/dev/null 2>&1 || docker run -d --name portal-postgres --restart unless-stopped \
    -e POSTGRES_DB="$POSTGRES_DB" -e POSTGRES_USER="$POSTGRES_USER" -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
    -p 127.0.0.1:5432:5432 -v portal-pgdata:/var/lib/postgresql postgres:18 >/dev/null
  docker start portal-redis >/dev/null 2>&1 || docker run -d --name portal-redis --restart unless-stopped \
    -p 127.0.0.1:6379:6379 redis:7-alpine >/dev/null
  echo ">>> Warte auf PostgreSQL ..."
  for i in $(seq 1 30); do docker exec portal-postgres pg_isready -U "$POSTGRES_USER" >/dev/null 2>&1 && break; sleep 2; done
fi
if [ "$MIT_CADDY" = 1 ]; then
  docker start portal-caddy >/dev/null 2>&1 || docker run -d --name portal-caddy --restart unless-stopped \
    --network host -v portal-caddy-data:/data caddy:2 >/dev/null
fi

./venv/bin/python manage.py migrate
./venv/bin/python manage.py collectstatic --noinput >/dev/null
echo ">>> Administrator der Portalverwaltung anlegen:"
./venv/bin/python manage.py createsuperuser

unit() { # unit NAME BESCHREIBUNG EXEC
  sudo tee "/etc/systemd/system/$1.service" >/dev/null <<EOF
[Unit]
Description=$2
After=network.target docker.service

[Service]
User=$(id -un)
WorkingDirectory=$DIR
EnvironmentFile=$DIR/.env
ExecStart=$3
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
  sudo systemctl daemon-reload && sudo systemctl enable --now "$1"
}
[ "$MIT_WORKER" = 1 ] && unit portal-worker "Portalverwaltung Celery-Worker" "$DIR/venv/bin/celery -A config worker -l info --concurrency 2"
[ "$MIT_WEB" = 1 ] && unit portal-web "Portalverwaltung Weboberflaeche" "$DIR/venv/bin/gunicorn config.wsgi --bind 127.0.0.1:8000 --workers 2"
[ "$MIT_CADDY" = 1 ] && [ -n "$PORTAL_DOMAIN" ] && ./venv/bin/python manage.py shell -c "from apps.mandanten import proxy; proxy.proxy_aktualisieren()" || true

echo
echo "=== Fertig ==="
[ "$MIT_WEB" = 1 ] && echo "Weboberflaeche: ${PORTAL_DOMAIN:+https://$PORTAL_DOMAIN/admin/}${PORTAL_DOMAIN:-http://127.0.0.1:8000/admin/}"
echo "Neuen Verein anlegen: Admin > Mandanten-Instanzen > Hinzufuegen, dann Aktion 'Bereitstellen'."
echo "Status aller Instanzen: ./venv/bin/python manage.py mandant_status"
