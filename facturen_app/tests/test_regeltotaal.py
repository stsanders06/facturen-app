"""Het bedrag op een regelkaart: groot totaal, klein tarief, tijden bij elkaar.

De PDF tekent nl_bedrag van het subtotaal. Dat subtotaal komt van rond_centen
of van bedrag_van_minuten, allebei met een halve cent omhoog. Het script op
de kaart hoort hetzelfde getal te laten zien, ook terwijl je nog typt.
"""
from decimal import Decimal

import pytest

from conftest import facturen
from test_smalle_weergave import css_van, regel_met


def _euro(bedrag):
    return "€\u00a0" + facturen.nl_bedrag(bedrag)


def test_de_kaart_toont_het_totaal_en_laat_de_opgeslagen_zin_staan(client, db):
    """Groot bedrag, kleine opbouw, en de tijden in een span. Het veld niet."""
    factuur_id = db.execute(
        """INSERT INTO facturen
           (nummer, datum, vervalt_op, klant_naam, klant_adres, klant_email,
            status, totaal)
           VALUES ('2026-091', '2026-10-18', '2026-11-01', 'Voorbeeld Bedrijf BV',
                   'Voorbeeldstraat 1\n1234 AB Voorbeeldstad', 'info@example.org',
                   'concept', 611.9)"""
    ).lastrowid
    omschrijving = "zo 27 sep 2026, 09:00\u201317:30 \u00b7 Schutting \u00b7 plaatsen"
    db.execute(
        """INSERT INTO regels
           (factuur_id, omschrijving, type, aantal, prijs, subtotaal)
           VALUES (?, ?, 'arbeid_uur', 8.5, 65, 552.5)""",
        (factuur_id, omschrijving),
    )
    db.execute(
        """INSERT INTO regels
           (factuur_id, omschrijving, type, aantal, prijs, subtotaal)
           VALUES (?, 'Bevestigingsmiddelen', 'bon', 1, 45.9, 45.9)""",
        (factuur_id,),
    )
    db.execute(
        """INSERT INTO regels
           (factuur_id, omschrijving, type, aantal, prijs, subtotaal)
           VALUES (?, 'Buis 32 mm', 'materiaal', 3, 4.5, 13.5)""",
        (factuur_id,),
    )
    db.commit()

    html = client.get(f"/factuur/{factuur_id}/bewerk").data.decode()
    assert 'class="regel-totaal"' in html
    assert 'data-pdf="552,50"' in html
    assert "8,5 u × €\u00a065,00" in html
    assert "3 × €\u00a04,50" in html
    # Eén stuk of een bon: het totaal is het tarief. Alleen het grote bedrag.
    assert html.count('class="regel-tarief" hidden') >= 1
    assert '<span class="regel-tijd">09:00\u201317:30</span>' in html
    assert "\u00a0· Schutting\u00a0· plaatsen" in html
    # Het invoerveld gaat zo de database in. De span en de harde spatie zijn
    # alleen de weergave op de kaart.
    assert f'value="{omschrijving}"' in html
    assert 'inputmode="decimal"' in html
    assert html.count('inputmode="decimal"') >= 2
    assert "nowrap" in regel_met(css_van(html), ".regel-tijd")

    offerte = client.get("/offertes/nieuw").data.decode()
    assert 'class="regel-totaal"' in offerte
    assert 'inputmode="decimal"' in offerte


def test_tarief_verdwijnt_bij_een_stuk_of_een_bon():
    assert facturen.tarief_voor_kaart(
        {"type": "materiaal", "aantal": 1, "prijs": 4.5}) == ""
    assert facturen.tarief_voor_kaart(
        {"type": "bon", "aantal": 1, "prijs": 45.9}) == ""
    assert facturen.tarief_voor_kaart(
        {"type": "arbeid_klus", "aantal": 1, "prijs": 250}) == ""
    assert facturen.tarief_voor_kaart(
        {"type": "arbeid_uur", "aantal": 8.5, "prijs": 65, "uur_id": 4}
    ) == "8,5 u × €\u00a065,00"


def test_kaarttekst_plakt_de_tijden_zonder_de_zin_te_veranderen():
    ruw = "ma 12 okt 2026, 07:00\u201314:20 · Schutting · plaatsen"
    uit = str(facturen.kaart_omschrijving(ruw))
    assert '<span class="regel-tijd">07:00\u201314:20</span>' in uit
    assert "\u00a0· Schutting\u00a0· plaatsen" in uit
    # De opgeslagen zin heeft gewone spaties. Die komen niet meer voor
    # rond het puntje, anders kan een regel alsnog met · beginnen.
    assert ruw not in uit
    assert uit.index("\u00a0·") < uit.index("Schutting")


def _vul(pagina, naam, waarde):
    """Zet een veld en stuur het input-event, zoals een toetsaanslag dat doet."""
    pagina.evaluate(
        """([naam, waarde]) => {
          const veld = document.querySelector('.regel [name="' + naam + '"]');
          veld.value = waarde;
          veld.dispatchEvent(new Event('input', { bubbles: true }));
        }""",
        [naam, waarde],
    )


def _totaal_op_kaart(pagina):
    return pagina.evaluate(
        """() => document.querySelector('.regel-totaal').textContent"""
    )


def test_het_regeltotaal_rekent_als_de_server_en_werkt_mee(client):
    """1,5 × 45,55 is 68,33, niet 68,32. Het bedrag wijzigt vóór het opslaan."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    verwacht = facturen.rond_centen(Decimal("1.5") * Decimal("45.55"))
    assert facturen.nl_bedrag(verwacht) == "68,33"

    html = client.get("/nieuw").data.decode()
    with sync_playwright() as p:
        browser = p.webkit.launch()
        pagina = browser.new_page(
            viewport={"width": 390, "height": 844}, device_scale_factor=1,
        )
        pagina.set_content(html)
        _vul(pagina, "aantal", "2")
        _vul(pagina, "prijs", "10")
        assert _totaal_op_kaart(pagina) == _euro(20)
        # Alleen het tarief wijzigt. Het totaal hoort meteen mee te gaan.
        _vul(pagina, "prijs", "11")
        assert _totaal_op_kaart(pagina) == _euro(22)
        assert "2 × €\u00a011,00" in pagina.evaluate(
            """() => document.querySelector('.regel-tarief').textContent"""
        )
        _vul(pagina, "aantal", "1.5")
        _vul(pagina, "prijs", "45.55")
        assert _totaal_op_kaart(pagina) == _euro(verwacht)
        assert "1,5 × €\u00a045,55" in pagina.evaluate(
            """() => document.querySelector('.regel-tarief').textContent"""
        )
        # Eén stuk: de opbouw zegt niets wat het totaal niet al zegt.
        _vul(pagina, "aantal", "1")
        verborgen = pagina.evaluate(
            """() => document.querySelector('.regel-tarief').hidden"""
        )
        assert verborgen is True
        assert _totaal_op_kaart(pagina) == _euro(45.55)
        browser.close()


def _dagfactuur(post, db):
    post("/klanten/nieuw", {
        "naam": "Voorbeeld Bedrijf BV", "email": "info@example.org",
    })
    klant_id = db.execute("SELECT id FROM klanten").fetchone()[0]
    post("/klussen/nieuw", {
        "naam": "Schutting", "uurtarief": "45.55", "klant_id": str(klant_id),
    })
    klus_id = db.execute("SELECT id FROM klussen").fetchone()[0]
    post(f"/klus/{klus_id}/dag", {
        "datum": "2026-10-12", "van": "07:00", "tot": "14:20",
        "notitie": "plaatsen bij de schuur",
    })
    uur_id = db.execute("SELECT id FROM uren").fetchone()[0]
    post("/nieuw", {
        "klant_naam": "Voorbeeld Bedrijf BV",
        "klant_id": str(klant_id),
        "klant_email": "info@example.org",
        "datum": "2026-10-16",
        "omschrijving": "ma 12 okt 2026, 07:00\u201314:20 · Schutting · plaatsen bij de schuur",
        "type": "arbeid_uur",
        "aantal": "7.33",
        "prijs": "45.55",
        "regel_klus": str(klus_id),
        "regel_uur": str(uur_id),
    })
    return db.execute("SELECT id FROM facturen").fetchone()[0]


def test_een_dagregel_rekent_met_de_minuten_en_de_tijden_blijven_heel(post, client, db):
    """440 minuten maal een nieuw tarief, niet 7,33 uur maal dat tarief.

    Op 320px mag de tijd niet doormidden, en een regel niet met · beginnen.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    factuur_id = _dagfactuur(post, db)
    # 7,33 × 20 zou 146,60 zijn. De minuten geven 146,67, net als de PDF.
    assert facturen.nl_bedrag(facturen.bedrag_van_minuten(440, "20")) == "146,67"
    assert facturen.nl_bedrag(
        facturen.rond_centen(Decimal("7.33") * Decimal("20"))) == "146,60"

    html = client.get(f"/factuur/{factuur_id}/bewerk").data.decode()
    ruw = db.execute("SELECT omschrijving FROM regels").fetchone()[0]
    assert "07:00\u201314:20" in ruw
    assert " · " in ruw
    assert "\u00a0" not in ruw

    with sync_playwright() as p:
        browser = p.webkit.launch()
        pagina = browser.new_page(
            viewport={"width": 320, "height": 844}, device_scale_factor=1,
        )
        pagina.set_content(html)
        assert _totaal_op_kaart(pagina) == _euro(334.03)
        veld = pagina.locator('.regel [name="omschrijving"]')
        assert veld.input_value() == ruw
        _vul(pagina, "prijs", "20")
        assert _totaal_op_kaart(pagina) == _euro(146.67)
        assert "7,33 u × €\u00a020,00" in pagina.evaluate(
            """() => document.querySelector('.regel-tarief').textContent"""
        )
        gemeten = pagina.evaluate(
            """() => {
              const tekst = document.querySelector('.regel-tekst');
              const tijd = tekst.querySelector('.regel-tijd');
              const stijl = getComputedStyle(tijd);
              const walker = document.createTreeWalker(tekst, NodeFilter.SHOW_TEXT);
              const begin = [];
              let node;
              while ((node = walker.nextNode())) {
                const waarde = node.textContent;
                for (let i = 0; i < waarde.length; i++) {
                  const range = document.createRange();
                  range.setStart(node, i);
                  range.setEnd(node, i + 1);
                  const rect = range.getBoundingClientRect();
                  if (!rect.width && !rect.height) continue;
                  const top = Math.round(rect.top);
                  if (!begin.some((r) => r.top === top)) {
                    begin.push({top: top, teken: waarde[i]});
                  }
                }
              }
              return {
                nowrap: stijl.whiteSpace,
                rechthoeken: tijd.getClientRects().length,
                tijd: tijd.textContent,
                begin: begin.map((r) => r.teken),
                veld: document.querySelector('[name="omschrijving"]').value,
              };
            }"""
        )
        assert gemeten["nowrap"] == "nowrap"
        assert gemeten["rechthoeken"] == 1
        assert gemeten["tijd"] == "07:00\u201314:20"
        assert "\u00b7" not in gemeten["begin"]
        assert gemeten["veld"] == ruw
        assert len(gemeten["begin"]) >= 2
        browser.close()
