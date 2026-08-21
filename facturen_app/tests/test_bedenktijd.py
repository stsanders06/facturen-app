"""Een mail gaat pas na een paar tellen weg, en is tot die tijd tegen te houden.

De rest van de tests zet de bedenktijd op nul, zodat een mail meteen weggaat en de
uitslag in hetzelfde antwoord staat. Hier draaien we hem juist op, want het gaat om
het uitstel zelf.
"""
import pytest

from conftest import facturen


@pytest.fixture
def met_bedenktijd(app):
    """Een ruime bedenktijd, zodat de mail zeker niet weggaat tijdens de test."""
    facturen.MAIL_BEDENKTIJD = 30
    yield
    for klok in list(facturen.UITGESTELDE_MAILS.values()):
        klok.cancel()
    facturen.UITGESTELDE_MAILS.clear()
    facturen.MAILUITSLAGEN.clear()
    facturen.MAIL_BEDENKTIJD = 0


@pytest.fixture
def mailbare_rekening(db, maak_factuur):
    """Een rekening die te mailen is: met adres en met een mailserver ingesteld."""
    db.execute("UPDATE settings SET smtp_host='smtp.example.com', smtp_port=587 WHERE id=1")
    db.commit()
    return maak_factuur(nummer="", status="concept", email="jan@example.com")


def test_een_mail_gaat_niet_meteen_weg(post, mailbare_rekening, met_bedenktijd):
    antwoord = post(f"/factuur/{mailbare_rekening}/verstuur", follow_redirects=True)
    assert "wordt verstuurd" in antwoord.data.decode()
    assert len(facturen.UITGESTELDE_MAILS) == 1


def test_een_concept_houdt_zijn_nummer_nog_even_tegoed(post, db, mailbare_rekening,
                                                       met_bedenktijd):
    """Het nummer hoort bij een verstuurde rekening. Zolang de mail nog kan worden
    tegengehouden, is er niets verstuurd en blijft het dus een concept."""
    post(f"/factuur/{mailbare_rekening}/verstuur")
    rij = db.execute("SELECT nummer, status FROM facturen WHERE id=?",
                     (mailbare_rekening,)).fetchone()
    assert rij["nummer"] == ""
    assert rij["status"] == "concept"


def test_de_melding_heeft_een_knop_met_een_balkje(post, mailbare_rekening, met_bedenktijd):
    inhoud = post(f"/factuur/{mailbare_rekening}/verstuur",
                  follow_redirects=True).data.decode()
    assert "Toch niet" in inhoud
    assert 'class="melding bezig"' in inhoud
    assert f"animation-duration: {facturen.MAIL_BEDENKTIJD}s" in inhoud


def test_tegenhouden_verstuurt_hem_niet(post, db, mailbare_rekening, met_bedenktijd):
    post(f"/factuur/{mailbare_rekening}/verstuur")
    sleutel = next(iter(facturen.UITGESTELDE_MAILS))

    antwoord = post(f"/mail/{sleutel}/tegenhouden", follow_redirects=True)
    assert "Tegengehouden" in antwoord.data.decode()
    assert facturen.UITGESTELDE_MAILS == {}
    assert db.execute("SELECT status FROM facturen WHERE id=?",
                      (mailbare_rekening,)).fetchone()[0] == "concept"


def test_twee_keer_tegenhouden_zegt_dat_hij_al_weg_is(post, mailbare_rekening,
                                                      met_bedenktijd):
    post(f"/factuur/{mailbare_rekening}/verstuur")
    sleutel = next(iter(facturen.UITGESTELDE_MAILS))
    post(f"/mail/{sleutel}/tegenhouden")

    antwoord = post(f"/mail/{sleutel}/tegenhouden", follow_redirects=True)
    assert "al de deur uit" in antwoord.data.decode()


def test_zonder_mailadres_krijg_je_meteen_te_horen_dat_het_niet_kan(post, db, maak_factuur,
                                                                   met_bedenktijd):
    """Dat hoort niet pas na de bedenktijd te blijken."""
    db.execute("UPDATE settings SET smtp_host='smtp.example.com' WHERE id=1")
    db.commit()
    factuur_id = maak_factuur(email="")

    antwoord = post(f"/factuur/{factuur_id}/verstuur", follow_redirects=True)
    inhoud = antwoord.data.decode()
    assert "geen e-mailadres" in inhoud
    assert 'class="melding fout"' in inhoud
    assert facturen.UITGESTELDE_MAILS == {}


def test_zonder_mailserver_ook(post, maak_factuur, met_bedenktijd):
    factuur_id = maak_factuur(email="jan@example.com")
    antwoord = post(f"/factuur/{factuur_id}/verstuur", follow_redirects=True)
    assert "geen mailserver ingesteld" in antwoord.data.decode()
    assert facturen.UITGESTELDE_MAILS == {}


def test_de_uitslag_komt_op_de_volgende_pagina(client, met_bedenktijd):
    """De klok loopt buiten een verzoek om, dus er is op dat moment geen scherm om
    iets op te zetten."""
    facturen.MAILUITSLAGEN.append(("Rekening 2026-001 gemaild naar jan@example.com.",
                                   "gelukt"))
    inhoud = client.get("/").data.decode()
    assert "gemaild naar jan@example.com" in inhoud
    assert facturen.MAILUITSLAGEN == []
