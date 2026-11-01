"""De bon die automatisch gelezen wordt, en het formulier dat voorgevuld klaarstaat.

Hier draait geen OCR. De lezer is een eigen container; wat die antwoordt wordt
hier nagedaan door `_vraag_herkenning` te vervangen. Zo testen deze tests wat
de app ermee doet, en de parser heeft zijn eigen tests in `bon_ocr/tests`.
"""
import json
import os

import pytest

from conftest import facturen
from test_scans import bestand, code_maken, png_bytes, stuur


# Wat de lezer van een nette bon maakt. Dezelfde vorm als bon_parser teruggeeft.
GELEZEN = {
    "winkel": "Karwei",
    "datum": "2026-10-02",
    "regels": [
        {"omschrijving": "Spaanplaatschroef 4x40", "aantal": 2,
         "stukprijs": 7.19, "regelbedrag": 14.38},
        {"omschrijving": "Houtlijm 250ml", "aantal": 1,
         "stukprijs": 5.49, "regelbedrag": 5.49},
    ],
    "korting": None,
    "btw": [{"tarief": 21, "bedrag": 3.44}],
    "btw_totaal": 3.44,
    "totaal": 19.87,
    "betaalwijze": "pin",
    "onzeker": [],
    "ocr_regels": ["KAR", "WEI", "Spaanplaatschroef 4x40"],
}


@pytest.fixture
def lezer(monkeypatch):
    """Doet de bonnenlezer na en houdt bij wat er langskwam.

    `BON_OCR_URL` moet gevuld zijn, anders slaat de app het lezen over; het
    adres wordt niet echt gebruikt omdat `_vraag_herkenning` is vervangen.
    """
    gevraagd = []

    def nep(bron, mime):
        gevraagd.append((bron, mime))
        return facturen._schone_herkenning(nep.antwoord)

    nep.antwoord = dict(GELEZEN)
    nep.gevraagd = gevraagd
    monkeypatch.setattr(facturen, "BON_OCR_URL", "http://bon-ocr:8099/lees")
    monkeypatch.setattr(facturen, "_vraag_herkenning", nep)
    return nep


def scan_erin(client, post, db, naam="bon.png"):
    """Eén scan in de inbox, en het id ervan."""
    token = code_maken(client, post)
    stuur(client, token, bestand(png_bytes(), naam))
    return db.execute("SELECT id FROM scans ORDER BY id DESC").fetchone()[0]


def klus_erin(db, naam="Badkamer"):
    klus = db.execute(
        """INSERT INTO klussen (naam, uurtarief, gestart)
           VALUES (?, 0, '2026-10-01')""", (naam,),
    ).lastrowid
    db.commit()
    return klus


def test_een_nieuwe_scan_wordt_meteen_gelezen(client, post, db, lezer):
    scan_erin(client, post, db)
    facturen._wacht_op_herkenning()
    assert len(lezer.gevraagd) == 1
    bron, mime = lezer.gevraagd[0]
    assert mime == "image/png"
    assert os.path.basename(os.path.dirname(bron)) == "scans"


def test_het_formulier_staat_voorgevuld(client, post, db, lezer):
    klus_erin(db)
    scan_id = scan_erin(client, post, db)
    facturen._wacht_op_herkenning()
    pagina = client.get(f"/scans/{scan_id}/bon").data.decode()
    assert 'value="Karwei"' in pagina
    assert 'value="2026-10-02"' in pagina
    assert 'value="19.87"' in pagina
    assert 'value="Spaanplaatschroef 4x40"' in pagina
    assert 'value="7.19"' in pagina
    # Twee stuks, niet '2.0'.
    assert 'value="2"' in pagina
    # Btw en betaalwijze staan er om te vergelijken, niet om te bewaren.
    assert "21% btw &euro; 3,44" in pagina
    assert "pin" in pagina


def test_de_regels_en_de_datum_komen_in_de_database(client, post, db, lezer):
    klus = klus_erin(db)
    scan_id = scan_erin(client, post, db)
    facturen._wacht_op_herkenning()
    antwoord = post(f"/scans/{scan_id}/bon", {
        "klus_id": klus,
        "omschrijving": "Karwei",
        "wanneer": "2026-10-02",
        "bedrag": "19,87",
        "materiaal_omschrijving": ["Spaanplaatschroef 4x40", "Houtlijm 250ml"],
        "materiaal_aantal": ["2", "1"],
        "materiaal_prijs": ["7,19", "5,49"],
    })
    assert antwoord.status_code == 302
    bon = db.execute("SELECT * FROM inkopen").fetchone()
    assert bon["omschrijving"] == "Karwei"
    assert bon["bedrag"] == 19.87
    # De datum van de bon, niet van vandaag.
    assert bon["toegevoegd"] == "2026-10-02"
    regels = db.execute(
        "SELECT omschrijving, aantal, prijs FROM inkoop_materialen ORDER BY id"
    ).fetchall()
    assert [(r["omschrijving"], r["aantal"], r["prijs"]) for r in regels] == [
        ("Spaanplaatschroef 4x40", 2.0, 7.19),
        ("Houtlijm 250ml", 1.0, 5.49),
    ]
    assert klus  # de bon hangt aan de gekozen klus
    assert bon["klus_id"] == klus


def test_een_lege_regel_wordt_niet_bewaard(client, post, db, lezer):
    """Een leeg veld dat is blijven staan hoort geen materiaalregel te worden."""
    klus = klus_erin(db)
    scan_id = scan_erin(client, post, db)
    facturen._wacht_op_herkenning()
    post(f"/scans/{scan_id}/bon", {
        "klus_id": klus, "omschrijving": "Karwei", "bedrag": "19,87",
        "materiaal_omschrijving": ["Houtlijm 250ml", "  "],
        "materiaal_aantal": ["1", "1"],
        "materiaal_prijs": ["5,49", ""],
    })
    assert db.execute("SELECT COUNT(*) FROM inkoop_materialen").fetchone()[0] == 1


def test_onzekere_velden_worden_gemarkeerd(client, post, db, lezer):
    lezer.antwoord = dict(GELEZEN, datum=None, onzeker=["datum", "totaal"])
    klus_erin(db)
    scan_id = scan_erin(client, post, db)
    facturen._wacht_op_herkenning()
    pagina = client.get(f"/scans/{scan_id}/bon").data.decode()
    assert 'aria-describedby="datum-nakijken"' in pagina
    assert "De datum stond niet op de bon" in pagina
    assert 'aria-describedby="bedrag-nakijken"' in pagina
    # De winkel was wel zeker, dus die krijgt geen streep.
    assert 'aria-describedby="omschrijving-nakijken"' not in pagina


def test_een_btw_die_niet_klopt_staat_in_de_leesregel(client, post, db, lezer):
    """Btw heeft geen eigen veld (we bewaren het niet), dus de waarschuwing
    hoort bij de regel waar het bedrag staat. Zonder dit zie je nergens dat de
    controle op het totaal is gevallen."""
    lezer.antwoord = dict(GELEZEN, btw=[{"tarief": 21, "bedrag": 7.28}],
                          btw_totaal=7.28, onzeker=["btw"])
    klus_erin(db)
    scan_id = scan_erin(client, post, db)
    facturen._wacht_op_herkenning()
    pagina = client.get(f"/scans/{scan_id}/bon").data.decode()
    assert "De btw klopt niet met het totaal" in pagina


def test_zonder_lezer_werkt_het_formulier_gewoon(client, post, db, monkeypatch):
    """Staat de container er niet, dan is dit precies de app van vóór 2.2.0."""
    monkeypatch.setattr(facturen, "BON_OCR_URL", "")
    klus = klus_erin(db)
    scan_id = scan_erin(client, post, db, naam="bouwmarkt.png")
    pagina = client.get(f"/scans/{scan_id}/bon").data.decode()
    assert 'value="bouwmarkt"' in pagina
    assert "De bon wordt gelezen" not in pagina
    assert "Gelezen:" not in pagina
    assert post(f"/scans/{scan_id}/bon", {
        "klus_id": klus, "omschrijving": "Bouwmarkt", "bedrag": "12,50",
    }).status_code == 302
    assert db.execute("SELECT bedrag FROM inkopen").fetchone()[0] == 12.5


def test_de_instelling_zet_het_lezen_uit(client, post, db, lezer):
    db.execute("UPDATE settings SET bon_herkenning=0 WHERE id=1")
    db.commit()
    scan_erin(client, post, db)
    facturen._wacht_op_herkenning()
    assert lezer.gevraagd == []


def test_een_lezer_die_niets_teruggeeft_laat_de_app_doorwerken(client, post, db,
                                                               monkeypatch):
    monkeypatch.setattr(facturen, "BON_OCR_URL", "http://bon-ocr:8099/lees")
    monkeypatch.setattr(facturen, "_vraag_herkenning", lambda bron, mime: None)
    klus = klus_erin(db)
    scan_id = scan_erin(client, post, db, naam="onleesbaar.png")
    facturen._wacht_op_herkenning()
    pagina = client.get(f"/scans/{scan_id}/bon").data.decode()
    assert 'value="onleesbaar"' in pagina
    assert post(f"/scans/{scan_id}/bon", {
        "klus_id": klus, "omschrijving": "Bon", "bedrag": "5",
    }).status_code == 302


def test_een_mislukte_poging_wordt_niet_herhaald(client, post, db, monkeypatch):
    """Anders probeert elke paginaweergave het opnieuw, dertig seconden per keer."""
    pogingen = []

    def nep(bron, mime):
        pogingen.append(bron)
        return None

    monkeypatch.setattr(facturen, "BON_OCR_URL", "http://bon-ocr:8099/lees")
    monkeypatch.setattr(facturen, "_vraag_herkenning", nep)
    scan_id = scan_erin(client, post, db)
    facturen._wacht_op_herkenning()
    client.get("/scans")
    client.get(f"/scans/{scan_id}/bon")
    facturen._wacht_op_herkenning()
    assert len(pogingen) == 1


def test_een_scan_van_voor_de_update_wordt_bij_de_inbox_gelezen(client, post, db,
                                                                lezer):
    """De scan stond er al; pas bij het openen van de inbox komt hij langs."""
    with_uit = facturen.get_settings()
    assert with_uit  # instellingen staan klaar
    naam = "oud.png"
    opslag = "aaaabbbbccccdddd.png"
    with open(os.path.join(facturen.SCAN_DIR, opslag), "wb") as doel:
        doel.write(png_bytes())
    db.execute(
        """INSERT INTO scans (bestand, naam, soort, grootte, ontvangen)
           VALUES (?, ?, 'png', 100, '2026-10-01T10:00:00')""",
        (opslag, naam),
    )
    db.commit()
    assert lezer.gevraagd == []
    client.get("/scans")
    facturen._wacht_op_herkenning()
    assert len(lezer.gevraagd) == 1


def test_een_vervangen_scan_wordt_opnieuw_gelezen(client, post, db, lezer):
    scan_id = scan_erin(client, post, db)
    facturen._wacht_op_herkenning()
    rij = db.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
    assert facturen._lees_herkenning(rij) is not None
    # Een ander bestand onder dezelfde naam: de oude uitslag hoort niet meer
    # bij deze bon.
    with open(os.path.join(facturen.SCAN_DIR, rij["bestand"]), "wb") as doel:
        doel.write(png_bytes(breedte=16, hoogte=16, rgb=(10, 10, 10)))
    assert facturen._lees_herkenning(rij) is None
    client.get("/scans")
    facturen._wacht_op_herkenning()
    assert len(lezer.gevraagd) == 2


def test_een_kapot_antwoord_valt_weg(client, post, db, monkeypatch):
    """De lezer is van ons, maar wat hieruit komt gaat de database in."""
    monkeypatch.setattr(facturen, "BON_OCR_URL", "http://bon-ocr:8099/lees")
    assert facturen._schone_herkenning("geen dict") is None
    assert facturen._schone_herkenning([1, 2, 3]) is None
    uit = facturen._schone_herkenning({
        "winkel": "x" * 500,
        "totaal": "geen getal",
        "regels": [{"omschrijving": "Goed", "aantal": 1, "stukprijs": 1.0,
                    "regelbedrag": 1.0},
                   {"omschrijving": "Zonder bedrag"},
                   "helemaal geen regel"],
        "btw": [{"tarief": 21, "bedrag": 0.21}],
        "onzeker": ["totaal"],
    })
    assert len(uit["winkel"]) == facturen._BON_MAX_TEKST
    assert uit["totaal"] is None
    assert len(uit["regels"]) == 1
    assert uit["regels"][0]["omschrijving"] == "Goed"


def test_te_veel_regels_worden_afgekapt():
    veel = [{"omschrijving": f"Regel {i}", "aantal": 1, "stukprijs": 1.0,
             "regelbedrag": 1.0} for i in range(500)]
    uit = facturen._schone_herkenning({"regels": veel})
    assert len(uit["regels"]) == facturen._BON_MAX_REGELS


def test_een_heic_scan_wordt_niet_naar_de_lezer_gestuurd(client, post, db, lezer):
    """De lezer kan HEIC niet lezen; een verzoek sturen dat zeker misgaat
    kost alleen tijd."""
    opslag = "eeeeffffgggghhhh.heic"
    with open(os.path.join(facturen.SCAN_DIR, opslag), "wb") as doel:
        doel.write(b"ftypheic" + b"\x00" * 64)
    db.execute(
        """INSERT INTO scans (bestand, naam, soort, grootte, ontvangen)
           VALUES (?, 'foto.heic', 'heic', 72, '2026-10-01T10:00:00')""",
        (opslag,),
    )
    db.commit()
    client.get("/scans")
    facturen._wacht_op_herkenning()
    assert lezer.gevraagd == []


def test_het_adres_om_na_te_vragen(client, post, db, lezer):
    scan_id = scan_erin(client, post, db)
    facturen._wacht_op_herkenning()
    antwoord = client.get(f"/scans/{scan_id}/herkenning")
    assert antwoord.status_code == 200
    uit = json.loads(antwoord.data)
    assert uit["klaar"] is True
    assert uit["bon"]["winkel"] == "Karwei"
    assert antwoord.headers["Cache-Control"] == "no-store"
    # Een scan die niet bestaat geeft geen uitslag en geen fout in het log.
    assert client.get("/scans/9999/herkenning").status_code == 404


def test_de_uitslag_verdwijnt_met_de_scan(client, post, db, lezer):
    klus = klus_erin(db)
    scan_id = scan_erin(client, post, db)
    facturen._wacht_op_herkenning()
    rij = db.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
    gelezen, stempel, _mislukt = facturen._bon_paden(rij["bestand"])
    assert os.path.isfile(gelezen) and os.path.isfile(stempel)
    post(f"/scans/{scan_id}/bon",
         {"klus_id": klus, "omschrijving": "Karwei", "bedrag": "19,87"})
    # Het bestand is naar de bijlagen verhuisd; de uitslag hoort niet achter
    # te blijven in de scanmap.
    assert not os.path.isfile(gelezen)
    assert not os.path.isfile(stempel)


def test_de_uitslag_verdwijnt_als_de_scan_verwijderd_wordt(client, post, db, lezer):
    scan_id = scan_erin(client, post, db)
    facturen._wacht_op_herkenning()
    rij = db.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
    gelezen = facturen._bon_paden(rij["bestand"])[0]
    assert os.path.isfile(gelezen)
    post(f"/scans/{scan_id}/verwijder", {})
    assert not os.path.isfile(gelezen)


def test_de_correctie_wordt_opgeschreven(client, post, db, lezer):
    """Wat de lezer zei en wat Stone ervan maakte, als trainingsset voor later."""
    if os.path.isfile(facturen.CORRECTIE_LOG):
        os.remove(facturen.CORRECTIE_LOG)
    klus = klus_erin(db)
    scan_id = scan_erin(client, post, db)
    facturen._wacht_op_herkenning()
    post(f"/scans/{scan_id}/bon", {
        "klus_id": klus,
        "omschrijving": "Karwei grote maat",
        "wanneer": "2026-10-02",
        "bedrag": "19,87",
        "materiaal_omschrijving": ["Spaanplaatschroef 4x40"],
        "materiaal_aantal": ["2"],
        "materiaal_prijs": ["7,19"],
    })
    with open(facturen.CORRECTIE_LOG, encoding="utf-8") as bron:
        regels = [json.loads(r) for r in bron if r.strip()]
    assert len(regels) == 1
    assert regels[0]["gelezen"]["winkel"] == "Karwei"
    assert regels[0]["bewaard"]["omschrijving"] == "Karwei grote maat"
    assert regels[0]["bewaard"]["bedrag"] == 19.87
    assert regels[0]["regels"][0]["omschrijving"] == "Spaanplaatschroef 4x40"
    os.remove(facturen.CORRECTIE_LOG)


def test_zonder_uitslag_wordt_er_niets_opgeschreven(client, post, db, monkeypatch):
    """Geen lezer, geen voorbeeld om van te leren."""
    monkeypatch.setattr(facturen, "BON_OCR_URL", "")
    if os.path.isfile(facturen.CORRECTIE_LOG):
        os.remove(facturen.CORRECTIE_LOG)
    klus = klus_erin(db)
    scan_id = scan_erin(client, post, db)
    post(f"/scans/{scan_id}/bon",
         {"klus_id": klus, "omschrijving": "Bon", "bedrag": "5"})
    assert not os.path.isfile(facturen.CORRECTIE_LOG)


def test_een_onschrijfbare_datamap_kost_geen_bon(client, post, db, lezer,
                                                 monkeypatch):
    """De bon staat al in de database; de aantekening is bijzaak."""
    monkeypatch.setattr(facturen, "CORRECTIE_LOG", "/niet/bestaande/map/log.jsonl")
    klus = klus_erin(db)
    scan_id = scan_erin(client, post, db)
    facturen._wacht_op_herkenning()
    antwoord = post(f"/scans/{scan_id}/bon",
                    {"klus_id": klus, "omschrijving": "Karwei", "bedrag": "19,87"})
    assert antwoord.status_code == 302
    assert db.execute("SELECT COUNT(*) FROM inkopen").fetchone()[0] == 1


def test_de_schakelaar_staat_in_de_instellingen(client, post, db, monkeypatch):
    monkeypatch.setattr(facturen, "BON_OCR_URL", "http://bon-ocr:8099/lees")
    pagina = client.get("/instellingen").data.decode()
    assert 'name="bon_herkenning"' in pagina
    post("/instellingen", {"naam": "Hogedruk", "smtp_port": "587",
                           "bon_herkenning_aanwezig": "1"})
    assert db.execute("SELECT bon_herkenning FROM settings").fetchone()[0] == 0
    post("/instellingen", {"naam": "Hogedruk", "smtp_port": "587",
                           "bon_herkenning_aanwezig": "1",
                           "bon_herkenning": "ja"})
    assert db.execute("SELECT bon_herkenning FROM settings").fetchone()[0] == 1


def test_zonder_lezer_staat_de_schakelaar_er_niet(client, post, db, monkeypatch):
    monkeypatch.setattr(facturen, "BON_OCR_URL", "")
    assert 'name="bon_herkenning"' not in client.get("/instellingen").data.decode()


def test_opslaan_zonder_de_schakelaar_laat_de_stand_staan(client, post, db):
    """De schakelaar staat alleen op de pagina als er een lezer is. Opslaan van
    de andere instellingen mag hem dan niet stil uitzetten."""
    assert db.execute("SELECT bon_herkenning FROM settings").fetchone()[0] == 1
    post("/instellingen", {"naam": "Hogedruk", "smtp_port": "587"})
    assert db.execute("SELECT bon_herkenning FROM settings").fetchone()[0] == 1
