"""Materialen van een bon: wijzigen, het totaal dat ze leiden, en vastliggen
zodra de bon op een rekening staat (2.7.0)."""
import re

import pytest
from werkzeug.datastructures import MultiDict

from conftest import facturen
from test_bonherkenning import lezer  # noqa: F401  (fixture)


@pytest.fixture
def klus_id(post, db):
    post("/klussen/nieuw", {"naam": "Badkamer Voorbeeldstraat", "uurtarief": "45"})
    return db.execute("SELECT id FROM klussen").fetchone()[0]


def _bon_met_materialen(client, db, klus_id, bedrag="99"):
    client.post(f"/klus/{klus_id}/inkoop", data=MultiDict([
        ("csrf_token", "test-token"),
        ("omschrijving", "Bouwmarkt"),
        ("bedrag", bedrag),
        ("materiaal_omschrijving", "Tegellijm"),
        ("materiaal_aantal", "2"),
        ("materiaal_prijs", "12,50"),
        ("materiaal_omschrijving", "Voegmiddel"),
        ("materiaal_aantal", "1"),
        ("materiaal_prijs", "8"),
    ]))
    return db.execute("SELECT id FROM inkopen").fetchone()[0]


def _bedrag(db, inkoop_id):
    return db.execute("SELECT bedrag FROM inkopen WHERE id=?", (inkoop_id,)).fetchone()[0]


def _materialen(db, inkoop_id):
    return db.execute(
        "SELECT * FROM inkoop_materialen WHERE inkoop_id=? ORDER BY id", (inkoop_id,)
    ).fetchall()


def test_de_som_van_de_materialen_is_het_bedrag_van_de_bon(client, db, klus_id):
    # Het ingetypte totaal van 99 telt niet: de materialen zijn de prijs.
    inkoop_id = _bon_met_materialen(client, db, klus_id)
    assert _bedrag(db, inkoop_id) == 33.0


def test_zonder_materialen_blijft_het_ingetypte_totaal(post, db, klus_id):
    post(f"/klus/{klus_id}/inkoop", {"omschrijving": "Gamma", "bedrag": "24,95"})
    assert db.execute("SELECT bedrag FROM inkopen").fetchone()[0] == 24.95


def test_materiaal_erbij_en_weg_verandert_het_totaal(post, client, db, klus_id):
    inkoop_id = _bon_met_materialen(client, db, klus_id)
    post(f"/inkoop/{inkoop_id}/materiaal", {"omschrijving": "Kit", "aantal": "3", "prijs": "4,50"})
    assert _bedrag(db, inkoop_id) == 46.5

    kit = _materialen(db, inkoop_id)[-1]["id"]
    post(f"/inkoop_materiaal/{kit}/verwijder")
    assert _bedrag(db, inkoop_id) == 33.0


def test_een_teruggehaald_materiaal_telt_weer_mee(post, client, db, klus_id):
    inkoop_id = _bon_met_materialen(client, db, klus_id)
    voegmiddel = _materialen(db, inkoop_id)[1]["id"]
    post(f"/inkoop_materiaal/{voegmiddel}/verwijder")
    assert _bedrag(db, inkoop_id) == 25.0

    prullenbak_id = db.execute("SELECT id FROM prullenbak").fetchone()[0]
    post(f"/prullenbak/{prullenbak_id}/terug")
    assert _bedrag(db, inkoop_id) == 33.0


def test_materialen_wijzigen_via_bewerken(client, db, klus_id):
    inkoop_id = _bon_met_materialen(client, db, klus_id)
    lijm, voeg = (m["id"] for m in _materialen(db, inkoop_id))
    client.post(f"/inkoop/{inkoop_id}", data=MultiDict([
        ("csrf_token", "test-token"),
        ("omschrijving", "Bouwmarkt Venlo"),
        ("materiaal_id", str(lijm)),
        ("materiaal_omschrijving", "Flexlijm"),
        ("materiaal_aantal", "3"),
        ("materiaal_prijs", "10"),
        ("materiaal_id", str(voeg)),
        ("materiaal_omschrijving", "Voegmiddel"),
        ("materiaal_aantal", "1"),
        ("materiaal_prijs", "8"),
    ]))
    lijm_na = _materialen(db, inkoop_id)[0]
    assert (lijm_na["omschrijving"], lijm_na["aantal"], lijm_na["prijs"]) == ("Flexlijm", 3.0, 10.0)
    assert _bedrag(db, inkoop_id) == 38.0
    assert db.execute("SELECT omschrijving FROM inkopen").fetchone()[0] == "Bouwmarkt Venlo"


def test_bewerken_raakt_geen_materiaal_van_een_andere_bon(client, db, klus_id):
    eerste = _bon_met_materialen(client, db, klus_id)
    vreemd = _materialen(db, eerste)[0]["id"]
    client.post(f"/klus/{klus_id}/inkoop", data={
        "csrf_token": "test-token", "omschrijving": "Tweede", "bedrag": "5"})
    tweede = db.execute("SELECT MAX(id) FROM inkopen").fetchone()[0]
    client.post(f"/inkoop/{tweede}", data=MultiDict([
        ("csrf_token", "test-token"), ("omschrijving", "Tweede"), ("bedrag", "5"),
        ("materiaal_id", str(vreemd)), ("materiaal_omschrijving", "Gekaapt"),
        ("materiaal_aantal", "1"), ("materiaal_prijs", "0"),
    ]))
    assert _materialen(db, eerste)[0]["omschrijving"] == "Tegellijm"
    assert _bedrag(db, eerste) == 33.0


def test_een_leeg_gemaakte_omschrijving_verwijdert_niets(client, db, klus_id):
    inkoop_id = _bon_met_materialen(client, db, klus_id)
    lijm = _materialen(db, inkoop_id)[0]["id"]
    client.post(f"/inkoop/{inkoop_id}", data=MultiDict([
        ("csrf_token", "test-token"), ("omschrijving", "Bouwmarkt"),
        ("materiaal_id", str(lijm)), ("materiaal_omschrijving", "  "),
        ("materiaal_aantal", "1"), ("materiaal_prijs", "0"),
    ]))
    assert _materialen(db, inkoop_id)[0]["omschrijving"] == "Tegellijm"
    assert _bedrag(db, inkoop_id) == 33.0


def test_bewerken_zonder_totaalveld_laat_het_bedrag_staan(post, db, klus_id):
    # Een bon met materialen heeft geen totaalveld meer; dat mag geen 0 worden.
    post(f"/klus/{klus_id}/inkoop", {"omschrijving": "Gamma", "bedrag": "24,95"})
    inkoop_id = db.execute("SELECT id FROM inkopen").fetchone()[0]
    post(f"/inkoop/{inkoop_id}", {"omschrijving": "Gamma Venlo"})
    assert _bedrag(db, inkoop_id) == 24.95


def test_op_een_rekening_liggen_de_materialen_vast(post, client, db, klus_id):
    inkoop_id = _bon_met_materialen(client, db, klus_id)
    factuur_id = db.execute(
        "INSERT INTO facturen (nummer, datum, klant_naam) VALUES ('', '2026-11-14', 'Jan')"
    ).lastrowid
    db.execute("UPDATE inkopen SET factuur_id=? WHERE id=?", (factuur_id, inkoop_id))
    db.commit()
    lijm = _materialen(db, inkoop_id)[0]["id"]

    antwoord = post(f"/inkoop/{inkoop_id}/materiaal",
                    {"omschrijving": "Kit", "prijs": "4"}, follow_redirects=True)
    assert "staat op een rekening" in antwoord.data.decode()
    post(f"/inkoop_materiaal/{lijm}/verwijder")
    client.post(f"/inkoop/{inkoop_id}", data=MultiDict([
        ("csrf_token", "test-token"), ("omschrijving", "Bouwmarkt"),
        ("materiaal_id", str(lijm)), ("materiaal_omschrijving", "Anders"),
        ("materiaal_aantal", "9"), ("materiaal_prijs", "9"),
    ]))
    assert len(_materialen(db, inkoop_id)) == 2
    assert _materialen(db, inkoop_id)[0]["omschrijving"] == "Tegellijm"
    assert _bedrag(db, inkoop_id) == 33.0

    # Op de pagina geen prullenbakjes, geen + Materiaal en geen invulvelden.
    pagina = client.get(f"/klus/{klus_id}").data.decode()
    assert "Materiaalregel Tegellijm verwijderen" not in pagina
    assert f"mat-erbij-{inkoop_id}" not in pagina
    assert "De materialen liggen vast" in pagina


def test_bij_het_starten_wordt_een_oude_bon_herrekend(db, klus_id):
    # Tot 2.7.0 waren materialen een notitie en stond er een eigen totaal.
    los = db.execute(
        "INSERT INTO inkopen (klus_id, omschrijving, bedrag, toegevoegd) "
        "VALUES (?, 'Los', 50, '2026-11-01')", (klus_id,)).lastrowid
    factuur_id = db.execute(
        "INSERT INTO facturen (nummer, datum, klant_naam) VALUES ('2026-001', '2026-11-01', 'Jan')"
    ).lastrowid
    gefactureerd = db.execute(
        "INSERT INTO inkopen (klus_id, omschrijving, bedrag, toegevoegd, factuur_id) "
        "VALUES (?, 'Gefactureerd', 50, '2026-11-01', ?)", (klus_id, factuur_id)).lastrowid
    for bon in (los, gefactureerd):
        db.execute("INSERT INTO inkoop_materialen (inkoop_id, omschrijving, aantal, prijs) "
                   "VALUES (?, 'Kit', 3, 4.5)", (bon,))
    db.commit()

    facturen.init_db()

    assert _bedrag(db, los) == 13.5
    # Die rekening is met het oude bedrag gemaakt.
    assert _bedrag(db, gefactureerd) == 50.0


def test_materiaal_toevoegen_zit_achter_een_knop_met_bewaren(client, db, klus_id):
    inkoop_id = _bon_met_materialen(client, db, klus_id)
    pagina = client.get(f"/klus/{klus_id}").data.decode()
    form = re.search(rf'<form[^>]*id="mat-erbij-{inkoop_id}".*?</form>', pagina, re.S).group(0)
    assert 'class="btn btn-outline">Bewaren</button>' in form
    assert f'data-mat-open="mat-erbij-{inkoop_id}"' in pagina
    assert "html.js .invoerrij .mat-toevoegen:not(.is-open) { display: none; }" in pagina


def test_het_bewerkformulier_toont_de_materialen(client, db, klus_id):
    inkoop_id = _bon_met_materialen(client, db, klus_id)
    pagina = client.get(f"/klus/{klus_id}").data.decode()
    form = re.search(rf'<form[^>]*id="inkoop-{inkoop_id}-form".*?</form>', pagina, re.S).group(0)
    assert form.count('name="materiaal_id"') == 2
    assert 'value="Tegellijm"' in form
    # Het totaal heeft geen naam: de server rekent het zelf uit.
    assert 'name="bedrag"' not in form
    assert 'data-materiaal-totaal="altijd"' in form


def test_de_takenkaart_heeft_een_plusknop_en_lijntjes(client, klus_id):
    pagina = client.get(f"/klus/{klus_id}").data.decode()
    kaart = pagina[pagina.index("<h2>Taken</h2>"):pagina.index('id="taken"')]
    assert "+ Taak</button>" in kaart
    assert 'class="taak-nieuw invoerform"' in pagina
    assert "Nog geen taken bij deze klus." in pagina
    assert ".takenlijst > .invoerform { display: none; }" in pagina


def test_een_verschil_met_het_totaal_op_de_bon_geeft_een_waarschuwing(client, klus_id):
    """Korting staat als losse regel op een bon. Neem je alleen de artikelen
    over, dan zijn de materialen samen meer dan je betaald hebt."""
    from browser import meetbrowser, playwright_verplicht
    html = client.get(f"/klus/{klus_id}").data.decode()
    with playwright_verplicht()() as p:
        browser = meetbrowser(p)
        pagina = browser.new_page()
        try:
            pagina.set_content(html)
            kaart = pagina.locator(".invoerkaart:has(#inkoop-bedrag)")
            kaart.locator(".invoer-plus").click()
            pagina.fill("#inkoop-bedrag", "30")
            pagina.click("#materiaal-erbij")
            rij = pagina.locator("#inkoop-materialen .materiaal-rij").last
            rij.locator("[name=materiaal_prijs]").fill("33")
            rij.locator("[name=materiaal_omschrijving]").fill("Tegellijm")
            waarschuwing = kaart.locator("[data-materiaal-verschil]")
            assert waarschuwing.is_visible()
            assert "€ 30,00" in waarschuwing.inner_text()
            assert "€ 33,00" in waarschuwing.inner_text()
            assert pagina.input_value("#inkoop-bedrag") == "33.00"

            # Met de korting erbij klopt het weer.
            pagina.click("#materiaal-erbij")
            korting = pagina.locator("#inkoop-materialen .materiaal-rij").last
            korting.locator("[name=materiaal_prijs]").fill("-3")
            korting.locator("[name=materiaal_omschrijving]").fill("Korting")
            assert not waarschuwing.is_visible()
        finally:
            browser.close()


def test_de_log_houdt_het_totaal_op_de_bon(client, post, db, lezer):
    """Het bedrag van de bon is de som; de log is er voor de lezer, en die
    wordt afgerekend op het totaal dat op de bon staat."""
    import json
    import os
    from test_bonherkenning import klus_erin, scan_erin
    if os.path.isfile(facturen.CORRECTIE_LOG):
        os.remove(facturen.CORRECTIE_LOG)
    klus = klus_erin(db)
    scan_id = scan_erin(client, post, db)
    facturen._wacht_op_herkenning()
    post(f"/scans/{scan_id}/bon", {
        "klus_id": klus, "omschrijving": "Karwei", "wanneer": "2026-10-02",
        "bedrag": "21,00", "bon_totaal": "19,87",
        "materiaal_omschrijving": ["Schroef"], "materiaal_aantal": ["3"],
        "materiaal_prijs": ["7"],
    })
    with open(facturen.CORRECTIE_LOG, encoding="utf-8") as bron:
        regel = json.loads(bron.readline())
    os.remove(facturen.CORRECTIE_LOG)
    assert regel["bewaard"]["bedrag"] == 19.87
    assert db.execute("SELECT bedrag FROM inkopen").fetchone()[0] == 21.0
