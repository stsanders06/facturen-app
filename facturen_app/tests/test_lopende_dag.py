"""Een gewerkte dag mag zonder eindtijd, en telt dan nog nergens mee.

's Ochtends vul je de begintijd in. De dag is dan bezig. Pas als de eindtijd
er is, horen de uren bij de totalen, het bedrag, de rekening en een export.
"""
from conftest import facturen
from test_smalle_weergave import css_van, regel_met


def _klus(post, db, naam="Gevel", tarief="45"):
    post("/klanten/nieuw", {"naam": "Stone"})
    klant_id = db.execute("SELECT id FROM klanten").fetchone()[0]
    post("/klussen/nieuw", {"naam": naam, "uurtarief": tarief, "klant_id": str(klant_id)})
    klus_id = db.execute("SELECT id FROM klussen").fetchone()[0]
    return klant_id, klus_id


def test_een_dag_opslaan_mag_zonder_eindtijd(post, client, db):
    _, klus_id = _klus(post, db)
    antwoord = post(f"/klus/{klus_id}/dag", {
        "datum": "2026-10-17", "van": "07:30", "notitie": "voegen",
    }, follow_redirects=True)
    inhoud = antwoord.data.decode()

    rij = db.execute("SELECT * FROM uren").fetchone()
    assert rij["van"] == "07:30"
    assert rij["tot"] is None
    assert rij["notitie"] == "voegen"
    assert "De dag is opgeslagen, bezig sinds 07:30." in inhoud
    assert "Bezig sinds 07:30" in inhoud
    assert "Dag afsluiten" in inhoud
    assert 'name="tot"' in inhoud
    # Het invoerveld voor een nieuwe dag eist geen eindtijd.
    assert 'aria-label="Eindtijd, mag leeg"' in inhoud


def test_een_lopende_dag_sluit_je_later_af(post, client, db):
    _, klus_id = _klus(post, db)
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-17", "van": "07:30"})
    uur_id = db.execute("SELECT id FROM uren").fetchone()[0]

    antwoord = post(f"/uur/{uur_id}/bewerk", {
        "datum": "2026-10-17", "van": "07:30", "tot": "16:00",
        "notitie": "", "afsluiten": "ja",
    }, follow_redirects=True)
    inhoud = antwoord.data.decode()

    rij = db.execute("SELECT van, tot FROM uren WHERE id=?", (uur_id,)).fetchone()
    assert rij["van"] == "07:30"
    assert rij["tot"] == "16:00"
    assert "Dag afgesloten, van 07:30 tot 16:00." in inhoud
    assert "Bezig sinds" not in inhoud
    assert "07:30 – 16:00" in inhoud
    _, _, uren = facturen.klus_met_uren(db, klus_id)
    assert uren == 8.5


def test_een_lopende_dag_telt_niet_mee_in_totalen_factuur_of_csv(post, client, db):
    """Acht uur is dicht. De ochtend loopt nog. Alleen de acht uur tellen."""
    klant_id, klus_id = _klus(post, db)
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-16", "van": "09:00", "tot": "17:00"})
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-17", "van": "07:30"})

    klus = facturen.klussenlijst()[0]
    assert klus["uren"] == 8.0
    assert klus["uren_open"] == 8.0
    assert klus["bedrag"] == 360.0
    assert klus["bedrag_open"] == 360.0
    assert klus["bezig_sinds"] == "07:30"

    kluspagina = client.get(f"/klus/{klus_id}").data.decode()
    assert 'id="tegel-totaal">8<' in kluspagina
    assert 'id="tegel-open">8<' in kluspagina
    assert "360,00" in kluspagina
    assert "Bezig sinds 07:30" in kluspagina

    overzicht = client.get("/klussen").data.decode()
    assert "8 u" in overzicht
    assert "Bezig sinds 07:30" in overzicht
    # De lopende dag heeft nog geen uren, dus hij mag de knop niet groter maken.
    assert "16,5 u" not in overzicht

    klantpagina = client.get(f"/klant/{klant_id}").data.decode()
    assert "Bezig sinds 07:30" in klantpagina
    assert "8 u factureren" in klantpagina

    nieuw = client.get(f"/nieuw?klus={klus_id}&klant={klant_id}").data.decode()
    assert 'data-uren="8.0"' in nieuw

    antwoord = post("/nieuw", {
        "klant_naam": "Stone", "klant_id": str(klant_id),
        "datum": facturen.vandaag().isoformat(),
        "omschrijving": "Uren gevel", "type": "arbeid_uur", "aantal": "8",
        "prijs": "45", "regel_klus": str(klus_id),
    }, follow_redirects=True)
    tekst = antwoord.data.decode()
    assert "Bij Gevel is nog een dag bezig, sinds 07:30." in tekst
    assert "Die staat niet op de rekening." in tekst

    lopend = db.execute(
        "SELECT factuur_id FROM uren WHERE van='07:30'"
    ).fetchone()
    dicht = db.execute(
        "SELECT factuur_id FROM uren WHERE van='09:00'"
    ).fetchone()
    assert lopend["factuur_id"] is None
    assert dicht["factuur_id"] is not None

    # Het jaaroverzicht telt rekeningen. De lopende dag staat er niet op,
    # dus alleen de 8 × 45 van de gesloten dag.
    begin = client.get("/").data.decode()
    assert "360,00" in begin

    csv_tekst = facturen.csv_gewerkte_dagen(db)
    assert "2026-10-16;09:00;17:00;8;Gevel" in csv_tekst
    assert "07:30" not in csv_tekst
    assert "2026-10-17" not in csv_tekst


def test_alleen_een_lopende_dag_komt_niet_op_de_rekening(post, db):
    """De urenkiezer biedt hem niet aan, en een zelf verzonnen aantal koppelt
    de dag ook niet aan de rekening."""
    klant_id, klus_id = _klus(post, db)
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-17", "van": "07:30"})
    assert facturen.geboekte_klussen() == []

    antwoord = post("/nieuw", {
        "klant_naam": "Stone", "klant_id": str(klant_id),
        "datum": facturen.vandaag().isoformat(),
        "omschrijving": "Uren gevel", "type": "arbeid_uur", "aantal": "3",
        "prijs": "45", "regel_klus": str(klus_id),
    }, follow_redirects=True)
    assert "Bij Gevel is nog een dag bezig, sinds 07:30." in antwoord.data.decode()
    assert "Die staat niet op de rekening." in antwoord.data.decode()
    assert db.execute("SELECT factuur_id FROM uren").fetchone()[0] is None


def test_de_eindtijd_moet_na_de_begintijd_liggen(post, db):
    _, klus_id = _klus(post, db)

    zonder = post(f"/klus/{klus_id}/dag", {
        "datum": "2026-10-17", "van": "", "tot": "",
    }, follow_redirects=True)
    assert "Vul een begintijd in om de dag op te slaan." in zonder.data.decode()
    assert db.execute("SELECT COUNT(*) FROM uren").fetchone()[0] == 0

    onleesbaar = post(f"/klus/{klus_id}/dag", {
        "datum": "2026-10-17", "van": "kwart over", "tot": "",
    }, follow_redirects=True)
    assert "De begintijd klopt niet." in onleesbaar.data.decode()

    gelijk = post(f"/klus/{klus_id}/dag", {
        "datum": "2026-10-17", "van": "09:00", "tot": "09:00",
    }, follow_redirects=True)
    assert "De eindtijd moet na de begintijd liggen." in gelijk.data.decode()
    assert db.execute("SELECT COUNT(*) FROM uren").fetchone()[0] == 0

    # Na middernacht doorwerken blijft geldig: de eindtijd ligt de volgende ochtend.
    post(f"/klus/{klus_id}/dag", {
        "datum": "2026-10-17", "van": "22:00", "tot": "01:30",
    })
    assert facturen.duur_in_uren("22:00", "01:30") == 3.5
    _, dagen, uren = facturen.klus_met_uren(db, klus_id)
    assert uren == 3.5
    assert dagen[0]["lopend"] is False

    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-18", "van": "07:30"})
    uur_id = db.execute(
        "SELECT id FROM uren WHERE tot IS NULL"
    ).fetchone()[0]
    leeg = post(f"/uur/{uur_id}/bewerk", {
        "datum": "2026-10-18", "van": "07:30", "tot": "", "afsluiten": "ja",
    }, follow_redirects=True)
    assert "Vul een eindtijd in om de dag af te sluiten." in leeg.data.decode()
    assert db.execute("SELECT tot FROM uren WHERE id=?", (uur_id,)).fetchone()[0] is None


def test_een_gefactureerde_dag_houdt_zijn_eindtijd(post, db):
    _, klus_id = _klus(post, db)
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-16", "van": "09:00", "tot": "17:00"})
    uur_id = db.execute("SELECT id FROM uren").fetchone()[0]
    db.execute("UPDATE uren SET factuur_id=1 WHERE id=?", (uur_id,))
    db.commit()

    antwoord = post(f"/uur/{uur_id}/bewerk", {
        "datum": "2026-10-16", "van": "09:00", "tot": "",
    }, follow_redirects=True)
    assert "De eindtijd kun je niet leegmaken." in antwoord.data.decode()
    assert db.execute("SELECT tot FROM uren WHERE id=?", (uur_id,)).fetchone()[0] == "17:00"


def test_oude_eindtijd_blijft_bewaard_als_de_kolom_optioneel_wordt(db):
    """Een database van voor deze versie eiste een eindtijd. Die tijden blijven,
    en daarna mag een nieuwe dag zonder eindtijd erbij."""
    db.execute("DROP TABLE uren")
    db.execute(
        """CREATE TABLE uren (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            klus_id INTEGER NOT NULL,
            datum TEXT NOT NULL,
            van TEXT NOT NULL,
            tot TEXT NOT NULL,
            notitie TEXT DEFAULT '',
            factuur_id INTEGER
        )"""
    )
    db.execute(
        """INSERT INTO uren (klus_id, datum, van, tot, notitie)
           VALUES (1, '2026-08-14', '09:00', '17:00', 'voegen')"""
    )
    db.commit()

    facturen.init_db()

    conn = facturen.get_db()
    info = {rij["name"]: rij for rij in conn.execute("PRAGMA table_info(uren)")}
    assert info["tot"]["notnull"] == 0
    rij = conn.execute("SELECT * FROM uren").fetchone()
    assert rij["datum"] == "2026-08-14"
    assert rij["van"] == "09:00"
    assert rij["tot"] == "17:00"
    assert rij["notitie"] == "voegen"
    assert rij["factuur_id"] is None
    conn.execute(
        """INSERT INTO uren (klus_id, datum, van, tot)
           VALUES (1, '2026-08-15', '07:30', NULL)"""
    )
    conn.commit()
    facturen.init_db()
    assert conn.execute("SELECT COUNT(*) FROM uren").fetchone()[0] == 2
    assert conn.execute(
        "SELECT tot FROM uren WHERE van='07:30'"
    ).fetchone()[0] is None
    assert conn.execute(
        "SELECT tot FROM uren WHERE van='09:00'"
    ).fetchone()[0] == "17:00"
    conn.close()


def test_zonder_eindtijd_zijn_er_nul_uren():
    assert facturen.duur_in_uren("07:30", None) == 0.0
    assert facturen.duur_in_uren("07:30", "") == 0.0


def test_de_afsluitknop_is_groot_genoeg_op_een_telefoon(client, post, db):
    """Op 320px naast elkaar werd de knop smaller dan een duim, of het tijdveld
    stak buiten de kaart. Onder elkaar, en minstens 44px hoog."""
    _, klus_id = _klus(post, db)
    css = css_van(client.get(f"/klus/{klus_id}").data.decode())
    assert "min-height: 44px" in regel_met(css, ".afsluiten .btn")
    assert "min-height: 44px" in regel_met(css, ".afsluiten input[type=\"time\"]")
    smal = css[css.index("@media (max-width: 720px)"):]
    assert "flex-direction: column" in regel_met(smal, ".afsluiten")
    assert "calc(100% - 22px)" in regel_met(smal, ".afsluiten input[type=\"time\"]")
    assert "display: block" in regel_met(
        css_van(client.get("/klussen").data.decode()), ".klus .bezig")
