"""Het kenmerk van een nieuwe rekening volgt de klus waar hij over gaat.

Het kenmerk is het veld waaraan je later terugziet waarover een rekening ging.
Dat met de hand overtypen terwijl de klus al op de regels staat is dubbel werk,
dus vult de app het in als je het leeg laat.
"""
from conftest import facturen


def klus_met_klant(db, klusnaam="Badkamer", klant="Jan Jansen", tarief=45.0):
    klant_id = db.execute("INSERT INTO klanten (naam) VALUES (?)", (klant,)).lastrowid
    klus_id = db.execute(
        """INSERT INTO klussen (naam, klant_id, uurtarief, gestart)
           VALUES (?, ?, ?, '2026-10-01')""",
        (klusnaam, klant_id, tarief),
    ).lastrowid
    db.commit()
    return klant_id, klus_id


def test_het_kenmerk_wordt_de_naam_van_de_klus(post, db):
    klant_id, klus_id = klus_met_klant(db)
    post("/nieuw", {
        "klant_naam": "Jan Jansen", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": ["Werk"], "type": ["arbeid_uur"],
        "aantal": ["4"], "prijs": ["45"], "regel_klus": [str(klus_id)],
    })
    assert db.execute("SELECT kenmerk FROM facturen").fetchone()[0] == "Badkamer"


def test_een_eigen_kenmerk_blijft_staan(post, db):
    """Wat je zelf intikt is belangrijker dan wat de app kan verzinnen."""
    klant_id, klus_id = klus_met_klant(db)
    post("/nieuw", {
        "klant_naam": "Jan Jansen", "klant_id": str(klant_id), "datum": "2026-10-16",
        "kenmerk": "Tweede voorstel",
        "omschrijving": ["Werk"], "type": ["arbeid_uur"],
        "aantal": ["4"], "prijs": ["45"], "regel_klus": [str(klus_id)],
    })
    assert db.execute("SELECT kenmerk FROM facturen").fetchone()[0] == "Tweede voorstel"


def test_meer_klussen_komen_allemaal_in_het_kenmerk(post, db):
    klant_id, eerste = klus_met_klant(db, "Badkamer")
    tweede = db.execute(
        """INSERT INTO klussen (naam, klant_id, uurtarief, gestart)
           VALUES ('Zolder', ?, 45.0, '2026-10-01')""", (klant_id,),
    ).lastrowid
    db.commit()
    post("/nieuw", {
        "klant_naam": "Jan Jansen", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": ["Werk", "Meer werk"], "type": ["arbeid_uur", "arbeid_uur"],
        "aantal": ["4", "2"], "prijs": ["45", "45"],
        "regel_klus": [str(eerste), str(tweede)],
    })
    # Alfabetisch, zodat dezelfde rekening altijd hetzelfde kenmerk krijgt.
    assert db.execute("SELECT kenmerk FROM facturen").fetchone()[0] == "Badkamer, Zolder"


def test_zonder_klus_blijft_het_kenmerk_leeg(post, db):
    """Een losse rekening zonder klus heeft niets om naar te verwijzen."""
    klant_id = db.execute(
        "INSERT INTO klanten (naam) VALUES ('Jan Jansen')").lastrowid
    db.commit()
    post("/nieuw", {
        "klant_naam": "Jan Jansen", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": ["Advies"], "type": ["vast"], "aantal": ["1"], "prijs": ["100"],
    })
    assert db.execute("SELECT kenmerk FROM facturen").fetchone()[0] == ""


def test_bij_bewerken_wordt_het_kenmerk_niet_opnieuw_gevuld(post, db):
    """Anders kun je een kenmerk nooit weghalen: leegmaken en opslaan zou het
    elke keer terugzetten."""
    klant_id, klus_id = klus_met_klant(db)
    post("/nieuw", {
        "klant_naam": "Jan Jansen", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": ["Werk"], "type": ["arbeid_uur"],
        "aantal": ["4"], "prijs": ["45"], "regel_klus": [str(klus_id)],
    })
    rij = db.execute("SELECT id, nummer FROM facturen").fetchone()
    assert db.execute("SELECT kenmerk FROM facturen").fetchone()[0] == "Badkamer"
    post(f"/factuur/{rij['id']}/bewerk", {
        "nummer": rij["nummer"],
        "klant_naam": "Jan Jansen", "klant_id": str(klant_id), "datum": "2026-10-16",
        "kenmerk": "",
        "omschrijving": ["Werk"], "type": ["arbeid_uur"],
        "aantal": ["4"], "prijs": ["45"], "regel_klus": [str(klus_id)],
    })
    assert db.execute("SELECT kenmerk FROM facturen").fetchone()[0] == ""


def test_meer_dan_drie_klussen_worden_geteld(db):
    """Alle namen zouden langer worden dan het veld laat zien."""
    conn = facturen.get_db()
    factuur_id = conn.execute(
        """INSERT INTO facturen (nummer, datum, klant_naam, totaal)
           VALUES ('2026-900', '2026-10-16', 'Jan Jansen', 0)""").lastrowid
    for naam in ("Aanbouw", "Badkamer", "Carport", "Dakkapel", "Erf"):
        klus_id = conn.execute(
            """INSERT INTO klussen (naam, uurtarief, gestart)
               VALUES (?, 45.0, '2026-10-01')""", (naam,)).lastrowid
        conn.execute(
            """INSERT INTO regels (factuur_id, omschrijving, type, aantal, prijs,
               subtotaal, klus_id) VALUES (?, 'Werk', 'arbeid_uur', 1, 45, 45, ?)""",
            (factuur_id, klus_id))
    conn.commit()
    uit = facturen.kenmerk_van_de_klussen(conn, factuur_id)
    conn.close()
    assert uit == "Aanbouw, Badkamer, Carport en 2 andere"
