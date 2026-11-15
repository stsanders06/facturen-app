"""Marges en witruimte, gemeten in een echte browser op telefoonbreedte.

Gevonden bij het doorlopen van de app in 2.7.1: tekst die tegen de rand van
een vlak plakte, velden zonder ruimte ertussen, een rand in een rand, en een
bedrag dat na het euroteken afbrak.
"""
import pytest

from browser import meetbrowser, playwright_verplicht


@pytest.fixture
def meet():
    """Zet een gerenderde pagina in de browser en geeft evaluate terug."""
    sync_playwright = playwright_verplicht()
    with sync_playwright() as p:
        browser = meetbrowser(p)
        pagina = browser.new_page()

        def _meet(html, breedte, js):
            pagina.set_viewport_size({"width": breedte, "height": 800})
            pagina.set_content(html)
            return pagina.evaluate(js)

        yield _meet, pagina
        browser.close()


def test_een_open_regel_heeft_binnenmarge(client, meet):
    """Open kreeg het kopje een witte achtergrond met padding 0: "Nieuwe regel"
    en "1×" plakten tegen de rand."""
    _meet, pagina = meet
    for pad in ("/nieuw", "/offertes/nieuw"):
        html = client.get(pad).data.decode()
        for breedte in (390, 320):
            _meet(html, breedte, "() => 0")
            pagina.locator(".regel-samenvatting").first.click()
            ruimte = pagina.evaluate("""() => {
              const kop = document.querySelector('.regel-samenvatting');
              const vlak = kop.getBoundingClientRect();
              const tekst = kop.querySelector('.regel-tekst').getBoundingClientRect();
              const qty = kop.querySelector('.regel-qty').getBoundingClientRect();
              return {
                open: kop.closest('.regel').dataset.open,
                links: tekst.left - vlak.left,
                rechts: vlak.right - qty.right,
                boven: tekst.top - vlak.top,
              };
            }""")
            assert ruimte["open"] == "true"
            assert ruimte["links"] >= 8, (pad, breedte, ruimte)
            assert ruimte["rechts"] >= 8, (pad, breedte, ruimte)
            assert ruimte["boven"] >= 6, (pad, breedte, ruimte)


def test_de_klantvelden_hebben_ruimte_ertussen(client, meet):
    """Klantnaam, Adres en E-mailadres zaten in een extra div binnen .fields en
    plakten op elkaar."""
    _meet, _ = meet
    for pad in ("/nieuw", "/offertes/nieuw"):
        tussen = _meet(client.get(pad).data.decode(), 390, """() => {
          const v = [...document.querySelectorAll('#klant-gegevens > .field')];
          return v.slice(1).map((el, i) =>
            el.getBoundingClientRect().top - v[i].getBoundingClientRect().bottom);
        }""")
        assert len(tussen) == 2
        assert all(t >= 12 for t in tussen), (pad, tussen)


def test_de_knoppen_onderaan_staan_niet_in_een_eigen_kaart(client, meet):
    """Een balk met een eigen rand in een kaart gaf een rand in een rand."""
    _meet, _ = meet
    for pad in ("/nieuw", "/offertes/nieuw"):
        balk = _meet(client.get(pad).data.decode(), 390, """() => {
          const b = document.querySelector('.indienen');
          const s = getComputedStyle(b);
          return {inKaart: !!b.closest('.card'), positie: s.position,
                  rand: s.borderTopWidth};
        }""")
        assert balk == {"inKaart": False, "positie": "static", "rand": "1px"}, pad


def test_een_knoppenbalk_in_een_kaart_heeft_geen_eigen_rand(client, meet):
    _meet, _ = meet
    html = client.get("/klanten/import").data.decode()
    balk = _meet(html, 390, """() => {
      const b = document.querySelector('.card .indienen');
      const s = getComputedStyle(b);
      return {rand: s.borderTopWidth, positie: s.position, vulling: s.paddingLeft};
    }""")
    assert balk == {"rand": "0px", "positie": "static", "vulling": "0px"}


def test_een_betaling_breekt_niet_na_het_euroteken(client, post, maak_factuur, meet):
    _meet, _ = meet
    factuur_id = maak_factuur(totaal=677.0, status="verzonden")
    post(f"/factuur/{factuur_id}/betalingen", {
        "datum": "2026-11-06", "bedrag": "292,00", "wijze": "bank",
        "notitie": "Deelbetaling per bank, met een wat langere notitie erbij",
    })
    html = client.get(f"/factuur/{factuur_id}/betalingen").data.decode()
    for breedte in (390, 320):
        regels = _meet(html, breedte, """() => {
          const r = document.createRange();
          r.selectNodeContents(document.querySelector('.boeking .bedrag'));
          return new Set([...r.getClientRects()].map(x => Math.round(x.top))).size;
        }""")
        assert regels == 1, breedte


def test_de_uitleg_bij_een_aanbetaling_plakt_niet_tegen_het_volgende_veld(client, maak_offerte, meet):
    _meet, _ = meet
    offerte_id = maak_offerte(totaal=1450.0, status="verzonden")
    tussen = _meet(client.get(f"/offerte/{offerte_id}/aanbetaling").data.decode(), 390, """() => {
      const uitleg = document.getElementById('uitkomst').getBoundingClientRect();
      const label = document.querySelector('label[for=omschrijving]').getBoundingClientRect();
      return label.top - uitleg.bottom;
    }""")
    assert tussen >= 12


def test_pdf_valt_op_de_klantpagina_niet_los_op_een_eigen_regel(client, post, db, maak_factuur, meet):
    """Met 14px gap naast de opvulling van elke tekstknop paste de rij niet."""
    _meet, _ = meet
    post("/klanten/nieuw", {"naam": "Voorbeeld Bedrijf BV"})
    klant_id = db.execute("SELECT id FROM klanten").fetchone()[0]
    factuur_id = maak_factuur(totaal=178.95, status="verzonden")
    db.execute("UPDATE facturen SET klant_id=? WHERE id=?", (klant_id, factuur_id))
    db.commit()
    html = client.get(f"/klant/{klant_id}").data.decode()
    hoogtes = _meet(html, 390, """() => [...document.querySelectorAll('.rekening .rechts > *')]
      .map(k => k.getBoundingClientRect().top)""")
    # Bedrag en knoppen zijn niet even hoog; een nieuwe regel is 44px lager.
    assert len(hoogtes) == 4
    assert max(hoogtes) - min(hoogtes) < 20, hoogtes


def test_het_bedrag_in_een_tegel_zakt_in_plaats_van_tegen_de_rand(client):
    """Op 320px stond "€ 1.273,75" anders 1px van de rand van de kaart."""
    from test_smalle_weergave import css_van, regel_met
    tegel = regel_met(css_van(client.get("/klussen").data.decode()), ".overzicht div")
    assert "flex-wrap: wrap" in tegel
