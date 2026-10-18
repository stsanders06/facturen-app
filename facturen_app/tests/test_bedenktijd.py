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
    facturen.MAIL_STAND.clear()
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


def _laat_de_klok_aflopen(sleutel):
    """De bedenktijd is in de test expres lang. De functie zelf meteen aanroepen."""
    klok = facturen.UITGESTELDE_MAILS.pop(sleutel)
    klok.cancel()
    klok.function()


def test_het_mailscherm_blijft_open_tijdens_de_bedenktijd(post, mailbare_rekening,
                                                         met_bedenktijd):
    """Terug naar de rekeningen pas als de mail echt weg is, niet al bij versturen."""
    antwoord = post(
        f"/factuur/{mailbare_rekening}/verstuur",
        headers={"Referer": f"http://localhost/factuur/{mailbare_rekening}/mail"},
    )
    assert antwoord.status_code == 302
    assert antwoord.headers["Location"].endswith(f"/factuur/{mailbare_rekening}/mail")


def test_de_melding_weet_waar_het_scherm_naartoe_gaat(post, mailbare_rekening,
                                                     met_bedenktijd):
    inhoud = post(
        f"/factuur/{mailbare_rekening}/verstuur",
        headers={"Referer": f"http://localhost/factuur/{mailbare_rekening}/mail"},
        follow_redirects=True,
    ).data.decode()
    assert 'data-na="/"' in inhoud
    assert 'data-stand="/mail/' in inhoud
    assert "location.assign" in inhoud


def test_een_offerte_gaat_terug_naar_de_offertes(post, db, maak_offerte, met_bedenktijd):
    db.execute("UPDATE settings SET smtp_host='smtp.example.com' WHERE id=1")
    db.commit()
    offerte_id = maak_offerte()
    db.execute(
        "UPDATE offertes SET klant_email='info@example.org' WHERE id=?",
        (offerte_id,),
    )
    db.commit()
    inhoud = post(
        f"/offerte/{offerte_id}/verstuur",
        headers={"Referer": f"http://localhost/offerte/{offerte_id}/mail"},
        follow_redirects=True,
    ).data.decode()
    assert 'data-na="/offertes"' in inhoud


def test_stand_wacht_tot_de_mail_gelukt_is(client, post, mailbare_rekening,
                                             met_bedenktijd, monkeypatch):
    monkeypatch.setattr(facturen, "_mail_pdf", lambda *a, **k: (True, ""))
    post(f"/factuur/{mailbare_rekening}/verstuur")
    sleutel = next(iter(facturen.UITGESTELDE_MAILS))
    assert client.get(f"/mail/{sleutel}/stand").get_json()["stand"] == "wacht"
    _laat_de_klok_aflopen(sleutel)
    assert facturen.MAIL_STAND[sleutel] == "gelukt"
    # De uitslag blijft voor de pagina waar je naartoe gaat, niet voor het
    # mailscherm dat dan al dicht is.
    assert facturen.MAILUITSLAGEN
    assert "gemaild" in client.get("/").data.decode()
    assert client.get(f"/mail/{sleutel}/stand").get_json()["stand"] == "gelukt"


def test_een_mislukte_mail_krijgt_stand_fout(client, post, mailbare_rekening,
                                             met_bedenktijd, monkeypatch):
    monkeypatch.setattr(
        facturen, "verstuur_email",
        lambda *a, **k: (False, "De mailserver weigert je gebruikersnaam of wachtwoord."),
    )
    post(f"/factuur/{mailbare_rekening}/verstuur")
    sleutel = next(iter(facturen.UITGESTELDE_MAILS))
    _laat_de_klok_aflopen(sleutel)
    assert client.get(f"/mail/{sleutel}/stand").get_json()["stand"] == "fout"


def test_tegenhouden_zet_de_stand_niet_op_gelukt(client, post, mailbare_rekening,
                                                met_bedenktijd):
    # Eerst de melding met de terugkeer tonen, anders blijft die in de sessie
    # en lijkt het alsof tegenhouden het scherm alsnog sluit.
    post(f"/factuur/{mailbare_rekening}/verstuur", follow_redirects=True)
    sleutel = next(iter(facturen.UITGESTELDE_MAILS))
    inhoud = post(f"/mail/{sleutel}/tegenhouden", follow_redirects=True).data.decode()
    assert "Tegengehouden" in inhoud
    assert 'data-na="/"' not in inhoud
    assert client.get(f"/mail/{sleutel}/stand").get_json()["stand"] == "tegengehouden"


def test_het_scherm_gaat_alleen_dicht_als_de_mail_gelukt_is(post, mailbare_rekening,
                                                           met_bedenktijd):
    """De balk leeg: gelukt gaat weg, een fout of tegenhouden niet."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    html = post(
        f"/factuur/{mailbare_rekening}/verstuur",
        headers={"Referer": f"http://localhost/factuur/{mailbare_rekening}/mail"},
        follow_redirects=True,
    ).data.decode().replace(
        'data-na="/"', 'data-na="http://test.example/terug"', 1,
    )

    def na_balk(stand):
        with sync_playwright() as p:
            browser = p.webkit.launch()
            pagina = browser.new_page()

            def antwoord(route):
                if route.request.url.endswith("/terug"):
                    route.fulfill(
                        status=200, content_type="text/html", body="<p>terug</p>",
                    )
                else:
                    route.fulfill(
                        status=200, content_type="text/html", body=html,
                    )

            pagina.route("**/*", antwoord)
            pagina.goto("http://test.example/mail")
            pagina.evaluate(
                """(stand) => {
                  window.fetch = () => Promise.resolve({
                    ok: true,
                    json: () => Promise.resolve({stand: stand}),
                  });
                }""",
                stand,
            )
            if stand == "gelukt":
                with pagina.expect_navigation(timeout=3000):
                    pagina.locator(".balk").dispatch_event("animationend")
            else:
                pagina.locator(".balk").dispatch_event("animationend")
                pagina.wait_for_timeout(400)
            url = pagina.url
            browser.close()
            return url

    assert na_balk("gelukt").endswith("/terug")
    assert not na_balk("fout").endswith("/terug")
    assert not na_balk("tegengehouden").endswith("/terug")


def test_de_uitslag_komt_op_de_volgende_pagina(client, met_bedenktijd):
    """De klok loopt buiten een verzoek om, dus er is op dat moment geen scherm om
    iets op te zetten."""
    facturen.MAILUITSLAGEN.append(("Rekening 2026-001 gemaild naar jan@example.com.",
                                   "gelukt"))
    inhoud = client.get("/").data.decode()
    assert "gemaild naar jan@example.com" in inhoud
    assert facturen.MAILUITSLAGEN == []
