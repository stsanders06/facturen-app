"""Tot drie rekeningnummers op de betaalstrook, met een bedrag per nummer.

De nummers staan in Instellingen. Op de rekening vul je in wat er naar 2 en 3
gaat; rekening 1 krijgt de rest. Wie er maar één heeft, merkt er niets van:
dan blijven het formulier en de strook zoals ze waren.
"""
from reportlab.lib.units import mm

from conftest import facturen
from test_pdf import tekst_uit_pdf


IBAN_1 = "NL91 ABNA 0417 1643 00"
IBAN_2 = "NL20 INGB 0001 2345 67"
IBAN_3 = "NL02 RABO 0123 4567 89"


def zonder_spaties(tekst):
    """De labels op de PDF staan uitgespatieerd; voor het vergelijken zijn de
    spaties alleen maar in de weg."""
    return tekst.replace(" ", "").lower()


def zet_rekeningen(db, tweede=True, derde=True):
    db.execute(
        """UPDATE settings SET naam='Stone Sanders', iban=?, tenaamstelling='S. Sanders',
           iban_2=?, tenaamstelling_2=?, iban_3=?, tenaamstelling_3=? WHERE id=1""",
        (IBAN_1,
         IBAN_2 if tweede else "", "Klusbedrijf De Maas" if tweede else "",
         IBAN_3 if derde else "", "" if derde else ""),
    )
    db.commit()


def verdeel(db, factuur_id, tweede=0.0, derde=0.0):
    db.execute("UPDATE facturen SET bedrag_iban_2=?, bedrag_iban_3=? WHERE id=?",
               (tweede, derde, factuur_id))
    db.commit()


def rekening_formulier(extra=None):
    gegevens = {
        "klant_naam": "Jan Jansen",
        "datum": "2026-08-14",
        "betaalmethode": "bank",
        "omschrijving": "Schutting plaatsen",
        "type": "arbeid_klus",
        "aantal": "1",
        "prijs": "650",
    }
    gegevens.update(extra or {})
    return gegevens


# ---------- Instellingen ----------

def test_instellingen_bewaart_rekening_2_en_3(post, db):
    post("/instellingen", {"naam": "Stone", "iban": IBAN_1, "tenaamstelling": "",
                           "iban_2": IBAN_2, "tenaamstelling_2": "De Maas",
                           "iban_3": IBAN_3, "tenaamstelling_3": ""})
    s = facturen.get_settings()
    assert s["iban"] == IBAN_1
    assert s["iban_2"] == IBAN_2
    assert s["tenaamstelling_2"] == "De Maas"
    assert s["iban_3"] == IBAN_3


def test_instellingen_toont_drie_rekeningen(client, db):
    zet_rekeningen(db)
    inhoud = client.get("/instellingen").data.decode()
    assert 'name="iban"' in inhoud
    assert 'name="iban_2"' in inhoud
    assert 'name="iban_3"' in inhoud
    assert IBAN_2 in inhoud and IBAN_3 in inhoud


def test_een_lege_naam_bij_rekening_3_is_je_eigen_naam(db):
    zet_rekeningen(db)
    namen = {nr: tnv for nr, _iban, tnv in facturen.extra_rekeningen()}
    assert namen == {2: "Klusbedrijf De Maas", 3: "Stone Sanders"}


def test_alleen_rekening_3_ingevuld_telt_ook(db):
    zet_rekeningen(db, tweede=False)
    assert [nr for nr, _i, _t in facturen.extra_rekeningen()] == [3]


# ---------- Wat er op de strook komt ----------

def doc(totaal=650.0, tweede=0.0, derde=0.0, betaald=0.0):
    return {"totaal": totaal, "bedrag_iban_2": tweede, "bedrag_iban_3": derde,
            "betaald": betaald}


def test_rekening_1_krijgt_de_rest(db):
    zet_rekeningen(db)
    delen = facturen.verdeling_op_strook(doc(tweede=237.5, derde=112.5),
                                         facturen.get_settings())
    assert delen == [(IBAN_1, "S. Sanders", 300.0),
                     (IBAN_2, "Klusbedrijf De Maas", 237.5),
                     (IBAN_3, "Stone Sanders", 112.5)]


def test_zonder_bedragen_is_het_een_gewone_strook(db):
    zet_rekeningen(db)
    delen = facturen.verdeling_op_strook(doc(), facturen.get_settings())
    assert delen == [(IBAN_1, "S. Sanders", 650.0)]


def test_alles_naar_rekening_2_geeft_alleen_dat_nummer(db):
    """Rekening 1 met € 0,00 op de strook zou de klant alleen in verwarring brengen."""
    zet_rekeningen(db)
    delen = facturen.verdeling_op_strook(doc(tweede=650.0), facturen.get_settings())
    assert delen == [(IBAN_2, "Klusbedrijf De Maas", 650.0)]


def test_meer_dan_het_totaal_valt_terug_op_rekening_1(db):
    """Een strook die niet optelt is erger dan een zonder verdeling."""
    zet_rekeningen(db)
    delen = facturen.verdeling_op_strook(doc(tweede=500.0, derde=500.0),
                                         facturen.get_settings())
    assert delen == [(IBAN_1, "S. Sanders", 650.0)]


def test_een_deelbetaling_haalt_de_verdeling_weg(db):
    """De app weet niet op welk nummer het binnenkwam, dus ook niet wat er per
    nummer nog moet."""
    zet_rekeningen(db)
    delen = facturen.verdeling_op_strook(doc(tweede=237.5, betaald=100.0),
                                         facturen.get_settings())
    assert [d[0] for d in delen] == [IBAN_1]


def test_een_deelbetaling_met_alles_op_rekening_2_blijft_rekening_2(db):
    zet_rekeningen(db)
    delen = facturen.verdeling_op_strook(doc(tweede=650.0, betaald=100.0),
                                         facturen.get_settings())
    assert [d[0] for d in delen] == [IBAN_2]


def test_een_weggehaalde_rekening_krijgt_niets_meer(db):
    """Haal je rekening 3 uit Instellingen, dan gaat zijn deel naar rekening 1
    in plaats van naar een nummer dat er niet meer is."""
    zet_rekeningen(db, derde=False)
    delen = facturen.verdeling_op_strook(doc(tweede=237.5, derde=112.5),
                                         facturen.get_settings())
    assert delen == [(IBAN_1, "S. Sanders", 412.5),
                     (IBAN_2, "Klusbedrijf De Maas", 237.5)]


# ---------- Het formulier lezen ----------

def test_lees_verdeling_leest_komma_en_punt(db):
    zet_rekeningen(db)
    verdeling, fout = facturen.lees_verdeling(
        {"betaalmethode": "bank", "bedrag_iban_2": "237,50", "bedrag_iban_3": "112.5"},
        650.0, facturen.get_settings())
    assert fout is None
    assert verdeling == {"bedrag_iban_2": 237.5, "bedrag_iban_3": 112.5}


def test_samen_meer_dan_het_totaal_wordt_geweigerd(db):
    zet_rekeningen(db)
    _verdeling, fout = facturen.lees_verdeling(
        {"betaalmethode": "bank", "bedrag_iban_2": "400", "bedrag_iban_3": "300"},
        650.0, facturen.get_settings())
    assert "€ 700,00" in fout and "€ 650,00" in fout


def test_een_negatief_bedrag_wordt_geweigerd(db):
    zet_rekeningen(db)
    _verdeling, fout = facturen.lees_verdeling(
        {"betaalmethode": "bank", "bedrag_iban_2": "-10"}, 650.0, facturen.get_settings())
    assert "negatief" in fout


def test_tekst_in_plaats_van_een_bedrag_wordt_geweigerd(db):
    zet_rekeningen(db)
    _verdeling, fout = facturen.lees_verdeling(
        {"betaalmethode": "bank", "bedrag_iban_3": "veel"}, 650.0, facturen.get_settings())
    assert "rekening 3" in fout


def test_contant_negeert_de_bedragen(db):
    """Contant heeft geen nummers op de strook. Een vergeten bedrag mag het
    opslaan dan niet tegenhouden."""
    zet_rekeningen(db)
    verdeling, fout = facturen.lees_verdeling(
        {"betaalmethode": "cash", "bedrag_iban_2": "9999"}, 650.0, facturen.get_settings())
    assert fout is None
    assert verdeling == {"bedrag_iban_2": 0.0, "bedrag_iban_3": 0.0}


def test_een_bedrag_voor_een_lege_rekening_telt_niet(db):
    zet_rekeningen(db, derde=False)
    verdeling, fout = facturen.lees_verdeling(
        {"betaalmethode": "bank", "bedrag_iban_3": "100"}, 650.0, facturen.get_settings())
    assert fout is None
    assert verdeling["bedrag_iban_3"] == 0.0


# ---------- Opslaan ----------

def test_een_nieuwe_rekening_bewaart_de_verdeling(post, db):
    zet_rekeningen(db)
    post("/nieuw", rekening_formulier({"bedrag_iban_2": "237,50", "bedrag_iban_3": "112,50"}))
    rij = db.execute("SELECT totaal, bedrag_iban_2, bedrag_iban_3 FROM facturen").fetchone()
    assert tuple(rij) == (650.0, 237.5, 112.5)


def test_te_veel_verdeeld_maakt_geen_rekening(post, db):
    zet_rekeningen(db)
    antwoord = post("/nieuw", rekening_formulier({"bedrag_iban_2": "700"}))
    assert db.execute("SELECT COUNT(*) FROM facturen").fetchone()[0] == 0
    inhoud = antwoord.data.decode()
    assert "Maak die bedragen lager" in inhoud
    # Wat je typte staat er nog, anders moet je het opnieuw intikken.
    assert 'value="700"' in inhoud


def test_wijzigen_bewaart_de_verdeling(post, db, maak_factuur):
    zet_rekeningen(db)
    factuur_id = maak_factuur(totaal=121.0)
    post(f"/factuur/{factuur_id}/bewerk", rekening_formulier({"bedrag_iban_2": "100"}))
    rij = db.execute("SELECT totaal, bedrag_iban_2 FROM facturen WHERE id=?",
                     (factuur_id,)).fetchone()
    assert tuple(rij) == (650.0, 100.0)


def test_te_veel_bij_wijzigen_laat_alles_staan(post, db, maak_factuur):
    zet_rekeningen(db)
    factuur_id = maak_factuur(totaal=121.0)
    antwoord = post(f"/factuur/{factuur_id}/bewerk",
                    rekening_formulier({"bedrag_iban_2": "1000"}))
    rij = db.execute("SELECT totaal, bedrag_iban_2 FROM facturen WHERE id=?",
                     (factuur_id,)).fetchone()
    assert tuple(rij) == (121.0, 0.0)
    assert "Maak die bedragen lager" in antwoord.data.decode()


def test_een_kopie_neemt_de_verdeling_mee(post, db, maak_factuur):
    zet_rekeningen(db)
    factuur_id = maak_factuur(totaal=121.0)
    verdeel(db, factuur_id, tweede=21.0)
    post(f"/factuur/{factuur_id}/kopieer")
    rij = db.execute("SELECT bedrag_iban_2 FROM facturen WHERE id != ?",
                     (factuur_id,)).fetchone()
    assert rij[0] == 21.0


# ---------- Het formulier ----------

def test_met_een_rekening_geen_verdeelkaart(client, db):
    """Wie er maar één heeft, ziet het formulier zoals het altijd was."""
    zet_rekeningen(db, tweede=False, derde=False)
    inhoud = client.get("/nieuw").data.decode()
    assert 'id="verdeling"' not in inhoud
    assert "bedrag_iban_2" not in inhoud


def test_met_twee_rekeningen_alleen_een_veld_voor_rekening_2(client, db):
    zet_rekeningen(db, derde=False)
    inhoud = client.get("/nieuw").data.decode()
    assert 'id="verdeling"' in inhoud
    assert 'name="bedrag_iban_2"' in inhoud
    assert 'name="bedrag_iban_3"' not in inhoud
    assert IBAN_1 in inhoud and IBAN_2 in inhoud


def test_met_drie_rekeningen_twee_velden(client, db):
    zet_rekeningen(db)
    inhoud = client.get("/nieuw").data.decode()
    assert 'name="bedrag_iban_2"' in inhoud
    assert 'name="bedrag_iban_3"' in inhoud


def test_het_wijzigformulier_toont_het_bewaarde_bedrag(client, db, maak_factuur):
    zet_rekeningen(db)
    factuur_id = maak_factuur(totaal=121.0)
    verdeel(db, factuur_id, tweede=21.5)
    inhoud = client.get(f"/factuur/{factuur_id}/bewerk").data.decode()
    assert 'value="21,50"' in inhoud


def test_een_offerte_heeft_geen_verdeelkaart(client, db):
    """Een offerte heeft geen betaalstrook, dus ook niets te verdelen."""
    zet_rekeningen(db)
    inhoud = client.get("/offertes/nieuw").data.decode()
    assert 'id="verdeling"' not in inhoud


# ---------- De PDF ----------

def test_drie_rekeningen_op_de_strook(db, maak_factuur):
    zet_rekeningen(db)
    factuur_id = maak_factuur(nummer="2026-041", status="verzonden", totaal=650.0)
    verdeel(db, factuur_id, tweede=237.5, derde=112.5)
    tekst = tekst_uit_pdf(facturen.maak_pdf(factuur_id))
    for iban, bedrag in ((IBAN_1, "300,00"), (IBAN_2, "237,50"), (IBAN_3, "112,50")):
        assert iban in tekst
        assert bedrag in tekst
    assert "Klusbedrijf De Maas" in tekst
    assert "O.v.v." in tekst and "2026-041" in tekst
    # Het grote bedrag is het totaal; dat moet er ook staan.
    assert "tebetalen,totaal" in zonder_spaties(tekst)
    assert "650,00" in tekst


def test_twee_rekeningen_op_de_strook(db, maak_factuur):
    zet_rekeningen(db, derde=False)
    factuur_id = maak_factuur(status="verzonden", totaal=650.0)
    verdeel(db, factuur_id, tweede=237.5)
    tekst = tekst_uit_pdf(facturen.maak_pdf(factuur_id))
    assert IBAN_1 in tekst and "412,50" in tekst
    assert IBAN_2 in tekst and "237,50" in tekst
    assert IBAN_3 not in tekst


def test_met_een_rekening_blijft_de_strook_zoals_hij_was(db, maak_factuur):
    """Geen O.v.v., geen 'totaal' in het vak: precies de oude strook."""
    zet_rekeningen(db, tweede=False, derde=False)
    factuur_id = maak_factuur(status="verzonden", totaal=121.0)
    tekst = tekst_uit_pdf(facturen.maak_pdf(factuur_id))
    assert IBAN_1 in tekst
    assert "T.n.v." in tekst and "S. Sanders" in tekst
    assert "O.v.v." not in tekst
    assert "tebetalen,totaal" not in zonder_spaties(tekst)


def test_extra_rekeningen_zonder_verdeling_geven_de_oude_strook(db, maak_factuur):
    """Rekening 2 en 3 in Instellingen, maar niets verdeeld: dan staat alleen
    rekening 1 erop, net als vroeger."""
    zet_rekeningen(db)
    factuur_id = maak_factuur(status="verzonden", totaal=121.0)
    tekst = tekst_uit_pdf(facturen.maak_pdf(factuur_id))
    assert IBAN_1 in tekst
    assert IBAN_2 not in tekst and IBAN_3 not in tekst


def test_contant_toont_geen_rekeningnummers(db, maak_factuur):
    zet_rekeningen(db)
    factuur_id = maak_factuur(status="verzonden", totaal=650.0)
    verdeel(db, factuur_id, tweede=237.5)
    db.execute("UPDATE facturen SET betaalmethode='cash' WHERE id=?", (factuur_id,))
    db.commit()
    tekst = tekst_uit_pdf(facturen.maak_pdf(factuur_id))
    assert IBAN_1 not in tekst and IBAN_2 not in tekst


def test_een_concept_zet_geen_leeg_kenmerk_op_de_strook(db, maak_factuur):
    """Een concept heeft nog geen nummer; 'O.v.v.' zonder iets erachter is raar."""
    zet_rekeningen(db)
    factuur_id = maak_factuur(nummer="", totaal=650.0)
    verdeel(db, factuur_id, tweede=237.5)
    tekst = tekst_uit_pdf(facturen.maak_pdf(factuur_id))
    assert IBAN_2 in tekst
    assert "O.v.v." not in tekst


def test_drie_rekeningen_blijven_boven_de_btw_regel(db, maak_factuur, monkeypatch):
    """Met het nummer en de naam onder elkaar liep de strook over de btw-regel
    op 14 mm. Alles op de strook hoort daar ruim boven te blijven."""
    zet_rekeningen(db)
    factuur_id = maak_factuur(nummer="2026-041", status="verzonden", totaal=650.0)
    verdeel(db, factuur_id, tweede=237.5, derde=112.5)

    hoogtes = []
    echte = facturen.GenummerdCanvas.drawString

    def onthoud(canvas, x, y, tekst, *args, **kwargs):
        if tekst not in (facturen.BTW_REGEL, facturen.OFFERTE_REGEL):
            hoogtes.append(y)
        return echte(canvas, x, y, tekst, *args, **kwargs)

    monkeypatch.setattr(facturen.GenummerdCanvas, "drawString", onthoud)
    facturen.maak_pdf(factuur_id)
    # De btw-regel staat op 14 mm en is 7 pt hoog; 5 mm ertussen is genoeg lucht.
    assert min(hoogtes) >= 19 * mm
