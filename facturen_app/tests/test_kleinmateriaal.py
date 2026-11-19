"""Klein materiaal van een bon als één post op de rekening.

Regels van een bon die minder kosten dan de grens uit Instellingen (standaard
€ 20) komen op de PDF samen als "Klein-, bevestigings- en montagemateriaal".
Het regelbedrag telt, niet de stukprijs: veertig balken van € 5 blijven een
eigen regel. In de app blijven de regels los, met per regel de keuze
automatisch, altijd los of altijd mee. Pas vanaf twee regels wordt het een post.
"""
from conftest import facturen
from test_pdf import tekst_uit_pdf, zet_bedrijf


POST = "Klein-, bevestigings- en montagemateriaal (posten onder 20 euro)"


def regel(omschrijving, subtotaal, type="materiaal", bon=1, bundel="auto", id=None,
          aantal=1, materiaal_id=None):
    """Een regel zoals de PDF hem uit de database krijgt."""
    return {"id": id, "omschrijving": omschrijving, "type": type, "aantal": aantal,
            "prijs": subtotaal / aantal if aantal else subtotaal, "subtotaal": subtotaal,
            "inkoop_id": bon, "bon_id": None, "uur_id": None, "bundel": bundel,
            "materiaal_id": materiaal_id}


def namen(regels):
    return [r["omschrijving"] for r in regels]


# ---------- Wat er samengaat ----------

def test_twee_kleine_bonregels_worden_een_post():
    uit = facturen.bundel_kleinmateriaal(
        [regel("Schroeven", 4.5, id=1), regel("Pluggen", 3.25, id=2)], 2000)
    assert namen(uit) == [POST]
    assert uit[0]["subtotaal"] == 7.75
    assert uit[0]["aantal"] == 1
    assert uit[0]["bronnen"] == [1, 2]


def test_de_grens_is_strikt_kleiner():
    """€ 19,99 gaat mee, € 20,00 niet."""
    uit = facturen.bundel_kleinmateriaal(
        [regel("A", 19.99), regel("B", 1.0), regel("C", 20.0)], 2000)
    assert namen(uit) == [POST, "C"]
    assert uit[0]["subtotaal"] == 20.99


def test_het_regelbedrag_telt_niet_de_stukprijs():
    """Veertig balken van € 5 kosten € 200: die horen op een eigen regel."""
    uit = facturen.bundel_kleinmateriaal(
        [regel("Balken", 200.0, aantal=40), regel("Schroeven", 4.5), regel("Pluggen", 2.0)],
        2000)
    assert namen(uit) == ["Balken", POST]


def test_een_enkele_kleine_regel_blijft_los():
    """Eén ding onder een verzamelnaam verbergt alleen wat het is."""
    uit = facturen.bundel_kleinmateriaal(
        [regel("Schroeven", 4.5), regel("Tegels", 80.0)], 2000)
    assert namen(uit) == ["Schroeven", "Tegels"]


def test_zelf_getypt_materiaal_blijft_los():
    uit = facturen.bundel_kleinmateriaal(
        [regel("Schroeven", 4.5, bon=None), regel("Pluggen", 2.0, bon=None)], 2000)
    assert namen(uit) == ["Schroeven", "Pluggen"]


def test_een_regel_met_alleen_bon_id_telt_ook_als_bonregel():
    """De tweede regel van een bon draagt de bon in bon_id, niet in inkoop_id."""
    tweede = regel("Pluggen", 2.0, bon=None)
    tweede["bon_id"] = 1
    uit = facturen.bundel_kleinmateriaal([regel("Schroeven", 4.5), tweede], 2000)
    assert namen(uit) == [POST]


def test_arbeid_en_een_hele_bon_gaan_nooit_mee():
    uit = facturen.bundel_kleinmateriaal(
        [regel("Voorrijden", 15.0, type="arbeid_klus", bon=None),
         regel("Bon Gamma", 12.0, type="bon"),
         regel("Schroeven", 4.5), regel("Pluggen", 2.0)], 2000)
    assert namen(uit) == ["Voorrijden", "Bon Gamma", POST]


def test_altijd_los_blijft_los_onder_de_grens():
    uit = facturen.bundel_kleinmateriaal(
        [regel("Schroeven", 4.5), regel("Kit", 6.0, bundel="los"), regel("Pluggen", 2.0)],
        2000)
    assert namen(uit) == [POST, "Kit"]
    assert uit[0]["subtotaal"] == 6.5


def test_altijd_mee_gaat_mee_boven_de_grens():
    uit = facturen.bundel_kleinmateriaal(
        [regel("Schroeven", 4.5), regel("Boormachine", 89.0, bundel="mee")], 2000)
    assert namen(uit) == [POST]
    assert uit[0]["subtotaal"] == 93.5


def test_grens_nul_zet_alleen_automatisch_uit():
    uit = facturen.bundel_kleinmateriaal(
        [regel("Schroeven", 4.5), regel("Pluggen", 2.0)], 0)
    assert namen(uit) == ["Schroeven", "Pluggen"]
    uit = facturen.bundel_kleinmateriaal(
        [regel("Schroeven", 4.5, bundel="mee"), regel("Pluggen", 2.0, bundel="mee")], 0)
    assert namen(uit) == [POST.replace("onder 20 euro", "onder 0 euro")]


def test_de_som_is_in_hele_centen():
    """Met floats is 3 × 0,10 gelijk aan 0,30000000000000004."""
    uit = facturen.bundel_kleinmateriaal(
        [regel("Ring", 0.1), regel("Ring", 0.1), regel("Ring", 0.1)], 2000)
    assert uit[0]["centen"] == 30
    assert uit[0]["subtotaal"] == 0.3


def test_de_post_staat_op_de_plek_van_de_eerste_kleine_regel():
    uit = facturen.bundel_kleinmateriaal(
        [regel("Tegels", 80.0), regel("Schroeven", 4.5), regel("Lijm", 45.0),
         regel("Pluggen", 2.0)], 2000)
    assert namen(uit) == ["Tegels", POST, "Lijm"]


def test_de_omschrijving_volgt_de_grens():
    assert facturen.omschrijving_kleinmateriaal(1500).endswith("(posten onder 15 euro)")
    assert facturen.omschrijving_kleinmateriaal(1250).endswith("(posten onder 12,50 euro)")


def test_een_onbekende_keuze_is_automatisch():
    assert facturen.bundelkeuze("raar") == "auto"
    assert facturen.bundelkeuze(None) == "auto"
    assert facturen.bundelkeuze("los") == "los"


# ---------- Opslaan en de koppeling naar de bonregel ----------

def bon_met_vier_materialen(db):
    """Twee kleine en twee grote regels, zoals een gewone bouwmarktbon."""
    klus_id = db.execute(
        "INSERT INTO klussen (naam, gestart) VALUES ('Schutting', '2026-08-01')").lastrowid
    inkoop_id = db.execute(
        """INSERT INTO inkopen (klus_id, omschrijving, bedrag, toegevoegd)
           VALUES (?, 'Bon Gamma', 256.75, '2026-08-02')""", (klus_id,)).lastrowid
    ids = []
    for omschrijving, aantal, prijs in (("Schroeven", 1, 4.5), ("Palen", 4, 12.5),
                                        ("Pluggen", 1, 2.25), ("Schermen", 4, 50.0)):
        ids.append(db.execute(
            """INSERT INTO inkoop_materialen (inkoop_id, omschrijving, aantal, prijs)
               VALUES (?, ?, ?, ?)""", (inkoop_id, omschrijving, aantal, prijs)).lastrowid)
    db.commit()
    return inkoop_id, ids


def rekening_van_bon(inkoop_id, materiaal_ids, keuzes=None):
    keuzes = keuzes or ["auto"] * 4
    return {
        "klant_naam": "Jan", "datum": "2026-08-14",
        "omschrijving": ["Schroeven", "Palen", "Pluggen", "Schermen"],
        "type": ["materiaal"] * 4,
        "aantal": ["1", "4", "1", "4"], "prijs": ["4.5", "12.5", "2.25", "50"],
        "regel_inkoop": [str(inkoop_id)] * 4,
        "regel_bundel": keuzes,
        "regel_materiaal": [str(i) for i in materiaal_ids],
    }


def test_het_menu_geeft_de_bonregel_mee(db):
    inkoop_id, ids = bon_met_vier_materialen(db)
    bon = facturen.geboekte_inkopen()[0]
    assert [m["id"] for m in bon["materialen"]] == ids


def test_de_regel_bewaart_zijn_bonregel_en_keuze(db, post):
    inkoop_id, ids = bon_met_vier_materialen(db)
    post("/nieuw", rekening_van_bon(inkoop_id, ids, ["auto", "los", "mee", "auto"]))
    regels = db.execute("SELECT * FROM regels ORDER BY id").fetchall()
    assert [r["materiaal_id"] for r in regels] == ids
    assert [r["bundel"] for r in regels] == ["auto", "los", "mee", "auto"]


def test_wijzigen_houdt_de_keuze(db, post):
    inkoop_id, ids = bon_met_vier_materialen(db)
    post("/nieuw", rekening_van_bon(inkoop_id, ids))
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    post(f"/factuur/{factuur_id}/bewerk",
         rekening_van_bon(inkoop_id, ids, ["los", "auto", "auto", "auto"]))
    regels = db.execute("SELECT bundel, materiaal_id FROM regels ORDER BY id").fetchall()
    assert [r["bundel"] for r in regels] == ["los", "auto", "auto", "auto"]
    assert [r["materiaal_id"] for r in regels] == ids


def test_het_wijzigformulier_toont_de_keuze(db, post, client):
    inkoop_id, ids = bon_met_vier_materialen(db)
    post("/nieuw", rekening_van_bon(inkoop_id, ids, ["auto", "auto", "auto", "los"]))
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    inhoud = client.get(f"/factuur/{factuur_id}/bewerk").data.decode()
    assert inhoud.count('name="regel_bundel"') == 4
    assert '<option value="los" selected>' in inhoud
    assert f'name="regel_materiaal" value="{ids[0]}"' in inhoud


def test_een_geweigerde_opslag_houdt_de_keuze(db, post):
    """Te veel naar een tweede rekening laat de opslag weigeren. Het scherm komt
    terug zoals het was, met de keuze per regel."""
    db.execute("""UPDATE settings SET iban='NL91 ABNA 0417 1643 00',
                  iban_2='NL20 INGB 0001 2345 67' WHERE id=1""")
    db.commit()
    inkoop_id, ids = bon_met_vier_materialen(db)
    gegevens = rekening_van_bon(inkoop_id, ids, ["auto", "auto", "mee", "los"])
    gegevens.update({"betaalmethode": "bank", "bedrag_iban_2": "9999"})
    inhoud = post("/nieuw", gegevens).data.decode()
    assert db.execute("SELECT COUNT(*) FROM facturen").fetchone()[0] == 0
    assert '<option value="mee" selected>' in inhoud
    assert '<option value="los" selected>' in inhoud
    assert f'name="regel_materiaal" value="{ids[2]}"' in inhoud


def test_zonder_keuze_in_het_formulier_is_het_automatisch(db, post):
    """Een formulier zonder het nieuwe veld (oud tabblad) mag niets breken."""
    inkoop_id, ids = bon_met_vier_materialen(db)
    gegevens = rekening_van_bon(inkoop_id, ids)
    del gegevens["regel_bundel"], gegevens["regel_materiaal"]
    post("/nieuw", gegevens)
    regels = db.execute("SELECT bundel, materiaal_id FROM regels").fetchall()
    assert {r["bundel"] for r in regels} == {"auto"}
    assert {r["materiaal_id"] for r in regels} == {None}


def test_nabouwen_van_de_tabel_houdt_keuze_en_bonregel(db, post):
    inkoop_id, ids = bon_met_vier_materialen(db)
    post("/nieuw", rekening_van_bon(inkoop_id, ids, ["los", "auto", "mee", "auto"]))
    conn = facturen.get_db()
    facturen._herbouw_regels(conn)
    rijen = conn.execute("SELECT bundel, materiaal_id FROM regels ORDER BY id").fetchall()
    conn.close()
    assert [r["bundel"] for r in rijen] == ["los", "auto", "mee", "auto"]
    assert [r["materiaal_id"] for r in rijen] == ids


# ---------- De PDF ----------

def test_de_pdf_toont_de_post_en_niet_de_kleine_namen(db, post):
    zet_bedrijf(db)
    inkoop_id, ids = bon_met_vier_materialen(db)
    post("/nieuw", rekening_van_bon(inkoop_id, ids))
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    tekst = tekst_uit_pdf(facturen.maak_pdf(factuur_id))
    assert "Klein-, bevestigings- en montagemateriaal" in tekst
    assert "1 post" in tekst
    assert "6,75" in tekst  # 4,50 + 2,25
    assert "Schroeven" not in tekst and "Pluggen" not in tekst
    # De grote regels staan er gewoon, en het totaal klopt nog.
    assert "Palen" in tekst and "Schermen" in tekst
    assert "256,75" in tekst


def test_de_regels_op_papier_tellen_op_tot_het_totaal(db, post):
    inkoop_id, ids = bon_met_vier_materialen(db)
    post("/nieuw", rekening_van_bon(inkoop_id, ids))
    factuur = db.execute("SELECT * FROM facturen").fetchone()
    regels = db.execute("SELECT * FROM regels WHERE factuur_id=? ORDER BY id",
                        (factuur["id"],)).fetchall()
    op_papier = facturen.bundel_kleinmateriaal(regels, 2000)
    assert sum(facturen.naar_centen(r["subtotaal"]) for r in op_papier) == \
        facturen.naar_centen(factuur["totaal"])


def test_altijd_los_staat_los_op_de_pdf(db, post):
    zet_bedrijf(db)
    inkoop_id, ids = bon_met_vier_materialen(db)
    post("/nieuw", rekening_van_bon(inkoop_id, ids, ["los", "auto", "auto", "auto"]))
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    tekst = tekst_uit_pdf(facturen.maak_pdf(factuur_id))
    # Alleen Pluggen is nog klein en automatisch: één regel, dus geen post.
    assert "Klein-, bevestigings-" not in tekst
    assert "Schroeven" in tekst and "Pluggen" in tekst


def test_een_andere_grens_in_instellingen(db, post):
    zet_bedrijf(db)
    db.execute("UPDATE settings SET bundel_grens_centen=6000 WHERE id=1")
    db.commit()
    inkoop_id, ids = bon_met_vier_materialen(db)
    post("/nieuw", rekening_van_bon(inkoop_id, ids))
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    tekst = tekst_uit_pdf(facturen.maak_pdf(factuur_id))
    # Palen (€ 50) gaat nu mee, Schermen (€ 200) niet.
    # De omschrijving loopt op de PDF over twee regels: "...(posten onder" en "60 euro)".
    assert "(posten onder" in tekst and "60 euro)" in tekst
    assert "Palen" not in tekst and "Schermen" in tekst


def test_een_offerte_wordt_niet_samengevoegd(db, maak_offerte):
    """Een offerte heeft geen bonnen; daar verandert niets."""
    offerte_id = maak_offerte()
    tekst = tekst_uit_pdf(facturen.maak_offerte_pdf(offerte_id))
    assert "Klein-, bevestigings-" not in tekst


# ---------- In de app ----------

def test_het_overzicht_toont_welke_regels_samengaan(db, post, client):
    inkoop_id, ids = bon_met_vier_materialen(db)
    post("/nieuw", rekening_van_bon(inkoop_id, ids))
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    inhoud = client.get(f"/factuur/{factuur_id}/bewerk").data.decode()
    assert inhoud.count("op de rekening bij klein materiaal") == 2


def test_het_keuzeveld_is_verborgen_bij_een_gewone_regel(client):
    inhoud = client.get("/nieuw").data.decode()
    assert '<div class="field bundel-veld" hidden>' in inhoud


# ---------- Instellingen ----------

def test_de_grens_staat_standaard_op_twintig_euro(db, client):
    assert facturen.get_settings()["bundel_grens_centen"] == 2000
    assert 'name="bundel_grens"' in client.get("/instellingen").data.decode()


def test_instellingen_bewaart_de_grens_in_centen(db, post):
    post("/instellingen", {"naam": "Stone", "bundel_grens": "12,50"})
    assert facturen.get_settings()["bundel_grens_centen"] == 1250


def test_een_ander_formulier_laat_de_grens_staan(db, post):
    """Zonder het veld mag de grens niet stil op 0 springen."""
    db.execute("UPDATE settings SET bundel_grens_centen=1500 WHERE id=1")
    db.commit()
    post("/instellingen", {"naam": "Stone"})
    assert facturen.get_settings()["bundel_grens_centen"] == 1500


def test_een_negatieve_grens_wordt_nul(db, post):
    post("/instellingen", {"naam": "Stone", "bundel_grens": "-5"})
    assert facturen.get_settings()["bundel_grens_centen"] == 0


# ---------- Een bestaande database ----------

def test_een_oude_database_krijgt_de_kolommen(db, post):
    inkoop_id, ids = bon_met_vier_materialen(db)
    post("/nieuw", rekening_van_bon(inkoop_id, ids))
    db.execute("ALTER TABLE regels DROP COLUMN bundel")
    db.execute("ALTER TABLE regels DROP COLUMN materiaal_id")
    db.execute("ALTER TABLE settings DROP COLUMN bundel_grens_centen")
    db.commit()
    facturen.init_db()
    rijen = db.execute("SELECT bundel, materiaal_id FROM regels").fetchall()
    assert {r["bundel"] for r in rijen} == {"auto"}
    assert {r["materiaal_id"] for r in rijen} == {None}
    assert facturen.get_settings()["bundel_grens_centen"] == 2000
