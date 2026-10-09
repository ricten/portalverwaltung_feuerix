# AGENTS.md

Hinweise für KI-Coding-Agenten zu diesem Projekt. Sprache der Oberfläche, Doku und Commit-Nachrichten:
**Deutsch**.

## Worum es geht
Portalverwaltung für [Feuerix](https://github.com/ricten/Feuerix): Django-Anwendung, die je Verein eine
vollständig isolierte Feuerix-Instanz (eigene Datenbank, eigener Docker-Compose-Stack) bereitstellt, im
Reverse Proxy (Caddy) einträgt und betreibt; optional mit Paperless-ngx und OpenSlides je Mandant.
Überblick und Bedienung: [README.md](README.md).

## Aufbau
* `apps/mandanten/`: die einzige App. `models.py` (Mandant-Instanz, Geheimnisse verschlüsselt über `fields.py`),
  `provisioning.py` (Bereitstellung/Abbau), `paperless.py`, `openslides.py`, `proxy.py` (Caddyfile über die
  Admin-API), `betrieb.py` (Backup, Status, Update), `basis.py` (Docker-Aufrufe, Fortschritt), `tasks.py`
  (Celery), `admin.py` (Weboberfläche), `management/commands/` (CLI).
* `install.sh`: interaktiver Installer für Linux mit Docker (legt `.env`, Container und systemd-Dienste an).
* Die Compose-Dateien kommen aus dem Feuerix-Checkout (`FEUERIX_REPO_PFAD`), nichts wird kopiert.

## Befehle
* Entwicklung/Tests ohne Postgres/Redis: `python manage.py test --settings=config.settings_sqlite`
* Integrationstest: `.github/workflows/integrationstest.yml` (echte Bereitstellung, läuft bei Push nach `master`).

## Update-Prüfung
`updates.py`: `pruefen()` (git fetch, Version/Changelog des Upstream) und `ausrollen()` (git pull, dann nacheinander
`betrieb.update_ausrollen` je veralteter Instanz). Der Rollout startet nie automatisch, nur per Klick/Befehl.
Zustand in der Tabelle `UpdateZustand` (eine Zeile), Hinweis in `templates/admin/mandanten/update_banner.html`.

## Regeln
* Eingaben, die in `.env` oder Caddyfile landen, immer über `validatoren.py` prüfen (Injection).
* Geheimnisse einer Instanz nie neu erzeugen, wenn sie schon existieren - eine vorhandene Datenbank wäre sonst
  nicht mehr erreichbar. `FIELD_ENCRYPTION_KEY` dieser Anwendung geht nie ins Repository.
* Der Celery-Worker braucht Docker-Zugriff (root-äquivalent) und läuft deshalb direkt auf dem Host, nicht im
  Container.
* Docker-/Compose-Aufrufe nur über `basis.docker()`, Pfade in Compose-Dateien über `basis.yaml_pfad()`.
* Shell-Skripte mit LF-Zeilenenden (siehe `.gitattributes`).

## Dokumentation
Bei jeder Änderung die Doku mitpflegen: [README.md](README.md) (Funktionen, Betrieb), [INSTALL.md](INSTALL.md)
(Installation, Update, Fehlerbehebung - neu entdeckte Stolpersteine dort eintragen) und diese Datei, falls sich
Aufbau oder Regeln ändern. Die Version in der Fußzeile des Portals kommt aus `VERSION.md` (fett, `**x.y.z**`).

## Versionierung
Bei jeder Änderung mit Nutzerwirkung: Nummer in [VERSION.md](VERSION.md) erhöhen (Semantic Versioning) und einen
Eintrag oben in [CHANGELOG.md](CHANGELOG.md) schreiben. Nach dem Commit den Stand mit einem annotierten Git-Tag
`vX.Y.Z` markieren (`git tag -a vX.Y.Z -m "..."`) und Tags mitpushen (`git push --tags`). Rein interne Commits brauchen keinen Eintrag.
