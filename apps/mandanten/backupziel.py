"""Backup-Ziel: legt die Sicherungen der Instanzen zusaetzlich auf einer SMB-/CIFS-Netzwerkfreigabe ab (Windows-Freigabe,
NAS, Samba) - mit Benutzer und Passwort, ohne dass das Portal etwas mounten oder Root-Rechte haben muss
(Bibliothek smbprotocol; SMB-Signierung ist aktiv, die Verschluesselung (SMB 3) ist standardmaessig erzwungen). Konfiguriert wird das im Admin unter "Backup-Ziel"."""
import ntpath
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.utils import timezone as django_zeit

from . import rechte
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
    smb.register_session(ziel.server, username=ziel.benutzer, password=ziel.passwort, connection_timeout=15,
                         encrypt=True if ziel.verschluesselung else None)


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


def auflisten(mandant, unterordner=""):
    """Dateien (Name, Groesse) direkt im Ordner `<Wurzel>\\<kuerzel>\\[<unterordner>\\]` auf der Freigabe."""
    ziel = aktuell()
    if ziel is None:
        return []
    smb = _smb()
    try:
        _verbinden(ziel, smb)
        remote = ntpath.join(_wurzel(ziel), mandant.kuerzel, *[t for t in re.split(r"[\\/]+", unterordner) if t])
        return [(e.name, e.stat().st_size) for e in smb.scandir(remote) if e.is_file()]
    except BereitstellungsFehler:
        raise
    except Exception as e:
        raise _fehler("Freigabe lesen", e)


def herunterladen(mandant, name, zielordner, unterordner=""):
    """Laedt `<Wurzel>\\<kuerzel>\\[<unterordner>\\]<name>` nach `zielordner/name` und prueft die Groesse. -> lokaler Pfad."""
    if name != ntpath.basename(name) or "/" in name or name in ("", ".", ".."):
        raise BereitstellungsFehler(f"Ungültiger Dateiname: {name!r}")
    ziel = aktuell()
    if ziel is None:
        raise BereitstellungsFehler("Kein aktives Backup-Ziel.")
    smb = _smb()
    lokal = Path(zielordner) / name
    try:
        _verbinden(ziel, smb)
        remote = ntpath.join(_wurzel(ziel), mandant.kuerzel, *[t for t in re.split(r"[\\/]+", unterordner) if t], name)
        erwartet = smb.stat(remote).st_size
        with smb.open_file(remote, mode="rb") as quelle, open(lokal, "wb") as senke:
            while True:
                stueck = quelle.read(CHUNK)
                if not stueck:
                    break
                senke.write(stueck)
        if lokal.stat().st_size != erwartet:
            raise BereitstellungsFehler(f"{name}: Größe nach dem Herunterladen stimmt nicht")
        try:
            rechte.datei_sichern(lokal)
        except OSError:
            pass
    except BereitstellungsFehler:
        raise
    except Exception as e:
        raise _fehler(f"Herunterladen von {name}", e)
    return lokal


def _spiegeln(smb, ziel, lokal, remote, rekursiv):
    """Laedt die Dateien aus `lokal` nach `remote`, die dort fehlen (gleicher Name und gleiche Groesse = schon da),
    prueft die Groesse und - mit `rekursiv` - auch Unterordner. -> Anzahl neu hochgeladener Dateien."""
    vorhanden = {e.name: e.stat().st_size for e in smb.scandir(remote) if e.is_file()}
    neu = 0
    for datei in sorted(lokal.iterdir()):
        if datei.is_dir():
            if rekursiv:
                unter = ntpath.join(remote, datei.name)
                smb.makedirs(unter, exist_ok=True)
                neu += _spiegeln(smb, ziel, datei, unter, True)
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
        if ziel.lokal_loeschen and not rekursiv:
            datei.unlink()
    return neu


def hochladen(mandant, lokaler_ordner, unterordner="", rekursiv=False):
    """Laedt die Dateien aus `lokaler_ordner` nach `<Wurzel>\\<kuerzel>\\[<unterordner>\\]`, die dort noch fehlen,
    prueft die Groesse nach dem Hochladen und raeumt bei Bedarf auf (lokale Kopie loeschen, alte Sicherungen auf der
    Freigabe nach `aufbewahrung_tage`). Mit `rekursiv` werden auch Unterordner gespiegelt (z. B. der Paperless-Export);
    dabei wird weder lokal noch auf der Freigabe etwas geloescht. -> Anzahl neu hochgeladener Dateien."""
    ziel = aktuell()
    if ziel is None:
        return 0
    smb = _smb()
    try:
        _verbinden(ziel, smb)
        teile = [t for t in re.split(r"[\\/]+", unterordner) if t]
        remote = ntpath.join(_wurzel(ziel), mandant.kuerzel, *teile)
        smb.makedirs(remote, exist_ok=True)
        neu = _spiegeln(smb, ziel, Path(lokaler_ordner), remote, rekursiv)
        if ziel.aufbewahrung_tage and not rekursiv:
            grenze = datetime.now(timezone.utc) - timedelta(days=ziel.aufbewahrung_tage)
            for e in smb.scandir(remote):
                if e.is_file() and datetime.fromtimestamp(e.stat().st_mtime, tz=timezone.utc) < grenze:
                    smb.remove(ntpath.join(remote, e.name))
    except BereitstellungsFehler:
        raise
    except Exception as e:
        raise _fehler("Upload zur Netzwerkfreigabe", e)
    ort = ntpath.join(mandant.kuerzel, *teile)
    BackupZiel.objects.filter(pk=1).update(letzter_upload_am=django_zeit.now(),
                                           letzter_upload_text=f"{ort}: {neu} neue Datei(en)"[:255])
    return neu
