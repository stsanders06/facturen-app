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


def test_een_bon_die_uit_stond_gaat_een_keer_mee(db):
    """Elke start zette meesturen weer aan. Een vlag onthoudt dat het al gebeurd
    is, anders is een latere uitzondering bij de volgende start weer weg."""
    klus_id = db.execute(
        "INSERT INTO klussen (naam, gestart) VALUES ('Badkamer', '2026-08-01')"
    ).lastrowid
    inkoop_id = db.execute(
        """INSERT INTO inkopen (klus_id, omschrijving, bedrag, toegevoegd)
           VALUES (?, 'Tegellijm', 12.5, '2026-08-01')""",
        (klus_id,),
    ).lastrowid
    db.execute(
        """INSERT INTO bijlagen
           (klus_id, bestand, naam, toegevoegd, meesturen, inkoop_id)
           VALUES (?, 'bon.png', 'bon.png', '2026-08-01', 0, ?)""",
        (klus_id, inkoop_id),
    )
    db.execute(
        """INSERT INTO bijlagen
           (klus_id, bestand, naam, toegevoegd, meesturen, notitie_id)
           VALUES (?, 'foto.png', 'foto.png', '2026-08-01', 0, 1)""",
        (klus_id,),
    )
    db.execute("UPDATE settings SET meesturen_bijgewerkt=0 WHERE id=1")
    db.commit()

    facturen.init_db()
    bon, foto = db.execute(
        "SELECT meesturen FROM bijlagen ORDER BY id"
    ).fetchall()
    assert bon["meesturen"] == 1
    assert foto["meesturen"] == 0
    assert db.execute(
        "SELECT meesturen_bijgewerkt FROM settings WHERE id=1"
    ).fetchone()[0] == 1

    db.execute("UPDATE bijlagen SET meesturen=0 WHERE inkoop_id IS NOT NULL")
    db.commit()
    facturen.init_db()
    assert db.execute(
        "SELECT meesturen FROM bijlagen WHERE inkoop_id IS NOT NULL"
    ).fetchone()[0] == 0


def test_de_instellingenrij_bestaat_altijd(db):
    assert db.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 1


def test_alle_tabellen_staan_er(db):
    namen = {r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"settings", "facturen", "regels", "klanten", "klussen", "uren",
            "offertes", "offerte_regels", "inkopen", "inkoop_materialen"} <= namen
