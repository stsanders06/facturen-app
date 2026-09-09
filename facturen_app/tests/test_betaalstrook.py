"""De betaalstrook onderaan een rekening rekent met wat er al binnen is.

Zonder dat stuurde een herinnering een strook met het volle bedrag, terwijl de
mailtekst ernaast het openstaande bedrag noemde. Een klant die de helft had
betaald maakte dan het hele bedrag nog een keer over.
"""
import pathlib

from conftest import facturen


# De uitpakker staat al in test_pdf.py; die hier hergebruiken scheelt een tweede
# versie die uit elkaar kan gaan lopen.
from test_pdf import tekst_uit_pdf as tekst_van_pdf


def zonder_spaties(tekst):
    """De labels op de PDF staan uitgespatieerd ("T E  B E T A L E N"); voor het
    vergelijken zijn de spaties alleen maar in de weg."""
    return tekst.replace(" ", "").lower()


def test_zonder_betaling_staat_het_hele_bedrag_op_de_strook(db, maak_factuur):
    factuur_id = maak_factuur(status="verzonden", totaal=121.0)
    pad = facturen.maak_pdf(factuur_id)
    tekst = tekst_van_pdf(pad)
    assert "121,00" in tekst
    assert "tebetalen" in zonder_spaties(tekst)


def test_met_een_deelbetaling_staat_het_restbedrag_op_de_strook(db, maak_factuur):
    factuur_id = maak_factuur(status="verzonden", totaal=121.0)
    db.execute("""INSERT INTO betalingen (factuur_id, datum, bedrag, notitie, automatisch)
                  VALUES (?, '2026-08-20', 50.0, '', 0)""", (factuur_id,))
    db.commit()
    tekst = tekst_van_pdf(facturen.maak_pdf(factuur_id))
    assert "71,00" in tekst, tekst
    assert "nogtebetalen" in zonder_spaties(tekst)
    assert "albetaald" in zonder_spaties(tekst)


def test_een_geboekte_betaling_maakt_de_pdf_opnieuw(post, db, maak_factuur):
    """De bewaarde PDF klopt anders niet meer met wat er nog open staat."""
    factuur_id = maak_factuur(status="verzonden", totaal=121.0)
    facturen.maak_pdf(factuur_id)
    post(f"/factuur/{factuur_id}/betalingen",
         {"bedrag": "50", "datum": "2026-08-20"}, follow_redirects=True)
    conn = facturen.get_db()
    rij = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    conn.close()
    pad = pathlib.Path(facturen.PDF_DIR) / facturen.pdf_bestandsnaam(rij)
    assert "71,00" in tekst_van_pdf(pad)


def test_een_teruggedraaide_betaling_ook(post, db, maak_factuur):
    factuur_id = maak_factuur(status="verzonden", totaal=121.0)
    post(f"/factuur/{factuur_id}/betalingen",
         {"bedrag": "50", "datum": "2026-08-20"}, follow_redirects=True)
    betaling_id = db.execute("SELECT id FROM betalingen").fetchone()[0]
    post(f"/betaling/{betaling_id}/verwijder", follow_redirects=True)
    conn = facturen.get_db()
    rij = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    conn.close()
    pad = pathlib.Path(facturen.PDF_DIR) / facturen.pdf_bestandsnaam(rij)
    assert "121,00" in tekst_van_pdf(pad)
