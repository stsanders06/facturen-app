"""De klantpagina: alles van één klant bij elkaar."""
from datetime import date, timedelta


def dagen_geleden(aantal):
    return (date.today() - timedelta(days=aantal)).isoformat()


def klant_met_rekening(db, **velden):
    klant_id = db.execute("INSERT INTO klanten (naam, email) VALUES ('Jan Jansen', 'jan@example.com')").lastrowid
    velden.setdefault("nummer", "2026-001")
    velden.setdefault("datum", date.today().isoformat())
    velden.setdefault("totaal", 100.0)
    velden.setdefault("status", "verzonden")
    if "vervalt_op" not in velden:
        # Standaardtermijn, zodat te-laat-testen blijven werken.
        velden["vervalt_op"] = (
            date.fromisoformat(velden["datum"]) + timedelta(days=14)
        ).isoformat()
    factuur_id = db.execute(
        """INSERT INTO facturen (nummer, datum, vervalt_op, klant_id, klant_naam, status,
           totaal) VALUES (?, ?, ?, ?, 'Jan Jansen', ?, ?)""",
        (velden["nummer"], velden["datum"], velden["vervalt_op"], klant_id,
         velden["status"], velden["totaal"]),
    ).lastrowid
    db.commit()
    return klant_id, factuur_id


def test_de_cijfers_kloppen(db, client):
    klant_id, _ = klant_met_rekening(db, totaal=250.0)
    inhoud = client.get(f"/klant/{klant_id}").data.decode()
    assert "250,00" in inhoud
    assert "Gefactureerd" in inhoud
    assert "Openstaand" in inhoud


def test_openstaand_trekt_een_deelbetaling_af(db, client, post):
    klant_id, factuur_id = klant_met_rekening(db, totaal=100.0)
    post(f"/factuur/{factuur_id}/betalingen", {"datum": dagen_geleden(1), "bedrag": "40"})

    inhoud = client.get(f"/klant/{klant_id}").data.decode()
    assert "60,00" in inhoud
    assert "40,00 binnen" in inhoud


def test_te_laat_krijgt_een_eigen_tegel(db, client):
    klant_id, _ = klant_met_rekening(db, datum=dagen_geleden(30))
    inhoud = client.get(f"/klant/{klant_id}").data.decode()
    assert "Te laat" in inhoud


def test_zonder_achterstand_geen_tegel_te_laat(db, client):
    klant_id, _ = klant_met_rekening(db, datum=dagen_geleden(1))
    assert "Te laat" not in client.get(f"/klant/{klant_id}").data.decode()


def test_uitstaande_offertes_staan_erbij(db, client):
    klant_id, _ = klant_met_rekening(db)
    db.execute(
        """INSERT INTO offertes (nummer, datum, klant_id, klant_naam, status, totaal)
           VALUES ('OFF-2026-001', ?, ?, 'Jan Jansen', 'verzonden', 780.0)""",
        (date.today().isoformat(), klant_id),
    )
    db.commit()
    inhoud = client.get(f"/klant/{klant_id}").data.decode()
    assert "Uitstaande offertes" in inhoud
    assert "780,00" in inhoud


def test_nog_te_factureren_uren_staan_erbij(db, client, post):
    klant_id, _ = klant_met_rekening(db)
    post("/klussen/nieuw", {"naam": "Badkamer", "klant_id": str(klant_id), "uurtarief": "45"})
    klus_id = db.execute("SELECT id FROM klussen").fetchone()[0]
    post(f"/klus/{klus_id}/dag", {"datum": dagen_geleden(1), "van": "09:00", "tot": "17:00"})

    inhoud = client.get(f"/klant/{klant_id}").data.decode()
    assert "Nog te factureren uren" in inhoud
    assert "8 u" in inhoud
    assert "360,00" in inhoud


def test_concept_heet_op_de_klantpagina_geen_lege_naam(db, client):
    klant_id, _ = klant_met_rekening(db, nummer="", status="concept")
    assert "nog geen nummer" in client.get(f"/klant/{klant_id}").data.decode()


def test_onbekende_klant_geeft_404(client, db):
    assert client.get("/klant/9999").status_code == 404


def test_nieuw_op_de_klantpagina_biedt_rekening_offerte_en_klus(client, db):
    """Drie losse knoppen pasten niet naast de naam. + Nieuw houdt de klant erbij."""
    klant_id = db.execute("INSERT INTO klanten (naam) VALUES ('Jan Jansen')").lastrowid
    db.commit()
    inhoud = client.get(f"/klant/{klant_id}").data.decode()
    assert "+ Nieuw" in inhoud
    assert f"/nieuw?klant={klant_id}" in inhoud
    assert f"/offertes/nieuw?klant={klant_id}" in inhoud
    assert f"/klussen/nieuw?klant={klant_id}" in inhoud
    assert ">Rekening</a>" in inhoud
    assert ">Offerte</a>" in inhoud
    assert ">Klus</a>" in inhoud
    assert "+ Nieuwe rekening" not in inhoud
    assert "+ Offerte" not in inhoud


def test_de_klantenlijst_begint_ook_een_klus_voor_die_klant(client, db):
    klant_id = db.execute("INSERT INTO klanten (naam) VALUES ('Jan Jansen')").lastrowid
    db.commit()
    inhoud = client.get("/klanten").data.decode()
    assert f"/klussen/nieuw?klant={klant_id}" in inhoud
    assert ">Klus</a>" in inhoud


def test_nieuwe_klus_vanaf_de_klant_heeft_die_klant_al_gekozen(post, client, db):
    jan = db.execute("INSERT INTO klanten (naam) VALUES ('Jan Jansen')").lastrowid
    piet = db.execute("INSERT INTO klanten (naam) VALUES ('Piet Pietersen')").lastrowid
    db.commit()

    pagina = client.get(f"/klussen/nieuw?klant={jan}").data.decode()
    assert f'value="{jan}" selected' in pagina
    assert f'value="{piet}" selected' not in pagina

    post("/klussen/nieuw", {"naam": "Badkamer", "klant_id": str(jan), "uurtarief": "45"})
    klus = db.execute("SELECT klant_id, naam FROM klussen").fetchone()
    assert klus["klant_id"] == jan
    assert klus["naam"] == "Badkamer"


def test_onbekende_klant_bij_een_nieuwe_klus_kiest_niemand(client, db):
    db.execute("INSERT INTO klanten (naam) VALUES ('Jan Jansen')")
    db.commit()
    pagina = client.get("/klussen/nieuw?klant=9999").data.decode()
    assert "selected" not in pagina
    assert client.get("/klussen/nieuw?klant=geen").status_code == 200


def test_bewerken_houdt_de_klant_van_de_klus(post, client, db):
    klant_id = db.execute("INSERT INTO klanten (naam) VALUES ('Jan Jansen')").lastrowid
    db.commit()
    post("/klussen/nieuw", {"naam": "Badkamer", "klant_id": str(klant_id)})
    klus_id = db.execute("SELECT id FROM klussen").fetchone()[0]
    pagina = client.get(f"/klus/{klus_id}/bewerk").data.decode()
    assert f'value="{klant_id}" selected' in pagina


def test_een_adres_opent_een_keuze_voor_de_route(client, db):
    from conftest import facturen

    adres = "Nachtegaalstraat 212\n5932 CH Tegelen"
    bestemming = "Nachtegaalstraat%20212%2C%205932%20CH%20Tegelen"
    links = dict(facturen.navigatie_voor(adres))
    assert links == {
        "Google Maps":
            f"https://www.google.com/maps/dir/?api=1&destination={bestemming}",
        "Apple Kaarten": f"https://maps.apple.com/?daddr={bestemming}",
        "Waze": f"https://waze.com/ul?q={bestemming}&navigate=yes",
    }
    # Alleen de straat: geen lege postcode erachter plakken.
    assert facturen.navigatie_voor("Molenstraat 14")[0][1].endswith("Molenstraat%2014")
    assert facturen.navigatie_voor("") == []
    assert facturen.navigatie_voor("  \n  ") == []

    klant_id = db.execute(
        "INSERT INTO klanten (naam, adres) VALUES ('Jan Jansen', ?)", (adres,)
    ).lastrowid
    db.commit()
    inhoud = client.get(f"/klant/{klant_id}").data.decode()
    assert "Google Maps" in inhoud
    assert "Apple Kaarten" in inhoud
    assert ">Waze</a>" in inhoud
    assert f"destination={bestemming}" in inhoud
    assert f"daddr={bestemming}" in inhoud
    assert f"q={bestemming}&amp;navigate=yes" in inhoud
    assert 'target="_blank" rel="noopener"' in inhoud
    # Het adres zelf is de knop, niet een losse zin ernaast.
    assert "Nachtegaalstraat 212" in inhoud
    assert inhoud.count("Google Maps") == 1


def test_zonder_adres_is_er_niets_om_aan_te_tikken(client, db):
    klant_id = db.execute("INSERT INTO klanten (naam) VALUES ('Jan Jansen')").lastrowid
    db.commit()
    inhoud = client.get(f"/klant/{klant_id}").data.decode()
    assert 'class="menu navigatie"' not in inhoud
    assert "google.com/maps" not in inhoud
    assert "maps.apple.com" not in inhoud
    assert "waze.com" not in inhoud


def test_een_notitie_bij_de_klant_maakt_een_adres_aanklikbaar(client, db):
    klant_id = db.execute(
        """INSERT INTO klanten (naam, notitie)
           VALUES ('Jan Jansen', 'Foto''s: https://example.com/pad\nwww.voorbeeld.nl')"""
    ).lastrowid
    db.commit()
    inhoud = client.get(f"/klant/{klant_id}").data.decode()
    assert 'href="https://example.com/pad"' in inhoud
    assert 'href="https://www.voorbeeld.nl"' in inhoud
    assert 'class="externe-link"' in inhoud
    assert 'target="_blank" rel="noopener"' in inhoud
    assert db.execute("SELECT notitie FROM klanten").fetchone()[0] == \
        "Foto's: https://example.com/pad\nwww.voorbeeld.nl"
