"""De opmaak op telefoonbreedte.

Deze regels zijn stuk voor stuk toegevoegd nadat er op een smal scherm iets buiten
beeld viel of een bedrag middenin afbrak. Een test kan geen pagina opmeten, maar wel
bewaken dat de regel die het verhelpt blijft staan — zonder die regel komt de fout
terug en ziet niemand het, want in een breed venster is er niets aan de hand.

Nagemeten met een venster van 320px en 390px breed: geen enkele pagina hoefde nog
horizontaal te schuiven.
"""
import re


def css_van(inhoud):
    """De stijlregels uit een gerenderde pagina, zonder de rest van de HTML."""
    return "\n".join(re.findall(r"<style>(.*?)</style>", inhoud, re.S))


def regel_met(css, kiezer):
    """Het blok achter een kiezer, of een lege tekst als hij er niet staat."""
    gevonden = re.search(re.escape(kiezer) + r"\s*\{([^}]*)\}", css)
    return gevonden.group(1) if gevonden else ""


def test_een_bedrag_in_de_overzichtsbalk_breekt_niet_na_het_euroteken(client):
    """"€ 1.407,75" stond anders met het bedrag op de volgende regel."""
    css = css_van(client.get("/").data.decode())
    assert "nowrap" in regel_met(css, ".overzicht b")


def test_het_label_voor_een_bedrag_valt_niet_uiteen(client):
    """"Te laat" werd "Te" op de ene regel en "laat" op de volgende."""
    css = css_van(client.get("/").data.decode())
    assert "nowrap" in regel_met(css, ".overzicht small:first-child")


def test_de_toelichting_achter_een_bedrag_mag_wel_afbreken(client):
    """Het is een hele zin ("2 rekeningen over de vervaldatum"); die op nowrap
    zetten zou hem juist het scherm uit duwen."""
    css = css_van(client.get("/").data.decode())
    assert "nowrap" not in regel_met(css, ".overzicht small")


def test_het_menu_hangt_op_een_telefoon_aan_de_knoppenrij(client):
    """Hing het aan de drie puntjes zelf, dan begon de lijst van 210px pas
    halverwege het scherm en liep hij er rechts uit."""
    css = css_van(client.get("/").data.decode())
    smal = css[css.index("@media (max-width: 620px)", css.index(".menu-lijst")):]
    assert "position: static" in regel_met(smal, ".menu")
    assert "left: 0" in regel_met(smal, ".menu-lijst")
    assert "right: 0" in regel_met(smal, ".menu-lijst")


def test_een_menu_item_mag_op_een_telefoon_over_twee_regels(client):
    """"Kopiëren naar nieuwe rekening" past niet op één regel in een menu dat niet
    breder is dan de kaart."""
    css = css_van(client.get("/").data.decode())
    smal = css[css.index("@media (max-width: 620px)", css.index(".menu-lijst")):]
    assert "white-space: normal" in regel_met(smal, ".menu-item")


def test_op_de_klantenlijst_breekt_alleen_het_woord_onder_het_bedrag(client):
    """Het bedrag blijft heel, maar "gefactureerd" eronder moet kunnen afbreken:
    met allebei op nowrap duwde de kaart zichzelf het scherm uit."""
    css = css_van(client.get("/klanten").data.decode())
    assert "nowrap" in regel_met(css, ".klant .cijfers b")
    assert "nowrap" not in regel_met(css, ".klant .cijfers")


def test_een_lang_e_mailadres_rekt_de_klantenlijst_niet_op(client):
    """Een adres is één onafbreekbaar woord en werd zo breed als het was."""
    css = css_van(client.get("/klanten").data.decode())
    assert "anywhere" in regel_met(css, ".klant .naam, .klant .contact")


def test_op_de_klantpagina_zakken_de_knoppen_naar_de_regel_eronder(db, client):
    """Bedrag, status en knoppen op één rij pasten niet op een smal toestel."""
    klant_id = db.execute("INSERT INTO klanten (naam) VALUES ('Jan Jansen')").lastrowid
    db.commit()
    css = css_van(client.get(f"/klant/{klant_id}").data.decode())
    assert "flex-wrap: wrap" in regel_met(css, ".rekening .rechts")
    assert "nowrap" in regel_met(css, ".rekening .bedrag")


def test_een_lang_e_mailadres_rekt_de_klantgegevens_niet_op(db, client):
    klant_id = db.execute("INSERT INTO klanten (naam) VALUES ('Jan Jansen')").lastrowid
    db.commit()
    css = css_van(client.get(f"/klant/{klant_id}").data.decode())
    assert "anywhere" in regel_met(css, ".gegevens dd")
