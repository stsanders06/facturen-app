"""De tabbalk, het filtersegment en de lage kaarten van 2.0.1.

Op de iPhone staat Klanten los in de balk en zit Scans achter Meer. Het
filtersegment blijft op een smalle telefoon één rij die zijwaarts schuift.
De kaarten in de lijsten hebben geen losse onderste rij meer: nummer, status
en knoppen staan op één regel (Mac) of op twee lage regels (telefoon).
"""
import re

import pytest

from test_smalle_weergave import css_van, regel_met


def _tabpil(html):
    """De losse tabbladen en de puntjes, in de volgorde van de balk."""
    pil = html.split('<div class="tab-pil">', 1)[1].split('<div class="meer-vel">', 1)[0]
    namen = re.findall(r"<span>([^<]+)</span>", pil)
    return pil, namen


def _vel(html):
    return html.split('<div class="meer-vel">', 1)[1].split("</div>", 1)[0]


def _scans(db, aantal):
    for i in range(aantal):
        db.execute(
            "INSERT INTO scans (bestand, naam, soort, grootte, ontvangen) VALUES (?, ?, 'foto', 10, '2026-10-01')",
            (f"scan-{i}.png", f"scan-{i}.png"),
        )
    db.commit()


# ---------- Tabbalk ----------

def test_de_tabbalk_heeft_precies_deze_vijf_in_deze_volgorde(client):
    pil, namen = _tabpil(client.get("/vandaag").data.decode())
    assert namen == ["Vandaag", "Rekeningen", "Klussen", "Klanten", "Meer"]
    links = re.findall(r'<a href="([^"]+)"', pil)
    assert links == ["/vandaag", "/", "/klussen", "/klanten"]
    assert pil.count("<details") == 1
    assert 'href="/scans"' not in pil


def test_scans_zit_achter_meer(client):
    vel = _vel(client.get("/vandaag").data.decode())
    assert re.findall(r'<a href="([^"]+)"', vel) == ["/offertes", "/scans", "/instellingen"]


@pytest.mark.parametrize("pad, tab", [
    ("/vandaag", "/vandaag"), ("/", "/"), ("/klussen", "/klussen"), ("/klanten", "/klanten"),
])
def test_een_los_tabblad_staat_aan_en_meer_niet(client, pad, tab):
    html = client.get(pad).data.decode()
    pil, _ = _tabpil(html)
    assert f'<a href="{tab}" class="aan" aria-current="page">' in pil
    assert pil.count('class="aan"') == 1
    assert '<details class="meer">' in pil
    assert "<summary>" in pil


@pytest.mark.parametrize("pad", ["/offertes", "/scans", "/instellingen"])
def test_achter_meer_staat_meer_aan(client, pad):
    html = client.get(pad).data.decode()
    pil, _ = _tabpil(html)
    assert '<details class="meer aan">' in pil
    assert '<summary aria-current="page">' in pil
    assert 'class="aan"' not in pil.split("<details", 1)[0]
    assert f'<a href="{pad}" class="aan" aria-current="page">' in _vel(html)


def test_het_aantal_scans_staat_op_de_puntjes_en_in_het_menu(client, db):
    _scans(db, 3)
    html = client.get("/klanten").data.decode()
    pil, _ = _tabpil(html)
    summary = pil.split("<summary", 1)[1].split("</summary>", 1)[0]
    assert '<span class="tab-tel">3</span>' in summary
    assert 'Scans<span class="meer-tel">3</span>' in _vel(html)
    # Het getal hoort zichtbaar te blijven: de regel die "Meer" verbergt
    # mag het niet meepakken.
    css = css_van(html)
    assert ".tabbar .meer > summary > span:not(.tab-tel)" in css


def test_zonder_scans_geen_getal_op_de_puntjes(client):
    html = client.get("/vandaag").data.decode()
    assert 'class="tab-tel"' not in html
    assert 'class="meer-tel"' not in html


def test_op_een_mac_staan_klanten_en_scans_in_de_zijbalk(client):
    html = client.get("/vandaag").data.decode()
    zij = html.split('<aside', 1)[1].split("</aside>", 1)[0]
    assert 'href="/klanten"' in zij
    assert 'href="/scans"' in zij


def test_de_tabbalk_past_en_is_met_een_duim_te_raken(client, db):
    """Vijf vakken naast elkaar, allemaal 44 px, niets over elkaar, en Scans
    is via Meer te openen. Op 390 en 430 staan de namen erbij."""
    from browser import meetbrowser, playwright_verplicht

    sync_playwright = playwright_verplicht()
    _scans(db, 2)
    html = client.get("/klanten").data.decode()
    script = """() => {
      const vakken = [...document.querySelectorAll('.tabbar .tab-pil > a, .tabbar .meer > summary')];
      const r = vakken.map(v => v.getBoundingClientRect());
      let over = false;
      for (let i = 0; i < r.length; i++) for (let j = i + 1; j < r.length; j++) {
        if (r[i].right > r[j].left + 0.5 && r[j].right > r[i].left + 0.5
            && r[i].bottom > r[j].top + 0.5 && r[j].bottom > r[i].top + 0.5) over = true;
      }
      const tel = document.querySelector('.tabbar .meer > summary .tab-tel').getBoundingClientRect();
      const plus = document.querySelector('.tabbar .plus').getBoundingClientRect();
      return {
        aantal: vakken.length,
        klein: r.filter(x => x.width < 43.5 || x.height < 43.5).length,
        over,
        rechts: Math.max(...r.map(x => x.right), plus.right),
        breed: innerWidth,
        tel: tel.width > 8 && tel.height > 8,
        namen: [...document.querySelectorAll('.tabbar .tab-pil > a > span:first-of-type')]
          .filter(s => s.getClientRects().length && s.getBoundingClientRect().width > 2).length,
        aan: document.querySelector('.tabbar .tab-pil > a.aan').getAttribute('href'),
      };
    }"""
    with sync_playwright() as p:
        browser = meetbrowser(p)
        try:
            for breedte in (320, 390, 430):
                pagina = browser.new_page(viewport={"width": breedte, "height": 844})
                pagina.set_content(html)
                uit = pagina.evaluate(script)
                assert uit["aantal"] == 5, (breedte, uit)
                assert uit["klein"] == 0, (breedte, uit)
                assert not uit["over"], (breedte, uit)
                assert uit["rechts"] <= uit["breed"], (breedte, uit)
                assert uit["tel"], (breedte, uit)
                assert uit["aan"] == "/klanten", (breedte, uit)
                if breedte >= 390:
                    assert uit["namen"] == 4, (breedte, uit)
                pagina.locator(".tabbar .meer > summary").click()
                scans = pagina.locator(".tabbar .meer-vel a[href='/scans']")
                assert scans.is_visible()
                vak = scans.bounding_box()
                assert vak["height"] >= 43.5
                assert vak["x"] >= 0 and vak["x"] + vak["width"] <= breedte
                pagina.close()
        finally:
            browser.close()


# ---------- Filtersegment ----------

def test_het_filtersegment_breekt_niet_af(client):
    css = css_van(client.get("/offertes").data.decode())
    regel = regel_met(css, ".filters")
    assert "flex-wrap: nowrap" in regel
    assert "overflow-x: auto" in regel
    assert "scrollbar-width: none" in regel
    assert "display: none" in regel_met(css, ".filters::-webkit-scrollbar")
    assert "flex: none" in regel_met(css, ".filters a")


def _filter_script():
    return """() => {
      const f = document.querySelector('.filters');
      const knoppen = [...f.querySelectorAll('a')];
      const fr = f.getBoundingClientRect();
      const tops = knoppen.map(a => Math.round(a.getBoundingClientRect().top));
      const aan = f.querySelector('a.aan').getBoundingClientRect();
      const doc = document.documentElement;
      return {
        rijen: new Set(tops).size,
        hoog: fr.height,
        laagste: Math.min(...knoppen.map(a => a.getBoundingClientRect().height)),
        aanInBeeld: aan.left >= fr.left - 0.5 && aan.right <= fr.right + 0.5,
        schuift: f.scrollWidth > f.clientWidth + 1,
        balk: f.offsetHeight - f.clientHeight,
        pagina: doc.scrollWidth > doc.clientWidth + 1,
        breed: fr.width,
        y: scrollY,
      };
    }"""


@pytest.mark.parametrize("pad", ["/", "/offertes", "/offertes?status=verlopen",
                                 "/offertes?status=afgewezen", "/?status=betaald"])
def test_het_filtersegment_is_een_rij_op_een_telefoon(client, db, maak_factuur, maak_offerte, pad):
    from browser import meetbrowser, playwright_verplicht

    sync_playwright = playwright_verplicht()
    _lijst(db, maak_factuur, maak_offerte)
    _te_laat(maak_factuur, maak_offerte)
    html = client.get(pad).data.decode()
    with sync_playwright() as p:
        browser = meetbrowser(p)
        try:
            for breedte in (320, 390, 430):
                pagina = browser.new_page(viewport={"width": breedte, "height": 844}, has_touch=True)
                pagina.set_content(html)
                uit = pagina.evaluate(_filter_script())
                pagina.close()
                assert uit["rijen"] == 1, (pad, breedte, uit)
                assert uit["hoog"] <= 52, (pad, breedte, uit)
                assert uit["laagste"] >= 43.5, (pad, breedte, uit)
                assert uit["aanInBeeld"], (pad, breedte, uit)
                assert uit["balk"] == 0, (pad, breedte, uit)
                assert not uit["pagina"], (pad, breedte, uit)
                assert uit["y"] == 0, (pad, breedte, uit)
        finally:
            browser.close()


def test_op_een_mac_schuift_het_filtersegment_niet(client, db, maak_factuur, maak_offerte):
    from browser import meetbrowser, playwright_verplicht

    sync_playwright = playwright_verplicht()
    _lijst(db, maak_factuur, maak_offerte)
    with sync_playwright() as p:
        browser = meetbrowser(p)
        try:
            for pad in ("/", "/offertes"):
                pagina = browser.new_page(viewport={"width": 1280, "height": 900})
                pagina.set_content(client.get(pad).data.decode())
                uit = pagina.evaluate(_filter_script())
                klassen = pagina.evaluate("() => document.querySelector('.filters').className")
                pagina.close()
                assert uit["rijen"] == 1, (pad, uit)
                assert not uit["schuift"], (pad, uit)
                assert uit["hoog"] <= 42, (pad, uit)
                # Zo breed als de knoppen, niet de hele kolom.
                assert uit["breed"] < 700, (pad, uit)
                assert klassen == "filters", (pad, klassen)
        finally:
            browser.close()


# ---------- Kaarten ----------

def _kaart_script():
    return """() => {
      const uit = [];
      document.querySelectorAll('.cards > .card').forEach(kaart => {
        const r = el => el.getBoundingClientRect();
        const midden = el => { const b = r(el); return b.top + b.height / 2; };
        const onder = kaart.querySelector('.onderkant');
        const meta = kaart.querySelector('.meta');
        const chip = kaart.querySelector('.chips .chip');
        const puntjes = kaart.querySelector('.menu > summary');
        const knop = kaart.querySelector('.acties .btn-text:not(.menu-knop)');
        // Elk tikvlak raakt in zijn eigen midden zichzelf, en nooit een ander.
        const vlakken = [kaart.querySelector('.klant a'), knop, puntjes].filter(Boolean);
        const zelf = vlakken.every(el => {
          const b = r(el);
          const n = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
          return n && (n === el || el.contains(n));
        });
        const k = [knop, puntjes].filter(Boolean).map(r);
        let over = false;
        for (let i = 0; i < k.length; i++) for (let j = i + 1; j < k.length; j++) {
          if (k[i].right > k[j].left + 0.5 && k[j].right > k[i].left + 0.5
              && k[i].bottom > k[j].top + 0.5 && k[j].bottom > k[i].top + 0.5) over = true;
        }
        // Het nummer en de status openen de kaart zelf.
        const kaartlink = el => {
          const b = r(el);
          const n = document.elementFromPoint(b.left + 4, b.top + b.height / 2);
          return !!(n && n.classList.contains('kaartlink'));
        };
        // Hoe hoog de puntjes met een duim te raken zijn.
        const pb = r(puntjes);
        let boven = pb.bottom, beneden = pb.top;
        for (let y = pb.top - 20; y <= pb.bottom + 20; y += 1) {
          const n = document.elementFromPoint(pb.left + pb.width / 2, y);
          if (n && (n === puntjes || puntjes.contains(n))) {
            boven = Math.min(boven, y); beneden = Math.max(beneden, y);
          }
        }
        uit.push({
          hoog: r(kaart).height,
          rand: getComputedStyle(onder).borderTopWidth,
          ruimte: parseFloat(getComputedStyle(onder).marginTop) + parseFloat(getComputedStyle(onder).paddingTop),
          metaChip: Math.abs(midden(meta) - midden(chip)),
          chipPuntjes: Math.abs(midden(chip) - midden(puntjes)),
          gat: r(chip).top - r(meta).bottom,
          zelf, over,
          metaOpen: kaartlink(meta), chipOpen: kaartlink(chip),
          puntjes: beneden - boven + 1,
          metaInOnder: onder.contains(meta),
          binnen: r(puntjes).right <= r(kaart).right + 0.5 && pb.bottom <= r(kaart).bottom,
        });
      });
      return uit;
    }"""


def _lijst(db, maak_factuur, maak_offerte):
    """Gewone kaarten: één chip en één knop, nog niet te laat."""
    maak_factuur(nummer="2026-001", status="verzonden", vervalt_op="2099-01-01")
    maak_factuur(nummer="", status="concept", vervalt_op="2099-01-01")
    maak_offerte(nummer="O2026-001", status="verzonden", geldig="2099-01-01")
    maak_offerte(nummer="", status="concept", geldig="2099-01-01")


def _te_laat(maak_factuur, maak_offerte):
    """Twee chips op één kaart: Verzonden en Te laat of Verlopen."""
    maak_factuur(nummer="2026-002", status="verzonden", vervalt_op="2026-01-01")
    maak_offerte(nummer="O2026-002", status="verzonden", geldig="2026-01-01")


def test_de_kaarten_houden_hun_onderregel_en_de_tikvlakken_raken_elkaar_niet(client, db, maak_factuur, maak_offerte):
    """De kaarten blijven zoals in 2.0.0: nummer en datums bovenaan, en daaronder
    een eigen regel met een streep erboven voor de status en de knoppen. Een
    compactere kaart (alles op één regel) vond Stone te druk. Wel moet elk
    tikvlak zichzelf raken en geen ander overlappen."""
    from browser import meetbrowser, playwright_verplicht

    sync_playwright = playwright_verplicht()
    _lijst(db, maak_factuur, maak_offerte)
    _te_laat(maak_factuur, maak_offerte)
    with sync_playwright() as p:
        browser = meetbrowser(p)
        try:
            for pad in ("/", "/offertes"):
                html = client.get(pad).data.decode()
                for breedte in (320, 390, 430, 1280):
                    telefoon = breedte < 900
                    pagina = browser.new_page(viewport={"width": breedte, "height": 1600},
                                              has_touch=telefoon)
                    pagina.set_content(html)
                    kaarten = pagina.evaluate(_kaart_script())
                    pagina.close()
                    assert len(kaarten) == 3, (pad, breedte)
                    for uit in kaarten:
                        plek = (pad, breedte, uit)
                        # De onderregel: streep erboven, 12 px lucht ervoor en erna.
                        assert uit["rand"] == "1px", plek
                        assert uit["ruimte"] == 24, plek
                        assert not uit["metaInOnder"], plek
                        assert uit["gat"] >= 24, plek
                        assert uit["zelf"], plek
                        assert not uit["over"], plek
                        assert uit["metaOpen"], plek
                        assert uit["binnen"], plek
                        if telefoon:
                            assert uit["puntjes"] >= 43.5, plek
        finally:
            browser.close()


def test_de_puntjes_op_een_kaart_openen_hun_menu(client, db, maak_factuur, maak_offerte):
    from browser import meetbrowser, playwright_verplicht

    sync_playwright = playwright_verplicht()
    _lijst(db, maak_factuur, maak_offerte)
    with sync_playwright() as p:
        browser = meetbrowser(p)
        try:
            for pad in ("/", "/offertes"):
                for breedte in (320, 390, 1280):
                    pagina = browser.new_page(viewport={"width": breedte, "height": 1000},
                                              has_touch=breedte < 900)
                    pagina.set_content(client.get(pad).data.decode())
                    pagina.locator(".cards .menu > summary").first.click()
                    lijst = pagina.locator(".cards .menu[open] .menu-lijst")
                    assert lijst.is_visible(), (pad, breedte)
                    vak = lijst.bounding_box()
                    assert vak["x"] >= 0 and vak["x"] + vak["width"] <= breedte, (pad, breedte, vak)
                    assert pagina.locator(".cards .menu[open] .menu-item").first.is_visible()
                    pagina.close()
        finally:
            browser.close()
