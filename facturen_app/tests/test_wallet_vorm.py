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
    bedrag bijna zwart op de donkere kaart stond. Licht en donker, en op
    een telefoonbreedte."""
    from browser import meetbrowser, playwright_verplicht

    sync_playwright = playwright_verplicht()
    html = client.get("/vandaag").data.decode()
    with sync_playwright() as p:
        browser = meetbrowser(p)
        try:
            for schema, breedte in (("light", 1280), ("dark", 1280), ("light", 390), ("dark", 390)):
                pagina = browser.new_page(
                    color_scheme=schema,
                    viewport={"width": breedte, "height": 844},
                )
                pagina.set_content(html)
                kleur = pagina.evaluate(
                    """() => getComputedStyle(document.querySelector('.vandaag-kaart b')).color"""
                )
                pagina.close()
                assert kleur == "rgb(255, 255, 255)", (schema, breedte, kleur)
        finally:
            browser.close()


def test_escape_zet_de_focus_terug_op_meer(client):
    """Enter opent Meer, Tab gaat het menu in, Escape moet terug naar de knop."""
    from browser import meetbrowser, playwright_verplicht

    sync_playwright = playwright_verplicht()
    html = client.get("/offertes").data.decode()
    with sync_playwright() as p:
        browser = meetbrowser(p)
        pagina = browser.new_page(viewport={"width": 390, "height": 844})
        try:
            pagina.set_content(html)
            knop = pagina.locator("details.meer > summary")
            knop.focus()
            pagina.keyboard.press("Enter")
            assert pagina.locator("details.meer").get_attribute("open") is not None
            pagina.keyboard.press("Tab")
            binnen = pagina.evaluate(
                "() => !!document.activeElement.closest('.meer-vel')"
            )
            assert binnen
            pagina.keyboard.press("Escape")
            assert pagina.locator("details.meer").get_attribute("open") is None
            zelfde = pagina.evaluate(
                "() => document.activeElement === document.querySelector('details.meer > summary')"
            )
            assert zelfde
        finally:
            browser.close()


def test_een_nieuwe_regel_krijgt_een_eigen_id(client, maak_factuur):
    """Een kloon van de eerste regel hield soort-1 en aantal-1, zodat elk
    label naar de eerste regel wees."""
    from browser import meetbrowser, playwright_verplicht

    sync_playwright = playwright_verplicht()
    factuur_id = maak_factuur()
    with sync_playwright() as p:
        browser = meetbrowser(p)
        try:
            for pad in ("/nieuw", f"/factuur/{factuur_id}/bewerk"):
                pagina = browser.new_page()
                pagina.set_content(client.get(pad).data.decode())
                pagina.evaluate(
                    "() => { const d = document.querySelector('details.wijzig'); if (d) d.open = true; }"
                )
                pagina.locator("#regel-erbij").click()
                pagina.locator("#regel-erbij").click()
                ids = pagina.evaluate(
                    """() => [...document.querySelectorAll('#regels .regel')].map(rij => {
                      const sel = rij.querySelector('select.soort');
                      const aantal = rij.querySelector('[name="aantal"]');
                      const oms = rij.querySelector('[name="omschrijving"]');
                      return {
                        sel: sel.id,
                        aantal: aantal.id,
                        oms: oms.id,
                        selLabel: !!rij.querySelector('label[for="' + sel.id + '"]'),
                        aantalLabel: !!rij.querySelector('label[for="' + aantal.id + '"]'),
                        omsLabel: !!rij.querySelector('label[for="' + oms.id + '"]'),
                      };
                    })"""
                )
                pagina.close()
                assert len(ids) == 3, pad
                assert len({rij["sel"] for rij in ids}) == 3, ids
                assert len({rij["aantal"] for rij in ids}) == 3, ids
                assert len({rij["oms"] for rij in ids}) == 3, ids
                assert all(rij["selLabel"] and rij["aantalLabel"] and rij["omsLabel"] for rij in ids)
        finally:
            browser.close()
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
