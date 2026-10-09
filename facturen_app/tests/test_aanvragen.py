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


def formulier_rond(inhoud, actie):
    """De openingstag van het formulier waarvan de actie dit stuk bevat."""
    gevonden = re.search(
        rf'<form method="post" action="[^"]*{re.escape(actie)}"[^>]*>', inhoud)
    assert gevonden, actie
    return gevonden.group(0)


def test_het_vinkje_zelf_zet_de_intake_aan(client, post, db):
    """Geen losse knop meer: het vakje is de knop, ook als het nog leeg is."""
    klus_id = maak_aanvraag(post, db)
    inhoud = client.get(f"/klus/{klus_id}").data.decode()

    assert 'class="vink-knop"' in inhoud
    assert 'aria-pressed="false"' in inhoud
    # De naam blijft gelijk; de stand zit alleen in aria-pressed.
    assert 'aria-label="Intake geweest"' in inhoud
    assert 'aria-label="Intake afvinken"' not in inhoud
    assert "data-bevestig" not in formulier_rond(inhoud, f"/klus/{klus_id}/intake")
    assert ">Toch niet<" not in inhoud
    assert ">Afvinken<" not in inhoud


def test_uitvinken_vraagt_eerst_omdat_de_datum_dan_weg_is(client, post, db):
    """Aanvinken is één tik. Uitvinken wist de datum, dus daar vraagt de app het."""
    klus_id = maak_aanvraag(post, db)
    post(f"/klus/{klus_id}/intake")
    inhoud = client.get(f"/klus/{klus_id}").data.decode()

    formulier = formulier_rond(inhoud, f"/klus/{klus_id}/intake")
    assert 'aria-pressed="true"' in inhoud
    assert 'aria-label="Intake geweest"' in inhoud
    assert 'aria-label="Intake weer openzetten"' not in inhoud
    assert "Intake weer openzetten? De datum gaat dan weg." in formulier
    assert ">Toch niet<" not in inhoud


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


def test_een_gestarte_klus_zet_je_later_weer_terug(post, db):
    """Niet alleen meteen erna: de startdatum gaat terug naar de aanvraag."""
    klus_id = maak_aanvraag(post, db)
    aangevraagd = (date.today() - timedelta(days=10)).isoformat()
    db.execute("UPDATE klussen SET aangevraagd_op=?, gestart=? WHERE id=?",
               (aangevraagd, aangevraagd, klus_id))
    db.commit()
    post(f"/klus/{klus_id}/status", {"naar": "open"})

    post(f"/klus/{klus_id}/status", {"naar": "aangevraagd"})
    rij = db.execute("SELECT status, gestart, aangevraagd_op FROM klussen WHERE id=?",
                     (klus_id,)).fetchone()
    assert rij["status"] == "aangevraagd"
    assert rij["gestart"] == aangevraagd
    assert rij["aangevraagd_op"] == aangevraagd


def test_terugzetten_wist_dagen_notities_en_bonnen_niet(post, db, client):
    """Die blijven bewaard. De dagen gaan alleen even uit beeld."""
    klus_id = maak_aanvraag(post, db)
    post(f"/klus/{klus_id}/status", {"naar": "open"})
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-01", "van": "08:00", "tot": "12:00",
                                  "notitie": "Voorgevel"})
    post(f"/klus/{klus_id}/inkoop", {"omschrijving": "Tegellijm", "bedrag": "18.50"})
    db.execute("INSERT INTO notities (klus_id, wanneer, tekst) VALUES (?, ?, ?)",
               (klus_id, "2026-10-01", "Sleutel bij de buren"))
    db.commit()

    antwoord = post(f"/klus/{klus_id}/status", {"naar": "aangevraagd"},
                    follow_redirects=True)
    assert db.execute("SELECT COUNT(*) FROM uren WHERE klus_id=?",
                      (klus_id,)).fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM inkopen WHERE klus_id=?",
                      (klus_id,)).fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM notities WHERE klus_id=?",
                      (klus_id,)).fetchone()[0] == 1
    assert "De gewerkte dagen blijven bewaard." in antwoord.data.decode()

    inhoud = client.get(f"/klus/{klus_id}").data.decode()
    assert "<h2>Gewerkte dagen</h2>" not in inhoud
    assert "<h2>Hoe ver staat het</h2>" in inhoud
    assert "1 gewerkte dag bewaard" in inhoud
    assert "Tegellijm" in inhoud
    assert "Sleutel bij de buren" in inhoud


def test_de_vraag_noemt_dagen_op_een_rekening(post, db, client):
    """Die blijven aan de rekening hangen, maar je ziet ze op de klus even niet."""
    klus_id = maak_aanvraag(post, db)
    post(f"/klus/{klus_id}/status", {"naar": "open"})
    db.execute("""INSERT INTO uren (klus_id, datum, van, tot, factuur_id)
                  VALUES (?, '2026-10-01', '08:00', '12:00', 1)""", (klus_id,))
    db.commit()

    inhoud = client.get(f"/klus/{klus_id}").data.decode()
    assert "ook de dagen die al op een rekening staan" in inhoud
    assert ">Weer als aanvraag<" in inhoud


def test_zonder_dagen_vraagt_de_knop_alleen_naar_de_aanvraag(post, db, client):
    klus_id = maak_aanvraag(post, db)
    post(f"/klus/{klus_id}/status", {"naar": "open"})
    inhoud = client.get(f"/klus/{klus_id}").data.decode()
    assert "Notities en bonnen blijven staan." in inhoud
    assert "gewerkte dagen blijven bewaard" not in inhoud


def test_de_lijst_heeft_dezelfde_knop_alleen_bij_een_lopende_klus(post, db, client):
    """Afgerond heropen je eerst. Een aanvraag heeft de knop niet: die is al een aanvraag."""
    open_id = maak_aanvraag(post, db, naam="Loopt")
    post(f"/klus/{open_id}/status", {"naar": "open"})
    maak_aanvraag(post, db, naam="Wacht nog")
    af_id = maak_aanvraag(post, db, naam="Klaar")
    post(f"/klus/{af_id}/status", {"naar": "open"})
    post(f"/klus/{af_id}/status")

    inhoud = client.get("/klussen").data.decode()
    assert inhoud.count(">Weer als aanvraag<") == 1
    assert "gewerkte dagen blijven bewaard" not in inhoud


def test_de_vraag_bij_bewaarde_dagen():
    assert "bewaard" in facturen.vraag_weer_als_aanvraag(2)
    assert "nog niet gefactureerd" in facturen.vraag_weer_als_aanvraag(2)
    assert "op een rekening" in facturen.vraag_weer_als_aanvraag(2, op_rekening=True)
    assert "Notities en bonnen" in facturen.vraag_weer_als_aanvraag(0)


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
    assert "<h2>Gewerkte dagen</h2>" not in inhoud

    post(f"/klus/{klus_id}/status", {"naar": "open"})
    inhoud = client.get(f"/klus/{klus_id}").data.decode()
    assert "<h2>Gewerkte dagen</h2>" in inhoud
    assert "<h2>Hoe ver staat het</h2>" not in inhoud


def test_bestaande_klussen_blijven_gewoon_lopen(db):
    """De kolommen komen er via een migratie bij; wat er al stond is geen aanvraag."""
    db.execute("""INSERT INTO klussen (naam, uurtarief, status, gestart)
                  VALUES ('Oude klus', 47.5, 'open', '2026-01-05')""")
    db.commit()
    rij = db.execute("SELECT status FROM klussen WHERE naam='Oude klus'").fetchone()
    assert rij["status"] == "open"


def klant_met_naam(db, naam="Jan Jansen"):
    klant_id = db.execute(
        "INSERT INTO klanten (naam, email) VALUES (?, ?)",
        (naam, "jan@example.com"),
    ).lastrowid
    db.commit()
    return klant_id


def klus_voor(post, db, naam, klant_id):
    post("/klussen/nieuw", {
        "naam": naam, "klant_id": str(klant_id), "uurtarief": "50", "aanvraag": "ja",
    })
    return db.execute("SELECT id FROM klussen WHERE naam=?", (naam,)).fetchone()[0]


def test_dagen_van_een_aanvraag_tellen_nergens_mee_als_te_factureren(post, db, client):
    """Terugzetten verbergt de dagen. Die uren horen dan niet bij de klant,
    niet bij 'nog niet gefactureerd' en niet achter een factureerknop."""
    klant_id = klant_met_naam(db)
    lopend = klus_voor(post, db, "Loopt nog", klant_id)
    post(f"/klus/{lopend}/status", {"naar": "open"})
    post(f"/klus/{lopend}/dag", {"datum": "2026-10-01", "van": "08:00", "tot": "10:00"})

    terug = klus_voor(post, db, "Even terug", klant_id)
    post(f"/klus/{terug}/status", {"naar": "open"})
    post(f"/klus/{terug}/dag", {"datum": "2026-10-01", "van": "08:00", "tot": "12:00"})
    post(f"/klus/{terug}/status", {"naar": "aangevraagd"})

    klantpagina = client.get(f"/klant/{klant_id}").data.decode()
    assert "Nog te factureren uren" in klantpagina
    assert "2 u" in klantpagina
    assert "100,00" in klantpagina
    assert "4 u" not in klantpagina
    assert "200,00" not in klantpagina
    assert f"?klus={lopend}" in klantpagina
    assert f"?klus={terug}" not in klantpagina
    assert "Aanvraag" in klantpagina.split('class="chip chip-aangevraagd"', 1)[1][:40]
    # De datums van de verborgen dagen horen niet naast de aanvraag.
    bij_de_aanvraag = klantpagina.split(">Even terug<")[1].split('class="chip chip-aangevraagd"')[0]
    assert "datum" not in bij_de_aanvraag
    bij_de_lopende = klantpagina.split(">Loopt nog<")[1].split("chip-open")[0]
    assert "1 okt 2026" in bij_de_lopende

    overzicht = client.get("/klussen").data.decode()
    assert "Nog niet gefactureerd" in overzicht
    assert "2 u" in overzicht
    assert "4 u" not in overzicht
    assert overzicht.count("Op rekening zetten") == 1

    kluspagina = client.get(f"/klus/{terug}").data.decode()
    assert "Op rekening zetten" not in kluspagina
    assert "Nog niet gefactureerd" not in kluspagina
    assert "1 gewerkte dag bewaard" in kluspagina


def test_een_aanvraag_staat_niet_in_de_urenkiezer(post, db, client):
    klant_id = klant_met_naam(db)
    klus_id = klus_voor(post, db, "Even terug", klant_id)
    post(f"/klus/{klus_id}/status", {"naar": "open"})
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-01", "van": "08:00", "tot": "12:00"})
    post(f"/klus/{klus_id}/status", {"naar": "aangevraagd"})

    assert facturen.geboekte_klussen(klant_id=klant_id) == []
    pagina = client.get(f"/nieuw?klant={klant_id}&klus={klus_id}").data.decode()
    assert f'data-klus="{klus_id}"' not in pagina

    antwoord = post("/nieuw", {
        "klant_id": str(klant_id), "klant_naam": "Jan Jansen", "datum": "2026-10-02",
        "omschrijving": "Uren", "type": "arbeid_uur", "aantal": "4",
        "prijs": "50", "regel_klus": str(klus_id),
    }, follow_redirects=True)
    assert "nog een aanvraag" in antwoord.data.decode()
    assert db.execute("SELECT COUNT(*) FROM facturen").fetchone()[0] == 0
    assert db.execute(
        "SELECT factuur_id FROM uren WHERE klus_id=?", (klus_id,)
    ).fetchone()[0] is None


def test_opslaan_pakt_geen_extra_dagen_van_een_aanvraag(post, db):
    """Een rekening die de dagen al had mag blijven. Nieuwe, verborgen dagen
    komen er niet bij, ook niet als de regel meer uren vraagt."""
    klant_id = klant_met_naam(db)
    klus_id = klus_voor(post, db, "Gevel", klant_id)
    post(f"/klus/{klus_id}/status", {"naar": "open"})
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-01", "van": "08:00", "tot": "12:00"})
    post(f"/klus/{klus_id}/dag", {"datum": "2026-10-02", "van": "08:00", "tot": "12:00"})
    post("/nieuw", {
        "klant_id": str(klant_id), "klant_naam": "Jan Jansen", "datum": "2026-10-03",
        "omschrijving": "Uren gevel", "type": "arbeid_uur", "aantal": "4",
        "prijs": "50", "regel_klus": str(klus_id),
    })
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    post(f"/klus/{klus_id}/status", {"naar": "aangevraagd"})

    antwoord = post(f"/factuur/{factuur_id}/bewerk", {
        "klant_id": str(klant_id), "klant_naam": "Jan Jansen", "datum": "2026-10-03",
        "omschrijving": "Uren gevel", "type": "arbeid_uur", "aantal": "8",
        "prijs": "50", "regel_klus": str(klus_id),
    }, follow_redirects=True)
    assert "nog een aanvraag" not in antwoord.data.decode()
    gekoppeld = [
        rij["datum"] for rij in db.execute(
            """SELECT datum FROM uren WHERE klus_id=? AND factuur_id IS NOT NULL
               ORDER BY datum""",
            (klus_id,),
        )
    ]
    assert gekoppeld == ["2026-10-01"]


def test_opnieuw_starten_zet_de_oorspronkelijke_startdatum_terug(post, db):
    """De dagen die er al staan horen bij de dag waarop het werk begon, niet
    bij de dag waarop je de klus later opnieuw start."""
    klus_id = maak_aanvraag(post, db)
    post(f"/klus/{klus_id}/status", {"naar": "open"})
    begonnen = "2026-09-01"
    db.execute("UPDATE klussen SET gestart=? WHERE id=?", (begonnen, klus_id))
    db.commit()

    post(f"/klus/{klus_id}/status", {"naar": "aangevraagd"})
    rij = db.execute(
        "SELECT status, gestart, gestart_was, aangevraagd_op FROM klussen WHERE id=?",
        (klus_id,),
    ).fetchone()
    assert rij["status"] == "aangevraagd"
    assert rij["gestart"] == rij["aangevraagd_op"]
    assert rij["gestart_was"] == begonnen

    post(f"/klus/{klus_id}/status", {"naar": "open"})
    rij = db.execute(
        "SELECT status, gestart, gestart_was FROM klussen WHERE id=?", (klus_id,)
    ).fetchone()
    assert rij["status"] == "open"
    assert rij["gestart"] == begonnen
    assert rij["gestart_was"] == ""


def test_een_afgeronde_klus_gaat_niet_rechtstreeks_terug_naar_aanvraag(post, db):
    """De knop staat er niet, en een los verzoek slaat het heropenen niet over."""
    klus_id = maak_aanvraag(post, db)
    post(f"/klus/{klus_id}/status", {"naar": "open"})
    post(f"/klus/{klus_id}/status")

    antwoord = post(f"/klus/{klus_id}/status", {"naar": "aangevraagd"},
                    follow_redirects=True)
    assert db.execute(
        "SELECT status FROM klussen WHERE id=?", (klus_id,)
    ).fetchone()[0] == "afgerond"
    assert "Heropen de klus eerst" in antwoord.data.decode()

    post(f"/klus/{klus_id}/status")
    post(f"/klus/{klus_id}/status", {"naar": "aangevraagd"})
    assert db.execute(
        "SELECT status FROM klussen WHERE id=?", (klus_id,)
    ).fetchone()[0] == "aangevraagd"


def test_hoe_lang_iets_al_wacht(monkeypatch):
    """wachtduur leest vandaag(), niet date.today(): die volgt de tijdzone van
    Home Assistant. De test zet die klok dus vast, niet de systeemklok."""
    monkeypatch.setattr(facturen, "vandaag", lambda: date(2026, 8, 24))
    assert facturen.wachtduur("2026-08-24") == "vandaag"
    assert facturen.wachtduur("2026-08-23") == "gisteren"
    assert facturen.wachtduur("2026-08-20") == "4 dagen"
    assert facturen.wachtduur("2026-08-01") == "3 weken"
    assert facturen.wachtduur("2026-05-01") == "3 maanden"
    assert facturen.wachtduur("onzin") == ""
