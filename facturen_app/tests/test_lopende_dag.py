"""Een gewerkte dag mag zonder eindtijd, en telt dan nog nergens mee.

's Ochtends vul je de begintijd in. De dag is dan bezig. Pas als de eindtijd
er is, horen de uren bij de totalen, het bedrag, de rekening en een export.
"""
from datetime import timedelta

from conftest import facturen
from test_smalle_weergave import css_van, regel_met


def _klus(post, db, naam="Gevel", tarief="45"):
    post("/klanten/nieuw", {"naam": "Testklant A"})
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
    # De lopende dag is vandaag, anders zet de lijst er een datum bij en
    # leest 'Bezig sinds 07:30' niet meer hetzelfde.
    vandaag = facturen.vandaag().isoformat()
    gisteren = (facturen.vandaag() - timedelta(days=1)).isoformat()
    post(f"/klus/{klus_id}/dag", {"datum": gisteren, "van": "09:00", "tot": "17:00"})
    post(f"/klus/{klus_id}/dag", {"datum": vandaag, "van": "07:30"})

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
        "klant_naam": "Testklant A", "klant_id": str(klant_id),
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
    assert f"{gisteren};09:00;17:00;8;Gevel" in csv_tekst
    assert "07:30" not in csv_tekst
    assert vandaag not in csv_tekst


def test_alleen_een_lopende_dag_komt_niet_op_de_rekening(post, db):
    """De urenkiezer biedt hem niet aan, en een zelf verzonnen aantal koppelt
    de dag ook niet aan de rekening."""
    klant_id, klus_id = _klus(post, db)
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-17", "van": "07:30"})
    assert facturen.geboekte_klussen() == []

    antwoord = post("/nieuw", {
        "klant_naam": "Testklant A", "klant_id": str(klant_id),
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

    # Zonder vinkje is een eindtijd de volgende ochtend een tikfout.
    nacht = post(f"/klus/{klus_id}/dag", {
        "datum": "2026-10-17", "van": "22:00", "tot": "01:30",
    }, follow_redirects=True)
    assert "Eindtijd ligt vóór de begintijd." in nacht.data.decode()
    assert db.execute("SELECT COUNT(*) FROM uren").fetchone()[0] == 0

    post(f"/klus/{klus_id}/dag", {
        "datum": "2026-10-17", "van": "22:00", "tot": "01:30",
        "over_middernacht": "ja",
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
    # De klus hoort te bestaan: de foreign key op de dag geldt nu echt.
    db.execute(
        """INSERT INTO klussen (id, naam, uurtarief, gestart)
           VALUES (1, 'Oud', 45, '2026-08-01')"""
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
    assert "min-height: 44px" in regel_met(css, ".nacht")


def _gekoppelde_datums(db):
    return [
        rij["datum"] for rij in db.execute(
            "SELECT datum FROM uren WHERE factuur_id IS NOT NULL ORDER BY datum"
        )
    ]


def test_opnieuw_opslaan_laat_gekoppelde_dagen_staan(post, db):
    """13, 14 en 15 okt staan op de rekening, 12 okt is nog bezig.

    Na het afsluiten van 12 okt en ongewijzigd opslaan blijven 13, 14 en 15
    gekoppeld. De uren zijn 8, 8, 4 en 4 en de rekening telt 16 uur: kiezen
    op datum, oudste eerst, zou 12 en 13 pakken en 14 en 15 loslaten.
    """
    klant_id, klus_id = _klus(post, db)
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-12", "van": "08:00"})
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-13", "van": "08:00", "tot": "16:00"})
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-14", "van": "08:00", "tot": "12:00"})
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-15", "van": "08:00", "tot": "12:00"})
    rekening = {
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": "Uren gevel", "type": "arbeid_uur", "aantal": "16",
        "prijs": "45", "regel_klus": str(klus_id),
    }
    post("/nieuw", rekening)
    assert _gekoppelde_datums(db) == ["2026-10-13", "2026-10-14", "2026-10-15"]

    uur_id = db.execute(
        "SELECT id FROM uren WHERE datum='2026-10-12'"
    ).fetchone()[0]
    post(f"/uur/{uur_id}/bewerk", {
        "datum": "2026-10-12", "van": "08:00", "tot": "16:00",
        "notitie": "", "afsluiten": "ja",
    })
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    post(f"/factuur/{factuur_id}/bewerk", rekening)

    assert _gekoppelde_datums(db) == ["2026-10-13", "2026-10-14", "2026-10-15"]
    assert db.execute(
        "SELECT factuur_id FROM uren WHERE datum='2026-10-12'"
    ).fetchone()[0] is None

    # Meer uren op de regel is een echte wijziging: dan komt 12 er wél bij.
    rekening["aantal"] = "24"
    post(f"/factuur/{factuur_id}/bewerk", rekening)
    assert _gekoppelde_datums(db) == [
        "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15",
    ]


def test_eindtijd_voor_begintijd_wordt_geweigerd(post, client, db):
    """07:30 tot 07:00 is 23,5 uur als je het stil als nachtdienst rekent.
    Zonder vinkje mag dat niet, ook niet bij het afsluiten."""
    _, klus_id = _klus(post, db)

    invoer = post(f"/klus/{klus_id}/dag", {
        "datum": "2026-10-17", "van": "07:30", "tot": "07:00",
    }, follow_redirects=True)
    assert "Eindtijd ligt vóór de begintijd." in invoer.data.decode()
    assert "Dag afgesloten" not in invoer.data.decode()
    assert db.execute("SELECT COUNT(*) FROM uren").fetchone()[0] == 0

    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-17", "van": "07:30"})
    uur_id = db.execute("SELECT id FROM uren").fetchone()[0]
    afsluiten = post(f"/uur/{uur_id}/bewerk", {
        "datum": "2026-10-17", "van": "07:30", "tot": "07:00",
        "notitie": "", "afsluiten": "ja",
    }, follow_redirects=True)
    tekst = afsluiten.data.decode()
    assert "Eindtijd ligt vóór de begintijd." in tekst
    assert "Dag afgesloten" not in tekst
    assert db.execute(
        "SELECT tot FROM uren WHERE id=?", (uur_id,)
    ).fetchone()[0] is None

    gelijk = post(f"/klus/{klus_id}/dag", {
        "datum": "2026-10-18", "van": "07:30", "tot": "07:30",
        "over_middernacht": "ja",
    }, follow_redirects=True)
    assert "De eindtijd moet na de begintijd liggen." in gelijk.data.decode()

    met_vinkje = post(f"/uur/{uur_id}/bewerk", {
        "datum": "2026-10-17", "van": "07:30", "tot": "07:00",
        "notitie": "", "afsluiten": "ja", "over_middernacht": "ja",
    }, follow_redirects=True)
    assert "Dag afgesloten, van 07:30 tot 07:00." in met_vinkje.data.decode()
    assert facturen.duur_in_uren("07:30", "07:00") == 23.5
    _, _, uren = facturen.klus_met_uren(db, klus_id)
    assert uren == 23.5

    pagina = client.get(f"/klus/{klus_id}").data.decode()
    assert "Loopt door na middernacht" in pagina
    assert 'name="over_middernacht"' in pagina


def test_een_tweede_open_dag_op_dezelfde_datum_mag_niet(post, db):
    _, klus_id = _klus(post, db)
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-13", "van": "06:45"})
    # Een andere datum mag wel: daar is het een andere dag.
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-14", "van": "08:00"})

    antwoord = post(f"/klus/{klus_id}/dag", {
        "datum": "2026-10-13", "van": "09:00",
    }, follow_redirects=True)
    tekst = antwoord.data.decode()
    assert "loopt al een dag" in tekst
    assert "sinds 06:45" in tekst
    assert "Sluit die eerst af." in tekst
    assert db.execute(
        """SELECT COUNT(*) FROM uren
           WHERE datum='2026-10-13' AND tot IS NULL"""
    ).fetchone()[0] == 1

    # Een gesloten dag op die datum mag erbij. Leegmaken terwijl de andere
    # nog loopt, niet.
    post(f"/klus/{klus_id}/dag", {
        "datum": "2026-10-13", "van": "13:00", "tot": "17:00",
    })
    dicht = db.execute(
        "SELECT id FROM uren WHERE datum='2026-10-13' AND tot='17:00'"
    ).fetchone()[0]
    leeg = post(f"/uur/{dicht}/bewerk", {
        "datum": "2026-10-13", "van": "13:00", "tot": "",
    }, follow_redirects=True)
    assert "loopt al een dag" in leeg.data.decode()
    assert db.execute(
        "SELECT tot FROM uren WHERE id=?", (dicht,)
    ).fetchone()[0] == "17:00"


def test_bezig_sinds_noemt_de_datum_als_het_niet_vandaag_is(post, client, db):
    klant_id, klus_id = _klus(post, db)
    dag = facturen.vandaag() - timedelta(days=4)
    post(f"/klus/{klus_id}/dag", {"datum": dag.isoformat(), "van": "06:45"})

    weekdag = ["ma", "di", "wo", "do", "vr", "za", "zo"][dag.weekday()]
    maand = ["jan", "feb", "mrt", "apr", "mei", "jun",
             "jul", "aug", "sep", "okt", "nov", "dec"][dag.month - 1]
    tekst = f"Bezig sinds {weekdag} {dag.day} {maand} 06:45"

    assert facturen.klussenlijst()[0]["bezig_sinds"] == tekst.removeprefix("Bezig sinds ")
    assert tekst in client.get("/klussen").data.decode()
    assert tekst in client.get(f"/klant/{klant_id}").data.decode()
    # Op de klus zelf staat de datum al als titel van de rij.
    kluspagina = client.get(f"/klus/{klus_id}").data.decode()
    assert "Bezig sinds 06:45" in kluspagina
    assert tekst not in kluspagina


def test_csv_van_gewerkte_dagen_is_te_downloaden(post, client, db):
    _, klus_id = _klus(post, db)
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-16", "van": "09:00", "tot": "17:00"})
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-17", "van": "07:30"})

    antwoord = client.get("/klussen/gewerkte-dagen.csv")
    assert antwoord.status_code == 200
    assert "text/csv" in antwoord.mimetype
    assert "gewerkte-dagen.csv" in antwoord.headers["Content-Disposition"]
    tekst = antwoord.data.decode("utf-8-sig")
    assert tekst.startswith("datum;van;tot;uren;klus;notitie")
    assert "2026-10-16;09:00;17:00;8;Gevel" in tekst
    assert "07:30" not in tekst
    assert "2026-10-17" not in tekst

    pagina = client.get("/nieuw").data.decode()
    assert 'id="csv-dagen"' in pagina
    assert 'href="/klussen/gewerkte-dagen.csv"' in pagina
    smal = css_van(pagina)
    smal = smal[smal.rindex("@media (max-width: 620px)"):]
    assert "width: 100%" in regel_met(smal, ".regelknoppen .haal-op > a.csv-dagen")
    assert "min-height: 44px" in regel_met(smal, ".regelknoppen .haal-op > a.csv-dagen")


def _uren_nieuw(conn):
    conn.execute("DROP TABLE uren")
    conn.execute(
        """CREATE TABLE uren_nieuw (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            klus_id INTEGER NOT NULL,
            datum TEXT NOT NULL,
            van TEXT NOT NULL,
            tot TEXT,
            notitie TEXT DEFAULT '',
            factuur_id INTEGER
        )"""
    )
    conn.execute(
        """INSERT INTO uren_nieuw (id, klus_id, datum, van, tot, notitie, factuur_id)
           VALUES (4, 1, '2026-08-14', '09:00', '17:00', 'voegen', 2)"""
    )
    conn.commit()


def _uren_na_herstel(conn):
    namen = {
        rij["name"] for rij in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "uren" in namen
    assert "uren_nieuw" not in namen
    info = {rij["name"]: rij for rij in conn.execute("PRAGMA table_info(uren)")}
    assert info["tot"]["notnull"] == 0
    rij = conn.execute("SELECT * FROM uren").fetchone()
    assert rij["id"] == 4
    assert rij["van"] == "09:00"
    assert rij["tot"] == "17:00"
    assert rij["notitie"] == "voegen"
    assert rij["factuur_id"] == 2
    conn.execute(
        """INSERT INTO uren (klus_id, datum, van, tot)
           VALUES (1, '2026-08-15', '07:30', NULL)"""
    )
    nieuw = conn.execute("SELECT id, tot FROM uren WHERE van='07:30'").fetchone()
    assert nieuw["id"] == 5
    assert nieuw["tot"] is None


def test_herstel_als_alleen_de_kopie_er_nog_is(db):
    """De oude tabel is al weg, de hernoeming nog niet. De kopie ís de data."""
    _uren_nieuw(db)
    facturen._eindtijd_optioneel(db)
    db.commit()
    _uren_na_herstel(db)


def test_herstel_als_de_start_een_lege_urentabel_aanmaakte(db):
    """init_db maakt uren opnieuw aan zodra die weg is. De kopie mag dan
    niet worden weggegooid alsof het een achtergebleven klad is."""
    _uren_nieuw(db)
    facturen.init_db()
    conn = facturen.get_db()
    _uren_na_herstel(conn)
    conn.commit()
    conn.close()
