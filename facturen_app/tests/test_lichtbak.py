"""De lichtbak opent een foto overal, niet alleen op het mailscherm.

Een PDF blijft een gewone link. Verwijderen en meesturen zijn knoppen buiten
de link, dus een tik daarop opent de foto niet.
"""
import io

import pytest

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


def _vul(post, db, client):
    post("/klussen/nieuw", {"naam": "Badkamer Voorbeeldstraat", "uurtarief": "45"})
    klus_id = db.execute("SELECT id FROM klussen").fetchone()[0]
    post(f"/klus/{klus_id}/notitie", {
        "tekst": "Voegen in de badkamer",
        "foto": [
            (io.BytesIO(PNG), "voeg-links.png"),
            (io.BytesIO(PNG), "voeg-rechts.png"),
            (io.BytesIO(b"%PDF-1.4 test"), "tekening.pdf"),
        ],
    }, content_type="multipart/form-data")
    post(f"/klus/{klus_id}/inkoop", {
        "omschrijving": "Tegellijm",
        "bedrag": "12",
        "bijlage": [
            (io.BytesIO(PNG), "bon-gamma.png"),
            (io.BytesIO(b"heic-voorbeeld"), "scherm.heic"),
        ],
    }, content_type="multipart/form-data")
    return client.get(f"/klus/{klus_id}").data.decode()


def _browser(html):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    browser = p.webkit.launch()
    pagina = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=1)
    pagina.set_content(html)
    return p, browser, pagina


def _dicht(p, browser):
    browser.close()
    p.stop()


def _label(pagina):
    return pagina.locator("#lichtbak").get_attribute("aria-label")


def test_een_notitiefoto_opent_in_de_lichtbak(post, db, client):
    """Notitiefoto's, vorige en volgende, Escape na een tik op de foto.
    Een PDF en Verwijderen doen dat niet."""
    html = _vul(post, db, client)
    assert html.count('id="lichtbak"') == 1
    assert 'data-soort="foto"' in html
    assert 'data-naam="voeg-links.png"' in html
    assert 'data-soort="pdf"' in html
    # De knop staat na de link, niet erin.
    figuur = html.split('data-naam="voeg-links.png"', 1)[1].split("</figure>", 1)[0]
    assert figuur.index("</a>") < figuur.index("Verwijderen")

    p, browser, pagina = _browser(html)
    try:
        link = pagina.locator('a[data-naam="voeg-links.png"]')
        link.click()
        bak = pagina.locator("#lichtbak")
        assert bak.get_attribute("hidden") is None
        assert bak.get_attribute("role") == "dialog"
        assert bak.get_attribute("aria-modal") == "true"
        assert _label(pagina) == "voeg-links.png"
        assert pagina.evaluate(
            "() => document.activeElement.classList.contains('lichtbak-sluit')")
        doos = pagina.locator(".lichtbak-volgende").bounding_box()
        assert doos["width"] >= 44 and doos["height"] >= 44
        assert pagina.locator(".lichtbak-vorige").bounding_box()["width"] >= 44

        pagina.locator(".lichtbak-volgende").click()
        assert _label(pagina) == "voeg-rechts.png"
        pagina.keyboard.press("ArrowRight")
        assert _label(pagina) == "bon-gamma.png"
        pagina.keyboard.press("ArrowLeft")
        assert _label(pagina) == "voeg-rechts.png"
        pagina.locator(".lichtbak-vorige").click()
        assert _label(pagina) == "voeg-links.png"

        # WebKit in deze test heeft geen Touch-constructor. De listener leest
        # alleen clientX, dus een gewone gebeurtenis met die velden volstaat.
        geveegd = pagina.evaluate(
            """() => {
              const el = document.getElementById('lichtbak');
              function veeg(soort, x) {
                const ev = new Event(soort, {bubbles: true, cancelable: true});
                const punt = [{clientX: x, clientY: 420}];
                Object.defineProperty(ev, 'touches', {value: soort === 'touchstart' ? punt : []});
                Object.defineProperty(ev, 'changedTouches', {value: punt});
                el.dispatchEvent(ev);
              }
              veeg('touchstart', 280);
              veeg('touchend', 40);
              return el.getAttribute('aria-label');
            }"""
        )
        assert geveegd == "voeg-rechts.png"

        # Een klik op volgende laat de focus op die knop. De val begint bij sluiten.
        pagina.locator(".lichtbak-sluit").focus()
        assert pagina.evaluate(
            "() => document.activeElement.classList.contains('lichtbak-sluit')")
        pagina.keyboard.press("Tab")
        assert pagina.evaluate(
            "() => document.activeElement.classList.contains('lichtbak-vorige')")
        pagina.keyboard.press("Tab")
        assert pagina.evaluate(
            "() => document.activeElement.classList.contains('lichtbak-volgende')")
        pagina.keyboard.press("Tab")
        assert pagina.evaluate(
            "() => document.activeElement.classList.contains('lichtbak-sluit')")
        pagina.keyboard.press("Shift+Tab")
        assert pagina.evaluate(
            "() => document.activeElement.classList.contains('lichtbak-volgende')")

        pagina.locator("#lichtbak-foto").click()
        assert bak.get_attribute("hidden") is None
        pagina.keyboard.press("Escape")
        assert bak.get_attribute("hidden") is not None
        assert pagina.evaluate(
            """() => document.activeElement.getAttribute('data-naam')""") in {
            "voeg-links.png", "voeg-rechts.png",
        }

        pagina.locator('a[data-soort="pdf"]').evaluate(
            "el => el.addEventListener('click', e => e.preventDefault(), true)")
        pagina.locator('a[data-soort="pdf"]').click()
        assert bak.get_attribute("hidden") is not None

        weg = pagina.locator("button", has_text="Verwijderen").first
        weg.evaluate("el => el.addEventListener('click', e => e.preventDefault(), true)")
        weg.click()
        assert bak.get_attribute("hidden") is not None
    finally:
        _dicht(p, browser)


def test_een_bonfoto_en_een_heic_openen_in_de_lichtbak(post, db, client):
    """Een bon en een HEIC op de klus. Meesturen opent de foto niet."""
    html = _vul(post, db, client)
    assert 'data-naam="scherm.heic"' in html
    assert 'data-soort="foto"' in html.split('data-naam="scherm.heic"', 1)[0][-80:]

    p, browser, pagina = _browser(html)
    try:
        pagina.locator('a[data-naam="bon-gamma.png"]').click()
        bak = pagina.locator("#lichtbak")
        assert bak.get_attribute("hidden") is None
        assert _label(pagina) == "bon-gamma.png"
        assert bak.get_attribute("role") == "dialog"
        pagina.keyboard.press("Escape")
        assert bak.get_attribute("hidden") is not None
        assert pagina.evaluate(
            "() => document.activeElement.getAttribute('data-naam') === 'bon-gamma.png'")

        pagina.locator('a[data-naam="scherm.heic"]').click()
        assert _label(pagina) == "scherm.heic"
        pagina.locator("#lichtbak-foto").click()
        pagina.keyboard.press("Escape")
        assert bak.get_attribute("hidden") is not None
        assert pagina.evaluate(
            "() => document.activeElement.getAttribute('data-naam') === 'scherm.heic'")

        pagina.locator("button", has_text="Meesturen").first.evaluate(
            "el => el.addEventListener('click', e => e.preventDefault(), true)")
        pagina.locator("button", has_text="Meesturen").first.click()
        assert bak.get_attribute("hidden") is not None
    finally:
        _dicht(p, browser)


def test_een_heic_op_het_mailscherm_is_een_foto(post, db, client):
    """HEIC is geen miniatuur, maar hoort wel in de lichtbak. Een PDF niet."""
    from test_mailen import mailserver

    mailserver(db)
    post("/klussen/nieuw", {"naam": "Badkamer Voorbeeldstraat", "uurtarief": "45"})
    klus_id = db.execute("SELECT id FROM klussen").fetchone()[0]
    post(f"/klus/{klus_id}/bijlage", {
        "bijlage": (io.BytesIO(b"heic-voorbeeld"), "scherm.heic"),
    }, content_type="multipart/form-data")
    bijlage_id = db.execute("SELECT id FROM bijlagen").fetchone()[0]
    post(f"/bijlage/{bijlage_id}/meesturen")
    inkoop_id = db.execute("SELECT id FROM inkopen").fetchone()[0]
    post("/nieuw", {
        "klant_naam": "Voorbeeld Bedrijf BV", "klant_email": "klant@example.com",
        "datum": "2026-08-14",
        "omschrijving": "Tegellijm", "type": "bon", "aantal": "1", "prijs": "12",
        "regel_inkoop": str(inkoop_id),
    })
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    pagina = client.get(f"/factuur/{factuur_id}/mail").data.decode()
    assert pagina.count('id="lichtbak"') == 1
    regel = next(
        deel for deel in pagina.split('<li class="bijlage-regel">')
        if "scherm.heic" in deel
    )
    assert 'data-soort="foto"' in regel
    assert 'data-soort="pdf"' in pagina
