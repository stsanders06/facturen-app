"""Sturen (mail of WhatsApp), betalen met Tikkie en deels contant, en een bon
met materialen die als losse regels op de rekening komt."""
import os
import re

from PIL import Image

from conftest import facturen
from test_pdf import tekst_uit_pdf, zet_bedrijf


def _aantal_paginas(data):
    return len(re.findall(rb"/Type\s*/Page\b", data))


def _boek(db, factuur_id, bedrag, wijze):
    db.execute(
        """INSERT INTO betalingen (factuur_id, datum, bedrag, notitie, automatisch, wijze)
           VALUES (?, '2026-08-20', ?, '', 0, ?)""",
        (factuur_id, bedrag, wijze),
    )
    db.commit()


# --- Betalen ---------------------------------------------------------------

def test_tikkie_rekening_noemt_het_betaalverzoek_en_geen_iban(db, maak_factuur):
    zet_bedrijf(db)
    factuur_id = maak_factuur()
    db.execute("UPDATE facturen SET betaalmethode='tikkie' WHERE id=?", (factuur_id,))
    db.commit()
    tekst = tekst_uit_pdf(facturen.maak_pdf(factuur_id))
    assert "T I K K I E" in tekst
    assert "Tikkie-betaalverzoek" in tekst
    assert "NL91ABNA0417164300" not in tekst
    assert "121,00" in tekst


def test_betaalde_tikkie_rekening_staat_op_voldaan(db, maak_factuur, post):
    zet_bedrijf(db)
    factuur_id = maak_factuur(status="verzonden")
    db.execute("UPDATE facturen SET betaalmethode='tikkie' WHERE id=?", (factuur_id,))
    db.commit()
    post(f"/factuur/{factuur_id}/betaald")
    wijze = db.execute("SELECT wijze FROM betalingen WHERE factuur_id=?",
                       (factuur_id,)).fetchone()[0]
    assert wijze == "tikkie"
    tekst = tekst_uit_pdf(facturen.maak_pdf(factuur_id))
    assert "Voldaan via Tikkie." in tekst


def test_deels_contant_en_deels_tikkie_staat_verdeeld_op_de_pdf(db, maak_factuur, post):
    zet_bedrijf(db)
    factuur_id = maak_factuur(status="verzonden")
    db.execute("UPDATE facturen SET betaalmethode='tikkie' WHERE id=?", (factuur_id,))
    db.commit()
    post(f"/factuur/{factuur_id}/betalingen",
         {"datum": "2026-08-20", "bedrag": "50", "wijze": "contant"})
    tekst = tekst_uit_pdf(facturen.maak_pdf(factuur_id))
    assert "€ 50,00 contant" in tekst
    assert "71,00" in tekst  # nog te betalen via Tikkie
    post(f"/factuur/{factuur_id}/betaald")
    tekst = tekst_uit_pdf(facturen.maak_pdf(factuur_id))
    assert "Ontvangen: € 50,00 contant, € 71,00 Tikkie." in tekst


def test_bankrekening_met_contant_deel_houdt_de_iban(db, maak_factuur):
    zet_bedrijf(db)
    factuur_id = maak_factuur(status="verzonden")
    _boek(db, factuur_id, 21.0, "contant")
    tekst = tekst_uit_pdf(facturen.maak_pdf(factuur_id))
    assert "NL91ABNA0417164300" in tekst
    assert "€ 21,00 contant" in tekst
    assert "100,00" in tekst


def test_oude_betaling_zonder_wijze_toont_alleen_het_bedrag(db, maak_factuur):
    zet_bedrijf(db)
    factuur_id = maak_factuur(status="verzonden")
    _boek(db, factuur_id, 21.0, "")
    tekst = tekst_uit_pdf(facturen.maak_pdf(factuur_id))
    assert "Al betaald" in tekst
    assert "€ 21,00 contant" not in tekst


def test_onbekende_betaalwijze_wordt_leeg_bewaard(db, maak_factuur, post):
    factuur_id = maak_factuur(status="verzonden")
    post(f"/factuur/{factuur_id}/betalingen",
         {"datum": "2026-08-20", "bedrag": "10", "wijze": "bitcoin"})
    assert db.execute("SELECT wijze FROM betalingen").fetchone()[0] == ""


def test_betalingenpagina_kiest_vooraf_de_wijze_van_de_rekening(db, maak_factuur, client):
    factuur_id = maak_factuur(status="verzonden")
    db.execute("UPDATE facturen SET betaalmethode='cash' WHERE id=?", (factuur_id,))
    db.commit()
    pagina = client.get(f"/factuur/{factuur_id}/betalingen").data.decode()
    assert '<option value="contant" selected>Contant</option>' in pagina
    assert '<option value="tikkie" >Tikkie</option>' in pagina


def test_rekening_aanmaken_met_tikkie(db, post):
    post("/nieuw", {"klant_naam": "Jan", "datum": "2026-08-14", "betaalmethode": "tikkie",
                    "omschrijving": "Werk", "type": "arbeid_klus", "aantal": "1",
                    "prijs": "80"})
    assert db.execute("SELECT betaalmethode FROM facturen").fetchone()[0] == "tikkie"


def test_onbekende_betaalmethode_wordt_bank(db, post):
    post("/nieuw", {"klant_naam": "Jan", "datum": "2026-08-14", "betaalmethode": "goud",
                    "omschrijving": "Werk", "type": "arbeid_klus", "aantal": "1",
                    "prijs": "80"})
    assert db.execute("SELECT betaalmethode FROM facturen").fetchone()[0] == "bank"


def test_formulier_heeft_tikkie_als_keuze(client):
    pagina = client.get("/nieuw").data.decode()
    assert '<option value="tikkie"' in pagina


# --- Sturen ----------------------------------------------------------------

def _bon_op_rekening(db, factuur_id, bestandsnaam, maak_bestand=True):
    klus_id = db.execute("INSERT INTO klussen (naam, gestart) VALUES ('Badkamer', '2026-08-01')"
                         ).lastrowid
    inkoop_id = db.execute(
        """INSERT INTO inkopen (klus_id, omschrijving, bedrag, toegevoegd, factuur_id)
           VALUES (?, 'Karwei', 12.5, '2026-08-02', ?)""", (klus_id, factuur_id)).lastrowid
    pad = os.path.join(facturen.BIJLAGE_DIR, bestandsnaam)
    if maak_bestand:
        if bestandsnaam.endswith(".png"):
            Image.new("RGB", (40, 60), "white").save(pad)
        else:
            open(pad, "wb").write(b"niet echt")
    db.execute(
        """INSERT INTO bijlagen (klus_id, bestand, naam, toegevoegd, inkoop_id)
           VALUES (?, ?, ?, '2026-08-02', ?)""", (klus_id, bestandsnaam, bestandsnaam, inkoop_id))
    db.execute(
        """INSERT INTO regels (factuur_id, omschrijving, type, aantal, prijs, subtotaal, bon_id)
           VALUES (?, 'Kit', 'materiaal', 1, 12.5, 12.5, ?)""", (factuur_id, inkoop_id))
    db.commit()
    return inkoop_id


def test_sturen_biedt_mail_en_whatsapp(client, maak_factuur):
    factuur_id = maak_factuur(nummer="", status="concept")
    pagina = client.get(f"/factuur/{factuur_id}/sturen").data.decode()
    assert f'href="/factuur/{factuur_id}/mail"' in pagina
    assert "Via WhatsApp" in pagina
    assert "PDF zonder bonnen" in pagina
    assert "PDF met bonnen" not in pagina  # er zijn geen bonnen
    assert "krijgt daarbij zijn nummer" in pagina


def test_sturen_zonder_mailadres_wijst_naar_bewerken(client, maak_factuur):
    factuur_id = maak_factuur(email="")
    pagina = client.get(f"/factuur/{factuur_id}/sturen").data.decode()
    assert "geen mailadres" in pagina
    assert f'href="/factuur/{factuur_id}/mail"' not in pagina


def test_whatsapp_pdf_geeft_een_concept_zijn_nummer_en_zet_hem_op_verzonden(
        db, post, maak_factuur):
    factuur_id = maak_factuur(nummer="", status="concept")
    antwoord = post(f"/factuur/{factuur_id}/sturen", {"bonnen": "nee"})
    assert antwoord.status_code == 200
    assert antwoord.mimetype == "application/pdf"
    assert "attachment" in antwoord.headers["Content-Disposition"]
    rij = db.execute("SELECT nummer, status FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    assert rij["nummer"]
    assert rij["status"] == "verzonden"


def test_whatsapp_pdf_laat_een_betaalde_rekening_betaald(db, post, maak_factuur):
    factuur_id = maak_factuur(status="betaald")
    post(f"/factuur/{factuur_id}/sturen", {"bonnen": "nee"})
    assert db.execute("SELECT status FROM facturen").fetchone()[0] == "betaald"


def test_whatsapp_pdf_met_bonnen_heeft_de_bon_als_extra_pagina(db, post, maak_factuur, client):
    factuur_id = maak_factuur()
    _bon_op_rekening(db, factuur_id, "bon-1.png")
    pagina = client.get(f"/factuur/{factuur_id}/sturen").data.decode()
    assert "PDF met bonnen" in pagina
    met = post(f"/factuur/{factuur_id}/sturen", {"bonnen": "ja"}).data
    zonder = post(f"/factuur/{factuur_id}/sturen", {"bonnen": "nee"}).data
    assert _aantal_paginas(met) == _aantal_paginas(zonder) + 1


def test_heic_bon_kan_niet_in_de_pdf_en_dat_staat_erbij(db, client, post, maak_factuur):
    factuur_id = maak_factuur()
    _bon_op_rekening(db, factuur_id, "bon-2.heic")
    pagina = client.get(f"/factuur/{factuur_id}/sturen").data.decode()
    assert "kan niet in de PDF" in pagina
    # Alleen een HEIC: dan valt er niets mee te sturen, dus geen knop 'met bonnen'.
    assert "PDF met bonnen" not in pagina
    antwoord = post(f"/factuur/{factuur_id}/sturen", {"bonnen": "ja"})
    assert antwoord.mimetype == "application/pdf"


# --- Materialen van een bon ------------------------------------------------

def _bon_met_materialen(db):
    klus_id = db.execute("INSERT INTO klussen (naam, gestart) VALUES ('Badkamer', '2026-08-01')"
                         ).lastrowid
    inkoop_id = db.execute(
        """INSERT INTO inkopen (klus_id, omschrijving, bedrag, toegevoegd)
           VALUES (?, 'Bon Karwei', 20.5, '2026-08-02')""", (klus_id,)).lastrowid
    db.execute("""INSERT INTO inkoop_materialen (inkoop_id, omschrijving, aantal, prijs)
                  VALUES (?, 'Kit wit', 2, 6.25)""", (inkoop_id,))
    db.execute("""INSERT INTO inkoop_materialen (inkoop_id, omschrijving, aantal, prijs)
                  VALUES (?, 'Schroeven', 1, 8)""", (inkoop_id,))
    db.commit()
    return inkoop_id


def _rekening_met_materialen(post, inkoop_id):
    return post("/nieuw", {
        "klant_naam": "Jan", "datum": "2026-08-14",
        "omschrijving": ["Kit wit", "Schroeven"],
        "type": ["materiaal", "materiaal"],
        "aantal": ["2", "1"], "prijs": ["6.25", "8"],
        "regel_inkoop": [str(inkoop_id), str(inkoop_id)],
    })


def test_menu_kent_de_materialen_van_een_bon(db, client):
    _bon_met_materialen(db)
    pagina = client.get("/nieuw").data.decode()
    assert "data-materialen=" in pagina
    assert "Kit wit" in pagina
    assert "2 materialen" in pagina


def test_materialen_van_een_bon_komen_elk_op_een_eigen_regel(db, post):
    inkoop_id = _bon_met_materialen(db)
    _rekening_met_materialen(post, inkoop_id)
    factuur = db.execute("SELECT * FROM facturen").fetchone()
    regels = db.execute("SELECT * FROM regels ORDER BY id").fetchall()
    assert [r["omschrijving"] for r in regels] == ["Kit wit", "Schroeven"]
    assert [r["subtotaal"] for r in regels] == [12.5, 8.0]
    # De eerste regel houdt de unieke koppeling, de tweede wijst naar dezelfde bon.
    assert (regels[0]["inkoop_id"], regels[0]["bon_id"]) == (inkoop_id, None)
    assert (regels[1]["inkoop_id"], regels[1]["bon_id"]) == (None, inkoop_id)
    assert factuur["totaal"] == 20.5
    # De bon staat op deze rekening en komt dus niet opnieuw in het menu.
    bon = db.execute("SELECT factuur_id FROM inkopen WHERE id=?", (inkoop_id,)).fetchone()
    assert bon["factuur_id"] == factuur["id"]
    assert facturen.geboekte_inkopen() == []


def test_materialen_van_een_bon_staan_niet_als_bontitel_op_de_pdf(db, post):
    zet_bedrijf(db)
    inkoop_id = _bon_met_materialen(db)
    _rekening_met_materialen(post, inkoop_id)
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    tekst = tekst_uit_pdf(facturen.maak_pdf(factuur_id))
    assert "Kit wit" in tekst and "Schroeven" in tekst
    assert "Bon Karwei" not in tekst


def test_bewerken_houdt_de_materiaalregels_aan_hun_bon(db, post, client):
    inkoop_id = _bon_met_materialen(db)
    _rekening_met_materialen(post, inkoop_id)
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    pagina = client.get(f"/factuur/{factuur_id}/bewerk").data.decode()
    assert pagina.count(f'name="regel_inkoop" value="{inkoop_id}"') == 2
    # Opnieuw opslaan met dezelfde regels: de koppeling blijft, niets dubbel weg.
    post(f"/factuur/{factuur_id}/bewerk", {
        "klant_naam": "Jan", "datum": "2026-08-14",
        "omschrijving": ["Kit wit", "Schroeven"], "type": ["materiaal", "materiaal"],
        "aantal": ["2", "1"], "prijs": ["6.25", "8"],
        "regel_inkoop": [str(inkoop_id), str(inkoop_id)],
    })
    assert db.execute("SELECT COUNT(*) FROM regels WHERE ? IN (inkoop_id, bon_id)",
                      (inkoop_id,)).fetchone()[0] == 2


def test_materialen_van_een_bon_op_een_andere_rekening_worden_geweigerd(db, post):
    inkoop_id = _bon_met_materialen(db)
    _rekening_met_materialen(post, inkoop_id)
    _rekening_met_materialen(post, inkoop_id)
    assert db.execute("SELECT COUNT(*) FROM facturen").fetchone()[0] == 1


def test_bestanden_van_de_bon_gaan_mee_via_de_materiaalregels(db, maak_factuur):
    factuur_id = maak_factuur()
    _bon_op_rekening(db, factuur_id, "bon-3.png")
    conn = facturen.get_db()
    bonnen = facturen.bonnen_bij_factuur(conn, factuur_id)
    conn.close()
    assert [b["naam"] for b in bonnen] == ["bon-3.png"]


def test_bon_terughalen_zet_de_materiaalregels_weer_aan_de_bon(db, post):
    inkoop_id = _bon_met_materialen(db)
    _rekening_met_materialen(post, inkoop_id)
    post(f"/inkoop/{inkoop_id}/verwijder")
    assert db.execute("SELECT COUNT(*) FROM regels WHERE inkoop_id IS NULL AND bon_id IS NULL"
                      ).fetchone()[0] == 2
    prullenbak_id = db.execute("SELECT id FROM prullenbak").fetchone()[0]
    post(f"/prullenbak/{prullenbak_id}/terug")
    assert db.execute("SELECT COUNT(*) FROM regels WHERE ? IN (inkoop_id, bon_id)",
                      (inkoop_id,)).fetchone()[0] == 2


def test_bon_zonder_materialen_blijft_een_bonregel(db, post):
    klus_id = db.execute("INSERT INTO klussen (naam, gestart) VALUES ('Badkamer', '2026-08-01')"
                         ).lastrowid
    inkoop_id = db.execute(
        """INSERT INTO inkopen (klus_id, omschrijving, bedrag, toegevoegd)
           VALUES (?, 'Bon Gamma', 9.95, '2026-08-02')""", (klus_id,)).lastrowid
    db.commit()
    post("/nieuw", {"klant_naam": "Jan", "datum": "2026-08-14", "omschrijving": "Bon Gamma",
                    "type": "bon", "aantal": "1", "prijs": "9.95",
                    "regel_inkoop": str(inkoop_id)})
    regel = db.execute("SELECT * FROM regels").fetchone()
    assert regel["inkoop_id"] == inkoop_id and regel["bon_id"] is None


def test_herbouw_van_regels_houdt_de_koppeling_met_de_bon(db, post):
    """De oude migratie die regels opnieuw opbouwt, kende bon_id niet en gooide
    hem weg. Dan staat de bon daarna weer open en kan hij twee keer gerekend."""
    inkoop_id = _bon_met_materialen(db)
    _rekening_met_materialen(post, inkoop_id)
    facturen._herbouw_regels(db)
    db.execute("PRAGMA foreign_keys=ON")
    assert db.execute("SELECT COUNT(*) FROM regels WHERE ? IN (inkoop_id, bon_id)",
                      (inkoop_id,)).fetchone()[0] == 2


def test_kaarten_op_sturen_en_betalingen_staan_niet_tegen_elkaar(client, maak_factuur):
    """.card heeft geen eigen marge; de ruimte komt van de .cards eromheen.
    Zonder die omhulling plakten de kaarten aan elkaar."""
    factuur_id = maak_factuur()
    for url in (f"/factuur/{factuur_id}/sturen", f"/factuur/{factuur_id}/betalingen"):
        pagina = client.get(url).data.decode()
        assert pagina.index('<div class="cards">') < pagina.index('<div class="card">')
