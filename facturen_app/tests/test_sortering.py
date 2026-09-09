"""De volgorde van de lijsten: op datum, niet op de volgorde waarin je ze intypte."""


def test_een_later_ingevoerde_oude_rekening_springt_niet_bovenaan(db, client, maak_factuur):
    """Een rekening van vorige maand die je nu pas invoert hoort onder de nieuwe."""
    maak_factuur(nummer="2026-020", datum="2026-09-08", klant="Nieuwe Klus")
    maak_factuur(nummer="2026-021", datum="2026-07-02", klant="Oude Klus")
    inhoud = client.get("/").data.decode()
    assert inhoud.index("Nieuwe Klus") < inhoud.index("Oude Klus")


def test_op_dezelfde_dag_staat_de_laatst_gemaakte_bovenaan(db, client, maak_factuur):
    maak_factuur(nummer="2026-022", datum="2026-09-08", klant="Eerste Van Vandaag")
    maak_factuur(nummer="2026-023", datum="2026-09-08", klant="Tweede Van Vandaag")
    inhoud = client.get("/").data.decode()
    assert inhoud.index("Tweede Van Vandaag") < inhoud.index("Eerste Van Vandaag")


def test_offertes_staan_ook_op_datum(db, client, maak_offerte):
    maak_offerte(nummer="O-2026-030", datum="2026-09-08", klant="Nieuwe Offerte")
    maak_offerte(nummer="O-2026-031", datum="2026-06-14", klant="Oude Offerte")
    inhoud = client.get("/offertes").data.decode()
    assert inhoud.index("Nieuwe Offerte") < inhoud.index("Oude Offerte")


def test_op_de_klantpagina_ook(db, client, maak_factuur):
    klant_id = db.execute("INSERT INTO klanten (naam) VALUES ('Jan Jansen')").lastrowid
    nieuw = maak_factuur(nummer="2026-040", datum="2026-09-08", klant="Jan Jansen")
    oud = maak_factuur(nummer="2026-041", datum="2026-05-01", klant="Jan Jansen")
    db.execute("UPDATE facturen SET klant_id=? WHERE id IN (?, ?)", (klant_id, nieuw, oud))
    db.commit()
    inhoud = client.get(f"/klant/{klant_id}").data.decode()
    assert inhoud.index("2026-040") < inhoud.index("2026-041")
