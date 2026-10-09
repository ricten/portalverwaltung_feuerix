# Changelog

Alle nennenswerten Änderungen an der Portalverwaltung, neueste zuerst. Versionsnummern folgen Semantic
Versioning (siehe [VERSION.md](VERSION.md)). Die Einträge bis 0.6.0 wurden nachträglich aus der Git-Historie
zusammengestellt.

## 0.12.0 (2026-10-09)
- Neu: In jeder Zeile der Instanzliste gibt es die Schaltflächen **▶ Starten**, **■ Stoppen** und **↻ Neustarten**
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
