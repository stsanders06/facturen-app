"""Nachtelijke back-up van de datamappen op de Pi.

Gebruik (cron, zie DOCKER.md):
    python3 backup.py DOELMAP DATAMAP [DATAMAP ...]

Elke DATAMAP komt in het archief onder de naam van de map erboven, dus
~/facturen-app/data wordt facturen-app/data/... Zo kunnen de facturen en het
rooster in één archief zonder door elkaar te lopen.

Alleen de standaardbibliotheek: dit draait op de Pi zelf, buiten de container,
en daar staat geen sqlite3-programma.
"""
import pathlib
import sqlite3
import sys
import tarfile
import tempfile
from datetime import datetime

BEWAREN = 14


def _sla_over(pad):
    # ._-bestanden zijn macOS-resten van het kopiëren naar de Pi. -wal en -shm
    # horen bij een open database; de momentopname hieronder heeft ze al verwerkt.
    return pad.name.startswith("._") or pad.name.endswith(("-wal", "-shm", "-journal"))


def _momentopname(db, map_):
    """Een kopie die klopt, ook als de app op dat moment schrijft.

    Een database gewoon inpakken terwijl de app schrijft kan een half
    bijgewerkt bestand opleveren. De backup-functie van sqlite neemt een
    consistente stand."""
    kopie = pathlib.Path(map_) / db.name
    bron = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    doel = sqlite3.connect(kopie)
    with doel:
        bron.backup(doel)
    uitkomst = doel.execute("PRAGMA integrity_check").fetchone()[0]
    bron.close()
    doel.close()
    if uitkomst != "ok":
        raise SystemExit(f"{db} is beschadigd ({uitkomst}); back-up niet gemaakt")
    return kopie


def maak_backup(doel, bronnen, nu=None):
    doel = pathlib.Path(doel)
    doel.mkdir(mode=0o700, parents=True, exist_ok=True)
    naam = f"backup-{(nu or datetime.now()):%Y-%m-%d-%H%M}.tar.gz"
    # Eerst naar een tijdelijke naam: valt de stroom uit tijdens het inpakken,
    # dan staat er geen half archief dat er als een goede back-up uitziet.
    half = doel / (naam + ".bezig")
    # Er zitten de inlogsleutel en eventuele wachtwoorden in: alleen jij mag het
    # lezen, ook al tijdens het inpakken.
    half.touch(mode=0o600)
    try:
        with tempfile.TemporaryDirectory() as tmp, tarfile.open(half, "w:gz") as tar:
            # resolve: met een relatief pad als 'data' is er geen map erboven, en
            # dan klopt de naam in het archief niet met het terugzetten.
            for bron in (pathlib.Path(b).resolve() for b in bronnen):
                if not bron.is_dir():
                    raise SystemExit(f"{bron} bestaat niet")
                voorvoegsel = pathlib.Path(bron.parent.name) / bron.name
                for pad in sorted(bron.rglob("*")):
                    if not pad.is_file() or _sla_over(pad):
                        continue
                    if pad.suffix == ".db":
                        sub = pathlib.Path(tmp) / voorvoegsel
                        sub.mkdir(parents=True, exist_ok=True)
                        tar.add(_momentopname(pad, sub), voorvoegsel / pad.relative_to(bron))
                    else:
                        tar.add(pad, voorvoegsel / pad.relative_to(bron))
    except BaseException:
        half.unlink(missing_ok=True)
        raise
    archief = doel / naam
    half.replace(archief)
    # Oude back-ups weg; de naam sorteert op datum.
    for oud in sorted(doel.glob("backup-*.tar.gz"))[:-BEWAREN]:
        oud.unlink()
    return archief


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    print(maak_backup(sys.argv[1], sys.argv[2:]))
