"""Verwijderen is niet meer definitief: er komt een knop bij om het terug te halen.

Wat weggaat wordt eerst bewaard, compleet met alles wat eraan hing. Een rekening
zonder haar regels of een klus zonder haar uren terugzetten is immers niets waard.
"""
from datetime import datetime, timedelta

from conftest import facturen


def prullenbak_id(db):
    return db.execute("SELECT id FROM prullenbak ORDER BY id DESC").fetchone()[0]


def test_de_melding_na_verwijderen_heeft_een_knop(post, maak_factuur):
    factuur_id = maak_factuur()
    inhoud = post(f"/factuur/{factuur_id}/verwijder", follow_redirects=True).data.decode()
    assert "Ongedaan maken" in inhoud


def test_een_rekening_komt_compleet_terug(post, db, maak_factuur):
    """Met haar regels erbij: een lege rekening terugzetten helpt niemand."""
    factuur_id = maak_factuur(totaal=121.0)
    post(f"/factuur/{factuur_id}/verwijder")
    assert db.execute("SELECT COUNT(*) FROM facturen").fetchone()[0] == 0

    post(f"/prullenbak/{prullenbak_id(db)}/terug")

    factuur = db.execute("SELECT * FROM facturen").fetchone()
    assert factuur["id"] == factuur_id
    assert factuur["totaal"] == 121.0
    regels = db.execute("SELECT * FROM regels WHERE factuur_id=?", (factuur_id,)).fetchall()
    assert len(regels) == 1
    assert regels[0]["omschrijving"] == "Kraan vervangen"


def test_een_teruggehaalde_rekening_houdt_haar_betalingen(post, db, maak_factuur):
    factuur_id = maak_factuur(totaal=100.0, status="verzonden")
    post(f"/factuur/{factuur_id}/betalingen", {"datum": "2026-08-14", "bedrag": "40"})
    post(f"/factuur/{factuur_id}/verwijder")
    post(f"/prullenbak/{prullenbak_id(db)}/terug")

    betalingen = db.execute("SELECT * FROM betalingen WHERE factuur_id=?",
                            (factuur_id,)).fetchall()
    assert len(betalingen) == 1
    assert betalingen[0]["bedrag"] == 40.0


def test_een_klant_terughalen_koppelt_zijn_rekeningen_weer(post, db, maak_factuur):
    """De rekening blijft staan met de klantnaam zoals hij gedrukt is, maar de
    koppeling gaat los. Komt de klant terug met zijn oude id, dan klopt hij weer."""
    klant_id = db.execute("INSERT INTO klanten (naam) VALUES ('Jan Jansen')").lastrowid
    factuur_id = maak_factuur()
    db.execute("UPDATE facturen SET klant_id=? WHERE id=?", (klant_id, factuur_id))
    db.commit()

    post(f"/klant/{klant_id}/verwijder")
    assert db.execute("SELECT klant_id FROM facturen WHERE id=?",
                      (factuur_id,)).fetchone()[0] is None

    post(f"/prullenbak/{prullenbak_id(db)}/terug")
    terug = db.execute("SELECT * FROM klanten").fetchone()
    assert terug["id"] == klant_id
    assert terug["naam"] == "Jan Jansen"


def test_een_klus_komt_met_zijn_uren_terug(post, db):
    klus_id = db.execute(
        """INSERT INTO klussen (naam, uurtarief, gestart)
           VALUES ('Vloer reinigen', 47.5, '2026-08-10')""").lastrowid
    db.execute("""INSERT INTO uren (klus_id, datum, van, tot)
                  VALUES (?, '2026-08-10', '08:00', '16:00')""", (klus_id,))
    db.commit()

    post(f"/klus/{klus_id}/verwijder")
    assert db.execute("SELECT COUNT(*) FROM uren").fetchone()[0] == 0

    post(f"/prullenbak/{prullenbak_id(db)}/terug")
    assert db.execute("SELECT COUNT(*) FROM uren WHERE klus_id=?",
                      (klus_id,)).fetchone()[0] == 1


def test_een_offerte_komt_met_haar_regels_terug(post, db, maak_offerte):
    offerte_id = maak_offerte()
    post(f"/offerte/{offerte_id}/verwijder")
    post(f"/prullenbak/{prullenbak_id(db)}/terug")

    assert db.execute("SELECT COUNT(*) FROM offertes WHERE id=?",
                      (offerte_id,)).fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM offerte_regels WHERE offerte_id=?",
                      (offerte_id,)).fetchone()[0] == 1


def test_een_betaling_terughalen_zet_de_rekening_weer_op_betaald(post, db, maak_factuur):
    factuur_id = maak_factuur(totaal=100.0, status="verzonden")
    post(f"/factuur/{factuur_id}/betalingen", {"datum": "2026-08-14", "bedrag": "100"})
    betaling_id = db.execute("SELECT id FROM betalingen").fetchone()[0]

    post(f"/betaling/{betaling_id}/verwijder")
    assert db.execute("SELECT status FROM facturen WHERE id=?",
                      (factuur_id,)).fetchone()[0] != "betaald"

    post(f"/prullenbak/{prullenbak_id(db)}/terug")
    assert db.execute("SELECT COUNT(*) FROM betalingen").fetchone()[0] == 1


def test_terughalen_kan_maar_een_keer(post, db, maak_factuur):
    factuur_id = maak_factuur()
    post(f"/factuur/{factuur_id}/verwijder")
    weg_id = prullenbak_id(db)
    post(f"/prullenbak/{weg_id}/terug")

    antwoord = post(f"/prullenbak/{weg_id}/terug", follow_redirects=True)
    assert "niet meer terug te halen" in antwoord.data.decode()


def test_wat_te_lang_in_de_prullenbak_staat_gaat_er_echt_uit(post, db, maak_factuur):
    post(f"/factuur/{maak_factuur()}/verwijder")
    lang_geleden = (datetime.now()
                    - timedelta(days=facturen.PRULLENBAK_DAGEN + 1)).isoformat()
    db.execute("UPDATE prullenbak SET wanneer=?", (lang_geleden,))
    db.commit()

    # De volgende verwijdering ruimt op wat te oud is geworden.
    post(f"/factuur/{maak_factuur(nummer='2026-002')}/verwijder")
    assert db.execute("SELECT COUNT(*) FROM prullenbak").fetchone()[0] == 1
