# Changelog

Alle nennenswerten Ã„nderungen an der Portalverwaltung, neueste zuerst. Versionsnummern folgen Semantic
Versioning (siehe [VERSION.md](VERSION.md)). Die EintrÃ¤ge bis 0.6.0 wurden nachtrÃ¤glich aus der Git-Historie
zusammengestellt.

## 0.7.1 (2026-10-09)
- Fix: Die Datenbank-Zugangsdaten der Portalverwaltung (`POSTGRES_USER`/`-PASSWORD`/`-DB` aus deren `.env`) wurden
  über die Prozessumgebung an `docker compose` durchgereicht und überschrieben dort die der Mandanten-Instanz. Folge:
  Der Feuerix-Web-Container kam nicht hoch („password authentication failed for user mandant_…“), die
  Bereitstellung endete bei 35 % mit „Fehlgeschlagen“. Docker-Aufrufe bekommen jetzt eine bereinigte Umgebung.

## 0.7.0 (2026-10-09)
- Installer: Fehlt der Feuerix-Checkout, bietet `install.sh` an, ihn automatisch zu klonen (Standardpfad jetzt
  `../Feuerix`), statt mit einer `docker-compose.yml`-Fehlermeldung abzubrechen.
- Installer: erkennt ein unvollstÃ¤ndiges Verzeichnis (`requirements.txt`, `manage.py` oder `config` fehlt) und
  nennt den passenden `git clone`-Befehl.
- Die Startseite (`/`) leitet auf `/admin/` um, statt â€žNot Foundâ€œ zu zeigen.
- `.gitattributes`: Shell-Skripte behalten LF-Zeilenenden (sonst bricht `install.sh` unter Linux ab).

## 0.6.0 (2026-09-26)
- Paperless-ngx und OpenSlides lassen sich im Instanzformular per Haken nachinstallieren.

## 0.5.0 (2026-09-26)
- Fortschrittsbalken mit Live-Schritten im Admin wÃ¤hrend der Bereitstellung.
- Aktion â€žAdmin-Passwort der Instanz zurÃ¼cksetzenâ€œ.

## 0.4.0 (2026-09-26)
- Interaktiver Installer (`install.sh`) und WeboberflÃ¤che mit Live-Erreichbarkeit je Dienst.
- Eigene Domain der Portalverwaltung im Reverse Proxy.

## 0.3.0 (2026-09-26)
- Betrieb: Backup, Status, gestaffelter Update-Rollout, Admin-Aktionen, Dokumentation.

## 0.2.0 (2026-09-25)
- Ausbaustufe 2: Paperless-ngx und OpenSlides je Mandant (eigener Stack, Proxy-Eintrag, Anbindung in Feuerix).

## 0.1.0 (2026-09-25)
- Ausbaustufe 1: automatisierte Bereitstellung einer Feuerix-Instanz je Verein (`.env`, Port, Docker-Compose-Stack,
  BereitschaftsprÃ¼fung).
- Ausbaustufe 1b: Reverse-Proxy-Automatisierung (Caddy), Abbau von Instanzen, Eingabe-HÃ¤rtung.
- Integrationstest per GitHub Actions.
