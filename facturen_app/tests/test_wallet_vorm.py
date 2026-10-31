"""De rustige vorm hoort overal hetzelfde te zijn.

De tabbladen op een iPhone van 390 px houden hun naam: «Rekeningen» is daar
ongeveer 60 px, en Meer is alleen een icoon. Op 320 px passen die namen niet
meer, dus daar blijft het icoon. Een knop op de Mac is minstens 32 px hoog;
op een aanraakscherm 44.
"""
from test_smalle_weergave import css_van, regel_met


def test_de_tabbladen_houden_hun_naam_op_390(client):
    css = css_van(client.get("/vandaag").data.decode())
    assert "@media (max-width: 389px)" in css
    assert "@media (max-width: 480px)" not in css
    assert "flex: 0 0 44px" in regel_met(css, ".tabbar .meer")


def test_een_knop_is_hoog_genoeg_voor_een_muis_en_een_vinger(client):
    css = css_van(client.get("/").data.decode())
    assert "min-height: 32px" in regel_met(css, ".btn")
    smal = css[css.index("@media (pointer: coarse), (max-width: 620px)"):]
    assert "min-height: 44px" in regel_met(smal, ".btn")


def test_het_openstaande_bedrag_blijft_wit(client):
    """color: inherit op links won van het wit van de kaart, waardoor het
    bedrag bijna zwart op de donkere kaart stond."""
    from browser import meetbrowser, playwright_verplicht

    sync_playwright = playwright_verplicht()
    html = client.get("/vandaag").data.decode()
    with sync_playwright() as p:
        browser = meetbrowser(p)
        pagina = browser.new_page(color_scheme="light")
        try:
            pagina.set_content(html)
            kleur = pagina.evaluate(
                """() => getComputedStyle(document.querySelector('.vandaag-kaart b')).color"""
            )
        finally:
            browser.close()
    assert kleur == "rgb(255, 255, 255)", kleur


def test_escape_sluit_het_menu_meer(client):
    from browser import meetbrowser, playwright_verplicht

    sync_playwright = playwright_verplicht()
    html = client.get("/vandaag").data.decode()
    with sync_playwright() as p:
        browser = meetbrowser(p)
        pagina = browser.new_page(viewport={"width": 390, "height": 844})
        try:
            pagina.set_content(html)
            pagina.locator("details.meer > summary").click()
            assert pagina.locator("details.meer").get_attribute("open") is not None
            pagina.keyboard.press("Escape")
            assert pagina.locator("details.meer").get_attribute("open") is None
        finally:
            browser.close()


def test_de_actieve_pagina_staat_in_het_menu(client):
    tekst = client.get("/vandaag").data.decode()
    assert 'href="/vandaag" class="aan" aria-current="page"' in tekst


def test_meer_contrast_maakt_het_menu_dicht(client):
    from test_smalle_weergave import css_van
    css = css_van(client.get("/").data.decode())
    deel = css.split("@media (prefers-contrast: more) {", 1)[1]
    deel = deel.split("@media", 1)[0]
    assert "backdrop-filter: none" in deel
    assert "--border: rgba(0, 0, 0, .72)" in deel


def test_een_bewerkte_rekening_biedt_definitief_en_mailen(client, maak_factuur):
    """Die twee horen bij de rekening, niet alleen in het menu van de lijst."""
    factuur_id = maak_factuur(nummer="", status="concept", email="klant@example.org")
    tekst = client.get(f"/factuur/{factuur_id}/bewerk").data.decode()
    assert 'class="fabs"' in tekst
    assert "Definitief maken" in tekst
    assert "Mailen" in tekst
    assert 'action="/factuur/%d/definitief"' % factuur_id in tekst
