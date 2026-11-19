"""Een bijnaam voor een materiaal van een bon (2.11.0).

Een gescande bon noemt een product soms iets waar niemand iets van snapt.
Bij de bon geef je het een bijnaam; op de rekening staat het dan als
"(bijnaam) echte naam". De echte naam blijft erbij, zodat je hem op de bon
terugvindt. Staat de bon al op een conceptrekening, dan gaat de bijnaam mee;
een rekening met een nummer blijft zoals de klant hem kreeg.
"""
import json
import os

import pytest
from werkzeug.datastructures import MultiDict

from conftest import facturen
from test_bonherkenning import klus_erin, lezer, scan_erin, _personeelsantwoord  # noqa: F401
from test_pdf import tekst_uit_pdf, zet_bedrijf


KIT = "KIT MS POLY WT 290ML"


@pytest.fixture
def klus_id(post, db):
    post("/klussen/nieuw", {"naam": "Badkamer Voorbeeldstraat", "uurtarief": "45"})
    return db.execute("SELECT id FROM klussen").fetchone()[0]


def bon_met_kit(client, db, klus_id, bijnaam="witte kit"):
    """Een bon met één onduidelijke naam (met bijnaam) en één duidelijke."""
    client.post(f"/klus/{klus_id}/inkoop", data=MultiDict([
        ("csrf_token", "test-token"), ("omschrijving", "Gamma"), ("bedrag", "0"),
        ("materiaal_omschrijving", KIT), ("materiaal_bijnaam", bijnaam),
        ("materiaal_aantal", "3"), ("materiaal_prijs", "9,95"),
        ("materiaal_omschrijving", "Tegellijm"), ("materiaal_bijnaam", ""),
        ("materiaal_aantal", "2"), ("materiaal_prijs", "12,50"),
    ]))
    return db.execute("SELECT id FROM inkopen").fetchone()[0]


def materialen(db, inkoop_id):
    return db.execute("SELECT * FROM inkoop_materialen WHERE inkoop_id=? ORDER BY id",
                      (inkoop_id,)).fetchall()


def op_conceptrekening(db, inkoop_id, nummer=""):
    """Zet de bon op een rekening zoals het bonnenmenu dat doet."""
    factuur_id = db.execute(
        """INSERT INTO facturen (nummer, datum, klant_naam, status, totaal)
           VALUES (?, '2026-11-19', 'Jan', 'concept', 54.85)""", (nummer,)).lastrowid
    for i, m in enumerate(materialen(db, inkoop_id)):
        db.execute(
            """INSERT INTO regels (factuur_id, omschrijving, type, aantal, prijs, subtotaal,
               inkoop_id, bon_id, materiaal_id) VALUES (?, ?, 'materiaal', ?, ?, ?, ?, ?, ?)""",
            (factuur_id, facturen.naam_op_rekening(m["omschrijving"], m["bijnaam"]),
             m["aantal"], m["prijs"], m["aantal"] * m["prijs"],
             inkoop_id if i == 0 else None, None if i == 0 else inkoop_id, m["id"]))
    db.execute("UPDATE inkopen SET factuur_id=? WHERE id=?", (factuur_id, inkoop_id))
    db.commit()
    return factuur_id


def bijnaam_wijzigen(client, inkoop_id, ids, bijnamen):
    """Het formulier van een vastliggende bon: alleen id's en bijnamen."""
    gegevens = [("csrf_token", "test-token"), ("omschrijving", "Gamma")]
    for mat_id, bijnaam in zip(ids, bijnamen):
        gegevens += [("materiaal_id", str(mat_id)), ("materiaal_bijnaam", bijnaam)]
    return client.post(f"/inkoop/{inkoop_id}", data=MultiDict(gegevens),
                       follow_redirects=True)


# ---------- De tekst ----------

def test_met_bijnaam_komt_hij_tussen_haakjes_ervoor():
    assert facturen.naam_op_rekening(KIT, "witte kit") == f"(witte kit) {KIT}"


def test_zonder_bijnaam_blijft_de_naam():
    assert facturen.naam_op_rekening(KIT, "") == KIT
    assert facturen.naam_op_rekening(KIT, None) == KIT
    assert facturen.naam_op_rekening(KIT, "   ") == KIT


def test_witruimte_gaat_eraf():
    assert facturen.naam_op_rekening(f"  {KIT} ", "  witte kit ") == f"(witte kit) {KIT}"


def test_zelf_getypte_haakjes_worden_niet_dubbel():
    assert facturen.naam_op_rekening(KIT, "(witte kit)") == f"(witte kit) {KIT}"
    assert facturen.naam_op_rekening(KIT, "()") == KIT


# ---------- Invullen bij de bon ----------

def test_een_nieuwe_bon_bewaart_de_bijnaam(client, db, klus_id):
    inkoop_id = bon_met_kit(client, db, klus_id)
    rijen = materialen(db, inkoop_id)
    assert [(r["omschrijving"], r["bijnaam"]) for r in rijen] == [
        (KIT, "witte kit"), ("Tegellijm", "")]


def test_scan_als_bon_bewaart_de_bijnaam(client, post, db, lezer):
    klus = klus_erin(db)
    scan_id = scan_erin(client, post, db)
    facturen._wacht_op_herkenning()
    post(f"/scans/{scan_id}/bon", {
        "klus_id": klus, "omschrijving": "Karwei", "wanneer": "2026-10-02",
        "bedrag": "19,87",
        "materiaal_omschrijving": ["Spaanplaatschroef 4x40", "Houtlijm 250ml"],
        "materiaal_bijnaam": ["schroeven voor de plinten", ""],
        "materiaal_aantal": ["2", "1"],
        "materiaal_prijs": ["7,19", "5,49"],
    })
    rijen = db.execute("SELECT omschrijving, bijnaam FROM inkoop_materialen ORDER BY id")
    assert [tuple(r) for r in rijen] == [
        ("Spaanplaatschroef 4x40", "schroeven voor de plinten"), ("Houtlijm 250ml", "")]


def test_personeelskorting_laat_de_bijnaam_staan(client, post, db, lezer):
    lezer.antwoord = _personeelsantwoord()
    klus = klus_erin(db)
    scan_id = scan_erin(client, post, db)
    facturen._wacht_op_herkenning()
    post(f"/scans/{scan_id}/bon", {
        "klus_id": str(klus), "omschrijving": "Karwei", "wanneer": "2026-11-14",
        "bedrag": "17.49", "bon_totaal": "17.49", "personeelskorting": "15",
        "materiaal_omschrijving": ["Houtdraadbout M8x70", "Wandtegel wit"],
        "materiaal_bijnaam": ["bouten voor de pergola", "tegel badkamer"],
        "materiaal_aantal": ["2", "1"],
        "materiaal_prijs": ["5.29", "5.00"],
    })
    rijen = db.execute("SELECT bijnaam FROM inkoop_materialen ORDER BY id")
    assert [r[0] for r in rijen] == ["bouten voor de pergola", "tegel badkamer"]


def test_het_correctielog_kent_de_bijnaam_niet(client, post, db, lezer):
    """Een bijnaam is geen leesfout; de bonnenlezer mag er niets van leren."""
    if os.path.isfile(facturen.CORRECTIE_LOG):
        os.remove(facturen.CORRECTIE_LOG)
    klus = klus_erin(db)
    scan_id = scan_erin(client, post, db)
    facturen._wacht_op_herkenning()
    post(f"/scans/{scan_id}/bon", {
        "klus_id": klus, "omschrijving": "Karwei", "wanneer": "2026-10-02",
        "bedrag": "19,87",
        "materiaal_omschrijving": ["Spaanplaatschroef 4x40"],
        "materiaal_bijnaam": ["schroeven voor de plinten"],
        "materiaal_aantal": ["2"], "materiaal_prijs": ["7,19"],
    })
    with open(facturen.CORRECTIE_LOG, encoding="utf-8") as bron:
        inhoud = bron.read()
    os.remove(facturen.CORRECTIE_LOG)
    assert "Spaanplaatschroef 4x40" in inhoud
    assert "plinten" not in inhoud
    assert json.loads(inhoud.splitlines()[0])["regels"][0]["omschrijving"] == \
        "Spaanplaatschroef 4x40"


def test_los_materiaal_erbij_met_bijnaam(client, post, db, klus_id):
    inkoop_id = bon_met_kit(client, db, klus_id)
    post(f"/inkoop/{inkoop_id}/materiaal",
         {"omschrijving": "PURSCHUIM 750", "bijnaam": "schuim voor de kozijnen",
          "aantal": "1", "prijs": "8,99"})
    assert materialen(db, inkoop_id)[-1]["bijnaam"] == "schuim voor de kozijnen"


def test_bewerken_wijzigt_de_bijnaam(client, db, klus_id):
    inkoop_id = bon_met_kit(client, db, klus_id)
    ids = [m["id"] for m in materialen(db, inkoop_id)]
    client.post(f"/inkoop/{inkoop_id}", data=MultiDict([
        ("csrf_token", "test-token"), ("omschrijving", "Gamma"),
        ("materiaal_id", str(ids[0])), ("materiaal_omschrijving", KIT),
        ("materiaal_bijnaam", "kit badkamer"),
        ("materiaal_aantal", "3"), ("materiaal_prijs", "9,95"),
        ("materiaal_id", str(ids[1])), ("materiaal_omschrijving", "Tegellijm"),
        ("materiaal_bijnaam", "lijm"),
        ("materiaal_aantal", "2"), ("materiaal_prijs", "12,50"),
    ]))
    assert [m["bijnaam"] for m in materialen(db, inkoop_id)] == ["kit badkamer", "lijm"]


def test_een_oud_formulier_zonder_bijnaamveld_laat_hem_staan(client, db, klus_id):
    inkoop_id = bon_met_kit(client, db, klus_id)
    ids = [m["id"] for m in materialen(db, inkoop_id)]
    client.post(f"/inkoop/{inkoop_id}", data=MultiDict([
        ("csrf_token", "test-token"), ("omschrijving", "Gamma"),
        ("materiaal_id", str(ids[0])), ("materiaal_omschrijving", KIT),
        ("materiaal_aantal", "3"), ("materiaal_prijs", "9,95"),
    ]))
    assert materialen(db, inkoop_id)[0]["bijnaam"] == "witte kit"


def test_de_klus_toont_de_bijnaam(client, db, klus_id):
    bon_met_kit(client, db, klus_id)
    pagina = client.get(f"/klus/{klus_id}").data.decode()
    assert f"(witte kit) {KIT}" in pagina
    assert 'name="materiaal_bijnaam"' in pagina


# ---------- Naar de rekening ----------

def test_het_bonnenmenu_geeft_de_naam_met_bijnaam(client, db, klus_id):
    bon_met_kit(client, db, klus_id)
    namen = [m["omschrijving"] for m in facturen.geboekte_inkopen()[0]["materialen"]]
    assert namen == [f"(witte kit) {KIT}", "Tegellijm"]


def test_de_pdf_toont_de_bijnaam(client, db, klus_id):
    """€ 29,85 is meer dan de grens voor klein materiaal, dus een eigen regel."""
    zet_bedrijf(db)
    inkoop_id = bon_met_kit(client, db, klus_id)
    factuur_id = op_conceptrekening(db, inkoop_id)
    tekst = tekst_uit_pdf(facturen.maak_pdf(factuur_id))
    assert f"(witte kit) {KIT}" in tekst


# ---------- Een bon die al op een rekening staat ----------

def test_een_vastliggende_bon_krijgt_wel_een_bijnaam(client, db, klus_id):
    inkoop_id = bon_met_kit(client, db, klus_id, bijnaam="")
    op_conceptrekening(db, inkoop_id, nummer="2026-001")
    ids = [m["id"] for m in materialen(db, inkoop_id)]
    bijnaam_wijzigen(client, inkoop_id, ids, ["witte kit", ""])
    assert [m["bijnaam"] for m in materialen(db, inkoop_id)] == ["witte kit", ""]


def test_een_vastliggende_bon_houdt_naam_aantal_en_prijs(client, db, klus_id):
    """Ook als iemand die velden toch meestuurt."""
    inkoop_id = bon_met_kit(client, db, klus_id)
    op_conceptrekening(db, inkoop_id)
    bedrag = db.execute("SELECT bedrag FROM inkopen WHERE id=?", (inkoop_id,)).fetchone()[0]
    mat = materialen(db, inkoop_id)[0]
    client.post(f"/inkoop/{inkoop_id}", data=MultiDict([
        ("csrf_token", "test-token"), ("omschrijving", "Gamma"),
        ("materiaal_id", str(mat["id"])), ("materiaal_omschrijving", "Anders"),
        ("materiaal_bijnaam", "kit"), ("materiaal_aantal", "99"), ("materiaal_prijs", "99"),
    ]))
    na = materialen(db, inkoop_id)[0]
    assert (na["omschrijving"], na["aantal"], na["prijs"]) == (KIT, 3.0, 9.95)
    assert na["bijnaam"] == "kit"
    assert db.execute("SELECT bedrag FROM inkopen WHERE id=?",
                      (inkoop_id,)).fetchone()[0] == bedrag


def test_een_conceptrekening_gaat_mee(client, db, klus_id):
    inkoop_id = bon_met_kit(client, db, klus_id)
    factuur_id = op_conceptrekening(db, inkoop_id)
    ids = [m["id"] for m in materialen(db, inkoop_id)]
    pagina = bijnaam_wijzigen(client, inkoop_id, ids, ["kit badkamer", "lijm"]).data.decode()
    namen = [r[0] for r in db.execute(
        "SELECT omschrijving FROM regels WHERE factuur_id=? ORDER BY id", (factuur_id,))]
    assert namen == [f"(kit badkamer) {KIT}", "(lijm) Tegellijm"]
    assert "De bijnaam staat ook op 1 conceptrekening." in pagina


def test_bijnaam_weghalen_haalt_hem_ook_van_het_concept(client, db, klus_id):
    inkoop_id = bon_met_kit(client, db, klus_id)
    factuur_id = op_conceptrekening(db, inkoop_id)
    ids = [m["id"] for m in materialen(db, inkoop_id)]
    bijnaam_wijzigen(client, inkoop_id, ids, ["", ""])
    naam = db.execute("SELECT omschrijving FROM regels WHERE factuur_id=? ORDER BY id",
                      (factuur_id,)).fetchone()[0]
    assert naam == KIT


def test_een_rekening_met_nummer_blijft_zoals_hij_was(client, db, klus_id):
    inkoop_id = bon_met_kit(client, db, klus_id)
    factuur_id = op_conceptrekening(db, inkoop_id, nummer="2026-001")
    ids = [m["id"] for m in materialen(db, inkoop_id)]
    pagina = bijnaam_wijzigen(client, inkoop_id, ids, ["kit badkamer", ""]).data.decode()
    naam = db.execute("SELECT omschrijving FROM regels WHERE factuur_id=? ORDER BY id",
                      (factuur_id,)).fetchone()[0]
    assert naam == f"(witte kit) {KIT}"
    assert "De bijnaam staat ook op" not in pagina


def test_een_zelf_aangepaste_regel_blijft_staan(client, db, klus_id):
    """Heb je de tekst op de rekening zelf veranderd, dan wint die."""
    inkoop_id = bon_met_kit(client, db, klus_id)
    factuur_id = op_conceptrekening(db, inkoop_id)
    db.execute("UPDATE regels SET omschrijving='Kit voor de douche' WHERE factuur_id=? "
               "AND materiaal_id=?", (factuur_id, materialen(db, inkoop_id)[0]["id"]))
    db.commit()
    ids = [m["id"] for m in materialen(db, inkoop_id)]
    bijnaam_wijzigen(client, inkoop_id, ids, ["kit badkamer", ""])
    naam = db.execute("SELECT omschrijving FROM regels WHERE factuur_id=? ORDER BY id",
                      (factuur_id,)).fetchone()[0]
    assert naam == "Kit voor de douche"


def test_een_ongewijzigde_bijnaam_raakt_niets(client, db, klus_id):
    inkoop_id = bon_met_kit(client, db, klus_id)
    op_conceptrekening(db, inkoop_id)
    ids = [m["id"] for m in materialen(db, inkoop_id)]
    pagina = bijnaam_wijzigen(client, inkoop_id, ids, ["witte kit", ""]).data.decode()
    assert "De bijnaam staat ook op" not in pagina


def test_de_vastliggende_bon_toont_alleen_bijnaamvelden(client, db, klus_id):
    inkoop_id = bon_met_kit(client, db, klus_id)
    op_conceptrekening(db, inkoop_id)
    mat = materialen(db, inkoop_id)[0]
    pagina = client.get(f"/klus/{klus_id}").data.decode()
    assert f'id="mat-bijnaam-{mat["id"]}"' in pagina
    assert f"Bijnaam voor {KIT}" in pagina
    assert f'id="mat-oms-{mat["id"]}"' not in pagina


# ---------- Een bestaande database ----------

def test_een_oude_database_krijgt_de_kolom(client, db, klus_id):
    inkoop_id = bon_met_kit(client, db, klus_id)
    db.execute("ALTER TABLE inkoop_materialen DROP COLUMN bijnaam")
    db.commit()
    facturen.init_db()
    assert {m["bijnaam"] for m in materialen(db, inkoop_id)} == {""}
