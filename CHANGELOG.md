# Changelog

Alle nennenswerten Änderungen an der Portalverwaltung, neueste zuerst. Versionsnummern folgen Semantic
Versioning (siehe [VERSION.md](VERSION.md)). Die Einträge bis 0.6.0 wurden nachträglich aus der Git-Historie
zusammengestellt.

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
