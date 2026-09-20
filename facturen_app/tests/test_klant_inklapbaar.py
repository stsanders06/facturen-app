"""Bij een bestaande klant klappen de detailvelden in op rekening- en offerteformulier."""


def test_nieuw_formulier_heeft_inklapbare_klantgegevens(client):
    inhoud = client.get("/nieuw").data.decode()
    assert 'id="klant-gegevens"' in inhoud
    assert "Gegevens bewerken" in inhoud
    assert 'id="klant-samenvatting"' in inhoud


def test_bewerken_met_klant_id_toont_samenvatting_chrome(post, client, db, maak_factuur):
    post("/klanten/nieuw", {
        "naam": "Jan Jansen",
        "adres": "Kerkstraat 1\n5900 AA Venlo",
        "email": "jan@example.com",
    })
    klant_id = db.execute("SELECT id FROM klanten WHERE naam='Jan Jansen'").fetchone()[0]
    factuur_id = maak_factuur()
    db.execute("UPDATE facturen SET klant_id=? WHERE id=?", (klant_id, factuur_id))
    db.commit()

    inhoud = client.get(f"/factuur/{factuur_id}/bewerk").data.decode()
    assert 'id="klant-gegevens"' in inhoud
    assert "Gegevens bewerken" in inhoud
    assert f'value="{klant_id}"' in inhoud
    assert "selected" in inhoud
    # De velden blijven in de HTML staan (ingevuld), zodat POST met required blijft werken.
    assert 'id="klant_naam"' in inhoud
    assert 'name="klant_naam"' in inhoud
    assert "Jan Jansen" in inhoud


def test_nieuw_met_gekozen_klant_heeft_zelfde_structuur(post, client, db):
    post("/klanten/nieuw", {"naam": "Piet Peters", "email": "piet@example.com"})
    klant_id = db.execute("SELECT id FROM klanten WHERE naam='Piet Peters'").fetchone()[0]

    inhoud = client.get(f"/nieuw?klant={klant_id}").data.decode()
    assert 'id="klant-gegevens"' in inhoud
    assert "Gegevens bewerken" in inhoud
    assert f'value="{klant_id}"' in inhoud
    assert "Piet Peters" in inhoud
