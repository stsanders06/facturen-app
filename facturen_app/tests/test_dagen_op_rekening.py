"""Een klus op rekening zetten houdt elke dag apart, met de tijden erbij.

Vroeger werd het één regel met het totaal aantal uren. Datum, begin, eind en
pauze waren daarna weg. Een oude rekening die zo is opgeslagen blijft zo.
"""
import sqlite3
from datetime import date, timedelta

import pytest

from conftest import facturen
from test_pdf import tekst_uit_pdf, zet_bedrijf
from test_smalle_weergave import css_van, regel_met


def _klus(post, db, naam="Gevel", tarief="45"):
    post("/klanten/nieuw", {"naam": "Stone"})
    klant_id = db.execute("SELECT id FROM klanten").fetchone()[0]
    post("/klussen/nieuw", {
        "naam": naam, "uurtarief": tarief, "klant_id": str(klant_id),
    })
    klus_id = db.execute("SELECT id FROM klussen WHERE naam=?", (naam,)).fetchone()[0]
    return klant_id, klus_id


def test_elke_gesloten_dag_wordt_een_eigen_regel(post, client, db):
    """Drie dicht, één nog bezig. Alleen de drie komen op de rekening."""
    klant_id, klus_id = _klus(post, db)
    post(f"/klus/{klus_id}/dag", {
        "datum": "2026-10-12", "van": "07:30", "tot": "16:00",
    })
    post(f"/klus/{klus_id}/dag", {
        "datum": "2026-10-13", "van": "08:00", "tot": "12:00",
        "pauze": "30", "notitie": "keuken",
    })
    post(f"/klus/{klus_id}/dag", {
        "datum": "2026-10-14", "van": "09:00", "tot": "13:00",
    })
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-15", "van": "06:45"})
    # De melding van het opslaan van die dag hoort niet bij de rekening.
    client.get(f"/klus/{klus_id}")

    verwacht = [
        "ma 12 okt 2026, 07:30–16:00 · Gevel",
        "di 13 okt 2026, 08:00–12:00 (pauze 30 min) · Gevel · keuken",
        "wo 14 okt 2026, 09:00–13:00 · Gevel",
    ]
    pagina = client.get(f"/nieuw?klus={klus_id}&klant={klant_id}").data.decode()
    for tekst in verwacht:
        assert tekst in pagina
    assert "06:45" not in pagina

    dagen = db.execute(
        "SELECT id, datum FROM uren WHERE tot IS NOT NULL ORDER BY datum"
    ).fetchall()
    antwoord = post("/nieuw", {
        "klant_naam": "Stone", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": verwacht,
        "type": ["arbeid_uur", "arbeid_uur", "arbeid_uur"],
        "aantal": ["8.5", "3.5", "4"],
        "prijs": ["45", "45", "45"],
        "regel_klus": [str(klus_id), str(klus_id), str(klus_id)],
        "regel_uur": [str(dag["id"]) for dag in dagen],
    }, follow_redirects=True)
    tekst = antwoord.data.decode()
    assert "Bij Gevel is nog een dag bezig, sinds 06:45." in tekst
    assert "Die staat niet op de rekening." in tekst

    regels = db.execute(
        "SELECT omschrijving, aantal, prijs, subtotaal, uur_id FROM regels ORDER BY id"
    ).fetchall()
    assert [rij["omschrijving"] for rij in regels] == verwacht
    assert [rij["aantal"] for rij in regels] == [8.5, 3.5, 4.0]
    assert [rij["subtotaal"] for rij in regels] == [382.5, 157.5, 180.0]
    assert [rij["uur_id"] for rij in regels] == [dag["id"] for dag in dagen]
    totaal = db.execute("SELECT totaal FROM facturen").fetchone()[0]
    assert totaal == 720.0
    assert round(sum(rij["subtotaal"] for rij in regels), 2) == totaal

    gekoppeld = {
        rij["datum"]: rij["factuur_id"]
        for rij in db.execute("SELECT datum, factuur_id, van, tot, pauze_minuten FROM uren")
    }
    assert gekoppeld["2026-10-12"] is not None
    assert gekoppeld["2026-10-13"] is not None
    assert gekoppeld["2026-10-14"] is not None
    assert gekoppeld["2026-10-15"] is None
    pauze = db.execute(
        "SELECT van, tot, pauze_minuten FROM uren WHERE datum='2026-10-13'"
    ).fetchone()
    assert (pauze["van"], pauze["tot"], pauze["pauze_minuten"]) == ("08:00", "12:00", 30)


def test_tijden_blijven_staan_na_opnieuw_opslaan(post, db):
    """Bewerken en opslaan voegt de dagen niet samen en koppelt geen extra dag."""
    klant_id, klus_id = _klus(post, db)
    for datum, van, tot in (
        ("2026-10-12", "07:30", "16:00"),
        ("2026-10-13", "08:00", "12:00"),
        ("2026-10-14", "09:00", "13:00"),
    ):
        post(f"/klus/{klus_id}/dag", {"datum": datum, "van": van, "tot": tot})
    dagen = db.execute("SELECT id FROM uren ORDER BY datum").fetchall()
    omschrijvingen = [
        "ma 12 okt 2026, 07:30–16:00 · Gevel",
        "di 13 okt 2026, 08:00–12:00 · Gevel",
        "wo 14 okt 2026, 09:00–13:00 · Gevel",
    ]
    formulier = {
        "klant_naam": "Stone", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": omschrijvingen,
        "type": ["arbeid_uur"] * 3,
        "aantal": ["8.5", "4", "4"],
        "prijs": ["45"] * 3,
        "regel_klus": [str(klus_id)] * 3,
        "regel_uur": [str(dag["id"]) for dag in dagen],
    }
    post("/nieuw", formulier)
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]

    # De middelste tekst is met de hand aangepast. Opslaan mag hem niet
    # terugzetten naar de tekst die de app zelf had gemaakt.
    aangepast = list(omschrijvingen)
    aangepast[1] = "di 13 okt 2026, 08:00–12:00 · Gevel · extra"
    formulier["omschrijving"] = aangepast
    post(f"/factuur/{factuur_id}/bewerk", formulier)

    regels = db.execute(
        "SELECT omschrijving, uur_id FROM regels ORDER BY id"
    ).fetchall()
    assert [rij["omschrijving"] for rij in regels] == aangepast
    assert [rij["uur_id"] for rij in regels] == [dag["id"] for dag in dagen]
    tijden = db.execute(
        "SELECT van, tot, pauze_minuten FROM uren WHERE id=?", (dagen[1]["id"],)
    ).fetchone()
    assert (tijden["van"], tijden["tot"], tijden["pauze_minuten"]) == ("08:00", "12:00", 0)

    # Een oudere dag die je daarna afsluit komt er niet vanzelf bij.
    post(f"/klus/{klus_id}/dag", {
        "datum": "2026-10-11", "van": "08:00", "tot": "12:00",
    })
    post(f"/factuur/{factuur_id}/bewerk", formulier)
    assert db.execute(
        "SELECT factuur_id FROM uren WHERE datum='2026-10-11'"
    ).fetchone()[0] is None
    assert db.execute("SELECT COUNT(*) FROM regels").fetchone()[0] == 3


def test_een_dagregel_weghalen_koppelt_alleen_die_dag_los(post, db):
    """Zonder uur_id zou de oudste dag de plek van de weggehaalde innemen."""
    klant_id, klus_id = _klus(post, db)
    for datum, van, tot in (
        ("2026-10-12", "08:00", "16:00"),
        ("2026-10-13", "08:00", "12:00"),
        ("2026-10-14", "08:00", "12:00"),
    ):
        post(f"/klus/{klus_id}/dag", {"datum": datum, "van": van, "tot": tot})
    dagen = list(db.execute("SELECT id, datum FROM uren ORDER BY datum"))
    post("/nieuw", {
        "klant_naam": "Stone", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": ["eerste", "tweede", "derde"],
        "type": ["arbeid_uur"] * 3,
        "aantal": ["8", "4", "4"],
        "prijs": ["45"] * 3,
        "regel_klus": [str(klus_id)] * 3,
        "regel_uur": [str(dag["id"]) for dag in dagen],
    })
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    # De oudste dag (8 uur) gaat van de rekening af. 13 en 14 blijven.
    post(f"/factuur/{factuur_id}/bewerk", {
        "klant_naam": "Stone", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": ["tweede", "derde"],
        "type": ["arbeid_uur", "arbeid_uur"],
        "aantal": ["4", "4"],
        "prijs": ["45", "45"],
        "regel_klus": [str(klus_id), str(klus_id)],
        "regel_uur": [str(dagen[1]["id"]), str(dagen[2]["id"])],
    })
    los = db.execute(
        "SELECT datum FROM uren WHERE factuur_id IS NULL ORDER BY datum"
    ).fetchall()
    vast = db.execute(
        "SELECT datum FROM uren WHERE factuur_id=? ORDER BY datum", (factuur_id,)
    ).fetchall()
    assert [rij["datum"] for rij in los] == ["2026-10-12"]
    assert [rij["datum"] for rij in vast] == ["2026-10-13", "2026-10-14"]


def test_dezelfde_dag_wordt_niet_twee_keer_gerekend(post, db):
    klant_id, klus_id = _klus(post, db)
    post(f"/klus/{klus_id}/dag", {
        "datum": "2026-10-12", "van": "08:00", "tot": "12:00",
    })
    uur_id = db.execute("SELECT id FROM uren").fetchone()[0]
    antwoord = post("/nieuw", {
        "klant_naam": "Stone", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": ["eerste", "tweede"],
        "type": ["arbeid_uur", "arbeid_uur"],
        "aantal": ["4", "4"],
        "prijs": ["45", "45"],
        "regel_klus": [str(klus_id), str(klus_id)],
        "regel_uur": [str(uur_id), str(uur_id)],
    }, follow_redirects=True)
    assert facturen.DUBBELE_DAG in antwoord.data.decode()
    assert db.execute("SELECT COUNT(*) FROM regels").fetchone()[0] == 1
    assert db.execute("SELECT totaal FROM facturen").fetchone()[0] == 180.0
    assert db.execute(
        "SELECT COUNT(*) FROM uren WHERE factuur_id IS NOT NULL"
    ).fetchone()[0] == 1


def test_een_oude_samengevoegde_regel_blijft_ongewijzigd(post, client, db):
    """Geen herschrijven: de tekst en de koppeling blijven zoals ze waren."""
    klant_id, klus_id = _klus(post, db)
    for datum in ("2026-10-12", "2026-10-13", "2026-10-14"):
        post(f"/klus/{klus_id}/dag", {"datum": datum, "van": "08:00", "tot": "12:00"})
    post("/nieuw", {
        "klant_naam": "Stone", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": "Uren gevel", "type": "arbeid_uur", "aantal": "12",
        "prijs": "45", "regel_klus": str(klus_id),
    })
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    assert db.execute("SELECT uur_id FROM regels").fetchone()[0] is None
    assert db.execute(
        "SELECT COUNT(*) FROM uren WHERE factuur_id=?", (factuur_id,)
    ).fetchone()[0] == 3

    pagina = client.get(f"/factuur/{factuur_id}/bewerk").data.decode()
    assert 'value="Uren gevel"' in pagina
    assert "ma 12 okt 2026" not in pagina

    post(f"/factuur/{factuur_id}/bewerk", {
        "klant_naam": "Stone", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": "Uren gevel", "type": "arbeid_uur", "aantal": "12",
        "prijs": "45", "regel_klus": str(klus_id),
    })
    regels = db.execute("SELECT omschrijving, uur_id FROM regels").fetchall()
    assert [(rij["omschrijving"], rij["uur_id"]) for rij in regels] == [("Uren gevel", None)]
    assert db.execute(
        "SELECT COUNT(*) FROM uren WHERE factuur_id=?", (factuur_id,)
    ).fetchone()[0] == 3


def test_totaal_is_de_som_van_de_afgeronde_regels(post, db):
    """Drie keer 10,333 is 30,99, niet 31,00."""
    klant_id, _klus_id = _klus(post, db)
    post("/nieuw", {
        "klant_naam": "Stone", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": ["een", "twee", "drie"],
        "type": ["materiaal"] * 3,
        "aantal": ["1", "1", "1"],
        "prijs": ["10.333", "10.333", "10.333"],
    })
    regels = db.execute("SELECT subtotaal FROM regels").fetchall()
    assert [rij["subtotaal"] for rij in regels] == [10.33, 10.33, 10.33]
    assert db.execute("SELECT totaal FROM facturen").fetchone()[0] == 30.99


def test_pauze_langer_dan_de_dag_mag_niet(post, db):
    _, klus_id = _klus(post, db)
    antwoord = post(f"/klus/{klus_id}/dag", {
        "datum": "2026-10-12", "van": "08:00", "tot": "12:00", "pauze": "300",
    }, follow_redirects=True)
    assert "De pauze is langer dan de gewerkte tijd." in antwoord.data.decode()
    assert db.execute("SELECT COUNT(*) FROM uren").fetchone()[0] == 0

    onleesbaar = post(f"/klus/{klus_id}/dag", {
        "datum": "2026-10-12", "van": "08:00", "tot": "12:00", "pauze": "half uur",
    }, follow_redirects=True)
    assert "De pauze klopt niet." in onleesbaar.data.decode()


def test_veel_dagen_lopen_door_op_de_pdf(post, db):
    """Elke dag een regel, en een lange rekening gaat over meer dan één pagina."""
    zet_bedrijf(db)
    klant_id, klus_id = _klus(post, db)
    begin = date(2026, 1, 5)
    for nummer in range(28):
        dag = begin + timedelta(days=nummer)
        db.execute(
            """INSERT INTO uren (klus_id, datum, van, tot, notitie, pauze_minuten)
               VALUES (?, ?, '07:30', '16:00', '', 0)""",
            (klus_id, dag.isoformat()),
        )
    db.commit()
    dagen = db.execute("SELECT id, datum FROM uren ORDER BY datum").fetchall()
    omschrijvingen = [
        facturen.omschrijving_van_dag(rij["datum"], "07:30", "16:00", 0, "Gevel", "")
        for rij in dagen
    ]
    post("/nieuw", {
        "klant_naam": "Stone", "klant_id": str(klant_id), "datum": "2026-02-20",
        "omschrijving": omschrijvingen,
        "type": ["arbeid_uur"] * len(dagen),
        "aantal": ["8.5"] * len(dagen),
        "prijs": ["45"] * len(dagen),
        "regel_klus": [str(klus_id)] * len(dagen),
        "regel_uur": [str(rij["id"]) for rij in dagen],
    })
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    assert db.execute("SELECT COUNT(*) FROM regels").fetchone()[0] == 28
    tekst = tekst_uit_pdf(facturen.maak_pdf(factuur_id))
    assert "vervolg" in tekst
    assert "Pagina 1 van" in tekst
    assert "Pagina 2 van" in tekst
    assert "07:30" in tekst
    assert "16:00" in tekst
    assert "5 jan 2026" in tekst
    assert "1 feb 2026" in tekst


def _cent(waarde):
    return f"{float(waarde):.2f}"


def test_twee_tabbladen_zetten_de_dag_niet_dubbel(post, client, db):
    """Beide schermen zijn geladen toen de dagen nog vrij waren.

    Het tweede opslaan mag die regels niet bewaren: anders telt het
    jaaroverzicht ze dubbel terwijl de dag aan de eerste rekening hangt."""
    klant_id, klus_id = _klus(post, db)
    for datum, van, tot in (
        ("2026-10-12", "07:30", "16:00"),
        ("2026-10-13", "08:00", "12:00"),
        ("2026-10-14", "09:00", "13:00"),
    ):
        post(f"/klus/{klus_id}/dag", {"datum": datum, "van": van, "tot": tot})
    dagen = db.execute("SELECT id FROM uren ORDER BY datum").fetchall()
    formulier = {
        "klant_naam": "Stone", "klant_id": str(klant_id),
        "datum": facturen.vandaag().isoformat(),
        "omschrijving": ["dag een", "dag twee", "dag drie"],
        "type": ["arbeid_uur"] * 3,
        "aantal": ["8.5", "4", "4"],
        "prijs": ["45"] * 3,
        "regel_klus": [str(klus_id)] * 3,
        "regel_uur": [str(dag["id"]) for dag in dagen],
    }
    post("/nieuw", formulier)
    tweede = post("/nieuw", formulier, follow_redirects=True)
    assert "niet nog een keer" in tweede.data.decode()

    assert db.execute("SELECT COUNT(*) FROM facturen").fetchone()[0] == 1
    factuur_id = db.execute("SELECT id, totaal FROM facturen").fetchone()
    assert _cent(factuur_id["totaal"]) == "742.50"
    assert [rij["uur_id"] for rij in db.execute(
        "SELECT uur_id FROM regels ORDER BY uur_id"
    )] == [dag["id"] for dag in dagen]
    assert [rij["factuur_id"] for rij in db.execute(
        "SELECT factuur_id FROM uren ORDER BY datum"
    )] == [factuur_id["id"]] * 3

    overzicht = client.get("/").data.decode()
    assert "742,50" in overzicht
    assert "1.485,00" not in overzicht


def test_half_omhoog_bij_een_halve_cent(post, db):
    """1,5 en 3,5 maal 45,55 mogen niet naar de even cent."""
    klant_id, _klus_id = _klus(post, db)
    post("/nieuw", {
        "klant_naam": "Stone", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": ["acht", "anderhalf", "drie"],
        "type": ["arbeid_uur"] * 3,
        "aantal": ["8.5", "1.5", "3.5"],
        "prijs": ["45.55"] * 3,
    })
    bedragen = [
        _cent(rij["subtotaal"])
        for rij in db.execute("SELECT subtotaal FROM regels ORDER BY id")
    ]
    assert bedragen == ["387.18", "68.33", "159.43"]
    assert _cent(db.execute("SELECT totaal FROM facturen").fetchone()[0]) == "614.94"


def test_dagbedrag_rekent_met_de_minuten(post, client, db):
    """07:00–14:20 is 7,33 uur op de regel, maar € 334,03 en niet € 333,88."""
    klant_id, klus_id = _klus(post, db, tarief="45.55")
    post(f"/klus/{klus_id}/dag", {
        "datum": "2026-10-12", "van": "07:00", "tot": "14:20",
    })
    uur_id = db.execute("SELECT id FROM uren").fetchone()[0]
    formulier = client.get(f"/nieuw?klus={klus_id}&klant={klant_id}").data.decode()
    assert 'value="7.33"' in formulier
    assert 'value="334.03"' in formulier

    post("/nieuw", {
        "klant_naam": "Stone", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": "ma 12 okt 2026, 07:00–14:20 · Gevel",
        "type": "arbeid_uur", "aantal": "7.33", "prijs": "45.55",
        "regel_klus": str(klus_id), "regel_uur": str(uur_id),
        "regel_subtotaal": "333.88",
    })
    regel = db.execute("SELECT aantal, subtotaal FROM regels").fetchone()
    assert _cent(regel["aantal"]) == "7.33"
    assert _cent(regel["subtotaal"]) == "334.03"
    assert _cent(db.execute("SELECT totaal FROM facturen").fetchone()[0]) == "334.03"


def test_oude_rekening_van_774_35_blijft_staan(post, client, db):
    """Opgeslagen toen 1,5 × 45,55 nog 68,32 werd. Samen 774,35.

    Openen en opslaan mag dat niet naar 68,33 en 774,36 trekken."""
    factuur_id = db.execute(
        """INSERT INTO facturen (nummer, datum, klant_naam, status, totaal)
           VALUES ('2026-090', ?, 'Stone', 'definitief', 774.35)""",
        (facturen.vandaag().isoformat(),),
    ).lastrowid
    db.execute(
        """INSERT INTO regels (factuur_id, omschrijving, type, aantal, prijs, subtotaal)
           VALUES (?, 'Oude uren', 'arbeid_uur', 1.5, 45.55, 68.32)""",
        (factuur_id,),
    )
    db.execute(
        """INSERT INTO regels (factuur_id, omschrijving, type, aantal, prijs, subtotaal)
           VALUES (?, 'Rest', 'materiaal', 1, 706.03, 706.03)""",
        (factuur_id,),
    )
    db.commit()

    pagina = client.get(f"/factuur/{factuur_id}/bewerk").data.decode()
    assert "774,35" in pagina
    assert 'value="68.32"' in pagina
    assert 'data-bewaard="1"' in pagina

    post(f"/factuur/{factuur_id}/bewerk", {
        "klant_naam": "Stone", "datum": facturen.vandaag().isoformat(),
        "omschrijving": ["Oude uren", "Rest"],
        "type": ["arbeid_uur", "materiaal"],
        "aantal": ["1.5", "1"],
        "prijs": ["45.55", "706.03"],
        "regel_subtotaal": ["68.32", "706.03"],
    })
    assert _cent(db.execute(
        "SELECT totaal FROM facturen WHERE id=?", (factuur_id,)
    ).fetchone()[0]) == "774.35"
    assert [
        _cent(rij["subtotaal"])
        for rij in db.execute("SELECT subtotaal FROM regels ORDER BY id")
    ] == ["68.32", "706.03"]


def test_dag_van_een_andere_klant_weigert_de_rekening(post, db):
    klant_id, klus_id = _klus(post, db)
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-12", "van": "08:00", "tot": "12:00"})
    uur_id = db.execute("SELECT id FROM uren").fetchone()[0]
    post("/klanten/nieuw", {"naam": "Iemand anders"})
    ander = db.execute(
        "SELECT id FROM klanten WHERE naam='Iemand anders'"
    ).fetchone()[0]
    post("/klussen/nieuw", {
        "naam": "Hun klus", "uurtarief": "45", "klant_id": str(ander),
    })
    hun_klus = db.execute(
        "SELECT id FROM klussen WHERE naam='Hun klus'"
    ).fetchone()[0]
    # De regel noemt een klus van deze klant, maar de dag is van Stone.
    antwoord = post("/nieuw", {
        "klant_naam": "Iemand anders", "klant_id": str(ander), "datum": "2026-10-16",
        "omschrijving": "dag", "type": "arbeid_uur", "aantal": "4", "prijs": "45",
        "regel_klus": str(hun_klus), "regel_uur": str(uur_id),
    }, follow_redirects=True)
    assert "Deze dag hoort bij een andere klant." in antwoord.data.decode()
    assert db.execute("SELECT COUNT(*) FROM facturen").fetchone()[0] == 0
    assert db.execute("SELECT factuur_id FROM uren").fetchone()[0] is None


def test_dag_van_een_andere_klus_weigert_de_rekening(post, db):
    klant_id, klus_id = _klus(post, db)
    post("/klussen/nieuw", {
        "naam": "Dak", "uurtarief": "45", "klant_id": str(klant_id),
    })
    dak_id = db.execute("SELECT id FROM klussen WHERE naam='Dak'").fetchone()[0]
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-12", "van": "08:00", "tot": "12:00"})
    uur_id = db.execute("SELECT id FROM uren").fetchone()[0]
    antwoord = post("/nieuw", {
        "klant_naam": "Stone", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": "dag", "type": "arbeid_uur", "aantal": "4", "prijs": "45",
        "regel_klus": str(dak_id), "regel_uur": str(uur_id),
    }, follow_redirects=True)
    assert "Deze dag hoort bij een andere klus." in antwoord.data.decode()
    assert db.execute("SELECT COUNT(*) FROM facturen").fetchone()[0] == 0


def test_gefactureerde_dag_verwijderen_lukt_niet(post, db):
    klant_id, klus_id = _klus(post, db)
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-12", "van": "08:00", "tot": "12:00"})
    uur_id = db.execute("SELECT id FROM uren").fetchone()[0]
    post("/nieuw", {
        "klant_naam": "Stone", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": "dag", "type": "arbeid_uur", "aantal": "4", "prijs": "45",
        "regel_klus": str(klus_id), "regel_uur": str(uur_id),
    })
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    db.execute("UPDATE facturen SET nummer='2026-014' WHERE id=?", (factuur_id,))
    db.commit()
    antwoord = post(f"/uur/{uur_id}/verwijder", follow_redirects=True)
    tekst = antwoord.data.decode()
    assert "Deze dag staat op rekening 2026-014." in tekst
    assert "Naar de rekening" in tekst
    assert f"/factuur/{factuur_id}/bewerk" in tekst
    assert db.execute("SELECT id FROM uren WHERE id=?", (uur_id,)).fetchone() is not None


def test_tijden_van_een_gefactureerde_dag_wijzigen_lukt_niet(post, db):
    klant_id, klus_id = _klus(post, db)
    post(f"/klus/{klus_id}/dag", {
        "datum": "2026-10-12", "van": "08:00", "tot": "12:00", "notitie": "keuken",
    })
    uur_id = db.execute("SELECT id FROM uren").fetchone()[0]
    post("/nieuw", {
        "klant_naam": "Stone", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": "dag", "type": "arbeid_uur", "aantal": "4", "prijs": "45",
        "regel_klus": str(klus_id), "regel_uur": str(uur_id),
    })
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    db.execute("UPDATE facturen SET nummer='2026-015' WHERE id=?", (factuur_id,))
    db.commit()

    gewijzigd = post(f"/uur/{uur_id}/bewerk", {
        "datum": "2026-10-12", "van": "09:00", "tot": "13:00", "notitie": "keuken",
    }, follow_redirects=True)
    tekst = gewijzigd.data.decode()
    assert "Deze dag staat op rekening 2026-015." in tekst
    assert "Naar de rekening" in tekst
    assert f"/factuur/{factuur_id}/bewerk" in tekst
    assert db.execute(
        "SELECT van, tot FROM uren WHERE id=?", (uur_id,)
    ).fetchone()[:] == ("08:00", "12:00")

    post(f"/uur/{uur_id}/bewerk", {
        "datum": "2026-10-12", "van": "08:00", "tot": "12:00", "notitie": "badkamer",
    })
    assert db.execute(
        "SELECT notitie, van, tot FROM uren WHERE id=?", (uur_id,)
    ).fetchone()[:] == ("badkamer", "08:00", "12:00")


def test_lager_aantal_op_een_dagregel_wordt_niet_opgeslagen(post, db):
    klant_id, klus_id = _klus(post, db)
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-12", "van": "08:00", "tot": "16:00"})
    uur_id = db.execute("SELECT id FROM uren").fetchone()[0]
    antwoord = post("/nieuw", {
        "klant_naam": "Stone", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": "dag", "type": "arbeid_uur", "aantal": "1", "prijs": "45",
        "regel_klus": str(klus_id), "regel_uur": str(uur_id),
    }, follow_redirects=True)
    assert facturen.AANTAL_VOLGT_DE_DAG in antwoord.data.decode()
    regel = db.execute("SELECT aantal, subtotaal FROM regels").fetchone()
    assert _cent(regel["aantal"]) == "8.00"
    assert _cent(regel["subtotaal"]) == "360.00"


def test_uur_id_is_uniek_en_valt_weg_als_de_dag_weggaat(db):
    klus_id = db.execute(
        "INSERT INTO klussen (naam, uurtarief, gestart) VALUES ('Gevel', 45, '2026-10-01')"
    ).lastrowid
    uur_id = db.execute(
        """INSERT INTO uren (klus_id, datum, van, tot) VALUES (?, '2026-10-12', '08:00', '12:00')""",
        (klus_id,),
    ).lastrowid
    factuur_id = db.execute(
        """INSERT INTO facturen (nummer, datum, klant_naam, status, totaal)
           VALUES ('2026-001', '2026-10-16', 'Stone', 'concept', 180)"""
    ).lastrowid
    db.execute(
        """INSERT INTO regels (factuur_id, omschrijving, type, aantal, prijs, subtotaal, uur_id)
           VALUES (?, 'dag', 'arbeid_uur', 4, 45, 180, ?)""",
        (factuur_id, uur_id),
    )
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO regels
               (factuur_id, omschrijving, type, aantal, prijs, subtotaal, uur_id)
               VALUES (?, 'nog eens', 'arbeid_uur', 4, 45, 180, ?)""",
            (factuur_id, uur_id),
        )
    db.rollback()
    db.execute("DELETE FROM uren WHERE id=?", (uur_id,))
    db.commit()
    achtergebleven = db.execute(
        "SELECT uur_id, subtotaal FROM regels WHERE factuur_id=?", (factuur_id,)
    ).fetchone()
    assert achtergebleven["uur_id"] is None
    assert _cent(achtergebleven["subtotaal"]) == "180.00"


def test_migratie_bewaart_beide_bedragen_bij_een_dubbele_dag(db):
    """Een database waarin twee regels al naar dezelfde dag wijzen.

    De regel die bij de rekening van de dag hoort houdt de koppeling. De
    andere houdt zijn bedrag, maar niet de dag."""
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
            uur_id INTEGER,
            FOREIGN KEY (factuur_id) REFERENCES facturen (id)
        )"""
    )
    db.execute("DROP TABLE regels_los")
    klus_id = db.execute(
        "INSERT INTO klussen (naam, uurtarief, gestart) VALUES ('Gevel', 45, '2026-10-01')"
    ).lastrowid
    eerste = db.execute(
        """INSERT INTO facturen (nummer, datum, klant_naam, status, totaal)
           VALUES ('2026-001', '2026-10-16', 'Stone', 'concept', 180)"""
    ).lastrowid
    tweede = db.execute(
        """INSERT INTO facturen (nummer, datum, klant_naam, status, totaal)
           VALUES ('2026-002', '2026-10-16', 'Stone', 'concept', 180)"""
    ).lastrowid
    uur_id = db.execute(
        """INSERT INTO uren (klus_id, datum, van, tot, factuur_id)
           VALUES (?, '2026-10-12', '08:00', '12:00', ?)""",
        (klus_id, tweede),
    ).lastrowid
    db.execute(
        """INSERT INTO regels
           (factuur_id, omschrijving, type, aantal, prijs, subtotaal, uur_id)
           VALUES (?, 'eerste', 'arbeid_uur', 4, 45, 111, ?)""",
        (eerste, uur_id),
    )
    db.execute(
        """INSERT INTO regels
           (factuur_id, omschrijving, type, aantal, prijs, subtotaal, uur_id)
           VALUES (?, 'tweede', 'arbeid_uur', 4, 45, 222, ?)""",
        (tweede, uur_id),
    )
    db.commit()

    facturen._uur_koppeling_uniek(db)
    db.execute("PRAGMA foreign_keys=ON")
    db.commit()

    gekoppeld = db.execute(
        "SELECT factuur_id, subtotaal FROM regels WHERE uur_id=? ", (uur_id,)
    ).fetchone()
    assert gekoppeld["factuur_id"] == tweede
    assert _cent(gekoppeld["subtotaal"]) == "222.00"
    los = db.execute(
        "SELECT uur_id, subtotaal FROM regels WHERE factuur_id=?", (eerste,)
    ).fetchone()
    assert los["uur_id"] is None
    assert _cent(los["subtotaal"]) == "111.00"
    assert db.execute(
        "SELECT factuur_id FROM uren WHERE id=?", (uur_id,)
    ).fetchone()[0] == tweede
    assert facturen._uur_koppeling_al_uniek(db)


def test_een_lange_dagregel_breekt_op_een_smal_scherm(client):
    """Op telefoonbreedte staat de omschrijving niet over uren en prijs heen."""
    css = css_van(client.get("/nieuw").data.decode())
    # De basisregel houdt de tekst op één regel. Alleen onder 620px mag hij
    # breken, anders staat een datum over de uren en de prijs heen.
    smal = css[css.index("Een dagregel noemt datum"):]
    blok = regel_met(smal, ".regel-tekst")
    assert "normal" in blok
    assert "calc(100% - 52px)" in blok
