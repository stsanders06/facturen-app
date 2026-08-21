"""De herinneringsmail: wanneer hij weigert, en wat erin staat."""
from datetime import date, timedelta

import pytest

from conftest import facturen


@pytest.fixture
def verstuurd(monkeypatch, db):
    """Vangt de mail op in plaats van hem echt te versturen."""
    opgevangen = {}

    def nep_mail(s, ontvanger, onderwerp, tekst, pad, bestandsnaam):
        opgevangen.update(ontvanger=ontvanger, onderwerp=onderwerp, tekst=tekst,
                          pad=pad, bestandsnaam=bestandsnaam)
        return True, ""

    monkeypatch.setattr(facturen, "_mail_pdf", nep_mail)
    db.execute("UPDATE settings SET smtp_host='smtp.example.com', naam='Hogedruk Venlo' WHERE id=1")
    db.commit()
    return opgevangen


def dagen_geleden(aantal):
    return (date.today() - timedelta(days=aantal)).isoformat()


def test_herinnering_noemt_hoeveel_dagen_hij_te_laat_is(verstuurd, db, maak_factuur):
    factuur_id = maak_factuur(nummer="2026-001", datum=dagen_geleden(20), status="verzonden")
    gelukt, melding = facturen.herinnering_email(factuur_id)

    assert gelukt
    assert "2026-001" in verstuurd["onderwerp"]
    assert "Herinnering" in verstuurd["onderwerp"]
    assert "6 dagen geleden" in verstuurd["tekst"]   # veertien dagen betaaltermijn
    assert "121,00" in verstuurd["tekst"]


def test_een_dag_te_laat_staat_in_enkelvoud(verstuurd, db, maak_factuur):
    factuur_id = maak_factuur(nummer="2026-001", datum=dagen_geleden(15), status="verzonden")
    facturen.herinnering_email(factuur_id)
    assert "1 dag geleden" in verstuurd["tekst"]


def test_nog_niet_verlopen_noemt_de_vervaldatum(verstuurd, db, maak_factuur):
    factuur_id = maak_factuur(nummer="2026-001", datum=dagen_geleden(2), status="verzonden")
    facturen.herinnering_email(factuur_id)
    assert "staat open tot" in verstuurd["tekst"]
    assert "geleden" not in verstuurd["tekst"]


def test_herinnering_vraagt_alleen_om_wat_er_nog_openstaat(verstuurd, db, post, maak_factuur):
    factuur_id = maak_factuur(nummer="2026-001", datum=dagen_geleden(20),
                              totaal=121.0, status="verzonden")
    post(f"/factuur/{factuur_id}/betalingen", {"datum": "2026-08-20", "bedrag": "21,00"})

    facturen.herinnering_email(factuur_id)
    assert "100,00" in verstuurd["tekst"]
    assert "al € 21,00 ontvangen" in verstuurd["tekst"]


def test_betaalde_rekening_krijgt_geen_herinnering(verstuurd, db, maak_factuur):
    factuur_id = maak_factuur(nummer="2026-001", status="betaald")
    gelukt, melding = facturen.herinnering_email(factuur_id)
    assert not gelukt
    assert "al betaald" in melding


def test_concept_krijgt_geen_herinnering(verstuurd, db, maak_factuur):
    """Een herinnering aan een rekening die de klant nooit heeft gekregen, slaat
    nergens op."""
    factuur_id = maak_factuur(nummer="", status="concept")
    gelukt, melding = facturen.herinnering_email(factuur_id)
    assert not gelukt
    assert "concept" in melding


def test_zonder_mailadres_geen_herinnering(verstuurd, db, maak_factuur):
    factuur_id = maak_factuur(nummer="2026-001", status="verzonden", email="")
    gelukt, melding = facturen.herinnering_email(factuur_id)
    assert not gelukt
    assert "e-mailadres" in melding


def test_zonder_mailserver_geen_herinnering(db, maak_factuur):
    db.execute("UPDATE settings SET smtp_host='' WHERE id=1")
    db.commit()
    factuur_id = maak_factuur(nummer="2026-001", status="verzonden")
    gelukt, melding = facturen.herinnering_email(factuur_id)
    assert not gelukt
    assert "mailserver" in melding


def test_de_rekening_gaat_als_bijlage_mee(verstuurd, db, maak_factuur):
    factuur_id = maak_factuur(nummer="2026-001", datum=dagen_geleden(20), status="verzonden")
    facturen.herinnering_email(factuur_id)
    assert verstuurd["bestandsnaam"] == "2026-001.pdf"


def test_de_knop_op_de_kaart_werkt(verstuurd, db, post, maak_factuur):
    factuur_id = maak_factuur(nummer="2026-001", datum=dagen_geleden(20), status="verzonden")
    antwoord = post(f"/factuur/{factuur_id}/herinnering", follow_redirects=True)
    assert antwoord.status_code == 200
    assert verstuurd["ontvanger"] == "jan@example.com"


def test_herinneringsknop_staat_alleen_op_onbetaalde_rekeningen(db, client, maak_factuur):
    maak_factuur(nummer="2026-001", status="verzonden")
    assert "Herinnering sturen" in client.get("/").data.decode()

    db.execute("UPDATE facturen SET status='betaald'")
    db.commit()
    assert "Herinnering sturen" not in client.get("/").data.decode()


def test_mailen_van_een_concept_geeft_het_zijn_nummer(monkeypatch, db, post, maak_factuur):
    monkeypatch.setattr(facturen, "_mail_pdf", lambda *a, **k: (True, ""))
    db.execute("UPDATE settings SET smtp_host='smtp.example.com' WHERE id=1")
    db.commit()

    factuur_id = maak_factuur(nummer="", status="concept")
    post(f"/factuur/{factuur_id}/verstuur")

    factuur = db.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    assert factuur["nummer"] == f"{date.today().year}-001"
    assert factuur["status"] == "verzonden"


def test_mislukte_mail_laat_het_concept_zonder_nummer(monkeypatch, db, post, maak_factuur):
    """Anders raak je een nummer kwijt aan een rekening die nooit is verstuurd."""
    factuur_id = maak_factuur(nummer="", status="concept")
    post(f"/factuur/{factuur_id}/verstuur")   # geen mailserver ingesteld

    factuur = db.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    assert factuur["nummer"] == ""
    assert factuur["status"] == "concept"
