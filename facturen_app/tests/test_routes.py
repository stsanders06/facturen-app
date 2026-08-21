"""Werken de pagina's, en klopt er na een POST wat er in de database staat."""
from datetime import date

from conftest import facturen


def test_alle_pagina_s_laden(client):
    for url in ["/", "/nieuw", "/klanten", "/klanten/nieuw", "/klanten/import",
                "/klussen", "/klussen/nieuw", "/offertes", "/offertes/nieuw",
                "/instellingen"]:
        antwoord = client.get(url)
        assert antwoord.status_code == 200, f"{url} gaf {antwoord.status_code}"


def test_post_zonder_kenmerk_wordt_geweigerd(client):
    """Zonder dit kan een andere website in de achtergrond iets laten verwijderen."""
    assert client.post("/klanten/nieuw", data={"naam": "Jan"}).status_code == 400


def test_nieuwe_rekening_wordt_opgeslagen_met_pdf(post, db):
    antwoord = post("/nieuw", {
        "klant_naam": "Jan Jansen",
        "klant_email": "jan@example.com",
        "datum": "2026-08-14",
        "omschrijving": "Kraan vervangen",
        "type": "arbeid_uur",
        "aantal": "2",
        "prijs": "60,50",
    })
    assert antwoord.status_code == 302

    factuur = db.execute("SELECT * FROM facturen").fetchone()
    assert factuur["klant_naam"] == "Jan Jansen"
    assert factuur["totaal"] == 121.0
    assert factuur["nummer"].startswith(str(date.today().year))

    import os
    assert os.path.exists(os.path.join(facturen.PDF_DIR, f"{factuur['nummer']}.pdf"))


def test_rekening_zonder_regels_wordt_niet_aangemaakt(post, db):
    post("/nieuw", {"klant_naam": "Jan Jansen", "datum": "2026-08-14"})
    assert db.execute("SELECT COUNT(*) FROM facturen").fetchone()[0] == 0


def test_klant_opslaan_maakt_de_klant_aan(post, db):
    post("/nieuw", {
        "klant_naam": "Nieuwe Klant", "klant_opslaan": "ja", "datum": "2026-08-14",
        "omschrijving": "Iets", "type": "materiaal", "aantal": "1", "prijs": "10",
    })
    assert db.execute("SELECT COUNT(*) FROM klanten WHERE naam='Nieuwe Klant'").fetchone()[0] == 1


def test_rekening_op_betaald_zetten_en_weer_terug(post, db, maak_factuur):
    factuur_id = maak_factuur(status="verzonden")
    post(f"/factuur/{factuur_id}/betaald")
    assert db.execute("SELECT status FROM facturen WHERE id=?", (factuur_id,)).fetchone()[0] == "betaald"

    post(f"/factuur/{factuur_id}/niet-betaald")
    status = db.execute("SELECT status FROM facturen WHERE id=?", (factuur_id,)).fetchone()[0]
    assert status == "verzonden"   # terug naar wat het was, niet naar concept


def test_rekening_verwijderen_haalt_ook_de_regels_weg(post, db, maak_factuur):
    factuur_id = maak_factuur()
    post(f"/factuur/{factuur_id}/verwijder")
    assert db.execute("SELECT COUNT(*) FROM facturen").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM regels").fetchone()[0] == 0


def test_klant_bekijken_en_bewerken(post, db):
    post("/klanten/nieuw", {"naam": "Jan Jansen", "email": "jan@example.com"})
    klant_id = db.execute("SELECT id FROM klanten").fetchone()[0]

    assert b"Jan Jansen" in client_get(db, f"/klant/{klant_id}")

    post(f"/klant/{klant_id}/bewerk", {"naam": "Jan Jansen", "email": "nieuw@example.com"})
    assert db.execute("SELECT email FROM klanten WHERE id=?", (klant_id,)).fetchone()[0] == "nieuw@example.com"


def client_get(db, url):
    with facturen.app.test_client() as c:
        return c.get(url).data


def test_uren_op_een_klus_worden_opgeteld(post, db):
    post("/klussen/nieuw", {"naam": "Badkamer Kerkstraat", "uurtarief": "45"})
    klus_id = db.execute("SELECT id FROM klussen").fetchone()[0]

    post(f"/klus/{klus_id}/dag", {"datum": "2026-08-14", "van": "09:00", "tot": "17:00"})
    post(f"/klus/{klus_id}/dag", {"datum": "2026-08-15", "van": "09:00", "tot": "12:30"})

    _, dagen, uren = facturen.klus_met_uren(db, klus_id)
    assert len(dagen) == 2
    assert uren == 11.5
    assert dagen[0]["uren"] == 8.0


def test_gefactureerde_uren_tellen_niet_nog_een_keer_mee(post, db):
    post("/klussen/nieuw", {"naam": "Badkamer", "uurtarief": "45"})
    klus_id = db.execute("SELECT id FROM klussen").fetchone()[0]
    post(f"/klus/{klus_id}/dag", {"datum": "2026-08-14", "van": "09:00", "tot": "17:00"})

    post("/nieuw", {
        "klant_naam": "Jan", "datum": "2026-08-14",
        "omschrijving": "Uren badkamer", "type": "arbeid_uur", "aantal": "8",
        "prijs": "45", "regel_klus": str(klus_id),
    })

    assert [k["id"] for k in facturen.geboekte_klussen()] == []

    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    post(f"/factuur/{factuur_id}/verwijder")
    assert [k["id"] for k in facturen.geboekte_klussen()] == [klus_id]


def test_offerte_wordt_een_rekening(post, db, maak_offerte):
    offerte_id = maak_offerte(status="geaccepteerd")
    post(f"/offerte/{offerte_id}/naar-rekening")

    factuur = db.execute("SELECT * FROM facturen").fetchone()
    assert factuur["klant_naam"] == "Jan Jansen"
    assert factuur["totaal"] == 250.0

    offerte = db.execute("SELECT factuur_id FROM offertes WHERE id=?", (offerte_id,)).fetchone()
    assert offerte["factuur_id"] == factuur["id"]


def test_offerte_wordt_niet_twee_keer_een_rekening(post, db, maak_offerte):
    offerte_id = maak_offerte(status="geaccepteerd")
    post(f"/offerte/{offerte_id}/naar-rekening")
    post(f"/offerte/{offerte_id}/naar-rekening")
    assert db.execute("SELECT COUNT(*) FROM facturen").fetchone()[0] == 1


def test_offerte_status_wijzigen(post, db, maak_offerte):
    offerte_id = maak_offerte()
    post(f"/offerte/{offerte_id}/status", {"status": "afgewezen"})
    assert db.execute("SELECT status FROM offertes WHERE id=?", (offerte_id,)).fetchone()[0] == "afgewezen"


def test_onbekende_rekening_geeft_een_nette_404(client):
    assert client.get("/factuur/9999/bekijk").status_code == 404
