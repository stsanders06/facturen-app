"""De vervaldatum hoort bij de rekening; je kunt hem uitzetten net als offerte-geldigheid."""
from datetime import date, timedelta

import pytest

from conftest import facturen


def dagen_geleden(aantal):
    return (date.today() - timedelta(days=aantal)).isoformat()


def regels(**extra):
    gegevens = {
        "klant_naam": "Jan Jansen",
        "datum": "2026-08-14",
        "omschrijving": "Kraan vervangen",
        "type": "arbeid_uur",
        "aantal": "2",
        "prijs": "60,50",
        "betaalmethode": "bank",
    }
    gegevens.update(extra)
    return gegevens


def test_standaard_vervalt_is_veertien_dagen():
    assert facturen.standaard_vervalt("2026-08-14") == "2026-08-28"


def test_nieuwe_rekening_met_vinkje_bewaart_vervaldatum(post, db):
    post("/nieuw", regels(betaaltermijn="ja", vervalt_op="2026-08-28"))
    rij = db.execute("SELECT vervalt_op FROM facturen").fetchone()
    assert rij["vervalt_op"] == "2026-08-28"


def test_nieuwe_rekening_zonder_datum_krijgt_standaardtermijn(post, db):
    post("/nieuw", regels(betaaltermijn="ja", datum="2026-08-14"))
    rij = db.execute("SELECT vervalt_op FROM facturen").fetchone()
    assert rij["vervalt_op"] == "2026-08-28"


def test_zonder_vinkje_geen_vervaldatum_en_geen_te_laat(post, db, client):
    post("/nieuw", regels(datum=dagen_geleden(40)))  # geen betaaltermijn=ja
    rij = db.execute("SELECT id, vervalt_op, status FROM facturen").fetchone()
    assert rij["vervalt_op"] == ""
    db.execute("UPDATE facturen SET status='verzonden' WHERE id=?", (rij["id"],))
    db.commit()
    pagina = client.get("/").data.decode()
    assert "Te laat" not in pagina
    assert " · vóór " not in pagina


def test_bewerken_kan_termijn_zetten_en_wissen(post, db, maak_factuur):
    factuur_id = maak_factuur(vervalt_op="")
    post(f"/factuur/{factuur_id}/bewerk", regels(
        betaaltermijn="ja", vervalt_op="2026-09-01", datum="2026-08-14",
    ))
    assert db.execute("SELECT vervalt_op FROM facturen WHERE id=?",
                      (factuur_id,)).fetchone()[0] == "2026-09-01"

    post(f"/factuur/{factuur_id}/bewerk", regels(datum="2026-08-14"))
    assert db.execute("SELECT vervalt_op FROM facturen WHERE id=?",
                      (factuur_id,)).fetchone()[0] == ""


def test_te_laat_alleen_met_vervaldatum_in_het_verleden(db, client, maak_factuur):
    maak_factuur(nummer="2026-001", datum=dagen_geleden(20), status="verzonden",
                 vervalt_op=dagen_geleden(6))
    assert "Te laat" in client.get("/").data.decode()

    db.execute("DELETE FROM regels"); db.execute("DELETE FROM facturen"); db.commit()
    maak_factuur(nummer="2026-002", datum=dagen_geleden(20), status="verzonden",
                 vervalt_op="")
    assert "Te laat" not in client.get("/").data.decode()


def test_nieuw_formulier_heeft_vervaldatum_en_vinkje(client):
    inhoud = client.get("/nieuw").data.decode()
    assert 'name="vervalt_op"' in inhoud
    assert 'name="betaaltermijn"' in inhoud
    assert "Op de rekening zetten tot wanneer hij betaald moet zijn" in inhoud


def test_instellingen_heeft_geen_betaaltermijn_meer(client, post, db):
    inhoud = client.get("/instellingen").data.decode()
    assert "betaaltermijn_dagen" not in inhoud
    assert "Betaaltermijn" not in inhoud

    db.execute("UPDATE settings SET betaaltermijn_dagen=14 WHERE id=1")
    db.commit()
    post("/instellingen", {"naam": "Stone", "betaaltermijn_dagen": "30"})
    rij = db.execute("SELECT betaaltermijn_dagen, naam FROM settings WHERE id=1").fetchone()
    assert rij["naam"] == "Stone"
    assert rij["betaaltermijn_dagen"] == 14


def test_herinnering_met_termijn_noemt_dagen_te_laat(monkeypatch, db, maak_factuur):
    opgevangen = {}

    def nep_mail(s, ontvanger, onderwerp, tekst, pad, bestandsnaam):
        opgevangen.update(tekst=tekst)
        return True, ""

    monkeypatch.setattr(facturen, "_mail_pdf", nep_mail)
    db.execute("UPDATE settings SET smtp_host='smtp.example.com', naam='Hogedruk Venlo' WHERE id=1")
    db.commit()

    factuur_id = maak_factuur(
        nummer="2026-001", datum=dagen_geleden(20), status="verzonden",
        vervalt_op=dagen_geleden(6),
    )
    gelukt, _ = facturen.herinnering_email(factuur_id)
    assert gelukt
    assert "6 dagen geleden" in opgevangen["tekst"]


def test_herinnering_zonder_termijn_zonder_te_laat_taal(monkeypatch, db, maak_factuur):
    opgevangen = {}

    def nep_mail(s, ontvanger, onderwerp, tekst, pad, bestandsnaam):
        opgevangen.update(tekst=tekst)
        return True, ""

    monkeypatch.setattr(facturen, "_mail_pdf", nep_mail)
    db.execute("UPDATE settings SET smtp_host='smtp.example.com', naam='Hogedruk Venlo' WHERE id=1")
    db.commit()

    factuur_id = maak_factuur(
        nummer="2026-001", datum=dagen_geleden(40), status="verzonden", vervalt_op="",
    )
    gelukt, _ = facturen.herinnering_email(factuur_id)
    assert gelukt
    assert "geleden" not in opgevangen["tekst"]
    assert "staat open tot" not in opgevangen["tekst"]
    assert "121,00" in opgevangen["tekst"]


def test_migratie_vult_bestaande_rekeningen_bij(db):
    """Een oude database zonder kolom krijgt datum+14, één keer."""
    try:
        db.execute("ALTER TABLE facturen DROP COLUMN vervalt_op")
        db.commit()
    except Exception:
        pytest.skip("SQLite kan hier geen kolom laten vallen")

    db.execute(
        """INSERT INTO facturen (nummer, datum, klant_naam, status, totaal)
           VALUES ('2026-001', '2026-08-14', 'Jan', 'verzonden', 100)"""
    )
    db.commit()
    assert "vervalt_op" not in {r["name"] for r in db.execute("PRAGMA table_info(facturen)")}

    facturen.init_db()
    rij = db.execute("SELECT vervalt_op FROM facturen WHERE nummer='2026-001'").fetchone()
    assert rij["vervalt_op"] == "2026-08-28"
