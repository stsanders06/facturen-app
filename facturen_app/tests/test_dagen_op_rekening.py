"""Een klus op rekening zetten houdt elke dag apart, met de tijden erbij.

Vroeger werd het één regel met het totaal aantal uren. Datum, begin, eind en
pauze waren daarna weg. Een oude rekening die zo is opgeslagen blijft zo.
"""
import logging
import sqlite3
from datetime import date, timedelta
from html.parser import HTMLParser

import pytest

from conftest import facturen
from test_pdf import paginas_uit_pdf, tekst_uit_pdf, zet_bedrijf
from test_smalle_weergave import css_van, regel_met


def _klus(post, db, naam="Gevel", tarief="45"):
    post("/klanten/nieuw", {"naam": "Test Klant BV"})
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
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-10-16",
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
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-10-16",
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
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-10-16",
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
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-10-16",
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
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-10-16",
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
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-10-16",
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
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-10-16",
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
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-10-16",
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
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-02-20",
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
        "klant_naam": "Testklant A", "klant_id": str(klant_id),
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
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-10-16",
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
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-10-16",
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
           VALUES ('2026-090', ?, 'Testklant A', 'definitief', 774.35)""",
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
        "klant_naam": "Testklant A", "datum": facturen.vandaag().isoformat(),
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
    # De regel noemt een klus van deze klant, maar de dag is van Testklant A.
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
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-10-16",
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
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-10-16",
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
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-10-16",
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
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-10-16",
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
           VALUES ('2026-001', '2026-10-16', 'Testklant A', 'concept', 180)"""
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
           VALUES ('2026-001', '2026-10-16', 'Testklant A', 'concept', 180)"""
    ).lastrowid
    tweede = db.execute(
        """INSERT INTO facturen (nummer, datum, klant_naam, status, totaal)
           VALUES ('2026-002', '2026-10-16', 'Testklant A', 'concept', 180)"""
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
    assert "display: grid" in regel_met(smal, ".regel-samenvatting")
    blok = regel_met(smal, ".regel-tekst")
    assert "normal" in blok
    assert "grid-area: tekst" in blok
    assert "grid-area: qty" in regel_met(smal, ".regel-qty")
    assert "text-align: left" in regel_met(smal, ".regel-totaal")
    kruis = regel_met(smal, ".regel .weg")
    assert "width: 44px" in kruis
    assert "height: 44px" in kruis
    # De rand staat vóór het commentaar, in hetzelfde smalle blok.
    rand = css[css.index("Overal dezelfde rand"):css.index("Een dagregel noemt datum")]
    assert "padding: 8px" in rand


def _oude_regeltabel(db):
    """regels van voor de unieke dagkoppeling, met één bewaard bedrag."""
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
    factuur_id = db.execute(
        """INSERT INTO facturen (nummer, datum, klant_naam, status, totaal)
           VALUES ('2026-040', '2026-10-16', 'Testklant A', 'definitief', 111)"""
    ).lastrowid
    db.execute(
        """INSERT INTO regels
           (factuur_id, omschrijving, type, aantal, prijs, subtotaal)
           VALUES (?, 'oude regel', 'materiaal', 1, 111, 111)""",
        (factuur_id,),
    )
    db.commit()
    db.execute("PRAGMA foreign_keys=ON")
    return factuur_id


@pytest.mark.parametrize("stap", ["insert", "drop", "rename"])
def test_crash_tijdens_de_migratie_rolt_terug(db, stap):
    """CREATE, de kopie, DROP en RENAME zitten in één transactie.

    Valt het proces op een van die punten weg, dan is de volgende start niet
    een fout 'table regels_nieuw already exists' en blijft het bedrag staan."""
    _oude_regeltabel(db)
    facturen._MIGRATIE_STAP = stap
    try:
        with pytest.raises(facturen.MigratieAfgebroken):
            facturen._uur_koppeling_uniek(db)
    finally:
        facturen._MIGRATIE_STAP = None

    assert "regels_nieuw" not in facturen._tabelnamen(db)
    assert db.execute("SELECT subtotaal FROM regels").fetchone()[0] == 111
    facturen._uur_koppeling_uniek(db)
    assert facturen._uur_koppeling_al_uniek(db)
    assert "regels_nieuw" not in facturen._tabelnamen(db)
    assert db.execute("SELECT subtotaal FROM regels").fetchone()[0] == 111


def test_volgende_start_na_een_vastgelegde_kopie(db):
    """De tabel regels_nieuw was al vastgelegd, de oude tabel nog niet weg.

    Dat is de crash ná INSERT en vóór DROP bij een start die elke opdracht
    meteen vastlegde."""
    _oude_regeltabel(db)
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute(facturen._REGELS_NIEUW)
    db.execute(
        """INSERT INTO regels_nieuw
           (id, factuur_id, omschrijving, type, aantal, prijs, subtotaal,
            klus_id, inkoop_id, uur_id)
           SELECT id, factuur_id, omschrijving, type, aantal, prijs, subtotaal,
                  klus_id, inkoop_id, uur_id FROM regels"""
    )
    db.commit()
    facturen._uur_koppeling_uniek(db)
    assert "regels_nieuw" not in facturen._tabelnamen(db)
    assert facturen._uur_koppeling_al_uniek(db)
    assert db.execute("SELECT subtotaal FROM regels").fetchone()[0] == 111


def test_volgende_start_nadat_de_oude_tabel_weg_is(db):
    """Crash ná DROP, vóór RENAME. De start maakt anders een lege regels aan
    en weigert dan omdat regels_nieuw er al is. De kopie ís de data."""
    _oude_regeltabel(db)
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute(facturen._REGELS_NIEUW)
    db.execute(
        """INSERT INTO regels_nieuw
           (id, factuur_id, omschrijving, type, aantal, prijs, subtotaal,
            klus_id, inkoop_id, uur_id)
           SELECT id, factuur_id, omschrijving, type, aantal, prijs, subtotaal,
                  klus_id, inkoop_id, uur_id FROM regels"""
    )
    db.execute("DROP TABLE regels")
    db.commit()
    facturen.init_db()
    conn = facturen.get_db()
    try:
        assert "regels_nieuw" not in facturen._tabelnamen(conn)
        assert facturen._uur_koppeling_al_uniek(conn)
        assert conn.execute("SELECT subtotaal FROM regels").fetchone()[0] == 111
    finally:
        conn.close()


def test_migratie_laat_alleen_de_regel_van_de_dag_gekoppeld(db, client, caplog):
    """Twee rekeningen met dezelfde dagen. uren.factuur_id is leidend.

    Rekening 2 houdt het bedrag, maar niet de dag. Een regel naar een vrije
    dag verliest de koppeling ook. Bij het openen staat er een waarschuwing."""
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
            uur_id INTEGER
        )"""
    )
    db.execute("DROP TABLE regels_los")
    klus_id = db.execute(
        "INSERT INTO klussen (naam, uurtarief, gestart) VALUES ('Gevel', 45, '2026-10-01')"
    ).lastrowid
    eerste = db.execute(
        """INSERT INTO facturen (nummer, datum, klant_naam, status, totaal)
           VALUES ('2026-001', '2026-10-16', 'Testklant A', 'definitief', 400)"""
    ).lastrowid
    tweede = db.execute(
        """INSERT INTO facturen (nummer, datum, klant_naam, status, totaal)
           VALUES ('2026-002', '2026-10-17', 'Testklant A', 'definitief', 795.63)"""
    ).lastrowid
    dag1 = db.execute(
        """INSERT INTO uren (klus_id, datum, van, tot, factuur_id)
           VALUES (?, '2026-10-12', '08:00', '12:00', ?)""",
        (klus_id, eerste),
    ).lastrowid
    dag2 = db.execute(
        """INSERT INTO uren (klus_id, datum, van, tot, factuur_id)
           VALUES (?, '2026-10-13', '08:00', '12:00', NULL)""",
        (klus_id,),
    ).lastrowid
    for factuur_id, bedragen in (
        (eerste, (200, 200)),
        (tweede, (400, 395.63)),
    ):
        for uur_id, bedrag in ((dag1, bedragen[0]), (dag2, bedragen[1])):
            db.execute(
                """INSERT INTO regels
                   (factuur_id, omschrijving, type, aantal, prijs, subtotaal, uur_id)
                   VALUES (?, 'dag', 'arbeid_uur', 4, 45, ?, ?)""",
                (factuur_id, bedrag, uur_id),
            )
    db.commit()

    with caplog.at_level(logging.WARNING):
        facturen._uur_koppeling_uniek(db)
    db.execute("PRAGMA foreign_keys=ON")
    db.commit()

    assert "Dagkoppeling losgelaten" in caplog.text
    assert db.execute(
        "SELECT totaal FROM facturen WHERE id=?", (tweede,)
    ).fetchone()[0] == 795.63
    assert [
        rij["subtotaal"] for rij in db.execute(
            "SELECT subtotaal FROM regels WHERE factuur_id=? ORDER BY id", (tweede,)
        )
    ] == [400, 395.63]
    assert db.execute(
        "SELECT uur_id FROM regels WHERE factuur_id=? AND subtotaal=200", (eerste,)
    ).fetchone()[0] == dag1
    # De vrije dag blijft vrij, en geen regel wijst er nog naar.
    assert db.execute(
        "SELECT factuur_id FROM uren WHERE id=?", (dag2,)
    ).fetchone()[0] is None
    assert db.execute(
        "SELECT COUNT(*) FROM regels WHERE uur_id=?", (dag2,)
    ).fetchone()[0] == 0
    los = db.execute(
        """SELECT COUNT(*) FROM regels r
           LEFT JOIN uren u ON u.id = r.uur_id
           WHERE r.uur_id IS NOT NULL
             AND (u.id IS NULL OR u.factuur_id IS NULL OR u.factuur_id <> r.factuur_id)"""
    ).fetchone()[0]
    assert los == 0

    pagina = client.get(f"/factuur/{tweede}/bewerk").data.decode()
    assert "795,63" in pagina
    # Het euroteken en het bedrag blijven aan elkaar, ook als de regel smal is.
    assert "€\u00a0400,00" in pagina
    assert "hoort bij rekening 2026-001" in pagina
    assert "niet twee keer" in pagina


class _Formulier(HTMLParser):
    """De velden zoals de browser ze bij een ongewijzigde opslag zou sturen."""

    def __init__(self):
        super().__init__()
        self.velden = []
        self._select = None
        self._gekozen = None
        self._eerste = None
        self._textarea = None
        self._buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "disabled" in a:
            return
        if tag == "input":
            soort = (a.get("type") or "text").lower()
            if soort in ("submit", "button", "file", "reset"):
                return
            if soort in ("checkbox", "radio") and "checked" not in a:
                return
            naam = a.get("name")
            if not naam:
                return
            waarde = a.get("value", "")
            if soort == "checkbox" and "value" not in a:
                waarde = "on"
            self.velden.append((naam, waarde))
        elif tag == "select":
            self._select = a.get("name")
            self._gekozen = None
            self._eerste = None
        elif tag == "option" and self._select:
            waarde = a.get("value", "")
            if self._eerste is None:
                self._eerste = waarde
            if "selected" in a:
                self._gekozen = waarde
        elif tag == "textarea" and a.get("name"):
            self._textarea = a["name"]
            self._buf = []

    def handle_endtag(self, tag):
        if tag == "select" and self._select:
            gekozen = self._gekozen if self._gekozen is not None else (self._eerste or "")
            self.velden.append((self._select, gekozen))
            self._select = None
        elif tag == "textarea" and self._textarea:
            self.velden.append((self._textarea, "".join(self._buf)))
            self._textarea = None

    def handle_data(self, data):
        if self._textarea is not None:
            self._buf.append(data)


def _formulier(html):
    parser = _Formulier()
    parser.feed(html)
    gegevens = {}
    for naam, waarde in parser.velden:
        if naam in gegevens:
            if not isinstance(gegevens[naam], list):
                gegevens[naam] = [gegevens[naam]]
            gegevens[naam].append(waarde)
        else:
            gegevens[naam] = waarde
    return gegevens


def test_oude_bedragen_van_387_175_blijven_na_openen_en_opslaan(post, client, db):
    """8,5 × 45,55 stond als 387,175 op de regel. Samen 774,35.

    Het formulier mag dat niet naar 387,17 schrijven, en opslaan mag de
    database niet naar 774,34 trekken."""
    factuur_id = db.execute(
        """INSERT INTO facturen (nummer, datum, klant_naam, status, totaal)
           VALUES ('2026-091', ?, 'Testklant A', 'definitief', 774.35)""",
        (facturen.vandaag().isoformat(),),
    ).lastrowid
    for _ in range(2):
        db.execute(
            """INSERT INTO regels (factuur_id, omschrijving, type, aantal, prijs, subtotaal)
               VALUES (?, 'Oude uren', 'arbeid_uur', 8.5, 45.55, 387.175)""",
            (factuur_id,),
        )
    db.commit()
    voor = [
        rij["subtotaal"] for rij in db.execute(
            "SELECT subtotaal FROM regels ORDER BY id"
        )
    ]
    totaal_voor = db.execute(
        "SELECT totaal FROM facturen WHERE id=?", (factuur_id,)
    ).fetchone()[0]

    pagina = client.get(f"/factuur/{factuur_id}/bewerk").data.decode()
    assert 'value="387.175"' in pagina
    assert "774,35" in pagina

    post(f"/factuur/{factuur_id}/bewerk", _formulier(pagina))

    na = [
        rij["subtotaal"] for rij in db.execute(
            "SELECT subtotaal FROM regels ORDER BY id"
        )
    ]
    totaal_na = db.execute(
        "SELECT totaal FROM facturen WHERE id=?", (factuur_id,)
    ).fetchone()[0]
    assert na == voor
    assert totaal_na == totaal_voor


def test_prijs_wijzigen_rekent_het_subtotaal_opnieuw(post, db):
    """Zonder script blijft het oude subtotaal in het formulier staan."""
    klant_id, _klus_id = _klus(post, db)
    post("/nieuw", {
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": "Kit", "type": "materiaal", "aantal": "1", "prijs": "12.50",
    })
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    post(f"/factuur/{factuur_id}/bewerk", {
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": "Kit", "type": "materiaal", "aantal": "1", "prijs": "20",
        "regel_subtotaal": "12.50",
    })
    regel = db.execute("SELECT aantal, prijs, subtotaal FROM regels").fetchone()
    assert _cent(regel["prijs"]) == "20.00"
    assert _cent(regel["subtotaal"]) == "20.00"
    assert _cent(db.execute("SELECT totaal FROM facturen").fetchone()[0]) == "20.00"


def test_aantal_wijzigen_rekent_het_subtotaal_opnieuw(post, db):
    klant_id, _klus_id = _klus(post, db)
    post("/nieuw", {
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": "Kit", "type": "materiaal", "aantal": "1", "prijs": "12.50",
    })
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    post(f"/factuur/{factuur_id}/bewerk", {
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": "Kit", "type": "materiaal", "aantal": "3", "prijs": "12.50",
        "regel_subtotaal": "12.50",
    })
    regel = db.execute("SELECT aantal, subtotaal FROM regels").fetchone()
    assert _cent(regel["aantal"]) == "3.00"
    assert _cent(regel["subtotaal"]) == "37.50"


def test_soort_per_klus_rekent_niet_met_het_oude_aantal(post, db):
    """2 × 12,50 en daarna 'Arbeid per klus' is 1 × 12,50, niet € 25,00."""
    klant_id, _klus_id = _klus(post, db)
    post("/nieuw", {
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": "Werk", "type": "arbeid_uur", "aantal": "2", "prijs": "12.50",
    })
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    post(f"/factuur/{factuur_id}/bewerk", {
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": "Werk", "type": "arbeid_klus", "aantal": "1", "prijs": "12.50",
        "regel_subtotaal": "25.00",
    })
    regel = db.execute("SELECT aantal, prijs, subtotaal FROM regels").fetchone()
    assert _cent(regel["aantal"]) == "1.00"
    assert _cent(regel["prijs"]) == "12.50"
    assert _cent(regel["subtotaal"]) == "12.50"


def test_tweede_tabblad_met_materiaal_maakt_geen_rekening(post, db):
    klant_id, klus_id = _klus(post, db)
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-12", "van": "08:00", "tot": "12:00"})
    uur_id = db.execute("SELECT id FROM uren").fetchone()[0]
    formulier = {
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": ["dag", "kit"],
        "type": ["arbeid_uur", "materiaal"],
        "aantal": ["4", "1"],
        "prijs": ["45", "12.50"],
        "regel_klus": [str(klus_id), ""],
        "regel_uur": [str(uur_id), ""],
    }
    post("/nieuw", formulier)
    tweede = post("/nieuw", formulier, follow_redirects=True)
    tekst = tweede.data.decode()
    assert "niet nog een keer" in tekst
    assert "niet aangemaakt" in tekst
    assert db.execute("SELECT COUNT(*) FROM facturen").fetchone()[0] == 1
    # De kitregel hoort bij de eerste rekening. Een tweede rekening met
    # alleen die regel mag er niet bijkomen.
    assert db.execute(
        "SELECT COUNT(*) FROM regels WHERE omschrijving='kit'"
    ).fetchone()[0] == 1


def test_verouderde_omschrijving_volgt_de_dag(post, db):
    klant_id, klus_id = _klus(post, db)
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-12", "van": "08:00", "tot": "12:00"})
    uur_id = db.execute("SELECT id FROM uren").fetchone()[0]
    post(f"/uur/{uur_id}/bewerk", {
        "datum": "2026-10-12", "van": "09:00", "tot": "15:00",
    })
    post("/nieuw", {
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": "ma 12 okt 2026, 08:00–12:00 · Gevel",
        "type": "arbeid_uur", "aantal": "4", "prijs": "45",
        "regel_klus": str(klus_id), "regel_uur": str(uur_id),
    })
    tekst = db.execute("SELECT omschrijving FROM regels").fetchone()[0]
    assert "09:00–15:00" in tekst
    assert "08:00–12:00" not in tekst


def test_dagregel_toont_het_aantal_zonder_minuten_ernaast(client, post, db):
    """7,33 u blijft. '(7 u 20 min)' niet, ook niet bij bewerken of op de PDF.

    De tijden staan in de omschrijving. Het bedrag blijft dat van de minuten."""
    zet_bedrijf(db)
    klant_id, klus_id = _klus(post, db, tarief="45.55")
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-12", "van": "07:00", "tot": "14:20"})
    uur_id = db.execute("SELECT id FROM uren").fetchone()[0]
    nieuw = client.get(f"/nieuw?klus={klus_id}&klant={klant_id}").data.decode()
    assert "7,33 u" in nieuw
    assert "7 u 20 min" not in nieuw
    assert "07:00–14:20" in nieuw

    post("/nieuw", {
        "klant_naam": "Test Klant BV", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": "ma 12 okt 2026, 07:00–14:20 · Gevel",
        "type": "arbeid_uur", "aantal": "7.33", "prijs": "45.55",
        "regel_klus": str(klus_id), "regel_uur": str(uur_id),
    })
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    assert _cent(db.execute("SELECT subtotaal FROM regels").fetchone()[0]) == "334.03"
    bewerkt = client.get(f"/factuur/{factuur_id}/bewerk").data.decode()
    assert "7,33 u" in bewerkt
    assert "7 u 20 min" not in bewerkt
    assert "07:00–14:20" in bewerkt
    pdf = tekst_uit_pdf(facturen.maak_pdf(factuur_id))
    assert "7,33 u" in pdf
    assert "7 u 20 min" not in pdf
    assert "07:00" in pdf and "14:20" in pdf


def test_klus_met_gefactureerde_dag_verwijderen_lukt_niet(post, db):
    klant_id, klus_id = _klus(post, db)
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-12", "van": "08:00", "tot": "12:00"})
    uur_id = db.execute("SELECT id FROM uren").fetchone()[0]
    post("/nieuw", {
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": "dag", "type": "arbeid_uur", "aantal": "4", "prijs": "45",
        "regel_klus": str(klus_id), "regel_uur": str(uur_id),
    })
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    antwoord = post(f"/klus/{klus_id}/verwijder", follow_redirects=True)
    tekst = antwoord.data.decode()
    assert "Deze klus kan niet weg." in tekst
    assert "conceptrekening" in tekst
    assert "Testklant A" in tekst
    assert "rekening CONCEPT" not in tekst
    assert "Naar de rekening" in tekst
    assert f"/factuur/{factuur_id}/bewerk" in tekst
    assert db.execute("SELECT id FROM klussen WHERE id=?", (klus_id,)).fetchone()
    assert db.execute(
        "SELECT uur_id FROM regels"
    ).fetchone()[0] == uur_id


def test_melding_naar_de_rekening_staat_onder_de_tekst(client):
    """De link mag de tekst niet tot een smalle kolom drukken."""
    css = css_van(client.get("/nieuw").data.decode())
    links = regel_met(css, ".melding .melding-links")
    assert "100%" in links
    assert "44px" in links
    assert "flex: none" not in regel_met(css, ".melding .melding-link")
    assert "break-word" in regel_met(css, ".melding .woorden")


def test_terughalen_zet_de_dag_weer_op_de_regel(post, db):
    """Soft-delete zet uur_id op NULL. Terughalen moet die koppeling terugzetten."""
    klant_id, klus_id = _klus(post, db)
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-12", "van": "08:00", "tot": "12:00"})
    uur_id = db.execute("SELECT id FROM uren").fetchone()[0]
    post("/nieuw", {
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": "dag", "type": "arbeid_uur", "aantal": "4", "prijs": "45",
        "regel_klus": str(klus_id), "regel_uur": str(uur_id),
    })
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    regel_id = db.execute("SELECT id FROM regels").fetchone()[0]
    uur = dict(db.execute("SELECT * FROM uren WHERE id=?", (uur_id,)).fetchone())
    import json
    from datetime import datetime
    db.execute(
        "INSERT INTO prullenbak (omschrijving, inhoud, wanneer) VALUES (?, ?, ?)",
        (
            "Klus Gevel",
            json.dumps({
                "rijen": {"uren": [uur]},
                "bestanden": [],
                "koppelingen": {"uur_regels": [{"regel_id": regel_id, "uur_id": uur_id}]},
            }),
            datetime.now().isoformat(),
        ),
    )
    prul = db.execute("SELECT id FROM prullenbak ORDER BY id DESC").fetchone()[0]
    db.execute("DELETE FROM uren WHERE id=?", (uur_id,))
    db.commit()
    assert db.execute("SELECT uur_id FROM regels").fetchone()[0] is None

    post(f"/prullenbak/{prul}/terug")
    assert db.execute("SELECT uur_id FROM regels").fetchone()[0] == uur_id
    assert db.execute(
        "SELECT factuur_id FROM uren WHERE id=?", (uur_id,)
    ).fetchone()[0] == factuur_id


def test_rekening_terughalen_weigert_als_de_dag_elders_staat(post, client, db):
    klant_id, klus_id = _klus(post, db)
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-12", "van": "08:00", "tot": "16:00"})
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-13", "van": "08:00", "tot": "12:00"})
    dagen = db.execute("SELECT id FROM uren ORDER BY datum").fetchall()
    formulier = {
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-10-16",
        "omschrijving": ["eerste", "tweede"],
        "type": ["arbeid_uur", "arbeid_uur"],
        "aantal": ["8", "4"],
        "prijs": ["45", "45"],
        "regel_klus": [str(klus_id), str(klus_id)],
        "regel_uur": [str(dag["id"]) for dag in dagen],
    }
    post("/nieuw", formulier)
    eerste = db.execute("SELECT id, totaal FROM facturen").fetchone()
    post(f"/factuur/{eerste['id']}/verwijder")
    post("/nieuw", formulier)
    assert db.execute("SELECT COUNT(*) FROM facturen").fetchone()[0] == 1
    nieuw = db.execute("SELECT id, totaal FROM facturen").fetchone()
    prul = db.execute("SELECT id FROM prullenbak ORDER BY id DESC").fetchone()[0]

    antwoord = post(f"/prullenbak/{prul}/terug", follow_redirects=True)
    tekst = antwoord.data.decode()
    assert "kan niet terug" in tekst
    assert "12 okt 2026" in tekst
    assert "13 okt 2026" in tekst
    assert "conceptrekening" in tekst
    assert db.execute("SELECT COUNT(*) FROM facturen").fetchone()[0] == 1
    assert db.execute("SELECT id FROM facturen").fetchone()[0] == nieuw["id"]
    assert db.execute(
        "SELECT COUNT(*) FROM regels WHERE factuur_id=?", (nieuw["id"],)
    ).fetchone()[0] == 2
    assert db.execute("SELECT SUM(totaal) FROM facturen").fetchone()[0] == nieuw["totaal"]
    assert db.execute("SELECT id FROM prullenbak WHERE id=?", (prul,)).fetchone()
    overzicht = client.get("/").data.decode()
    assert f"{nieuw['totaal']:.2f}".replace(".", ",") in overzicht or "540,00" in overzicht


def _pdf_met_dagen(n, post, db):
    """Een rekening met n dagen van 07:00 tot 14:20, en de tekst per pagina."""
    zet_bedrijf(db)
    klant_id, klus_id = _klus(post, db, tarief="45.55")
    begin = date(2026, 1, 5)
    for nummer in range(n):
        dag = begin + timedelta(days=nummer)
        db.execute(
            """INSERT INTO uren (klus_id, datum, van, tot, notitie, pauze_minuten)
               VALUES (?, ?, '07:00', '14:20', '', 0)""",
            (klus_id, dag.isoformat()),
        )
    db.commit()
    dagen = db.execute("SELECT id, datum FROM uren ORDER BY datum").fetchall()
    omschrijvingen = [
        facturen.omschrijving_van_dag(rij["datum"], "07:00", "14:20", 0, "Gevel", "")
        for rij in dagen
    ]
    post("/nieuw", {
        "klant_naam": "Testklant A", "klant_id": str(klant_id), "datum": "2026-03-01",
        "omschrijving": omschrijvingen,
        "type": ["arbeid_uur"] * n,
        "aantal": ["7.33"] * n,
        "prijs": ["45.55"] * n,
        "regel_klus": [str(klus_id)] * n,
        "regel_uur": [str(rij["id"]) for rij in dagen],
    })
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    return paginas_uit_pdf(facturen.maak_pdf(factuur_id))


def _heeft(tekst, *woorden):
    return all(woord in tekst for woord in woorden)


@pytest.mark.parametrize("aantal", list(range(25, 46)))
def test_totaal_en_betaalstrook_blijven_samen(post, db, aantal):
    """Het totaal en de strook staan op één pagina, ook als de grens kantelt.

    Bij 32 en 44 dagen viel het totaal nog op de volle pagina en de strook
    alleen op de volgende. Die grens schuift mee met het aantal dagen."""
    paginas = _pdf_met_dagen(aantal, post, db)
    assert len(paginas) >= 2
    # De kopjes staan met spaties tussen de letters: "T O T A A L".
    met_totaal = [i for i, tekst in enumerate(paginas) if "T O T A A L" in tekst]
    met_strook = [i for i, tekst in enumerate(paginas) if "B E T A L E N" in tekst]
    assert met_totaal == met_strook
    assert len(met_totaal) == 1
    blad = paginas[met_totaal[0]]
    # De laatste dagen staan bij het totaal, niet op een blad met alleen de strook.
    assert "Gevel" in blad
    assert all("7 u 20 min" not in tekst for tekst in paginas)
    assert any("07:00" in tekst for tekst in paginas)


def test_geweigerde_opslag_houdt_het_formulier(post, db):
    """Zonder script: de POST zelf zet klant, datum en de andere regels terug."""
    klant_id, klus_id = _klus(post, db)
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-12", "van": "08:00", "tot": "12:00"})
    uur_id = db.execute("SELECT id FROM uren").fetchone()[0]
    formulier = {
        "klant_naam": "Testklant A",
        "klant_id": str(klant_id),
        "klant_adres": "Straat 1",
        "datum": "2026-10-16",
        "vervalt_op": "2026-10-30",
        "betaaltermijn": "ja",
        "betaalmethode": "cash",
        "kenmerk": "tweede scherm",
        "omschrijving": ["GEWEIGERDE DAG", "kit", "eigen buis"],
        "type": ["arbeid_uur", "materiaal", "materiaal"],
        "aantal": ["4", "2", "1"],
        "prijs": ["45", "12.50", "8"],
        "regel_klus": [str(klus_id), "", ""],
        "regel_uur": [str(uur_id), "", ""],
    }
    post("/nieuw", formulier)
    tweede = post("/nieuw", formulier)
    tekst = tweede.data.decode()
    assert "niet nog een keer" in tekst
    assert "niet aangemaakt" in tekst
    assert 'value="Testklant A"' in tekst
    assert 'value="2026-10-16"' in tekst
    assert 'value="2026-10-30"' in tekst
    assert 'value="tweede scherm"' in tekst
    assert "selected" in tekst and "Contant" in tekst
    assert 'value="kit"' in tekst
    assert 'value="eigen buis"' in tekst
    assert 'value="GEWEIGERDE DAG"' not in tekst
    assert db.execute("SELECT COUNT(*) FROM facturen").fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM regels WHERE omschrijving='kit'"
    ).fetchone()[0] == 1


def test_geweigerde_opslag_blijft_staan_met_script(post, db):
    """Met script blijven de teruggezette regels in de samenvatting staan."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    klant_id, klus_id = _klus(post, db)
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-12", "van": "08:00", "tot": "12:00"})
    uur_id = db.execute("SELECT id FROM uren").fetchone()[0]
    formulier = {
        "klant_naam": "Testklant A",
        "klant_id": str(klant_id),
        "datum": "2026-10-16",
        "betaaltermijn": "ja",
        "vervalt_op": "2026-10-30",
        "kenmerk": "tweede scherm",
        "omschrijving": ["GEWEIGERDE DAG", "kit", "eigen buis"],
        "type": ["arbeid_uur", "materiaal", "materiaal"],
        "aantal": ["4", "1", "3"],
        "prijs": ["45", "12.50", "8"],
        "regel_klus": [str(klus_id), "", ""],
        "regel_uur": [str(uur_id), "", ""],
    }
    post("/nieuw", formulier)
    tweede = post("/nieuw", formulier)
    html = tweede.data.decode()
    with sync_playwright() as p:
        browser = p.webkit.launch()
        page = browser.new_page()
        page.set_content(html)
        teksten = page.locator(".regel-tekst").all_inner_texts()
        browser.close()
    assert "kit" in teksten
    assert "eigen buis" in teksten
    assert "GEWEIGERDE DAG" not in teksten
    assert "Nieuwe regel" not in teksten
