"""De keuzelijst onder de regels mag niet onder Annuleren verdwijnen.

De lijst hing los boven de pagina. De knoppen eronder lagen er met een hogere
laag overheen, zodat de onderste bon of dag niet te lezen en niet te tikken was.
"""
import pytest

from test_smalle_weergave import css_van, regel_met


def test_de_keuzelijst_staat_in_de_pagina_en_niet_over_de_knoppen(client):
    for pad in ("/nieuw", "/offertes/nieuw"):
        css = css_van(client.get(pad).data.decode())
        lijst = regel_met(css, ".regelknoppen .haal-op .menu-lijst")
        assert "position: static" in lijst
        assert "overflow-y: auto" in lijst
        assert "scroll-margin-bottom" in lijst
        assert "env(safe-area-inset-bottom" in lijst
        assert "z-index: 40" not in css
        assert "scrollIntoView" in client.get(pad).data.decode()


def _twaalf(post, db):
    """Twaalf bonnen en twaalf dagen, genoeg om de lijst te laten scrollen."""
    post("/klanten/nieuw", {
        "naam": "Voorbeeld Bedrijf BV",
        "email": "info@example.org",
        "adres": "Voorbeeldstraat 1\n1234 AB Voorbeeldstad",
    })
    klant_id = db.execute("SELECT id FROM klanten").fetchone()[0]
    post("/klussen/nieuw", {
        "naam": "Schutting", "uurtarief": "65", "klant_id": str(klant_id),
    })
    klus_id = db.execute("SELECT id FROM klussen").fetchone()[0]
    for nummer in range(1, 13):
        post(f"/klus/{klus_id}/inkoop", {
            "omschrijving": f"Bevestigingsmiddel {nummer}",
            "bedrag": "4.50",
        })
        post(f"/klus/{klus_id}/dag", {
            "datum": f"2026-09-{nummer:02d}",
            "van": "09:00",
            "tot": "12:00",
            "notitie": "plaatsen",
        })
    return klant_id


def _meet(pagina, menu_id):
    pagina.locator(f"#{menu_id} summary").click()
    pagina.locator(".indienen").scroll_into_view_if_needed()
    return pagina.evaluate(
        """(menuId) => {
          const lijst = document.querySelector('#' + menuId + ' .menu-lijst');
          lijst.scrollTop = lijst.scrollHeight;
          const balk = document.querySelector('.indienen');
          const lijstDoos = lijst.getBoundingClientRect();
          const balkDoos = balk.getBoundingClientRect();
          const items = [...lijst.querySelectorAll('.menu-item')]
            .filter((el) => !el.hidden && el.offsetParent !== null);
          const laatste = items[items.length - 1].getBoundingClientRect();
          return {
            positie: getComputedStyle(lijst).position,
            aantal: items.length,
            overlap: lijstDoos.bottom > balkDoos.top + 1,
            laatsteInLijst: laatste.bottom <= lijstDoos.bottom + 2
              && laatste.top >= lijstDoos.top - 2,
            scroll: document.documentElement.scrollWidth
              - document.documentElement.clientWidth,
          };
        }""",
        menu_id,
    )


def test_een_lange_lijst_blijft_boven_de_knoppen(client, post, db):
    """Twaalf bonnen en twaalf dagen, op 390 en op 320."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    klant_id = _twaalf(post, db)
    html = client.get(f"/nieuw?klant={klant_id}").data.decode()
    assert html.count("Bevestigingsmiddel") >= 12
    assert 'id="bon-menu"' in html
    assert 'id="dag-menu"' in html

    with sync_playwright() as p:
        browser = p.webkit.launch()
        pagina = browser.new_page(device_scale_factor=1)
        for breedte in (390, 320):
            pagina.set_viewport_size({"width": breedte, "height": 700})
            pagina.set_content(html)
            for menu_id in ("bon-menu", "dag-menu"):
                gemeten = _meet(pagina, menu_id)
                assert gemeten["positie"] == "static"
                assert gemeten["aantal"] >= 12
                assert gemeten["overlap"] is False
                assert gemeten["laatsteInLijst"] is True
                assert gemeten["scroll"] <= 1
                pagina.locator(f"#{menu_id} summary").click()
        browser.close()
