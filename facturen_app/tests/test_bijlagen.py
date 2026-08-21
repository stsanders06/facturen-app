"""Foto's en bonnetjes bij een klus."""
import io
import os

import pytest

from conftest import facturen

# Het kleinst mogelijke geldige PNG-bestand.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


@pytest.fixture
def klus_id(post, db):
    post("/klussen/nieuw", {"naam": "Badkamer Kerkstraat", "uurtarief": "45"})
    return db.execute("SELECT id FROM klussen").fetchone()[0]


def upload(post, klus_id, naam="bon.png", inhoud=PNG):
    return post(f"/klus/{klus_id}/bijlage", {"bijlage": (io.BytesIO(inhoud), naam)},
                content_type="multipart/form-data", follow_redirects=True)


def test_foto_toevoegen(post, db, klus_id):
    upload(post, klus_id)
    rij = db.execute("SELECT * FROM bijlagen").fetchone()
    assert rij["naam"] == "bon.png"
    assert rij["klus_id"] == klus_id
    assert os.path.exists(os.path.join(facturen.BIJLAGE_DIR, rij["bestand"]))


def test_de_opslagnaam_is_niet_de_naam_van_het_bestand(post, db, klus_id):
    """Twee keer IMG_0001.jpg zou elkaar anders overschrijven."""
    upload(post, klus_id, "IMG_0001.jpg")
    upload(post, klus_id, "IMG_0001.jpg")
    bestanden = {r["bestand"] for r in db.execute("SELECT bestand FROM bijlagen")}
    assert len(bestanden) == 2
    assert "IMG_0001.jpg" not in bestanden


def test_verkeerd_bestandstype_wordt_geweigerd(post, db, klus_id):
    upload(post, klus_id, "virus.exe", b"MZ")
    assert db.execute("SELECT COUNT(*) FROM bijlagen").fetchone()[0] == 0


def test_pdf_mag_ook(post, db, klus_id):
    upload(post, klus_id, "bon.pdf", b"%PDF-1.4 test")
    rij = db.execute("SELECT * FROM bijlagen").fetchone()
    assert rij["naam"] == "bon.pdf"


def test_meerdere_bestanden_tegelijk(post, db, klus_id):
    post(f"/klus/{klus_id}/bijlage",
         {"bijlage": [(io.BytesIO(PNG), "een.png"), (io.BytesIO(PNG), "twee.png")]},
         content_type="multipart/form-data", follow_redirects=True)
    assert db.execute("SELECT COUNT(*) FROM bijlagen").fetchone()[0] == 2


def test_bijlage_is_op_te_halen(post, db, client, klus_id):
    upload(post, klus_id)
    bijlage_id = db.execute("SELECT id FROM bijlagen").fetchone()[0]
    antwoord = client.get(f"/bijlage/{bijlage_id}")
    assert antwoord.status_code == 200
    assert antwoord.data == PNG


def test_bijlage_verwijderen_haalt_ook_het_bestand_weg(post, db, klus_id):
    upload(post, klus_id)
    rij = db.execute("SELECT * FROM bijlagen").fetchone()
    pad = os.path.join(facturen.BIJLAGE_DIR, rij["bestand"])

    post(f"/bijlage/{rij['id']}/verwijder")
    assert db.execute("SELECT COUNT(*) FROM bijlagen").fetchone()[0] == 0
    assert not os.path.exists(pad)


def test_klus_verwijderen_neemt_de_bijlagen_mee(post, db, klus_id):
    upload(post, klus_id)
    pad = os.path.join(facturen.BIJLAGE_DIR,
                       db.execute("SELECT bestand FROM bijlagen").fetchone()[0])

    post(f"/klus/{klus_id}/verwijder")
    assert db.execute("SELECT COUNT(*) FROM bijlagen").fetchone()[0] == 0
    assert not os.path.exists(pad)


def test_meesturen_gaat_aan_en_uit(post, db, klus_id):
    upload(post, klus_id)
    bijlage_id = db.execute("SELECT id FROM bijlagen").fetchone()[0]
    assert db.execute("SELECT meesturen FROM bijlagen").fetchone()[0] == 0

    post(f"/bijlage/{bijlage_id}/meesturen")
    assert db.execute("SELECT meesturen FROM bijlagen").fetchone()[0] == 1

    post(f"/bijlage/{bijlage_id}/meesturen")
    assert db.execute("SELECT meesturen FROM bijlagen").fetchone()[0] == 0


def test_de_kluspagina_toont_de_bijlage(post, db, client, klus_id):
    upload(post, klus_id, "bonnetje-gamma.png")
    assert "bonnetje-gamma.png" in client.get(f"/klus/{klus_id}").data.decode()


def test_alleen_aangevinkte_bonnen_gaan_mee_met_de_rekening(post, db, klus_id):
    upload(post, klus_id, "meesturen.png")
    upload(post, klus_id, "prive.png")
    eerste = db.execute("SELECT id FROM bijlagen ORDER BY id").fetchone()[0]
    post(f"/bijlage/{eerste}/meesturen")

    post(f"/klus/{klus_id}/dag", {"datum": "2026-08-14", "van": "09:00", "tot": "17:00"})
    post("/nieuw", {"klant_naam": "Jan", "datum": "2026-08-14",
                    "omschrijving": "Uren", "type": "arbeid_uur", "aantal": "8",
                    "prijs": "45", "regel_klus": str(klus_id)})
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]

    namen = [naam for _, naam in facturen.bonnen_bij_factuur(db, factuur_id)]
    assert namen == ["meesturen.png"]


def test_zonder_klus_geen_bonnen_bij_de_rekening(post, db, maak_factuur):
    factuur_id = maak_factuur()
    assert facturen.bonnen_bij_factuur(db, factuur_id) == []


def test_de_bonnen_gaan_echt_als_bijlage_mee(monkeypatch, post, db, klus_id):
    opgevangen = {}

    def nep_mail(s, ontvanger, onderwerp, tekst, pad, bestandsnaam, extra=None):
        opgevangen["extra"] = extra
        opgevangen["tekst"] = tekst
        return True, ""

    monkeypatch.setattr(facturen, "_mail_pdf", nep_mail)
    db.execute("UPDATE settings SET smtp_host='smtp.example.com' WHERE id=1")
    db.commit()

    upload(post, klus_id, "bon.png")
    post(f"/bijlage/{db.execute('SELECT id FROM bijlagen').fetchone()[0]}/meesturen")
    post(f"/klus/{klus_id}/dag", {"datum": "2026-08-14", "van": "09:00", "tot": "17:00"})
    post("/nieuw", {"klant_naam": "Jan", "klant_email": "jan@example.com",
                    "datum": "2026-08-14", "omschrijving": "Uren", "type": "arbeid_uur",
                    "aantal": "8", "prijs": "45", "regel_klus": str(klus_id),
                    "verstuur": "ja"})

    assert [naam for _, naam in opgevangen["extra"]] == ["bon.png"]
    assert "bonnetjes zitten erbij" in opgevangen["tekst"]


def test_onbekende_bijlage_geeft_404(client, db):
    assert client.get("/bijlage/9999").status_code == 404
