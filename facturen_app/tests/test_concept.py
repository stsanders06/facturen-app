"""Een rekening is eerst een concept en krijgt zijn nummer pas als hij vastligt."""
import os
from datetime import date

from conftest import facturen
from test_pdf import tekst_uit_pdf


def maak_concept(post, db, klant="Jan Jansen"):
    post("/nieuw", {"klant_naam": klant, "datum": "2026-08-14",
                    "omschrijving": "Kraan", "type": "materiaal",
                    "aantal": "1", "prijs": "100"})
    return db.execute("SELECT id FROM facturen ORDER BY id DESC").fetchone()[0]


def test_concept_heeft_nog_geen_nummer(post, db):
    factuur_id = maak_concept(post, db)
    assert db.execute("SELECT nummer FROM facturen WHERE id=?", (factuur_id,)).fetchone()[0] == ""


def test_definitief_maken_geeft_het_volgende_nummer(post, db):
    factuur_id = maak_concept(post, db)
    post(f"/factuur/{factuur_id}/definitief")
    nummer = db.execute("SELECT nummer FROM facturen WHERE id=?", (factuur_id,)).fetchone()[0]
    assert nummer == f"{date.today().year}-001"


def test_definitief_maken_hernoemt_de_pdf(post, db):
    factuur_id = maak_concept(post, db)
    assert os.path.exists(os.path.join(facturen.PDF_DIR, f"concept-{factuur_id}.pdf"))

    post(f"/factuur/{factuur_id}/definitief")
    nummer = db.execute("SELECT nummer FROM facturen WHERE id=?", (factuur_id,)).fetchone()[0]
    assert os.path.exists(os.path.join(facturen.PDF_DIR, f"{nummer}.pdf"))
    assert not os.path.exists(os.path.join(facturen.PDF_DIR, f"concept-{factuur_id}.pdf"))


def test_weggegooid_concept_laat_geen_gat_in_de_nummering(post, db):
    """Dit is waar het hele concept voor is: een rekening die je toch niet verstuurt,
    hoort geen nummer op te souperen."""
    eerste = maak_concept(post, db)
    post(f"/factuur/{eerste}/verwijder")

    tweede = maak_concept(post, db)
    post(f"/factuur/{tweede}/definitief")
    nummer = db.execute("SELECT nummer FROM facturen WHERE id=?", (tweede,)).fetchone()[0]
    assert nummer == f"{date.today().year}-001"


def test_twee_concepten_krijgen_hun_nummer_in_de_volgorde_van_versturen(post, db):
    eerste = maak_concept(post, db, "Eerste Klant")
    tweede = maak_concept(post, db, "Tweede Klant")

    post(f"/factuur/{tweede}/definitief")
    post(f"/factuur/{eerste}/definitief")

    jaar = date.today().year
    assert db.execute("SELECT nummer FROM facturen WHERE id=?", (tweede,)).fetchone()[0] == f"{jaar}-001"
    assert db.execute("SELECT nummer FROM facturen WHERE id=?", (eerste,)).fetchone()[0] == f"{jaar}-002"


def test_al_definitief_verandert_niets(post, db, maak_factuur):
    factuur_id = maak_factuur(nummer="2026-042")
    post(f"/factuur/{factuur_id}/definitief")
    assert db.execute("SELECT nummer FROM facturen WHERE id=?", (factuur_id,)).fetchone()[0] == "2026-042"


def test_op_de_pdf_van_een_concept_staat_concept(post, db):
    factuur_id = maak_concept(post, db)
    tekst = tekst_uit_pdf(facturen.maak_pdf(factuur_id))
    assert "CONCEPT" in tekst


def test_na_definitief_staat_het_nummer_op_de_pdf(post, db):
    factuur_id = maak_concept(post, db)
    post(f"/factuur/{factuur_id}/definitief")
    nummer = db.execute("SELECT nummer FROM facturen WHERE id=?", (factuur_id,)).fetchone()[0]
    tekst = tekst_uit_pdf(os.path.join(facturen.PDF_DIR, f"{nummer}.pdf"))
    assert nummer in tekst
    assert "CONCEPT" not in tekst


def test_concept_is_te_bekijken_en_te_downloaden(post, db, client):
    factuur_id = maak_concept(post, db)
    assert client.get(f"/factuur/{factuur_id}/bekijk").status_code == 200
    assert client.get(f"/factuur/{factuur_id}/pdf").status_code == 200


def test_offerte_naar_rekening_levert_ook_een_concept(post, db, maak_offerte):
    offerte_id = maak_offerte(status="geaccepteerd")
    post(f"/offerte/{offerte_id}/naar-rekening")
    assert db.execute("SELECT nummer FROM facturen").fetchone()[0] == ""


def test_lege_nummers_gelden_niet_als_dubbel(post, db):
    """Zonder deze uitzondering zou het herstel van dubbele nummers alle concepten
    als duplicaat zien en ze allemaal een nummer geven."""
    maak_concept(post, db, "Eerste")
    maak_concept(post, db, "Tweede")
    assert facturen.herstel_dubbele_nummers() == []
    assert db.execute("SELECT COUNT(*) FROM facturen WHERE nummer=''").fetchone()[0] == 2


def test_kopie_is_een_nieuw_concept_met_dezelfde_regels(post, db, maak_factuur):
    origineel = maak_factuur(nummer="2026-001", klant="Jan Jansen", totaal=121.0)
    post(f"/factuur/{origineel}/kopieer")

    kopie = db.execute("SELECT * FROM facturen WHERE id<>? ORDER BY id DESC", (origineel,)).fetchone()
    assert kopie["klant_naam"] == "Jan Jansen"
    assert kopie["totaal"] == 121.0
    assert kopie["nummer"] == ""
    assert kopie["status"] == "concept"

    regels = db.execute("SELECT * FROM regels WHERE factuur_id=?", (kopie["id"],)).fetchall()
    assert len(regels) == 1
    assert regels[0]["omschrijving"] == "Kraan vervangen"


def test_kopie_krijgt_de_datum_van_vandaag(post, db, maak_factuur):
    origineel = maak_factuur(nummer="2026-001", datum="2020-01-01")
    post(f"/factuur/{origineel}/kopieer")
    kopie = db.execute("SELECT datum FROM facturen WHERE id<>? ORDER BY id DESC", (origineel,)).fetchone()
    assert kopie["datum"] == date.today().isoformat()


def test_kopie_neemt_de_klus_koppeling_niet_mee(post, db, maak_factuur):
    """Die uren staan al op de oorspronkelijke rekening; ze horen niet twee keer
    gefactureerd te raken."""
    origineel = maak_factuur(nummer="2026-001")
    db.execute("UPDATE regels SET klus_id=7 WHERE factuur_id=?", (origineel,))
    db.commit()

    post(f"/factuur/{origineel}/kopieer")
    kopie_id = db.execute("SELECT id FROM facturen WHERE id<>? ORDER BY id DESC", (origineel,)).fetchone()[0]
    assert db.execute("SELECT klus_id FROM regels WHERE factuur_id=?", (kopie_id,)).fetchone()[0] is None
