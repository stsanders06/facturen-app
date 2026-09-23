"""Inkopen (bonnen) bij een klus en als regel op de rekening."""
import io
import os
import sqlite3

import pytest
from werkzeug.datastructures import MultiDict

from conftest import facturen

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


@pytest.fixture
def klus_id(post, db):
    post("/klussen/nieuw", {"naam": "Badkamer Kerkstraat", "uurtarief": "45"})
    return db.execute("SELECT id FROM klussen").fetchone()[0]


def test_inkoop_met_omschrijving_bedrag_en_bestand(post, db, klus_id):
    post(f"/klus/{klus_id}/inkoop", {
        "omschrijving": "Gamma — tegellijm",
        "bedrag": "24,95",
        "bijlage": (io.BytesIO(PNG), "bon.png"),
    }, content_type="multipart/form-data", follow_redirects=True)

    inkoop = db.execute("SELECT * FROM inkopen").fetchone()
    assert inkoop["omschrijving"] == "Gamma — tegellijm"
    assert inkoop["bedrag"] == 24.95
    assert inkoop["klus_id"] == klus_id
    assert inkoop["factuur_id"] is None

    bijlage = db.execute("SELECT * FROM bijlagen").fetchone()
    assert bijlage["inkoop_id"] == inkoop["id"]
    assert bijlage["naam"] == "bon.png"
    assert os.path.exists(os.path.join(facturen.BIJLAGE_DIR, bijlage["bestand"]))


def test_optioneel_materiaal_onder_een_inkoop(client, db, klus_id):
    # post()-fixture maakt er een dict van; dan blijft alleen de laatste waarde
    # van elke sleutel over. MultiDict houdt herhaalde velden wél.
    gegevens = MultiDict([
        ("csrf_token", "test-token"),
        ("omschrijving", "Bouwmarkt"),
        ("bedrag", "50"),
        ("materiaal_omschrijving", "Tegellijm"),
        ("materiaal_aantal", "2"),
        ("materiaal_prijs", "12,50"),
        ("materiaal_omschrijving", "Voegmiddel"),
        ("materiaal_aantal", "1"),
        ("materiaal_prijs", "8"),
    ])
    client.post(f"/klus/{klus_id}/inkoop", data=gegevens, follow_redirects=True)

    inkoop_id = db.execute("SELECT id FROM inkopen").fetchone()[0]
    materialen = db.execute(
        "SELECT * FROM inkoop_materialen WHERE inkoop_id=? ORDER BY id",
        (inkoop_id,),
    ).fetchall()
    assert len(materialen) == 2
    assert materialen[0]["omschrijving"] == "Tegellijm"
    assert materialen[0]["aantal"] == 2.0
    assert materialen[0]["prijs"] == 12.5


def test_materiaal_erbij_en_weg(post, db, klus_id):
    post(f"/klus/{klus_id}/inkoop", {"omschrijving": "AH", "bedrag": "10"},
         follow_redirects=True)
    inkoop_id = db.execute("SELECT id FROM inkopen").fetchone()[0]
    post(f"/inkoop/{inkoop_id}/materiaal",
         {"omschrijving": "Kit", "aantal": "1", "prijs": "4,50"},
         follow_redirects=True)
    mat_id = db.execute("SELECT id FROM inkoop_materialen").fetchone()[0]
    post(f"/inkoop_materiaal/{mat_id}/verwijder", follow_redirects=True)
    assert db.execute("SELECT COUNT(*) FROM inkoop_materialen").fetchone()[0] == 0


def test_bon_regel_op_factuur_zet_factuur_id(post, db, klus_id):
    post(f"/klus/{klus_id}/inkoop", {"omschrijving": "Praxis", "bedrag": "33,00"},
         follow_redirects=True)
    inkoop_id = db.execute("SELECT id FROM inkopen").fetchone()[0]

    post("/nieuw", {
        "klant_naam": "Jan", "datum": "2026-08-14",
        "omschrijving": "Praxis", "type": "bon", "aantal": "1", "prijs": "33",
        "regel_inkoop": str(inkoop_id),
    }, follow_redirects=True)

    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    inkoop = db.execute("SELECT * FROM inkopen").fetchone()
    assert inkoop["factuur_id"] == factuur_id
    regel = db.execute("SELECT * FROM regels").fetchone()
    assert regel["type"] == "bon"
    assert regel["inkoop_id"] == inkoop_id
    assert regel["subtotaal"] == 33.0


def test_bon_is_vaste_prijs_zonder_aantal(db):
    from werkzeug.datastructures import MultiDict
    form = MultiDict([
        ("omschrijving", "Bon Gamma"),
        ("type", "bon"),
        ("aantal", "5"),
        ("prijs", "40"),
    ])
    regels, totaal = facturen.lees_regels(form)
    assert regels[0][2] == 1.0
    assert totaal == 40.0
    assert facturen.soort("bon")["naam"] == "Bon"
    assert facturen.soort("bon")["eenheid"] is None


def test_verwijderen_van_rekening_maakt_inkoop_weer_vrij(post, db, klus_id):
    post(f"/klus/{klus_id}/inkoop", {"omschrijving": "Hornbach", "bedrag": "12"},
         follow_redirects=True)
    inkoop_id = db.execute("SELECT id FROM inkopen").fetchone()[0]
    post("/nieuw", {
        "klant_naam": "Jan", "datum": "2026-08-14",
        "omschrijving": "Hornbach", "type": "bon", "aantal": "1", "prijs": "12",
        "regel_inkoop": str(inkoop_id),
    }, follow_redirects=True)
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    post(f"/factuur/{factuur_id}/verwijder", follow_redirects=True)
    assert db.execute("SELECT factuur_id FROM inkopen").fetchone()[0] is None


def test_migratie_van_losse_bijlagen_naar_inkopen(db, klus_id):
    """Bestaande databases hadden losse bijlagen zonder inkoop; die worden
    één bon per bestand."""
    # Simuleer een oude database: kolom weg, losse bijlage, opnieuw init_db.
    db.execute("DELETE FROM bijlagen")
    db.execute("DELETE FROM inkopen")
    db.commit()

    # Kolom opnieuw opbouwen alsof hij er nooit was: SQLite kan DROP COLUMN.
    try:
        db.execute("ALTER TABLE bijlagen DROP COLUMN inkoop_id")
        db.commit()
    except sqlite3.OperationalError:
        pytest.skip("SQLite hier kan geen kolom laten vallen")

    pad_naam = "oud-bon.png"
    opslag = "aabbccdd.png"
    open(os.path.join(facturen.BIJLAGE_DIR, opslag), "wb").write(PNG)
    db.execute(
        """INSERT INTO bijlagen (klus_id, bestand, naam, toegevoegd, meesturen, notitie_id)
           VALUES (?, ?, ?, '2026-01-10', 0, NULL)""",
        (klus_id, opslag, pad_naam),
    )
    db.commit()
    assert "inkoop_id" not in {
        r["name"] for r in db.execute("PRAGMA table_info(bijlagen)")
    }

    facturen.init_db()

    bijlage = db.execute("SELECT * FROM bijlagen").fetchone()
    assert bijlage["inkoop_id"] is not None
    inkoop = db.execute(
        "SELECT * FROM inkopen WHERE id=?", (bijlage["inkoop_id"],)
    ).fetchone()
    assert inkoop["omschrijving"] == pad_naam
    assert inkoop["bedrag"] == 0
    assert inkoop["toegevoegd"] == "2026-01-10"
    assert inkoop["klus_id"] == klus_id


def test_notitiefoto_blijft_los_van_inkoop(post, db, klus_id):
    post(f"/klus/{klus_id}/notitie", {
        "tekst": "Lekkage onder de wasbak",
        "wanneer": "2026-08-14",
        "foto": (io.BytesIO(PNG), "lekkage.png"),
    }, content_type="multipart/form-data", follow_redirects=True)
    bijlage = db.execute("SELECT * FROM bijlagen").fetchone()
    assert bijlage["notitie_id"] is not None
    assert bijlage["inkoop_id"] is None
    assert db.execute("SELECT COUNT(*) FROM inkopen").fetchone()[0] == 0


def test_inkoop_verwijderen_gaat_naar_prullenbak(post, db, klus_id):
    post(f"/klus/{klus_id}/inkoop", {
        "omschrijving": "Gamma", "bedrag": "9",
        "bijlage": (io.BytesIO(PNG), "bon.png"),
    }, content_type="multipart/form-data", follow_redirects=True)
    inkoop_id = db.execute("SELECT id FROM inkopen").fetchone()[0]
    pad = os.path.join(
        facturen.BIJLAGE_DIR,
        db.execute("SELECT bestand FROM bijlagen").fetchone()[0],
    )
    post(f"/inkoop/{inkoop_id}/verwijder", follow_redirects=True)
    assert db.execute("SELECT COUNT(*) FROM inkopen").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM bijlagen").fetchone()[0] == 0
    assert os.path.exists(pad)
    prullenbak_id = db.execute("SELECT id FROM prullenbak").fetchone()[0]
    post(f"/prullenbak/{prullenbak_id}/terug", follow_redirects=True)
    assert db.execute("SELECT omschrijving FROM inkopen").fetchone()[0] == "Gamma"


def test_kluspagina_toont_inkopen(post, client, klus_id):
    post(f"/klus/{klus_id}/inkoop", {"omschrijving": "Gamma-bon", "bedrag": "15"},
         follow_redirects=True)
    pagina = client.get(f"/klus/{klus_id}").data.decode()
    assert ">Inkopen<" in pagina
    assert "Gamma-bon" in pagina


def test_nieuw_formulier_heeft_bon_als_soort(client):
    inhoud = client.get("/nieuw").data.decode()
    assert 'value="bon"' in inhoud
    assert "badge: 'B'" in inhoud
