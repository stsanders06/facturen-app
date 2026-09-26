"""Mailen gaat via een controlescherm, en nooit vanzelf bij opslaan."""
import io

from conftest import facturen

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


def mailserver(db):
    db.execute("UPDATE settings SET smtp_host='smtp.example.com', naam='Jansen' WHERE id=1")
    db.commit()


def test_voorbeeld_noemt_onderwerp_tekst_pdf_en_bon(post, db, client):
    mailserver(db)
    post("/klussen/nieuw", {"naam": "Badkamer", "uurtarief": "45"})
    klus_id = db.execute("SELECT id FROM klussen").fetchone()[0]
    post(f"/klus/{klus_id}/bijlage", {"bijlage": (io.BytesIO(PNG), "bon-gamma.png")},
         content_type="multipart/form-data")
    post(f"/bijlage/{db.execute('SELECT id FROM bijlagen').fetchone()[0]}/meesturen")
    post(f"/klus/{klus_id}/dag", {"datum": "2026-08-14", "van": "09:00", "tot": "17:00"})
    post("/nieuw", {
        "klant_naam": "Jan Jansen", "klant_email": "jan@example.com",
        "datum": "2026-08-14", "omschrijving": "Uren", "type": "arbeid_uur",
        "aantal": "8", "prijs": "45", "regel_klus": str(klus_id),
    })
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]

    pagina = client.get(f"/factuur/{factuur_id}/mail").data.decode()
    assert "Mail controleren" in pagina
    assert "Aan" in pagina
    assert "jan@example.com" in pagina
    assert "Onderwerp" in pagina
    assert "Rekening 2026-001 - Jansen" in pagina
    assert "Hierbij de rekening (2026-001)" in pagina
    assert "De bonnetjes zitten erbij." in pagina
    assert "2026-001.pdf" in pagina
    assert "bon-gamma.png" in pagina
    assert ">Mail versturen</button>" in pagina
    assert f'action="/factuur/{factuur_id}/verstuur"' in pagina
    # Het voorbeeld zelf verstuurt niets en geeft het concept nog geen nummer.
    assert db.execute("SELECT nummer FROM facturen").fetchone()[0] == ""
    assert db.execute("SELECT status FROM facturen").fetchone()[0] == "concept"


def test_voorbeeld_van_een_genummerde_rekening_gebruikt_dat_nummer(client, db, maak_factuur):
    mailserver(db)
    factuur_id = maak_factuur(nummer="2026-007", status="verzonden")
    pagina = client.get(f"/factuur/{factuur_id}/mail").data.decode()
    assert "Rekening 2026-007 - Jansen" in pagina
    assert "2026-007.pdf" in pagina
    assert "Nogmaals" not in pagina
    assert "al eens gemaild" in pagina
    assert "Er gaan geen bonnetjes mee." in pagina


def test_zonder_mailserver_zie_je_waarom_en_geen_verstuurknop(client, maak_factuur):
    factuur_id = maak_factuur()
    pagina = client.get(f"/factuur/{factuur_id}/mail").data.decode()
    assert "mailserver" in pagina
    assert ">Mail versturen</button>" not in pagina
    assert "2026-001.pdf" in pagina


def test_mailen_op_de_kaart_opent_het_voorbeeld(client, maak_factuur):
    concept = maak_factuur(nummer="", status="concept", email="jan@example.com")
    verzonden = maak_factuur(nummer="2026-002", status="verzonden", email="jan@example.com")
    inhoud = client.get("/").data.decode()
    assert f'href="/factuur/{concept}/mail">Mailen</a>' in inhoud
    assert f'href="/factuur/{verzonden}/mail">Nogmaals mailen</a>' in inhoud
    assert f'action="/factuur/{concept}/verstuur"' not in inhoud
    assert f'action="/factuur/{verzonden}/verstuur"' not in inhoud


def test_opslaan_stuurt_geen_mail(monkeypatch, post, db):
    verstuurd = []
    monkeypatch.setattr(facturen, "_mail_pdf",
                        lambda *a, **k: verstuurd.append(a) or (True, ""))
    mailserver(db)
    post("/nieuw", {
        "klant_naam": "Jan", "klant_email": "jan@example.com", "datum": "2026-08-14",
        "omschrijving": "Werk", "type": "arbeid_klus", "aantal": "1", "prijs": "80",
        "verstuur": "ja",
    })
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    post(f"/factuur/{factuur_id}/bewerk", {
        "klant_naam": "Jan", "klant_email": "jan@example.com", "datum": "2026-08-14",
        "omschrijving": "Werk", "type": "arbeid_klus", "aantal": "1", "prijs": "90",
        "verstuur": "ja",
    })
    post(f"/factuur/{factuur_id}/definitief", {"verstuur": "ja"})

    assert verstuurd == []
    factuur = db.execute("SELECT nummer, status FROM facturen").fetchone()
    assert factuur["nummer"] != ""
    assert factuur["status"] == "concept"


def test_rekeningformulier_heeft_geen_vinkje_om_meteen_te_mailen(client, maak_factuur):
    factuur_id = maak_factuur()
    assert 'name="verstuur"' not in client.get("/nieuw").data.decode()
    assert 'name="verstuur"' not in client.get(f"/factuur/{factuur_id}/bewerk").data.decode()
    # Bij een offerte blijft dat vinkje: dit gaat over rekeningen.
    assert 'name="verstuur"' in client.get("/offertes/nieuw").data.decode()


def test_de_kaarten_op_mail_controleren_staan_uit_elkaar(client, maak_factuur):
    """Losse .card-secties hebben geen eigen marge. Zonder de wrapper raken
    Rekening, Bericht en Bijlagen elkaar."""
    factuur_id = maak_factuur()
    pagina = client.get(f"/factuur/{factuur_id}/mail").data.decode()
    begin = pagina.index('<div class="cards">')
    assert '<section class="card">' not in pagina[:begin]
    assert pagina[begin:].count('<section class="card">') == 4
    assert ".cards { display: flex; flex-direction: column; gap: 12px; }" in pagina


def test_mail_versturen_vanaf_het_voorbeeld_stuurt_wel(monkeypatch, post, db, maak_factuur):
    verstuurd = {}

    def nep_mail(s, ontvanger, onderwerp, tekst, pad, bestandsnaam, extra=None):
        verstuurd["onderwerp"] = onderwerp
        verstuurd["tekst"] = tekst
        verstuurd["bestandsnaam"] = bestandsnaam
        return True, ""

    monkeypatch.setattr(facturen, "_mail_pdf", nep_mail)
    mailserver(db)
    factuur_id = maak_factuur(nummer="", status="concept")
    post(f"/factuur/{factuur_id}/verstuur")
    assert verstuurd["onderwerp"].startswith("Rekening 2026-001")
    assert "Hierbij de rekening (2026-001)" in verstuurd["tekst"]
    assert verstuurd["bestandsnaam"] == "2026-001.pdf"
