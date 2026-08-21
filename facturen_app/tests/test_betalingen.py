"""Deelbetalingen, aanbetalingen en de knop Betaald."""
from datetime import date

from conftest import facturen


def betaald(db, factuur_id):
    return db.execute(
        "SELECT COALESCE(SUM(bedrag), 0) FROM betalingen WHERE factuur_id=?", (factuur_id,)
    ).fetchone()[0]


def status(db, factuur_id):
    return db.execute("SELECT status FROM facturen WHERE id=?", (factuur_id,)).fetchone()[0]


def test_betaling_boeken(post, db, maak_factuur):
    factuur_id = maak_factuur(totaal=121.0, status="verzonden")
    post(f"/factuur/{factuur_id}/betalingen", {"datum": "2026-08-20", "bedrag": "50,00"})
    assert betaald(db, factuur_id) == 50.0
    assert status(db, factuur_id) == "verzonden"   # nog niet alles binnen


def test_bedrag_mag_met_een_komma(post, db, maak_factuur):
    factuur_id = maak_factuur(totaal=121.0, status="verzonden")
    post(f"/factuur/{factuur_id}/betalingen", {"datum": "2026-08-20", "bedrag": "60,50"})
    assert betaald(db, factuur_id) == 60.5


def test_laatste_betaling_zet_de_rekening_op_betaald(post, db, maak_factuur):
    factuur_id = maak_factuur(totaal=121.0, status="verzonden")
    post(f"/factuur/{factuur_id}/betalingen", {"datum": "2026-08-20", "bedrag": "60,50"})
    post(f"/factuur/{factuur_id}/betalingen", {"datum": "2026-08-25", "bedrag": "60,50"})
    assert status(db, factuur_id) == "betaald"


def test_centen_afronding_houdt_de_rekening_niet_open(post, db, maak_factuur):
    """Drie keer een derde komt door het afronden net niet op het totaal uit."""
    factuur_id = maak_factuur(totaal=100.0, status="verzonden")
    for _ in range(3):
        post(f"/factuur/{factuur_id}/betalingen", {"datum": "2026-08-20", "bedrag": "33,33"})
    post(f"/factuur/{factuur_id}/betalingen", {"datum": "2026-08-21", "bedrag": "0,01"})
    assert status(db, factuur_id) == "betaald"


def test_betaling_verwijderen_zet_de_rekening_weer_open(post, db, maak_factuur):
    factuur_id = maak_factuur(totaal=121.0, status="verzonden")
    post(f"/factuur/{factuur_id}/betalingen", {"datum": "2026-08-20", "bedrag": "121,00"})
    assert status(db, factuur_id) == "betaald"

    betaling_id = db.execute("SELECT id FROM betalingen").fetchone()[0]
    post(f"/betaling/{betaling_id}/verwijder")
    assert status(db, factuur_id) == "verzonden"
    assert betaald(db, factuur_id) == 0


def test_nul_of_negatief_wordt_geweigerd(post, db, maak_factuur):
    factuur_id = maak_factuur(totaal=121.0)
    post(f"/factuur/{factuur_id}/betalingen", {"datum": "2026-08-20", "bedrag": "0"})
    post(f"/factuur/{factuur_id}/betalingen", {"datum": "2026-08-20", "bedrag": ""})
    assert db.execute("SELECT COUNT(*) FROM betalingen").fetchone()[0] == 0


def test_knop_betaald_boekt_wat_er_nog_openstond(post, db, maak_factuur):
    factuur_id = maak_factuur(totaal=121.0, status="verzonden")
    post(f"/factuur/{factuur_id}/betalingen", {"datum": "2026-08-20", "bedrag": "21,00"})
    post(f"/factuur/{factuur_id}/betaald")

    assert status(db, factuur_id) == "betaald"
    assert betaald(db, factuur_id) == 121.0


def test_toch_niet_betaald_laat_je_eigen_deelbetaling_staan(post, db, maak_factuur):
    """Alleen wat de app zelf boekte gaat weg; een aanbetaling die je hebt ingevoerd
    is een feit en hoort niet stilletjes te verdwijnen."""
    factuur_id = maak_factuur(totaal=121.0, status="verzonden")
    post(f"/factuur/{factuur_id}/betalingen", {"datum": "2026-08-20", "bedrag": "21,00"})
    post(f"/factuur/{factuur_id}/betaald")
    post(f"/factuur/{factuur_id}/niet-betaald")

    assert status(db, factuur_id) == "verzonden"
    assert betaald(db, factuur_id) == 21.0


def test_deels_betaalde_rekening_krijgt_een_chip(post, db, client, maak_factuur):
    factuur_id = maak_factuur(totaal=121.0, status="verzonden")
    post(f"/factuur/{factuur_id}/betalingen", {"datum": "2026-08-20", "bedrag": "21,00"})
    assert "van 121,00 binnen" in client.get("/").data.decode()


def test_openstaand_bedrag_trekt_deelbetalingen_af(post, db, client, maak_factuur):
    factuur_id = maak_factuur(totaal=100.0, status="verzonden")
    post(f"/factuur/{factuur_id}/betalingen", {"datum": "2026-08-20", "bedrag": "40,00"})
    inhoud = client.get("/").data.decode()
    assert "60,00" in inhoud


def test_betalingenpagina_toont_de_balans(post, db, client, maak_factuur):
    factuur_id = maak_factuur(nummer="2026-001", totaal=100.0, status="verzonden")
    post(f"/factuur/{factuur_id}/betalingen", {"datum": "2026-08-20", "bedrag": "40,00",
                                               "notitie": "Aanbetaling"})
    inhoud = client.get(f"/factuur/{factuur_id}/betalingen").data.decode()
    assert "Aanbetaling" in inhoud
    assert "40,00" in inhoud
    assert "60,00" in inhoud


def test_rekening_verwijderen_haalt_de_betalingen_mee(post, db, maak_factuur):
    factuur_id = maak_factuur(totaal=100.0)
    post(f"/factuur/{factuur_id}/betalingen", {"datum": "2026-08-20", "bedrag": "40,00"})
    post(f"/factuur/{factuur_id}/verwijder")
    assert db.execute("SELECT COUNT(*) FROM betalingen").fetchone()[0] == 0


def test_aanbetaling_maakt_een_rekening_voor_een_deel_van_de_offerte(post, db, maak_offerte):
    offerte_id = maak_offerte(totaal=1000.0, status="geaccepteerd")
    post(f"/offerte/{offerte_id}/aanbetaling", {"percentage": "30"})

    factuur = db.execute("SELECT * FROM facturen").fetchone()
    assert factuur["totaal"] == 300.0
    assert factuur["nummer"] == ""
    assert factuur["klant_naam"] == "Jan Jansen"

    regel = db.execute("SELECT * FROM regels WHERE factuur_id=?", (factuur["id"],)).fetchone()
    assert "30%" in regel["omschrijving"]


def test_aanbetaling_laat_de_offerte_gewoon_staan(post, db, maak_offerte):
    """Anders kun je de rest later niet meer factureren."""
    offerte_id = maak_offerte(totaal=1000.0, status="geaccepteerd")
    post(f"/offerte/{offerte_id}/aanbetaling", {"percentage": "30"})
    offerte = db.execute("SELECT * FROM offertes WHERE id=?", (offerte_id,)).fetchone()
    assert offerte["factuur_id"] is None
    assert offerte["totaal"] == 1000.0


def test_aanbetaling_met_eigen_omschrijving(post, db, maak_offerte):
    offerte_id = maak_offerte(totaal=1000.0)
    post(f"/offerte/{offerte_id}/aanbetaling",
         {"percentage": "50", "omschrijving": "Voorschot materiaal"})
    regel = db.execute("SELECT omschrijving FROM regels").fetchone()[0]
    assert regel == "Voorschot materiaal"


def test_onmogelijk_percentage_wordt_geweigerd(post, db, maak_offerte):
    offerte_id = maak_offerte(totaal=1000.0)
    for waarde in ["0", "-10", "150", "geen idee"]:
        post(f"/offerte/{offerte_id}/aanbetaling", {"percentage": waarde})
    assert db.execute("SELECT COUNT(*) FROM facturen").fetchone()[0] == 0


def test_aanbetalingspagina_laadt(client, db, maak_offerte):
    offerte_id = maak_offerte(totaal=1000.0)
    assert client.get(f"/offerte/{offerte_id}/aanbetaling").status_code == 200
