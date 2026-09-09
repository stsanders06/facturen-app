"""Een concept heeft nog geen nummer. Zonder terugval staat er een losse punt of
een leeg vakje waar het nummer hoort."""


def test_een_concept_offerte_zegt_dat_het_nummer_nog_komt(client, maak_offerte):
    maak_offerte(nummer="", status="concept")
    assert "nog geen nummer" in client.get("/offertes").data.decode()


def test_een_offerte_met_nummer_zegt_dat_niet(client, maak_offerte):
    maak_offerte(nummer="O2026-006", status="verzonden")
    inhoud = client.get("/offertes").data.decode()
    assert "O2026-006" in inhoud
    assert "nog geen nummer" not in inhoud


def test_ook_op_de_klantpagina(client, db, maak_offerte):
    klant_id = db.execute("INSERT INTO klanten (naam) VALUES ('Jan Jansen')").lastrowid
    offerte_id = maak_offerte(nummer="", status="concept")
    db.execute("UPDATE offertes SET klant_id=? WHERE id=?", (klant_id, offerte_id))
    db.commit()
    assert "nog geen nummer" in client.get(f"/klant/{klant_id}").data.decode()
