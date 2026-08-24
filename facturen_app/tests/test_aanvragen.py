"""Een klus begint als aanvraag en loopt door tot hij af is.

Iemand belt dat hij iets wil: dat zat eerst alleen in je hoofd. Nu staat het als
aanvraag in de lijst, met daaronder de stappen die nog moeten gebeuren. Uren zijn
stap vier — die komen pas als de klus echt loopt.
"""
import re
from datetime import date, timedelta

from conftest import facturen


def namen_op_de_pagina(inhoud):
    """Alleen de klusnamen; de meldingen bovenaan bevatten ze ook."""
    return re.findall(r'class="naam">([^<]+)<', inhoud)


def maak_aanvraag(post, db, naam="Gevel reinigen", **extra):
    gegevens = {"naam": naam, "aanvraag": "ja"}
    gegevens.update(extra)
    post("/klussen/nieuw", gegevens)
    return db.execute("SELECT id FROM klussen WHERE naam=?", (naam,)).fetchone()[0]


def test_een_nieuwe_klus_begint_als_aanvraag(post, db):
    klus_id = maak_aanvraag(post, db)
    rij = db.execute("SELECT * FROM klussen WHERE id=?", (klus_id,)).fetchone()
    assert rij["status"] == "aangevraagd"
    assert rij["aangevraagd_op"] == date.today().isoformat()
    assert rij["intake_op"] == ""


def test_je_kunt_er_ook_meteen_aan_beginnen(post, db):
    """Het vinkje uitzetten betekent: deze loopt al."""
    post("/klussen/nieuw", {"naam": "Spoedklus"})
    assert db.execute("SELECT status FROM klussen").fetchone()[0] == "open"


def test_de_intake_gaat_aan_en_weer_uit(post, db):
    klus_id = maak_aanvraag(post, db)

    post(f"/klus/{klus_id}/intake")
    assert db.execute("SELECT intake_op FROM klussen WHERE id=?",
                      (klus_id,)).fetchone()[0] == date.today().isoformat()

    post(f"/klus/{klus_id}/intake")
    assert db.execute("SELECT intake_op FROM klussen WHERE id=?",
                      (klus_id,)).fetchone()[0] == ""


def test_een_offerte_hoeft_niet(post, db):
    """Niet elke klus krijgt er een; je gaat gewoon door naar lopend."""
    klus_id = maak_aanvraag(post, db)
    post(f"/klus/{klus_id}/status", {"naar": "open"})

    rij = db.execute("SELECT status, offerte_id FROM klussen WHERE id=?",
                     (klus_id,)).fetchone()
    assert rij["status"] == "open"
    assert rij["offerte_id"] is None


def test_een_offerte_vanaf_een_aanvraag_blijft_eraan_hangen(post, db):
    """Anders zie je op de klus niet dat die stap is gedaan, en met welke offerte."""
    klus_id = maak_aanvraag(post, db)
    post("/offertes/nieuw", {
        "klant_naam": "Jan Jansen", "datum": "2026-08-14", "voor_klus": str(klus_id),
        "omschrijving": "Gevel reinigen", "type": "arbeid_klus",
        "aantal": "1", "prijs": "895",
    })

    offerte_id = db.execute("SELECT id FROM offertes").fetchone()[0]
    assert db.execute("SELECT offerte_id FROM klussen WHERE id=?",
                      (klus_id,)).fetchone()[0] == offerte_id


def test_een_gewone_offerte_hangt_nergens_aan(post, db):
    maak_aanvraag(post, db)
    post("/offertes/nieuw", {
        "klant_naam": "Jan Jansen", "datum": "2026-08-14",
        "omschrijving": "Iets anders", "type": "arbeid_klus",
        "aantal": "1", "prijs": "100",
    })
    assert db.execute("SELECT offerte_id FROM klussen").fetchone()[0] is None


def test_starten_zet_de_startdatum_op_vandaag(post, db):
    """De aanvraagdatum blijft staan, zodat je ziet hoe lang het heeft geduurd."""
    klus_id = maak_aanvraag(post, db)
    gisteren = (date.today() - timedelta(days=1)).isoformat()
    db.execute("UPDATE klussen SET aangevraagd_op=?, gestart=? WHERE id=?",
               (gisteren, gisteren, klus_id))
    db.commit()

    post(f"/klus/{klus_id}/status", {"naar": "open"})
    rij = db.execute("SELECT * FROM klussen WHERE id=?", (klus_id,)).fetchone()
    assert rij["gestart"] == date.today().isoformat()
    assert rij["aangevraagd_op"] == gisteren


def test_de_knop_zonder_opgave_wisselt_tussen_lopend_en_afgerond(post, db):
    """Zoals hij altijd deed, op de klussen die al liepen."""
    post("/klussen/nieuw", {"naam": "Loopt al"})
    klus_id = db.execute("SELECT id FROM klussen").fetchone()[0]

    post(f"/klus/{klus_id}/status")
    assert db.execute("SELECT status FROM klussen").fetchone()[0] == "afgerond"
    post(f"/klus/{klus_id}/status")
    assert db.execute("SELECT status FROM klussen").fetchone()[0] == "open"


def test_aanvragen_staan_apart_en_de_langst_wachtende_bovenaan(post, db, client):
    for naam in ("Eerste", "Tweede", "Derde"):
        maak_aanvraag(post, db, naam=naam)
    # De eerste ligt er al een week.
    db.execute("UPDATE klussen SET aangevraagd_op=? WHERE naam='Derde'",
               ((date.today() - timedelta(days=7)).isoformat(),))
    db.commit()

    inhoud = client.get("/klussen").data.decode()
    assert namen_op_de_pagina(inhoud) == ["Derde", "Eerste", "Tweede"]
    assert "Aanvragen" in inhoud


def test_een_aanvraag_telt_niet_mee_als_lopende_klus(post, db, client):
    maak_aanvraag(post, db)
    post("/klussen/nieuw", {"naam": "Loopt echt"})

    inhoud = client.get("/klussen").data.decode()
    # Eén lopende klus, één aanvraag — die tellen apart.
    assert ">1<" in inhoud


def test_op_een_aanvraag_staan_geen_uren_maar_wel_de_stappen(post, db, client):
    """Uren zijn stap vier; die blokken horen er pas te staan als de klus loopt."""
    klus_id = maak_aanvraag(post, db)
    inhoud = client.get(f"/klus/{klus_id}").data.decode()
    assert "<h2>Hoe ver staat het</h2>" in inhoud
    assert "<h2>Dag erbij</h2>" not in inhoud

    post(f"/klus/{klus_id}/status", {"naar": "open"})
    inhoud = client.get(f"/klus/{klus_id}").data.decode()
    assert "<h2>Dag erbij</h2>" in inhoud
    assert "<h2>Hoe ver staat het</h2>" not in inhoud


def test_bestaande_klussen_blijven_gewoon_lopen(db):
    """De kolommen komen er via een migratie bij; wat er al stond is geen aanvraag."""
    db.execute("""INSERT INTO klussen (naam, uurtarief, status, gestart)
                  VALUES ('Oude klus', 47.5, 'open', '2026-01-05')""")
    db.commit()
    rij = db.execute("SELECT status FROM klussen WHERE naam='Oude klus'").fetchone()
    assert rij["status"] == "open"


def test_hoe_lang_iets_al_wacht(monkeypatch):
    vandaag = date(2026, 8, 24)

    class NepDatum(date):
        @classmethod
        def today(cls):
            return vandaag

    monkeypatch.setattr(facturen, "date", NepDatum)
    assert facturen.wachtduur("2026-08-24") == "vandaag"
    assert facturen.wachtduur("2026-08-23") == "gisteren"
    assert facturen.wachtduur("2026-08-20") == "4 dagen"
    assert facturen.wachtduur("2026-08-01") == "3 weken"
    assert facturen.wachtduur("2026-05-01") == "3 maanden"
    assert facturen.wachtduur("onzin") == ""
