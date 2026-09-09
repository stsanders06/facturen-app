"""Notities bij een klus, met foto's die bij die notitie horen.

Los van de bonnetjes: een bon kan met de rekening mee naar de klant, een notitie
en de foto's erbij blijven altijd bij de klus.
"""
import io
import re

import pytest

# Het kleinst mogelijke geldige PNG-bestand.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


@pytest.fixture
def klus_id(post, db):
    post("/klussen/nieuw", {"naam": "Gevel Straelseweg", "uurtarief": "55"})
    return db.execute("SELECT id FROM klussen").fetchone()[0]


def schrijf_op(post, klus_id, tekst="Achtergevel hoort er ook bij", wanneer=None, fotos=()):
    gegevens = {"tekst": tekst}
    if wanneer:
        gegevens["wanneer"] = wanneer
    if fotos:
        gegevens["foto"] = [(io.BytesIO(PNG), naam) for naam in fotos]
    return post(f"/klus/{klus_id}/notitie", gegevens,
                content_type="multipart/form-data", follow_redirects=True)


def test_notitie_bewaren(post, db, klus_id):
    schrijf_op(post, klus_id, "Klant wil de achtergevel er ook bij")
    rij = db.execute("SELECT * FROM notities").fetchone()
    assert rij["tekst"] == "Klant wil de achtergevel er ook bij"
    assert rij["klus_id"] == klus_id


def test_de_notitie_staat_op_de_klus(post, client, klus_id):
    schrijf_op(post, klus_id, "Buitenkraan zit aan de zijkant")
    assert "Buitenkraan zit aan de zijkant" in client.get(f"/klus/{klus_id}").data.decode()


def test_zonder_datum_is_het_vandaag(post, db, klus_id):
    from datetime import date
    schrijf_op(post, klus_id)
    assert db.execute("SELECT wanneer FROM notities").fetchone()[0] == date.today().isoformat()


def test_je_kunt_een_eigen_datum_kiezen(post, db, klus_id):
    """Het gesprek was vaak een paar dagen eerder dan het moment dat je het intypt."""
    schrijf_op(post, klus_id, wanneer="2026-08-26")
    assert db.execute("SELECT wanneer FROM notities").fetchone()[0] == "2026-08-26"


def test_een_lege_notitie_zonder_foto_wordt_geweigerd(post, db, klus_id):
    antwoord = schrijf_op(post, klus_id, tekst="   ")
    assert db.execute("SELECT COUNT(*) FROM notities").fetchone()[0] == 0
    assert "Schrijf iets op of kies een foto" in antwoord.get_data(as_text=True)


def test_een_notitie_mag_ook_alleen_een_foto_zijn(post, db, klus_id):
    """Soms zegt een foto van de situatie genoeg."""
    schrijf_op(post, klus_id, tekst="", fotos=["situatie.png"])
    assert db.execute("SELECT COUNT(*) FROM notities").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM bijlagen").fetchone()[0] == 1


def test_een_foto_hangt_aan_de_notitie(post, db, klus_id):
    schrijf_op(post, klus_id, fotos=["situatie.png"])
    notitie_id = db.execute("SELECT id FROM notities").fetchone()[0]
    assert db.execute("SELECT notitie_id FROM bijlagen").fetchone()[0] == notitie_id


def test_een_bonnetje_hangt_aan_geen_enkele_notitie(post, db, klus_id):
    post(f"/klus/{klus_id}/bijlage", {"bijlage": (io.BytesIO(PNG), "bon.png")},
         content_type="multipart/form-data", follow_redirects=True)
    assert db.execute("SELECT notitie_id FROM bijlagen").fetchone()[0] is None


def test_notitiefotos_staan_niet_tussen_de_bonnetjes(post, klus_id):
    """Anders raakt de foto van hoe het eruitzag zoek tussen de bonnen."""
    from conftest import facturen
    schrijf_op(post, klus_id, fotos=["situatie.png"])
    post(f"/klus/{klus_id}/bijlage", {"bijlage": (io.BytesIO(PNG), "bon.png")},
         content_type="multipart/form-data", follow_redirects=True)

    conn = facturen.get_db()
    bonnen = facturen.bijlagen_van(conn, klus_id)
    notities = facturen.notities_van(conn, klus_id)
    conn.close()

    assert [b["naam"] for b in bonnen] == ["bon.png"]
    assert [f["naam"] for f in notities[0]["fotos"]] == ["situatie.png"]


def test_er_later_nog_een_foto_bij_zetten(post, db, klus_id):
    schrijf_op(post, klus_id)
    notitie_id = db.execute("SELECT id FROM notities").fetchone()[0]
    post(f"/notitie/{notitie_id}/foto", {"foto": (io.BytesIO(PNG), "extra.png")},
         content_type="multipart/form-data", follow_redirects=True)
    assert db.execute("SELECT naam FROM bijlagen WHERE notitie_id=?",
                      (notitie_id,)).fetchone()[0] == "extra.png"


def test_de_nieuwste_notitie_staat_bovenaan(post, client, klus_id):
    schrijf_op(post, klus_id, "Eerste bezoek", wanneer="2026-08-26")
    schrijf_op(post, klus_id, "Tweede bezoek", wanneer="2026-09-02")
    pagina = client.get(f"/klus/{klus_id}").data.decode()
    assert pagina.index("Tweede bezoek") < pagina.index("Eerste bezoek")


def test_notitie_verwijderen_neemt_de_fotos_mee(post, db, klus_id):
    schrijf_op(post, klus_id, fotos=["situatie.png"])
    notitie_id = db.execute("SELECT id FROM notities").fetchone()[0]
    post(f"/notitie/{notitie_id}/verwijder", follow_redirects=True)
    assert db.execute("SELECT COUNT(*) FROM notities").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM bijlagen").fetchone()[0] == 0


def test_een_verwijderde_notitie_is_terug_te_halen(post, db, klus_id):
    schrijf_op(post, klus_id, "Poort op slot, sleutel bij de buren", fotos=["poort.png"])
    notitie_id = db.execute("SELECT id FROM notities").fetchone()[0]
    post(f"/notitie/{notitie_id}/verwijder", follow_redirects=True)
    prullenbak_id = db.execute("SELECT id FROM prullenbak").fetchone()[0]
    post(f"/prullenbak/{prullenbak_id}/terug", follow_redirects=True)
    assert db.execute("SELECT tekst FROM notities").fetchone()[0] == \
        "Poort op slot, sleutel bij de buren"
    assert db.execute("SELECT COUNT(*) FROM bijlagen").fetchone()[0] == 1


def test_een_bon_verwijderen_laat_de_notities_staan(post, db, klus_id):
    schrijf_op(post, klus_id)
    post(f"/klus/{klus_id}/bijlage", {"bijlage": (io.BytesIO(PNG), "bon.png")},
         content_type="multipart/form-data", follow_redirects=True)
    bijlage_id = db.execute("SELECT id FROM bijlagen WHERE notitie_id IS NULL").fetchone()[0]
    post(f"/bijlage/{bijlage_id}/verwijder", follow_redirects=True)
    assert db.execute("SELECT COUNT(*) FROM notities").fetchone()[0] == 1


def test_notities_gaan_mee_als_de_klus_wordt_verwijderd(post, db, klus_id):
    schrijf_op(post, klus_id)
    post(f"/klus/{klus_id}/verwijder", follow_redirects=True)
    assert db.execute("SELECT COUNT(*) FROM notities").fetchone()[0] == 0


def test_een_verwijderde_klus_komt_met_notities_terug(post, db, klus_id):
    schrijf_op(post, klus_id, "Steiger blijft staan tot vrijdag")
    post(f"/klus/{klus_id}/verwijder", follow_redirects=True)
    prullenbak_id = db.execute("SELECT id FROM prullenbak").fetchone()[0]
    post(f"/prullenbak/{prullenbak_id}/terug", follow_redirects=True)
    assert db.execute("SELECT tekst FROM notities").fetchone()[0] == \
        "Steiger blijft staan tot vrijdag"


def test_de_kluspagina_heeft_twee_aparte_kopjes(client, klus_id):
    pagina = client.get(f"/klus/{klus_id}").data.decode()
    assert ">Notities<" in pagina
    assert ">Bonnetjes<" in pagina


def test_een_notitie_bij_een_klus_die_niet_bestaat_geeft_404(post):
    assert post("/klus/9999/notitie", {"tekst": "iets"}).status_code == 404


def bestandsvelden(pagina):
    """De <input type=file> op de pagina, dus niet de stijlregels die erover gaan."""
    return re.findall(r'<input[^>]*type="file"[^>]*>', pagina)


def test_de_bestandskiezer_zit_achter_een_knop(client, klus_id):
    """Het veld zelf toont "geen bestanden geselecteerd"; dat wil je niet lezen."""
    pagina = client.get(f"/klus/{klus_id}").data.decode()
    assert 'data-kiest="notitie-fotos"' in pagina
    assert all("hidden" in veld for veld in bestandsvelden(pagina))


def test_tekst_en_fotos_staan_elk_op_een_eigen_regel(client, klus_id):
    pagina = client.get(f"/klus/{klus_id}").data.decode()
    assert 'class="opschrijfregel"' in pagina
    assert 'class="fotoregel"' in pagina


def test_bij_een_bestaande_notitie_staat_geen_tweede_kiezer_open(post, client, klus_id):
    """Twee bestandsvelden tegelijk in beeld leest als twee losse dingen."""
    schrijf_op(post, klus_id)
    pagina = client.get(f"/klus/{klus_id}").data.decode()
    assert [veld for veld in bestandsvelden(pagina) if "hidden" not in veld] == []
