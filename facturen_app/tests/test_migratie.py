"""Bestaande databases die van een oudere versie komen, moeten blijven werken."""
import sqlite3

from conftest import facturen


def kolommen(conn, tabel):
    return {rij["name"] for rij in conn.execute(f"PRAGMA table_info({tabel})")}


def test_oude_tikkie_kolom_wordt_opgeruimd(db):
    """Tikkie is er in 1.3.0 uitgegaan, maar de kolom bleef staan in databases die
    van daarvoor komen."""
    try:
        db.execute("ALTER TABLE settings ADD COLUMN tikkie_link TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    db.commit()
    assert "tikkie_link" in kolommen(db, "settings")

    facturen.init_db()
    assert "tikkie_link" not in kolommen(db, "settings")


def test_init_db_mag_meerdere_keren_draaien(db):
    """Elke start roept init_db aan; dat hoort niets stuk te maken."""
    db.execute("INSERT INTO klanten (naam) VALUES ('Jan Jansen')")
    db.commit()
    facturen.init_db()
    facturen.init_db()
    assert db.execute("SELECT COUNT(*) FROM klanten").fetchone()[0] == 1


def test_de_instellingenrij_bestaat_altijd(db):
    assert db.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 1


def test_alle_tabellen_staan_er(db):
    namen = {r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"settings", "facturen", "regels", "klanten", "klussen", "uren",
            "offertes", "offerte_regels"} <= namen
