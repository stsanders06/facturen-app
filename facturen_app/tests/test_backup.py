"""De nachtelijke back-up (backup.py in de hoofdmap van de repo)."""
import pathlib
import sqlite3
import sys
import tarfile
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
import backup  # noqa: E402


@pytest.fixture
def datamap(tmp_path):
    data = tmp_path / "facturen-app" / "data"
    (data / "pdfs").mkdir(parents=True)
    (data / "pdfs" / "2026-001.pdf").write_bytes(b"%PDF-voorbeeld")
    (data / "._facturen.db").write_bytes(b"macos-rest")
    db = sqlite3.connect(data / "facturen.db")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE facturen (nummer TEXT)")
    db.execute("INSERT INTO facturen VALUES ('2026-001')")
    db.commit()
    # Open laten, zoals de draaiende app: de rij staat dan nog in de -wal.
    yield data
    db.close()


def test_een_back_up_is_terug_te_zetten(datamap, tmp_path):
    archief = backup.maak_backup(tmp_path / "backups", [datamap])
    terug = tmp_path / "terug"
    with tarfile.open(archief) as tar:
        namen = tar.getnames()
        tar.extractall(terug, filter="data")
    assert "facturen-app/data/pdfs/2026-001.pdf" in namen
    assert not any("._" in n or n.endswith(("-wal", "-shm")) for n in namen)
    db = sqlite3.connect(terug / "facturen-app" / "data" / "facturen.db")
    assert db.execute("SELECT nummer FROM facturen").fetchall() == [("2026-001",)]
    assert archief.stat().st_mode & 0o077 == 0


def test_alleen_de_laatste_veertien_blijven(datamap, tmp_path):
    begin = datetime(2026, 11, 1, 3, 0)
    for dag in range(16):
        backup.maak_backup(tmp_path / "b", [datamap], nu=begin + timedelta(days=dag))
    over = sorted(p.name for p in (tmp_path / "b").iterdir())
    assert len(over) == 14
    assert over[0] == "backup-2026-11-03-0300.tar.gz"


def test_een_beschadigde_database_geeft_geen_back_up(tmp_path):
    data = tmp_path / "app" / "data"
    data.mkdir(parents=True)
    (data / "facturen.db").write_bytes(b"geen database" * 100)
    with pytest.raises(Exception):
        backup.maak_backup(tmp_path / "b", [data])
    assert not list((tmp_path / "b").iterdir())


def test_een_relatief_pad_krijgt_dezelfde_naam(datamap, tmp_path, monkeypatch):
    """De handleiding draait hem als 'backup.py ~/backups data' vanuit de appmap."""
    monkeypatch.chdir(datamap.parent)
    archief = backup.maak_backup(tmp_path / "b", ["data"])
    with tarfile.open(archief) as tar:
        assert "facturen-app/data/pdfs/2026-001.pdf" in tar.getnames()
