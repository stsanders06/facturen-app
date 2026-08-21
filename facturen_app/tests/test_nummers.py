"""Nummering van rekeningen en offertes."""
from datetime import date

from conftest import facturen


def test_eerste_rekening_van_het_jaar_krijgt_001(db):
    assert facturen.volgend_nummer(db) == f"{date.today().year}-001"


def test_nummer_telt_door_op_het_hoogste_bestaande(db, maak_factuur):
    jaar = date.today().year
    maak_factuur(nummer=f"{jaar}-001")
    maak_factuur(nummer=f"{jaar}-007")
    assert facturen.volgend_nummer(db) == f"{jaar}-008"


def test_verwijderde_rekening_levert_geen_bestaand_nummer_op(db, maak_factuur):
    """Doortellen op het aantal rekeningen zou hier 003 geven, en dan zou de nieuwe
    PDF die van de bestaande 2026-003 overschrijven."""
    jaar = date.today().year
    maak_factuur(nummer=f"{jaar}-001")
    factuur_id = maak_factuur(nummer=f"{jaar}-002")
    maak_factuur(nummer=f"{jaar}-003")
    db.execute("DELETE FROM facturen WHERE id=?", (factuur_id,))
    db.commit()
    assert facturen.volgend_nummer(db) == f"{jaar}-004"


def test_offertes_tellen_apart_van_rekeningen(db, maak_factuur, maak_offerte):
    jaar = date.today().year
    maak_factuur(nummer=f"{jaar}-005")
    maak_offerte(nummer=f"OFF-{jaar}-001")
    assert facturen.volgend_offertenummer(db) == f"OFF-{jaar}-002"
    assert facturen.volgend_nummer(db) == f"{jaar}-006"


def test_nummers_van_vorig_jaar_tellen_niet_mee(db, maak_factuur):
    jaar = date.today().year
    maak_factuur(nummer=f"{jaar - 1}-042")
    assert facturen.volgend_nummer(db) == f"{jaar}-001"


def test_volgnummer_leest_het_cijferdeel(db):
    assert facturen._volgnummer("2026-014") == 14
    assert facturen._volgnummer("2026-abc") == 0
    assert facturen._volgnummer(None) == 0


def test_dubbele_nummers_worden_hersteld(db, maak_factuur):
    """De oudste houdt zijn nummer, de nieuwere schuift op naar een vrij nummer."""
    jaar = date.today().year
    maak_factuur(nummer=f"{jaar}-003")
    maak_factuur(nummer=f"{jaar}-003")
    hersteld = facturen.herstel_dubbele_nummers()
    assert hersteld == [(f"{jaar}-003", f"{jaar}-004")]

    nummers = [r["nummer"] for r in db.execute("SELECT nummer FROM facturen ORDER BY id")]
    assert nummers == [f"{jaar}-003", f"{jaar}-004"]


def test_zonder_dubbele_nummers_valt_er_niets_te_herstellen(db, maak_factuur):
    maak_factuur(nummer="2026-001")
    assert facturen.herstel_dubbele_nummers() == []
