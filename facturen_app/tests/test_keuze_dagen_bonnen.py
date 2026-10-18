"""Dagen en bonnen per stuk op de rekening, of allemaal tegelijk.

Een dag of bon die al op een rekening staat hoort niet meer in de lijst.
Haal je hem eraf, dan komt hij terug. Twee rekeningen kunnen hem niet allebei
hebben.
"""
import html
import json
import re

import pytest

from conftest import facturen
from test_pdf import tekst_uit_pdf


def _klant_en_klus(post, db, tarief="45"):
    post("/klanten/nieuw", {"naam": "Test Klant BV"})
    klant_id = db.execute("SELECT id FROM klanten").fetchone()[0]
    post("/klussen/nieuw", {
        "naam": "Gevel", "uurtarief": tarief, "klant_id": str(klant_id),
    })
    klus_id = db.execute("SELECT id FROM klussen").fetchone()[0]
    return klant_id, klus_id


def _dagen_in_menu(pagina, klus_id):
    """De dagen die de keuzeknop van deze klus meestuurt."""
    for knop in re.findall(
        r'<button type="button" class="menu-item klus-keuze".*?</button>',
        pagina, re.S,
    ):
        if f'data-klus="{klus_id}"' not in knop:
            continue
        ruw = re.search(r'data-dagen="([^"]*)"', knop)
        assert ruw, knop
        return json.loads(html.unescape(ruw.group(1)))
    raise AssertionError(f"geen klus {klus_id} in het menu")


def _bon_knop(pagina, naam):
    plek = pagina.find(f'data-naam="{naam}"')
    assert plek != -1, naam
    begin = pagina.rfind("<button", 0, plek)
    einde = pagina.find(">", plek)
    return pagina[begin:einde]


def test_elke_gesloten_dag_staat_apart_in_de_lijst(post, db, client):
    """Drie dicht, één nog bezig. Alleen de drie zijn te kiezen, per dag."""
    klant_id, klus_id = _klant_en_klus(post, db)
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-12", "van": "07:00", "tot": "14:20"})
    post(f"/klus/{klus_id}/dag", {
        "datum": "2026-10-13", "van": "08:00", "tot": "11:30", "pauze": "0",
    })
    post(f"/klus/{klus_id}/dag", {
        "datum": "2026-10-14", "van": "09:00", "tot": "17:00", "pauze": "30",
        "notitie": "voegen",
    })
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-15", "van": "06:45"})
    pagina = client.get(f"/nieuw?klant={klant_id}").data.decode()
    assert 'id="alle-dagen"' in pagina
    assert "Alle dagen toevoegen" in pagina
    dagen = _dagen_in_menu(pagina, klus_id)
    teksten = [dag["omschrijving"] for dag in dagen]
    assert any("07:00–14:20" in tekst for tekst in teksten)
    assert any("08:00–11:30" in tekst for tekst in teksten)
    assert any("(pauze 30 min)" in tekst and "voegen" in tekst for tekst in teksten)
    assert all("06:45" not in tekst for tekst in teksten)
    assert all(not dag["op_deze"] for dag in dagen)
    # Geen duur tussen haakjes naast het aantal.
    assert "7 u 20 min" not in pagina
    assert "3 u 30 min" not in pagina


def test_een_dag_erbij_en_alle_dagen_slaan_elk_een_eigen_regel_op(post, db, client):
    klant_id, klus_id = _klant_en_klus(post, db, tarief="45.55")
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-12", "van": "07:00", "tot": "14:20"})
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-13", "van": "08:00", "tot": "12:00"})
    dagen = db.execute("SELECT id, datum FROM uren ORDER BY datum").fetchall()
    eerste, tweede = dagen
    post("/nieuw", {
        "klant_naam": "Test Klant BV", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": "ma 12 okt 2026, 07:00–14:20 · Gevel",
        "type": "arbeid_uur", "aantal": "7.33", "prijs": "45.55",
        "regel_klus": str(klus_id), "regel_uur": str(eerste["id"]),
    })
    assert db.execute("SELECT COUNT(*) FROM regels").fetchone()[0] == 1
    assert db.execute("SELECT subtotaal FROM regels").fetchone()[0] == 334.03
    # De gezette dag is weg uit de lijst, de andere blijft.
    pagina = client.get(f"/nieuw?klant={klant_id}").data.decode()
    over = _dagen_in_menu(pagina, klus_id)
    assert [dag["id"] for dag in over] == [tweede["id"]]

    post("/nieuw", {
        "klant_naam": "Test Klant BV", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": "di 13 okt 2026, 08:00–12:00 · Gevel",
        "type": "arbeid_uur", "aantal": "4", "prijs": "45.55",
        "regel_klus": str(klus_id), "regel_uur": str(tweede["id"]),
    })
    leeg = client.get(f"/nieuw?klant={klant_id}").data.decode()
    assert f'data-klus="{klus_id}"' not in leeg


def test_dag_komt_terug_als_de_regel_eraf_gaat(post, db, client):
    klant_id, klus_id = _klant_en_klus(post, db)
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-12", "van": "08:00", "tot": "12:00"})
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-13", "van": "08:00", "tot": "16:00"})
    dagen = db.execute("SELECT id FROM uren ORDER BY datum").fetchall()
    post("/nieuw", {
        "klant_naam": "Test Klant BV", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": ["eerste", "tweede"],
        "type": ["arbeid_uur", "arbeid_uur"],
        "aantal": ["4", "8"], "prijs": ["45", "45"],
        "regel_klus": [str(klus_id), str(klus_id)],
        "regel_uur": [str(dagen[0]["id"]), str(dagen[1]["id"])],
    })
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    # Op déze rekening blijven ze in de data, zodat het script ze kan terughalen
    # als je de regel wegklikt vóór het opslaan.
    bewerkt = client.get(f"/factuur/{factuur_id}/bewerk").data.decode()
    ids = {dag["id"] for dag in _dagen_in_menu(bewerkt, klus_id)}
    assert dagen[0]["id"] in ids and dagen[1]["id"] in ids
    assert all(dag["op_deze"] for dag in _dagen_in_menu(bewerkt, klus_id))

    post(f"/factuur/{factuur_id}/bewerk", {
        "klant_naam": "Test Klant BV", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": "tweede", "type": "arbeid_uur", "aantal": "8", "prijs": "45",
        "regel_klus": str(klus_id), "regel_uur": str(dagen[1]["id"]),
    })
    terug = client.get(f"/nieuw?klant={klant_id}").data.decode()
    over = _dagen_in_menu(terug, klus_id)
    assert [dag["id"] for dag in over] == [dagen[0]["id"]]
    assert db.execute(
        "SELECT factuur_id FROM uren WHERE id=?", (dagen[0]["id"],)
    ).fetchone()[0] is None


def test_dezelfde_dag_op_twee_rekeningen_wordt_geweigerd(post, db):
    klant_id, klus_id = _klant_en_klus(post, db)
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-12", "van": "08:00", "tot": "12:00"})
    uur_id = db.execute("SELECT id FROM uren").fetchone()[0]
    formulier = {
        "klant_naam": "Test Klant BV", "klant_id": str(klant_id),
        "klant_adres": "Dorpsstraat 1", "datum": "2026-10-16",
        "kenmerk": "eerste scherm",
        "omschrijving": ["dag", "kit"],
        "type": ["arbeid_uur", "materiaal"],
        "aantal": ["4", "2"], "prijs": ["45", "12.50"],
        "regel_klus": [str(klus_id), ""],
        "regel_uur": [str(uur_id), ""],
    }
    post("/nieuw", formulier)
    assert db.execute("SELECT COUNT(*) FROM facturen").fetchone()[0] == 1
    tweede = post("/nieuw", formulier)
    tekst = tweede.data.decode()
    assert "De dag van 12 okt 2026 staat al op" in tekst
    assert "niet nog een keer" in tekst
    assert "niet aangemaakt" in tekst
    assert 'value="Test Klant BV"' in tekst
    assert 'value="kit"' in tekst
    assert 'value="dag"' not in tekst
    assert db.execute("SELECT COUNT(*) FROM facturen").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM regels").fetchone()[0] == 2


def test_bonnen_per_stuk_en_de_lijst_zonder_wat_al_op_een_rekening_staat(post, db, client):
    klant_id, klus_id = _klant_en_klus(post, db)
    post(f"/klus/{klus_id}/inkoop", {"omschrijving": "Kitvoeg", "bedrag": "12.50"})
    post(f"/klus/{klus_id}/inkoop", {"omschrijving": "Buis 32 mm", "bedrag": "8"})
    pagina = client.get(f"/nieuw?klant={klant_id}").data.decode()
    assert 'id="alle-bonnen"' in pagina
    assert "Alle bonnen toevoegen" in pagina
    assert "hidden" not in _bon_knop(pagina, "Kitvoeg")
    assert "hidden" not in _bon_knop(pagina, "Buis 32 mm")
    # Zichtbare tekst met een komma. data-bedrag houdt de punt: dat leest het
    # script als getal, de gebruiker ziet 12,50.
    start = pagina.find(_bon_knop(pagina, "Kitvoeg"))
    zichtbaar = pagina[pagina.find(">", start) + 1:pagina.find("</button>", start)]
    assert "12,50" in zichtbaar
    assert "12.50" not in zichtbaar

    kit = db.execute(
        "SELECT id FROM inkopen WHERE omschrijving='Kitvoeg'"
    ).fetchone()[0]
    post("/nieuw", {
        "klant_naam": "Test Klant BV", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": "Kitvoeg", "type": "bon", "aantal": "1", "prijs": "12.50",
        "regel_inkoop": str(kit),
    })
    daarna = client.get(f"/nieuw?klant={klant_id}").data.decode()
    assert 'data-naam="Kitvoeg"' not in daarna
    assert "hidden" not in _bon_knop(daarna, "Buis 32 mm")

    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    bewerkt = client.get(f"/factuur/{factuur_id}/bewerk").data.decode()
    # Op deze rekening blijft de bon in de pagina, verborgen zolang de regel er is.
    assert "hidden" in _bon_knop(bewerkt, "Kitvoeg")
    post(f"/factuur/{factuur_id}/bewerk", {
        "klant_naam": "Test Klant BV", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": "losse regel", "type": "materiaal", "aantal": "1", "prijs": "3",
    })
    terug = client.get(f"/nieuw?klant={klant_id}").data.decode()
    assert "hidden" not in _bon_knop(terug, "Kitvoeg")
    assert db.execute(
        "SELECT factuur_id FROM inkopen WHERE id=?", (kit,)
    ).fetchone()[0] is None


def test_dezelfde_bon_op_twee_rekeningen_wordt_geweigerd(post, db):
    klant_id, klus_id = _klant_en_klus(post, db)
    post(f"/klus/{klus_id}/inkoop", {"omschrijving": "Kitvoeg", "bedrag": "12.50"})
    kit = db.execute("SELECT id FROM inkopen").fetchone()[0]
    formulier = {
        "klant_naam": "Test Klant BV", "klant_id": str(klant_id),
        "datum": "2026-10-16", "kenmerk": "bonnen",
        "omschrijving": ["Kitvoeg", "eigen buis"],
        "type": ["bon", "materiaal"],
        "aantal": ["1", "3"], "prijs": ["12.50", "8"],
        "regel_inkoop": [str(kit), ""],
    }
    post("/nieuw", formulier)
    tweede = post("/nieuw", formulier)
    tekst = tweede.data.decode()
    assert "Kitvoeg" in tekst
    assert "niet nog een keer" in tekst
    assert "niet aangemaakt" in tekst
    assert 'value="eigen buis"' in tekst
    assert 'value="Kitvoeg"' not in tekst
    assert db.execute("SELECT COUNT(*) FROM facturen").fetchone()[0] == 1
    assert db.execute(
        "SELECT factuur_id FROM inkopen WHERE id=?", (kit,)
    ).fetchone()[0] == db.execute("SELECT id FROM facturen").fetchone()[0]


def test_bewerken_weigert_een_bon_die_net_elders_is_gezet(post, db):
    """Het scherm van de open rekening blijft gevuld; er wordt niets overschreven."""
    klant_id, klus_id = _klant_en_klus(post, db)
    post(f"/klus/{klus_id}/inkoop", {"omschrijving": "Kitvoeg", "bedrag": "12.50"})
    kit = db.execute("SELECT id FROM inkopen").fetchone()[0]
    post("/nieuw", {
        "klant_naam": "Test Klant BV", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": "eigen buis", "type": "materiaal", "aantal": "1", "prijs": "8",
    })
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    totaal = db.execute("SELECT totaal FROM facturen").fetchone()[0]
    post("/nieuw", {
        "klant_naam": "Test Klant BV", "klant_id": str(klant_id), "datum": "2026-10-17",
        "omschrijving": "Kitvoeg", "type": "bon", "aantal": "1", "prijs": "12.50",
        "regel_inkoop": str(kit),
    })
    antwoord = post(f"/factuur/{factuur_id}/bewerk", {
        "klant_naam": "Test Klant BV", "klant_id": str(klant_id),
        "datum": "2026-10-16", "kenmerk": "blijft staan",
        "omschrijving": ["Kitvoeg", "eigen buis"],
        "type": ["bon", "materiaal"],
        "aantal": ["1", "2"], "prijs": ["12.50", "8"],
        "regel_inkoop": [str(kit), ""],
    })
    tekst = antwoord.data.decode()
    assert "niet nog een keer" in tekst
    assert "niets gewijzigd" in tekst
    assert 'value="eigen buis"' in tekst
    assert 'value="Kitvoeg"' not in tekst
    assert 'value="blijft staan"' in tekst
    assert db.execute(
        "SELECT totaal FROM facturen WHERE id=?", (factuur_id,)
    ).fetchone()[0] == totaal
    assert db.execute(
        "SELECT COUNT(*) FROM regels WHERE factuur_id=?", (factuur_id,)
    ).fetchone()[0] == 1


def _tabel_zonder_unieke_bon(db):
    """regels zoals in 1.36: de dag is al uniek, de bon nog niet."""
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute("ALTER TABLE regels RENAME TO regels_los")
    db.execute(
        """CREATE TABLE regels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            factuur_id INTEGER NOT NULL,
            omschrijving TEXT NOT NULL,
            type TEXT NOT NULL,
            aantal REAL NOT NULL,
            prijs REAL NOT NULL,
            subtotaal REAL NOT NULL,
            klus_id INTEGER,
            inkoop_id INTEGER,
            uur_id INTEGER UNIQUE,
            FOREIGN KEY (factuur_id) REFERENCES facturen (id),
            FOREIGN KEY (uur_id) REFERENCES uren (id) ON DELETE SET NULL
        )"""
    )
    db.execute("DROP TABLE regels_los")
    db.commit()


def test_migratie_bewaart_beide_bedragen_bij_een_dubbele_bon(db):
    _tabel_zonder_unieke_bon(db)
    klus_id = db.execute(
        "INSERT INTO klussen (naam, uurtarief, gestart) VALUES ('Gevel', 45, '2026-10-01')"
    ).lastrowid
    eerste = db.execute(
        """INSERT INTO facturen (nummer, datum, klant_naam, status, totaal)
           VALUES ('2026-001', '2026-10-16', 'Test Klant BV', 'concept', 11.11)"""
    ).lastrowid
    tweede = db.execute(
        """INSERT INTO facturen (nummer, datum, klant_naam, status, totaal)
           VALUES ('2026-002', '2026-10-16', 'Test Klant BV', 'concept', 22.22)"""
    ).lastrowid
    bon_id = db.execute(
        """INSERT INTO inkopen (klus_id, omschrijving, bedrag, toegevoegd, factuur_id)
           VALUES (?, 'Kitvoeg', 22.22, '2026-10-16', ?)""",
        (klus_id, tweede),
    ).lastrowid
    db.execute(
        """INSERT INTO regels
           (factuur_id, omschrijving, type, aantal, prijs, subtotaal, inkoop_id)
           VALUES (?, 'eerste', 'bon', 1, 11.11, 11.11, ?)""",
        (eerste, bon_id),
    )
    db.execute(
        """INSERT INTO regels
           (factuur_id, omschrijving, type, aantal, prijs, subtotaal, inkoop_id)
           VALUES (?, 'tweede', 'bon', 1, 22.22, 22.22, ?)""",
        (tweede, bon_id),
    )
    db.commit()

    facturen._bon_koppeling_uniek(db)
    db.execute("PRAGMA foreign_keys=ON")
    db.commit()

    gekoppeld = db.execute(
        "SELECT factuur_id, subtotaal FROM regels WHERE inkoop_id=?", (bon_id,)
    ).fetchone()
    assert gekoppeld["factuur_id"] == tweede
    assert gekoppeld["subtotaal"] == 22.22
    los = db.execute(
        "SELECT inkoop_id, subtotaal FROM regels WHERE factuur_id=?", (eerste,)
    ).fetchone()
    assert los["inkoop_id"] is None
    assert los["subtotaal"] == 11.11
    assert facturen._bon_koppeling_al_uniek(db)
    waarschuwing = db.execute(
        "SELECT tekst FROM rekening_waarschuwing WHERE factuur_id=?", (eerste,)
    ).fetchone()[0]
    assert "Kitvoeg" in waarschuwing
    assert "11,11" in waarschuwing

    db.execute("DELETE FROM inkopen WHERE id=?", (bon_id,))
    db.commit()
    na_weg = db.execute(
        "SELECT inkoop_id, subtotaal FROM regels WHERE factuur_id=?", (tweede,)
    ).fetchone()
    assert na_weg["inkoop_id"] is None
    assert na_weg["subtotaal"] == 22.22


@pytest.mark.parametrize("stap", ["insert", "drop", "rename"])
def test_crash_tijdens_de_bonmigratie_rolt_terug(db, stap):
    _tabel_zonder_unieke_bon(db)
    factuur_id = db.execute(
        """INSERT INTO facturen (nummer, datum, klant_naam, status, totaal)
           VALUES ('2026-040', '2026-10-16', 'Test Klant BV', 'definitief', 18.50)"""
    ).lastrowid
    db.execute(
        """INSERT INTO regels
           (factuur_id, omschrijving, type, aantal, prijs, subtotaal)
           VALUES (?, 'oude bon', 'bon', 1, 18.50, 18.50)""",
        (factuur_id,),
    )
    db.commit()
    facturen._MIGRATIE_STAP = stap
    try:
        with pytest.raises(facturen.MigratieAfgebroken):
            facturen._bon_koppeling_uniek(db)
    finally:
        facturen._MIGRATIE_STAP = None

    assert "regels_nieuw" not in facturen._tabelnamen(db)
    assert db.execute("SELECT subtotaal FROM regels").fetchone()[0] == 18.50
    facturen._bon_koppeling_uniek(db)
    assert facturen._bon_koppeling_al_uniek(db)
    assert db.execute("SELECT subtotaal FROM regels").fetchone()[0] == 18.50


def test_terughalen_zet_de_bon_weer_op_de_regel(post, db):
    klant_id, klus_id = _klant_en_klus(post, db)
    post(f"/klus/{klus_id}/inkoop", {"omschrijving": "Kitvoeg", "bedrag": "12.50"})
    kit = db.execute("SELECT id FROM inkopen").fetchone()[0]
    post("/nieuw", {
        "klant_naam": "Test Klant BV", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": "Kitvoeg", "type": "bon", "aantal": "1", "prijs": "12.50",
        "regel_inkoop": str(kit),
    })
    regel_id = db.execute("SELECT id FROM regels").fetchone()[0]
    post(f"/inkoop/{kit}/verwijder")
    assert db.execute("SELECT inkoop_id FROM regels").fetchone()[0] is None
    prul = db.execute("SELECT id FROM prullenbak ORDER BY id DESC").fetchone()[0]
    post(f"/prullenbak/{prul}/terug")
    assert db.execute("SELECT inkoop_id FROM regels WHERE id=?", (regel_id,)).fetchone()[0] == kit


def test_pdf_van_dagen_en_een_bon_noemt_de_tijden_niet_de_duur(post, db):
    from test_pdf import zet_bedrijf
    zet_bedrijf(db)
    klant_id, klus_id = _klant_en_klus(post, db, tarief="45.55")
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-12", "van": "07:00", "tot": "14:20"})
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-13", "van": "08:00", "tot": "11:30"})
    post(f"/klus/{klus_id}/inkoop", {"omschrijving": "Kitvoeg", "bedrag": "12.50"})
    dagen = db.execute("SELECT id FROM uren ORDER BY datum").fetchall()
    kit = db.execute("SELECT id FROM inkopen").fetchone()[0]
    post("/nieuw", {
        "klant_naam": "Test Klant BV", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": [
            "ma 12 okt 2026, 07:00–14:20 · Gevel",
            "di 13 okt 2026, 08:00–11:30 · Gevel",
            "Kitvoeg",
        ],
        "type": ["arbeid_uur", "arbeid_uur", "bon"],
        "aantal": ["7.33", "3.5", "1"],
        "prijs": ["45.55", "45.55", "12.50"],
        "regel_klus": [str(klus_id), str(klus_id), ""],
        "regel_uur": [str(dagen[0]["id"]), str(dagen[1]["id"]), ""],
        "regel_inkoop": ["", "", str(kit)],
    })
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    tekst = tekst_uit_pdf(facturen.maak_pdf(factuur_id))
    assert "7,33 u" in tekst
    assert "3,5 u" in tekst
    assert "07:00" in tekst and "14:20" in tekst
    assert "08:00" in tekst and "11:30" in tekst
    assert "Kitvoeg" in tekst
    assert "7 u 20 min" not in tekst
    assert "3 u 30 min" not in tekst
    assert "334,03" in tekst


def test_kiezen_in_de_lijst_haalt_een_dag_en_een_bon_uit_het_menu(post, db, client):
    """Met script: één dag, daarna de rest, en een bon verdwijnt zodra hij erop staat."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    klant_id, klus_id = _klant_en_klus(post, db)
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-12", "van": "08:00", "tot": "12:00"})
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-13", "van": "09:00", "tot": "13:00"})
    post(f"/klus/{klus_id}/inkoop", {"omschrijving": "Kitvoeg", "bedrag": "12.50"})
    post(f"/klus/{klus_id}/inkoop", {"omschrijving": "Buis 32 mm", "bedrag": "8"})
    html_pagina = client.get(f"/nieuw?klant={klant_id}").data.decode()
    with sync_playwright() as p:
        browser = p.webkit.launch()
        pagina = browser.new_page()
        pagina.set_content(html_pagina)
        pagina.locator("summary", has_text="Dagen van een klus").click()
        pagina.locator(".klus-keuze").click()
        assert pagina.locator(".dag-keuze").count() == 2
        assert "08:00–12:00" in pagina.locator("#dag-lijst").inner_text()
        pagina.locator(".dag-keuze").first.click()
        assert pagina.locator(".dag-keuze").count() == 1
        assert pagina.locator('[name="regel_uur"]').nth(0).input_value()
        pagina.locator("#alle-dagen").click()
        assert pagina.locator(".dag-keuze").count() == 0
        assert "staan al op de rekening" in pagina.locator("#dag-leeg").inner_text()
        pagina.locator("summary", has_text="Bon van een klus").click()
        pagina.locator("#alle-bonnen").click()
        assert pagina.locator(".bon-keuze:visible").count() == 0
        # De bonnenregel weghalen zet hem terug in de lijst.
        bonregel = pagina.locator(".regel", has_text="Kitvoeg")
        bonregel.locator(".weg").click()
        assert pagina.locator('.bon-keuze:visible', has_text="Kitvoeg").count() == 1
        browser.close()
