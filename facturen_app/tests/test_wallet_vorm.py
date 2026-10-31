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


def test_na_verwijderen_krijgt_een_nieuwe_regel_een_vers_id(client, maak_factuur):
    """Vijf regels, regel 2 weg, en dan een nieuwe: soort-5 mag er niet
    twee keer zijn."""
    from browser import meetbrowser, playwright_verplicht

    sync_playwright = playwright_verplicht()
    factuur_id = maak_factuur()

    def ids_van(pagina):
        return pagina.evaluate(
            """() => [...document.querySelectorAll('#regels .regel')].map(rij => ({
              sel: rij.querySelector('select.soort').id,
              aantal: rij.querySelector('[name="aantal"]').id,
              oms: rij.querySelector('[name="omschrijving"]').id,
            }))"""
        )

    with sync_playwright() as p:
        browser = meetbrowser(p)
        try:
            for pad in ("/nieuw", f"/factuur/{factuur_id}/bewerk"):
                pagina = browser.new_page()
                pagina.set_content(client.get(pad).data.decode())
                pagina.evaluate(
                    "() => { const d = document.querySelector('details.wijzig'); if (d) d.open = true; }"
                )
                for _ in range(4):
                    pagina.locator("#regel-erbij").click()
                pagina.locator("#regels .regel").nth(1).locator(".weg").click()
                pagina.locator("#regel-erbij").click()
                ids = ids_van(pagina)
                pagina.close()
                assert len(ids) == 5, pad
                for sleutel in ("sel", "aantal", "oms"):
                    waarden = [rij[sleutel] for rij in ids]
                    assert len(waarden) == len(set(waarden)), (pad, sleutel, waarden)
        finally:
            browser.close()


def test_escape_sluit_het_menu_meer(client):
    """Een tik op Meer en daarna Escape hoort het menu dicht te doen."""
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


def _klant(db):
    return db.execute(
        "INSERT INTO klanten (naam, adres, email) VALUES (?, ?, ?)",
        ("Voorbeeld Klant", "Voorbeeldstraat 1", "klant@example.org"),
    ).lastrowid


def _met_klant(db, maak_factuur, maak_offerte):
    klant_id = _klant(db)
    maak_factuur(klant="Voorbeeld Klant", email="klant@example.org")
    maak_offerte(klant="Voorbeeld Klant")
    db.execute("UPDATE facturen SET klant_id=?", (klant_id,))
    db.execute("UPDATE offertes SET klant_id=?", (klant_id,))
    db.commit()
    return klant_id


def test_een_tik_naast_de_klantnaam_opent_de_rekening(client, db, maak_factuur, maak_offerte):
    """Op een telefoon is het tikvlak 44 px, boven de tekst. Het midden van
    het nummer en van het label blijft van de rekening."""
    from browser import meetbrowser, playwright_verplicht

    sync_playwright = playwright_verplicht()
    _met_klant(db, maak_factuur, maak_offerte)

    script = """() => {
      function raakt(el, doel) {
        const r = el.getBoundingClientRect();
        const n = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return !!(n && n.closest(doel));
      }
      function tik(el) {
        const r = el.getBoundingClientRect();
        const x = Math.min(Math.max(r.left + 8, 1), innerWidth - 2);
        let boven = r.bottom, onder = r.top;
        for (let y = Math.max(0, r.top - 48); y <= Math.min(innerHeight - 1, r.bottom + 8); y += 1) {
          const n = document.elementFromPoint(x, y);
          if (n === el || (n && el.contains(n))) {
            if (y < boven) boven = y;
            if (y > onder) onder = y;
          }
        }
        return {hoog: onder - boven, breed: r.width};
      }
      const link = document.querySelector('.klant a');
      const meta = document.querySelector('.meta');
      const chip = document.querySelector('.onderkant .chip, .chips .chip');
      const vlak = tik(link);
      return {
        hoog: vlak.hoog,
        breed: Math.max(vlak.breed, link.getBoundingClientRect().width),
        rij: link.getBoundingClientRect().height,
        meta: raakt(meta, '.klant a'),
        chip: raakt(chip, '.klant a'),
      };
    }"""

    with sync_playwright() as p:
        browser = meetbrowser(p)
        try:
            for pad in ("/", "/offertes"):
                pagina = browser.new_page(viewport={"width": 390, "height": 844})
                pagina.set_content(client.get(pad).data.decode())
                uit = pagina.evaluate(script)
                pagina.close()
                assert uit["hoog"] >= 43, (pad, uit)
                assert uit["breed"] >= 44, (pad, uit)
                assert uit["rij"] <= 28, (pad, uit)
                assert not uit["meta"], (pad, uit)
                assert not uit["chip"], (pad, uit)
        finally:
            browser.close()


def test_op_een_mac_blijft_de_rij_zo_hoog_als_eerder(client, db, maak_factuur, maak_offerte):
    """De regels voor het tikvlak horen alleen op een telefoon. Op 1280 is
    de naamlink weer 36 px, met de oude marge, en de klusregel blijft laag."""
    from browser import meetbrowser, playwright_verplicht

    sync_playwright = playwright_verplicht()
    klant_id = _met_klant(db, maak_factuur, maak_offerte)
    db.execute(
        """INSERT INTO klussen (naam, klant_id, uurtarief, status, gestart)
           VALUES ('Badkamer Voorbeeldstraat', ?, 50, 'open', '2026-10-01')""",
        (klant_id,),
    )
    db.commit()
    klus_id = db.execute("SELECT id FROM klussen").fetchone()[0]

    script_lijst = """() => {
      const link = document.querySelector('.klant a');
      const stijl = getComputedStyle(link);
      return {
        toon: stijl.display,
        marge: stijl.marginTop,
        vulling: stijl.paddingTop,
        link: link.getBoundingClientRect().height,
        rij: link.parentElement.getBoundingClientRect().height,
      };
    }"""
    script_klus = """() => {
      const p = document.querySelector('.opsomming');
      const link = p.querySelector('a');
      function doos(node) {
        const r = document.createRange();
        r.selectNodeContents(node);
        return r.getBoundingClientRect();
      }
      const label = doos(p.firstChild);
      const naam = doos(link.firstChild);
      return {
        toon: getComputedStyle(link).display,
        regel: label.height,
        verschil: Math.abs(label.bottom - naam.bottom),
        link: link.getBoundingClientRect().height,
      };
    }"""

    with sync_playwright() as p:
        browser = meetbrowser(p)
        try:
            for pad in ("/", "/offertes"):
                pagina = browser.new_page(viewport={"width": 1280, "height": 800})
                pagina.set_content(client.get(pad).data.decode())
                uit = pagina.evaluate(script_lijst)
                pagina.close()
                assert uit["toon"] == "inline-block", (pad, uit)
                assert uit["marge"] == "-6px", (pad, uit)
                assert uit["vulling"] == "6px", (pad, uit)
                assert 34 <= uit["link"] <= 40, (pad, uit)
                # De regel zelf, niet de hele kaart: die hangt van de inhoud af.
                # 32 px was de te hoge flex-rij van de vorige ronde.
                assert 18 <= uit["rij"] <= 28, (pad, uit)
            pagina = browser.new_page(viewport={"width": 1280, "height": 800})
            pagina.set_content(client.get(f"/klus/{klus_id}").data.decode())
            uit = pagina.evaluate(script_klus)
            pagina.close()
            assert uit["toon"] == "inline-block", uit
            assert uit["regel"] <= 24, uit
            assert uit["verschil"] < 1, uit
            # De link is hoger door de vulling, de regel zelf niet.
            assert uit["link"] <= uit["regel"] + 16, uit
        finally:
            browser.close()


def test_de_klantnaam_op_een_klus_raakt_de_buren_niet(client, db):
    """Zelfde duimmaat op de klus, zonder over het vinkje of de status te liggen."""
    from browser import meetbrowser, playwright_verplicht

    sync_playwright = playwright_verplicht()
    klant_id = _klant(db)
    db.execute(
        """INSERT INTO klussen (naam, klant_id, uurtarief, status, gestart, aangevraagd_op)
           VALUES ('Badkamer Voorbeeldstraat', ?, 50, 'aangevraagd', '2026-10-01', '2026-10-01')""",
        (klant_id,),
    )
    db.commit()
    klus_id = db.execute("SELECT id FROM klussen").fetchone()[0]

    with sync_playwright() as p:
        browser = meetbrowser(p)
        pagina = browser.new_page(viewport={"width": 390, "height": 844})
        try:
            pagina.set_content(client.get(f"/klus/{klus_id}").data.decode())
            uit = pagina.evaluate(
                """() => {
                  const link = document.querySelector('.opsomming a');
                  const doos = link.getBoundingClientRect();
                  const knop = document.querySelector('.vink-knop');
                  const chip = document.querySelector('.opsomming .chip');
                  function overlapt(a, b) {
                    return a.left < b.right - 0.5 && a.right > b.left + 0.5
                      && a.top < b.bottom - 0.5 && a.bottom > b.top + 0.5;
                  }
                  function raaktLink(el) {
                    const r = el.getBoundingClientRect();
                    const raak = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                    return !!(raak && raak.closest('.opsomming a'));
                  }
                  const kr = knop.getBoundingClientRect();
                  const cr = chip.getBoundingClientRect();
                  const x = Math.min(Math.max(doos.left + 8, 1), innerWidth - 2);
                  let boven = doos.bottom, onder = doos.top;
                  for (let y = Math.max(0, doos.top - 48); y <= doos.bottom + 4; y += 1) {
                    const n = document.elementFromPoint(x, y);
                    if (n === link || (n && link.contains(n))) {
                      if (y < boven) boven = y;
                      if (y > onder) onder = y;
                    }
                  }
                  const label = document.createRange();
                  label.setStart(link.parentElement.firstChild, 0);
                  label.setEnd(link.parentElement.firstChild, link.parentElement.firstChild.length);
                  const lr = label.getBoundingClientRect();
                  const naam = document.createRange();
                  naam.selectNodeContents(link.firstChild);
                  return {
                    hoog: doos.height,
                    knop: overlapt(doos, kr),
                    chip: overlapt(doos, cr),
                    chipMidden: raaktLink(chip),
                    knopMidden: raaktLink(knop),
                    tik: onder - boven,
                    verschil: Math.abs(lr.bottom - naam.getBoundingClientRect().bottom),
                  };
                }"""
            )
            assert uit["hoog"] < 30, uit
            assert not uit["knop"], uit
            assert not uit["chip"], uit
            assert not uit["chipMidden"], uit
            assert not uit["knopMidden"], uit
            assert uit["tik"] >= 43, uit
            assert uit["verschil"] < 1, uit
        finally:
            browser.close()


def test_regel_erbij_ligt_niet_onder_de_balk(client, db, maak_factuur):
    """Definitief maken en Mailen hangen vast. + Regel moet daar vrij
    van blijven, ook als de regels lang zijn."""
    from browser import meetbrowser, playwright_verplicht

    sync_playwright = playwright_verplicht()
    factuur_id = maak_factuur(nummer="", status="concept", email="klant@example.org",
                              klant="Voorbeeld Klant")
    for i in range(8):
        db.execute(
            """INSERT INTO regels (factuur_id, omschrijving, type, aantal, prijs, subtotaal)
               VALUES (?, ?, 'materiaal', 1, 10, 10)""",
            (factuur_id, f"Extra regel {i}"),
        )
    db.commit()

    with sync_playwright() as p:
        browser = meetbrowser(p)
        pagina = browser.new_page(viewport={"width": 390, "height": 844})
        try:
            pagina.set_content(client.get(f"/factuur/{factuur_id}/bewerk").data.decode())
            pagina.evaluate(
                "() => { const d = document.querySelector('details.wijzig'); if (d) d.open = true; }"
            )
            uit = pagina.evaluate(
                """() => {
                  const knop = document.querySelector('#regel-erbij');
                  knop.scrollIntoView({block: 'end'});
                  const r = knop.getBoundingClientRect();
                  const x = r.left + r.width / 2;
                  const y = r.top + r.height / 2;
                  const raak = document.elementFromPoint(x, y);
                  const balk = document.querySelector('.fabs').getBoundingClientRect();
                  const tab = document.querySelector('.tabbar').getBoundingClientRect();
                  function snijdt(a, b) {
                    return a.left < b.right - 0.5 && a.right > b.left + 0.5
                      && a.top < b.bottom - 0.5 && a.bottom > b.top + 0.5;
                  }
                  return {
                    zelf: !!(raak && (raak === knop || knop.contains(raak))),
                    wie: raak && (raak.id || raak.className || raak.tagName),
                    balk: snijdt(r, balk),
                    tab: snijdt(r, tab),
                  };
                }"""
            )
            assert uit["zelf"], uit
            assert not uit["balk"], uit
            assert not uit["tab"], uit
        finally:
            browser.close()


def test_het_aanvraagvinkje_is_op_een_telefoon_te_raken(client, db):
    """Het rondje blijft 22 px, in lijn met de andere stappen. Het tikvlak
    van 44 px zit eromheen en pakt de volgende regel niet mee."""
    from browser import meetbrowser, playwright_verplicht

    sync_playwright = playwright_verplicht()
    db.execute(
        """INSERT INTO klussen (naam, uurtarief, status, gestart, aangevraagd_op)
           VALUES ('Badkamer Voorbeeldstraat', 50, 'aangevraagd', '2026-10-01', '2026-10-01')"""
    )
    db.commit()
    klus_id = db.execute("SELECT id FROM klussen").fetchone()[0]

    with sync_playwright() as p:
        browser = meetbrowser(p)
        pagina = browser.new_page(viewport={"width": 390, "height": 844})
        try:
            pagina.set_content(client.get(f"/klus/{klus_id}").data.decode())
            uit = pagina.evaluate(
                """() => {
                  const knop = document.querySelector('.vink-knop');
                  const li = knop.closest('li');
                  const kr = knop.getBoundingClientRect();
                  const lr = li.getBoundingClientRect();
                  const vr = knop.querySelector('.vink').getBoundingClientRect();
                  const stappen = [...document.querySelectorAll('.stappen li .wat')];
                  const volgende = li.nextElementSibling.querySelector('.wat');
                  const wr = volgende.getBoundingClientRect();
                  const cx = kr.left + kr.width / 2;
                  const cy = kr.top + kr.height / 2;
                  function raakt(x, y) {
                    const n = document.elementFromPoint(x, y);
                    return !!(n && (n === knop || knop.contains(n)));
                  }
                  const buur = document.elementFromPoint(wr.left + wr.width / 2, wr.top + wr.height / 2);
                  return {
                    breed: kr.width,
                    hoog: kr.height,
                    rondje: vr.width,
                    inDeRij: kr.top >= lr.top - 0.5 && kr.bottom <= lr.bottom + 0.5,
                    inspring: Math.abs(stappen[0].getBoundingClientRect().left - stappen[1].getBoundingClientRect().left),
                    boven: raakt(cx, cy - 16),
                    onder: raakt(cx, cy + 16),
                    buur: !!(buur && buur.closest('.vink-knop')),
                    zelf: raakt(cx, cy),
                  };
                }"""
            )
            assert 20 <= uit["breed"] <= 24, uit
            assert 20 <= uit["hoog"] <= 24, uit
            assert 20 <= uit["rondje"] <= 24, uit
            assert uit["inDeRij"], uit
            assert uit["inspring"] <= 2, uit
            assert uit["boven"], uit
            assert uit["onder"], uit
            assert not uit["buur"], uit
            assert uit["zelf"], uit
        finally:
            browser.close()


def test_het_laatste_veld_blijft_vrij_van_de_balk(client, db, maak_factuur):
    """Focus schuift het laatste veld boven Definitief maken en Mailen.
    De pagina zelf schuift, niet een vak erin."""
    from browser import meetbrowser, playwright_verplicht

    sync_playwright = playwright_verplicht()
    factuur_id = maak_factuur(nummer="", status="concept", email="klant@example.org",
                              klant="Voorbeeld Klant")
    for i in range(8):
        db.execute(
            """INSERT INTO regels (factuur_id, omschrijving, type, aantal, prijs, subtotaal)
               VALUES (?, ?, 'materiaal', 1, 10, 10)""",
            (factuur_id, f"Extra regel {i}"),
        )
    db.commit()

    with sync_playwright() as p:
        browser = meetbrowser(p)
        pagina = browser.new_page(viewport={"width": 390, "height": 844})
        try:
            pagina.set_content(client.get(f"/factuur/{factuur_id}/bewerk").data.decode())
            pagina.evaluate(
                """() => {
                  const d = document.querySelector('details.wijzig');
                  if (d) d.open = true;
                  document.querySelectorAll('.regel-detail').forEach(el => { el.hidden = false; });
                }"""
            )
            veld = pagina.locator("#regels input:not([type=hidden])").last
            veld.focus()
            uit = pagina.evaluate(
                """() => {
                  const veld = document.activeElement;
                  const r = veld.getBoundingClientRect();
                  const n = document.elementFromPoint(
                    Math.min(r.left + 24, r.right - 4), r.top + r.height / 2);
                  const balk = document.querySelector('.fabs').getBoundingClientRect();
                  const html = getComputedStyle(document.documentElement);
                  return {
                    id: veld.id,
                    zelf: n === veld || veld.contains(n),
                    wie: n && (n.id || n.className || n.tagName),
                    snijdt: r.top < balk.bottom - 0.5 && r.bottom > balk.top + 0.5,
                    schuiftVenster: html.overflowY !== 'hidden',
                  };
                }"""
            )
            assert uit["zelf"], uit
            assert not uit["snijdt"], uit
            assert uit["schuiftVenster"], uit
        finally:
            browser.close()


def test_afdrukken_knipt_de_rekening_niet_af(client, db, maak_factuur):
    """Een vaste hoogte op het scherm gold ook voor de printer, waardoor
    de rekening op één pagina bleef staan."""
    import re

    from browser import meetbrowser, playwright_verplicht

    sync_playwright = playwright_verplicht()
    factuur_id = maak_factuur(nummer="", status="concept", email="klant@example.org",
                              klant="Voorbeeld Klant")
    for i in range(12):
        db.execute(
            """INSERT INTO regels (factuur_id, omschrijving, type, aantal, prijs, subtotaal)
               VALUES (?, ?, 'materiaal', 1, 30, 30)""",
            (factuur_id, f"Afdrukregel {i} met een langere omschrijving"),
        )
    db.commit()

    with sync_playwright() as p:
        browser = meetbrowser(p)
        pagina = browser.new_page(viewport={"width": 390, "height": 844})
        try:
            pagina.set_content(client.get(f"/factuur/{factuur_id}/bewerk").data.decode())
            pagina.emulate_media(media="print")
            stijl = pagina.evaluate(
                """() => {
                  const html = getComputedStyle(document.documentElement);
                  const body = getComputedStyle(document.body);
                  return {html: html.overflowY, body: body.overflowY, hoog: html.height};
                }"""
            )
            assert stijl["html"] != "hidden", stijl
            assert stijl["body"] != "hidden", stijl
            pdf = pagina.pdf(format="A4")
            paginas = len(re.findall(rb"/Type\s*/Page(?!s)", pdf))
            assert paginas >= 2, paginas
        finally:
            browser.close()
