"""Een eigen aantekening bij een rekening of offerte.

Om twee stukken voor dezelfde klant uit elkaar te houden — "tweede voorstel",
"achterstallig onderhoud". Hij staat alleen in de app: de klant ziet hem niet, dus
hij hoort nergens in de PDF of de mail terecht te komen.
"""
import os

from conftest import facturen


def regels_van_een_rekening(extra=None):
    gegevens = {
        "klant_naam": "Jan Jansen",
        "datum": "2026-08-14",
        "omschrijving": "Kraan vervangen",
        "type": "arbeid_uur",
        "aantal": "2",
        "prijs": "60,50",
    }
    gegevens.update(extra or {})
    return gegevens


def test_een_rekening_bewaart_haar_kenmerk(post, db):
    post("/nieuw", regels_van_een_rekening({"kenmerk": "Tweede voorstel"}))
    assert db.execute("SELECT kenmerk FROM facturen").fetchone()[0] == "Tweede voorstel"


def test_het_kenmerk_staat_in_de_lijst(post, client):
    post("/nieuw", regels_van_een_rekening({"kenmerk": "Achterstallig onderhoud"}))
    inhoud = client.get("/").data.decode()
    assert "Achterstallig onderhoud" in inhoud
    assert 'class="kenmerk"' in inhoud


def test_zonder_kenmerk_staat_er_geen_leeg_vakje(post, client):
    post("/nieuw", regels_van_een_rekening())
    assert 'class="kenmerk"' not in client.get("/").data.decode()


def test_je_kunt_erop_zoeken(post, client):
    post("/nieuw", regels_van_een_rekening({"kenmerk": "Steiger"}))
    post("/nieuw", regels_van_een_rekening({"klant_naam": "Piet Peters"}))

    inhoud = client.get("/?q=steiger").data.decode()
    assert "Jan Jansen" in inhoud
    assert "Piet Peters" not in inhoud


def test_het_kenmerk_komt_niet_op_de_pdf(post, db):
    """Dat is het hele punt: het is een aantekening voor jezelf."""
    post("/nieuw", regels_van_een_rekening({"kenmerk": "Tweede voorstel"}))
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    facturen.maak_definitief(facturen.get_db(), factuur_id)

    factuur = db.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    pad = os.path.join(facturen.PDF_DIR, facturen.pdf_bestandsnaam(factuur))
    with open(pad, "rb") as bestand:
        assert b"Tweede voorstel" not in bestand.read()


def test_een_offerte_bewaart_haar_kenmerk_ook(post, db):
    post("/offertes/nieuw", {
        "klant_naam": "Jan Jansen", "datum": "2026-08-14", "kenmerk": "Variant met dak",
        "omschrijving": "Terras betegelen", "type": "arbeid_klus",
        "aantal": "1", "prijs": "250",
    })
    assert db.execute("SELECT kenmerk FROM offertes").fetchone()[0] == "Variant met dak"


def test_bij_het_omzetten_naar_een_rekening_gaat_het_kenmerk_mee(post, db, maak_offerte):
    """Juist dan wil je weten welke van de twee offertes dit was."""
    offerte_id = maak_offerte()
    db.execute("UPDATE offertes SET kenmerk='Variant met dak' WHERE id=?", (offerte_id,))
    db.commit()

    post(f"/offerte/{offerte_id}/naar-rekening")
    assert db.execute("SELECT kenmerk FROM facturen").fetchone()[0] == "Variant met dak"


def test_een_kopie_neemt_het_kenmerk_over(post, db, maak_factuur):
    factuur_id = maak_factuur()
    db.execute("UPDATE facturen SET kenmerk='Elke maand' WHERE id=?", (factuur_id,))
    db.commit()

    post(f"/factuur/{factuur_id}/kopieer")
    kenmerken = [r[0] for r in db.execute("SELECT kenmerk FROM facturen ORDER BY id")]
    assert kenmerken == ["Elke maand", "Elke maand"]


def test_het_kenmerk_is_achteraf_nog_te_wijzigen(post, db, maak_factuur):
    factuur_id = maak_factuur()
    post(f"/factuur/{factuur_id}/bewerk", regels_van_een_rekening({"kenmerk": "Toch dit"}))
    assert db.execute("SELECT kenmerk FROM facturen WHERE id=?",
                      (factuur_id,)).fetchone()[0] == "Toch dit"
