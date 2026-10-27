"""Prototype C: regels staan compact ingeklapt op rekening- en offerteformulier."""


def test_nieuw_formulier_heeft_regel_samenvatting(client):
    inhoud = client.get("/nieuw").data.decode()
    assert 'class="regel-samenvatting"' in inhoud
    # Op de rekening geen type-badge: omschrijving zegt al genoeg.
    assert 'class="regel-badge"' not in inhoud
    assert 'class="regel-detail"' in inhoud
    assert 'data-open="false"' in inhoud
    assert 'role="button"' in inhoud
    # Velden blijven in de HTML (voor POST), in het detailblok.
    assert 'name="omschrijving"' in inhoud
    assert 'name="type"' in inhoud
    assert 'name="aantal"' in inhoud
    assert 'name="prijs"' in inhoud
    assert 'class="weg"' in inhoud


def test_offerte_formulier_heeft_zelfde_regels_structuur(client):
    inhoud = client.get("/offertes/nieuw").data.decode()
    assert 'class="regel-samenvatting"' in inhoud
    assert 'class="regel-detail"' in inhoud
    # Geen type-badge op offerte: zelfde als rekening.
    assert 'class="regel-badge"' not in inhoud
    assert 'id="meer-regels"' in inhoud
    assert 'id="regel-erbij"' in inhoud


def test_bewerken_met_regels_toont_samenvatting_chrome(client, maak_factuur):
    factuur_id = maak_factuur()
    inhoud = client.get(f"/factuur/{factuur_id}/bewerk").data.decode()
    assert 'class="regel-samenvatting"' in inhoud
    assert 'class="regel-badge"' not in inhoud
    assert 'class="regel-detail"' in inhoud
    assert 'name="omschrijving"' in inhoud
    # Bestaande omschrijving blijft in het invoerveld staan voor POST.
    assert "Kraan vervangen" in inhoud


def test_script_heeft_prototype_c_gedrag(client):
    """Zonder JS-runner: bewaken dat open/dicht in het script zit.

    Badge-map mag nog in JS staan (dode code-pad); pasSamenvattingAan raakt
    de badge alleen als het element bestaat — en dat is nergens meer.
    """
    inhoud = client.get("/nieuw").data.decode()
    assert "function zetOpen(" in inhoud
    assert "function pasSamenvattingAan(" in inhoud
    assert "badge: 'M'" in inhoud
    assert "badge: 'U'" in inhoud
    assert "badge: 'D'" in inhoud
    assert "badge: 'V'" in inhoud
    assert "badge: 'B'" in inhoud
    assert "if (badge) badge.textContent" in inhoud
    assert "Nieuwe regel" in inhoud


def _rekening_met_drie_soorten(db):
    """Dag, bon en een zelfgetypte regel, met verzonnen gegevens."""
    factuur_id = db.execute(
        """INSERT INTO facturen
           (nummer, datum, vervalt_op, klant_naam, klant_adres, klant_email,
            status, totaal)
           VALUES ('2026-090', '2026-10-18', '2026-11-01', 'Voorbeeld Bedrijf BV',
                   'Voorbeeldstraat 1\n1234 AB Voorbeeldstad', 'info@example.org',
                   'concept', 611.9)"""
    ).lastrowid
    for oms, soort, aantal, prijs, sub in (
        ("zo 27 sep 2026, 09:00\u201317:30 \u00b7 Schutting \u00b7 plaatsen",
         "arbeid_uur", 8.5, 65, 552.5),
        ("Bevestigingsmiddelen", "bon", 1, 45.9, 45.9),
        ("Buis 32 mm", "materiaal", 3, 4.5, 13.5),
    ):
        db.execute(
            """INSERT INTO regels
               (factuur_id, omschrijving, type, aantal, prijs, subtotaal)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (factuur_id, oms, soort, aantal, prijs, sub),
        )
    db.commit()
    return factuur_id


def _meet_kaarten(pagina):
    return pagina.evaluate(
        """() => {
          function eersteRegel(el) {
            const range = document.createRange();
            range.selectNodeContents(el);
            const rects = [...range.getClientRects()];
            return rects.length ? rects[0] : el.getBoundingClientRect();
          }
          function vak(el) {
            const r = el.getBoundingClientRect();
            return {links: r.left, rechts: r.right, boven: r.top, onder: r.bottom,
                    breedte: r.width, hoogte: r.height};
          }
          const kaarten = [...document.querySelectorAll('#regels .regel')];
          return {
            scroll: document.documentElement.scrollWidth
              - document.documentElement.clientWidth,
            kaarten: kaarten.map((kaart) => {
              const tekst = kaart.querySelector('.regel-tekst');
              const uren = kaart.querySelector('.regel-qty');
              const prijs = kaart.querySelector('.regel-totaal');
              const kruis = kaart.querySelector('.weg');
              const stijl = getComputedStyle(kaart);
              const doos = kaart.getBoundingClientRect();
              return {
                tekst: tekst.textContent,
                urenTekst: uren.textContent,
                tekstVak: vak(tekst),
                urenVak: vak(uren),
                prijsVak: vak(prijs),
                kruisVak: vak(kruis),
                tekstRegel: eersteRegel(tekst),
                urenRegel: eersteRegel(uren),
                pad: [stijl.paddingTop, stijl.paddingRight,
                      stijl.paddingBottom, stijl.paddingLeft],
                onder: doos.bottom,
                boven: doos.top,
              };
            }),
          };
        }"""
    )


def _velden_van_open_kaart(pagina):
    return pagina.evaluate(
        """() => {
          const kaart = document.querySelector('.regel[data-open="true"]');
          const doos = kaart.getBoundingClientRect();
          const velden = [...kaart.querySelectorAll(
            '.regel-detail input, .regel-detail select'
          )].filter((el) => el.type !== 'hidden' && el.offsetParent !== null);
          return {
            scroll: document.documentElement.scrollWidth
              - document.documentElement.clientWidth,
            velden: velden.map((el) => {
              const r = el.getBoundingClientRect();
              return {
                naam: el.name,
                links: r.left,
                rechts: r.right,
                breedte: r.width,
                kaartLinks: doos.left,
                kaartRechts: doos.right,
                pagina: document.documentElement.clientWidth,
              };
            }),
          };
        }"""
    )


def test_de_regelkaarten_liggen_strak_op_een_smal_scherm(client, db):
    """Omschrijving en uren op één basislijn, bedrag links, kruisje rechts."""
    from browser import webkit_of_overslaan
    sync_playwright = webkit_of_overslaan()

    factuur_id = _rekening_met_drie_soorten(db)
    html = client.get(f"/factuur/{factuur_id}/bewerk").data.decode()
    assert "09:00\u201317:30" in html
    with sync_playwright() as p:
        browser = p.webkit.launch()
        pagina = browser.new_page(
            viewport={"width": 390, "height": 844}, device_scale_factor=1,
        )
        for breedte in (390, 320):
            pagina.set_viewport_size({"width": breedte, "height": 844})
            pagina.set_content(html)
            gemeten = _meet_kaarten(pagina)
            assert gemeten["scroll"] <= 1
            kaarten = gemeten["kaarten"]
            assert [k["urenTekst"] for k in kaarten] == ["8,5 u", "\u2014", "3\u00d7"]
            tussenruimtes = []
            for i, kaart in enumerate(kaarten):
                assert abs(kaart["tekstVak"]["links"] - kaart["prijsVak"]["links"]) <= 1
                assert kaart["urenVak"]["links"] >= kaart["tekstVak"]["rechts"] - 1
                # De uren delen de eerste regel van de omschrijving, ook als
                # die omschrijving daaronder nog doorloopt.
                assert abs(kaart["tekstRegel"]["top"] - kaart["urenRegel"]["top"]) <= 4
                assert abs(kaart["tekstRegel"]["bottom"] - kaart["urenRegel"]["bottom"]) <= 6
                assert abs(kaart["kruisVak"]["breedte"] - 44) <= 1
                assert abs(kaart["kruisVak"]["hoogte"] - 44) <= 1
                kruis_midden = (kaart["kruisVak"]["boven"] + kaart["kruisVak"]["onder"]) / 2
                prijs_midden = (kaart["prijsVak"]["boven"] + kaart["prijsVak"]["onder"]) / 2
                assert abs(kruis_midden - prijs_midden) <= 2
                assert kaart["kruisVak"]["links"] >= kaart["prijsVak"]["rechts"] - 1
                assert kaart["pad"] == ["8px", "8px", "8px", "8px"]
                if i:
                    tussenruimtes.append(kaart["boven"] - kaarten[i - 1]["onder"])
            assert max(tussenruimtes) - min(tussenruimtes) <= 1
            assert tussenruimtes[0] > 0

            for index in range(3):
                pagina.locator(".regel-tekst").nth(index).click()
                open_kaart = _velden_van_open_kaart(pagina)
                assert open_kaart["scroll"] <= 1
                assert open_kaart["velden"]
                namen = [veld["naam"] for veld in open_kaart["velden"]]
                assert "prijs" in namen
                assert "omschrijving" in namen
                if index == 1:
                    assert "aantal" not in namen
                else:
                    assert "aantal" in namen
                for veld in open_kaart["velden"]:
                    assert veld["breedte"] > 40
                    assert veld["links"] >= veld["kaartLinks"] - 1
                    assert veld["rechts"] <= veld["kaartRechts"] + 1
                    assert veld["rechts"] <= veld["pagina"] + 1
        browser.close()
