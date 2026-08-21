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
    factuur_id = db.execute(
        """INSERT INTO facturen (nummer, datum, klant_id, klant_naam, status, totaal)
           VALUES (?, ?, ?, 'Jan Jansen', ?, ?)""",
        (velden["nummer"], velden["datum"], klant_id, velden["status"], velden["totaal"]),
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
