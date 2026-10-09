"""Backup-Ziel: legt die Sicherungen der Instanzen zusaetzlich auf einer SMB-/CIFS-Netzwerkfreigabe ab (Windows-Freigabe,
NAS, Samba) - mit Benutzer und Passwort, ohne dass das Portal etwas mounten oder Root-Rechte haben muss
(Bibliothek smbprotocol, SMB-Signierung ist aktiv). Konfiguriert wird das im Admin unter "Backup-Ziel"."""
import ntpath
from datetime import datetime, timedelta, timezone

from .basis import BereitstellungsFehler
from .models import BackupZiel

CHUNK = 1024 * 1024


def aktuell():
    """Das aktive Backup-Ziel oder None."""
    return BackupZiel.objects.filter(pk=1, aktiv=True).first()


def _smb():
    import smbclient   # erst bei Bedarf laden (in den Tests ersetzt)
    return smbclient


def _wurzel(ziel):
    pfad = ntpath.join(f"\\\\{ziel.server}", ziel.freigabe)
    if ziel.unterordner:
        pfad = ntpath.join(pfad, *ziel.unterordner_teile())
    return pfad


def _verbinden(ziel, smb):
    smb.register_session(ziel.server, username=ziel.benutzer, password=ziel.passwort, connection_timeout=15)


def _fehler(was, exc):
    return BereitstellungsFehler(f"{was}: {type(exc).__name__}: {exc}")


def testen(ziel):
    """Verbindet, legt den Zielordner an, schreibt und loescht eine Testdatei. Wirft BereitstellungsFehler."""
    smb = _smb()
    try:
        _verbinden(ziel, smb)
        ordner = ntpath.join(_wurzel(ziel), "_portal_test")
        smb.makedirs(ordner, exist_ok=True)
        datei = ntpath.join(ordner, "test.txt")
        with smb.open_file(datei, mode="wb") as f:
            f.write(b"Portalverwaltung Verbindungstest")
        smb.remove(datei)
        try:
            smb.rmdir(ordner)
        except Exception:
            pass
    except BereitstellungsFehler:
        raise
    except Exception as e:
        raise _fehler(f"Verbindung zu \\\\{ziel.server}\\{ziel.freigabe} fehlgeschlagen", e)
    return f"Verbindung zu \\\\{ziel.server}\\{ziel.freigabe} erfolgreich (Schreiben und Löschen getestet)."


def hochladen(mandant, lokaler_ordner):
    """Laedt alle Dateien aus `lokaler_ordner` nach `<Wurzel>\\<kuerzel>\\`, die dort noch fehlen (gleicher Name und
    gleiche Groesse = schon vorhanden), prueft die Groesse nach dem Hochladen und raeumt bei Bedarf auf (lokale
    Kopie loeschen, alte Sicherungen auf der Freigabe nach `aufbewahrung_tage`). -> Anzahl neu hochgeladener Dateien."""
    ziel = aktuell()
    if ziel is None:
        return 0
    smb = _smb()
    neu = 0
    try:
        _verbinden(ziel, smb)
        remote = ntpath.join(_wurzel(ziel), mandant.kuerzel)
        smb.makedirs(remote, exist_ok=True)
        vorhanden = {e.name: e.stat().st_size for e in smb.scandir(remote) if e.is_file()}
        for datei in sorted(lokaler_ordner.iterdir()):
            if not datei.is_file():
                continue
            groesse = datei.stat().st_size
            ziel_pfad = ntpath.join(remote, datei.name)
            if vorhanden.get(datei.name) != groesse:
                with open(datei, "rb") as quelle, smb.open_file(ziel_pfad, mode="wb") as senke:
                    while True:
                        stueck = quelle.read(CHUNK)
                        if not stueck:
                            break
                        senke.write(stueck)
                if smb.stat(ziel_pfad).st_size != groesse:
                    raise BereitstellungsFehler(f"{datei.name}: Größe auf der Freigabe stimmt nicht")
                neu += 1
            if ziel.lokal_loeschen:
                datei.unlink()
        if ziel.aufbewahrung_tage:
            grenze = datetime.now(timezone.utc) - timedelta(days=ziel.aufbewahrung_tage)
            for e in smb.scandir(remote):
                if e.is_file() and datetime.fromtimestamp(e.stat().st_mtime, tz=timezone.utc) < grenze:
                    smb.remove(ntpath.join(remote, e.name))
    except BereitstellungsFehler:
        raise
    except Exception as e:
        raise _fehler("Upload zur Netzwerkfreigabe", e)
    return neu
