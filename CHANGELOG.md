# Changelog

Alle nennenswerten Änderungen an der Portalverwaltung, neueste zuerst. Versionsnummern folgen Semantic
Versioning (siehe [VERSION.md](VERSION.md)). Die Einträge bis 0.6.0 wurden nachträglich aus der Git-Historie
zusammengestellt.

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
