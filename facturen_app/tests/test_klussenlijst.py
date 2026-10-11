"""De klussenlijst in drie groepen, en de kleur van de status.

Lopend bovenaan, dan aanvragen (ook een klus die je terugzette), afgerond
onderaan. Een lege groep krijgt geen kop. Binnen een groep blijft de volgorde
die klussenlijst() al had: aanvragen op wachttijd, de rest op nieuwste eerst.
"""
import re
from datetime import date, timedelta


def namen(inhoud):
    """De klusnaam op de kaart. Bij afgerond zit er een extra klasse op."""
    return re.findall(r'class="naam(?: [^"]*)?">([^<]+)<', inhoud)


def koppen(inhoud):
    return re.findall(r'<h2 class="lijstkop">([^<]+)</h2>', inhoud)


def css_van(inhoud):
    return "\n".join(re.findall(r"<style>(.*?)</style>", inhoud, re.S))


def regel_met(css, kiezer):
    gevonden = re.search(re.escape(kiezer) + r"\s*\{([^}]*)\}", css)
    return gevonden.group(1) if gevonden else ""


def hex_waarden(css, naam):
    return re.findall(re.escape(naam) + r":\s*(#[0-9a-fA-F]{6})", css)


def _kanaal(waarde):
    c = waarde / 255
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def contrast(voor, achter):
    """WCAG-contrast van twee hexkleuren."""
    def lum(hexkleur):
        getal = int(hexkleur.lstrip("#"), 16)
        r, g, b = (getal >> 16) & 255, (getal >> 8) & 255, getal & 255
        return 0.2126 * _kanaal(r) + 0.7152 * _kanaal(g) + 0.0722 * _kanaal(b)

    licht, donker = sorted((lum(voor), lum(achter)), reverse=True)
    return (licht + 0.05) / (donker + 0.05)


def tint(hexkleur):
    """Tint in graden, zodat blauw van de aanvraag niet het accent kan worden."""
    getal = int(hexkleur.lstrip("#"), 16)
    r, g, b = ((getal >> 16) & 255) / 255, ((getal >> 8) & 255) / 255, (getal & 255) / 255
    grootste, kleinste = max(r, g, b), min(r, g, b)
    if grootste == kleinste:
        return 0
    verschil = grootste - kleinste
    if grootste == r:
        hue = ((g - b) / verschil) % 6
    elif grootste == g:
        hue = (b - r) / verschil + 2
    else:
        hue = (r - g) / verschil + 4
    return hue * 60


def tintafstand(een, twee):
    verschil = abs(tint(een) - tint(twee))
    return min(verschil, 360 - verschil)


def gemengd(voor, achter, dekking):
    """Wat je overhoudt als tekst met een dekking op een dekkende kaart ligt."""
    def rgb(hexkleur):
        getal = int(hexkleur.lstrip("#"), 16)
        return (getal >> 16) & 255, (getal >> 8) & 255, getal & 255

    fv, fa = rgb(voor), rgb(achter)
    return "#{:02x}{:02x}{:02x}".format(*(
        round(dekking * fv[i] + (1 - dekking) * fa[i]) for i in range(3)
    ))


def test_de_lijst_zet_lopend_boven_aanvragen_en_afgerond_onderaan(post, db, client):
    """Nieuwste lopende en afgeronde klus bovenaan hun groep; de aanvraag die
    het langst wacht bovenaan de zijne. Terugzetten hoort weer bij aanvragen."""
    def aanvraag(naam, dagen_wachten=0):
        post("/klussen/nieuw", {"naam": naam, "aanvraag": "ja"})
        if dagen_wachten:
            db.execute(
                "UPDATE klussen SET aangevraagd_op=? WHERE naam=?",
                ((date.today() - timedelta(days=dagen_wachten)).isoformat(), naam),
            )
            db.commit()

    def lopend(naam):
        post("/klussen/nieuw", {"naam": naam})

    def afronden(naam):
        klus_id = db.execute(
            "SELECT id FROM klussen WHERE naam=?", (naam,)
        ).fetchone()[0]
        post(f"/klus/{klus_id}/status")

    lopend("Oude klus")
    lopend("Nieuwe klus")
    aanvraag("Wacht lang", 10)
    aanvraag("Wacht kort")
    aanvraag("Teruggezet")
    terug = db.execute(
        "SELECT id FROM klussen WHERE naam='Teruggezet'"
    ).fetchone()[0]
    post(f"/klus/{terug}/status", {"naar": "open"})
    post(f"/klus/{terug}/status", {"naar": "aangevraagd"})
    lopend("Oud af")
    afronden("Oud af")
    lopend("Nieuw af")
    afronden("Nieuw af")

    inhoud = client.get("/klussen").data.decode()
    assert koppen(inhoud) == ["Lopend", "Aanvragen", "Afgerond"]
    assert namen(inhoud) == [
        "Nieuwe klus", "Oude klus",
        "Wacht lang", "Wacht kort", "Teruggezet",
        "Nieuw af", "Oud af",
    ]
    # De kop staat bij zijn eigen kaarten, niet ergens los boven de pagina.
    assert inhoud.index(">Lopend</h2>") < inhoud.index(">Nieuwe klus<")
    assert inhoud.index(">Oude klus<") < inhoud.index(">Aanvragen</h2>")
    assert inhoud.index(">Aanvragen</h2>") < inhoud.index(">Wacht lang<")
    assert inhoud.index(">Teruggezet<") < inhoud.index(">Afgerond</h2>")
    assert inhoud.index(">Afgerond</h2>") < inhoud.index(">Nieuw af<")


def test_een_lege_groep_krijgt_geen_kop(post, client):
    post("/klussen/nieuw", {"naam": "Alleen deze"})
    inhoud = client.get("/klussen").data.decode()
    assert koppen(inhoud) == ["Lopend"]


def test_alleen_aanvragen_verbergt_de_andere_groepen(post, client):
    """Geen lege regel 'Klussen' meer onder de aanvragen."""
    post("/klussen/nieuw", {"naam": "Belletje", "aanvraag": "ja"})
    inhoud = client.get("/klussen").data.decode()
    assert koppen(inhoud) == ["Aanvragen"]
    assert "Nog geen klussen" not in inhoud


def test_zonder_klussen_blijft_de_lege_kaart(client):
    inhoud = client.get("/klussen").data.decode()
    assert "Nog geen klussen" in inhoud
    assert koppen(inhoud) == []


def test_afgeronde_tekst_dimt_en_de_knop_niet(post, db, client):
    """0,55 op de hele kaart zou Heropenen onder het contrast trekken."""
    post("/klussen/nieuw", {"naam": "Klaar", "uurtarief": "40"})
    klus_id = db.execute("SELECT id FROM klussen").fetchone()[0]
    post(f"/klus/{klus_id}/status")

    inhoud = client.get("/klussen").data.decode()
    kaart = inhoud.split('class="card klus afgerond"', 1)[1].split("</article>", 1)[0]
    assert 'class="naam gedimd"' in kaart
    assert "Heropenen" in kaart
    # De knop blijft buiten de gedimde tekst, anders wordt hij onleesbaar.
    knop = kaart.split("Heropenen", 1)[0].rsplit("<button", 1)[-1]
    assert "gedimd" not in knop

    css = css_van(inhoud)
    assert "opacity" not in regel_met(css, ".klus.afgerond")
    gedimd = regel_met(css, ".klus.afgerond .gedimd")
    assert "opacity: 0.55" in gedimd
    assert "#000" in gedimd


def test_gedimde_tekst_blijft_leesbaar_in_licht_en_donker(client):
    css = css_van(client.get("/klussen").data.decode())
    dekking = float(re.search(r"opacity:\s*([0-9.]+)", regel_met(
        css, ".klus.afgerond .gedimd")).group(1))
    kaart = hex_waarden(css, "--card")
    tekst = hex_waarden(css, "--text")
    # Licht: zwart op de kaart. Donker: de gewone tekstkleur, die is al licht.
    licht = gemengd("#000000", kaart[0], dekking)
    donker = gemengd(tekst[1], kaart[1], dekking)
    assert contrast(licht, kaart[0]) >= 4.5
    assert contrast(donker, kaart[1]) >= 4.5


def test_statuskleuren_zijn_groen_blauw_en_grijs(client):
    """Het accent van de app is ook blauw. De aanvraag wijkt daar bewust van af,
    anders valt de chip weg tegen de knoppen en tegen 'verzonden'."""
    css = css_van(client.get("/klussen").data.decode())
    assert "var(--status-lopend)" in regel_met(css, ".chip-open")
    assert "var(--status-aanvraag)" in regel_met(css, ".chip-aangevraagd")
    assert "var(--neutral)" in regel_met(css, ".chip-afgerond")
    # Dezelfde streep op de kaart, zodat een aanvraag in de lijst ook blauw is.
    assert "var(--status-lopend)" in regel_met(css, ".klus.lopend")
    assert "var(--status-aanvraag)" in regel_met(css, ".klus.aanvraag")
    assert "var(--neutral)" in regel_met(css, ".klus.afgerond")

    for thema in (0, 1):
        lopend = hex_waarden(css, "--status-lopend")[thema]
        lopend_vlak = hex_waarden(css, "--status-lopend-soft")[thema]
        aanvraag = hex_waarden(css, "--status-aanvraag")[thema]
        aanvraag_vlak = hex_waarden(css, "--status-aanvraag-soft")[thema]
        grijs = hex_waarden(css, "--neutral")[thema]
        grijs_vlak = hex_waarden(css, "--neutral-soft")[thema]
        accent = hex_waarden(css, "--primary")[thema]
        info = hex_waarden(css, "--info")[thema]

        assert contrast(lopend, lopend_vlak) >= 4.5
        assert contrast(aanvraag, aanvraag_vlak) >= 4.5
        assert contrast(grijs, grijs_vlak) >= 4.5
        assert 80 <= tint(lopend) <= 170
        assert tintafstand(aanvraag, accent) >= 20
        assert tintafstand(aanvraag, info) >= 20
        assert aanvraag.lower() != accent.lower()


def test_de_kluspagina_gebruikt_dezelfde_statuschip(post, db, client):
    post("/klussen/nieuw", {"naam": "Gevel", "aanvraag": "ja"})
    klus_id = db.execute("SELECT id FROM klussen").fetchone()[0]

    def chip(klasse):
        inhoud = client.get(f"/klus/{klus_id}").data.decode()
        assert f'class="chip {klasse}"' in inhoud

    chip("chip-aangevraagd")
    post(f"/klus/{klus_id}/status", {"naar": "open"})
    chip("chip-open")
    post(f"/klus/{klus_id}/status")
    chip("chip-afgerond")
