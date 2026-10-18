"""Mailen gaat via een controlescherm, en nooit vanzelf bij opslaan."""
import io
import re

import pytest

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
    inkoop_id = db.execute("SELECT inkoop_id FROM bijlagen").fetchone()[0]
    post("/nieuw", {
        "klant_naam": "Jan Jansen", "klant_email": "jan@example.com",
        "datum": "2026-08-14",
        "omschrijving": ["Uren", "Bon gamma"],
        "type": ["arbeid_uur", "bon"],
        "aantal": ["8", "1"],
        "prijs": ["45", "12"],
        "regel_klus": [str(klus_id), ""],
        "regel_inkoop": ["", str(inkoop_id)],
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
    # Ook een offerte mailt niet meer vanzelf bij opslaan.
    assert 'name="verstuur"' not in client.get("/offertes/nieuw").data.decode()


def test_de_kaarten_op_mail_controleren_staan_uit_elkaar(client, maak_factuur):
    """Losse .card-secties hebben geen eigen marge. Zonder de wrapper raken
    Rekening, Bericht en Bijlagen elkaar."""
    factuur_id = maak_factuur()
    pagina = client.get(f"/factuur/{factuur_id}/mail").data.decode()
    begin = pagina.index('<div class="cards">')
    assert '<section class="card">' not in pagina[:begin]
    assert pagina[begin:].count('<section class="card">') == 4
    assert ".cards { display: flex; flex-direction: column; gap: 12px; }" in pagina


def test_de_rekening_pdf_zonder_inlog_gaat_naar_inloggen(maak_factuur, uitgelogde_client, client):
    factuur_id = maak_factuur(nummer="", status="concept")
    zonder = uitgelogde_client.get(f"/factuur/{factuur_id}/mail-pdf")
    assert zonder.status_code == 302
    assert "/inloggen" in zonder.headers["Location"]

    met = client.get(f"/factuur/{factuur_id}/mail-pdf")
    assert met.status_code == 200
    assert met.mimetype == "application/pdf"
    assert met.headers["Content-Disposition"].startswith("inline")
    assert met.headers["X-Content-Type-Options"] == "nosniff"
    assert met.headers["Cache-Control"] == "private, no-store"
    bekijk = client.get(f"/factuur/{factuur_id}/bekijk")
    assert bekijk.headers["X-Content-Type-Options"] == "nosniff"
    assert bekijk.headers["Cache-Control"] == "private, no-store"


def _koppen_van_een_bestand(antwoord):
    assert antwoord.headers["X-Content-Type-Options"] == "nosniff"
    assert antwoord.headers["Cache-Control"] == "private, no-store"


def test_mail_pdf_en_offerte_bekijken_sturen_nosniff(client, maak_factuur, maak_offerte):
    """Ook de download en een lege 404. Een cache mag die pagina's niet bewaren."""
    factuur_id = maak_factuur()
    offerte_id = maak_offerte()
    for pad in (
        f"/factuur/{factuur_id}/mail-pdf",
        f"/offerte/{offerte_id}/bekijk",
        f"/factuur/{factuur_id}/pdf",
        f"/offerte/{offerte_id}/pdf",
    ):
        antwoord = client.get(pad)
        assert antwoord.status_code == 200, pad
        _koppen_van_een_bestand(antwoord)
    for pad in (
        "/factuur/9999/mail-pdf",
        "/factuur/9999/bekijk",
        "/factuur/9999/pdf",
        "/offerte/9999/bekijk",
        "/offerte/9999/pdf",
    ):
        antwoord = client.get(pad)
        assert antwoord.status_code == 404, pad
        _koppen_van_een_bestand(antwoord)


def test_bijlagen_zijn_een_voorbeeld_en_het_vinkje_opent_ze_niet(post, db, client):
    """Het vinkje staat buiten de link. Een tik erop wisselt alleen of het
    bestand meegaat; het voorbeeld en de naam wijzen naar de bijlage zelf."""
    mailserver(db)
    post("/klussen/nieuw", {"naam": "Badkamer Voorbeeldstraat", "uurtarief": "45"})
    klus_id = db.execute("SELECT id FROM klussen").fetchone()[0]
    lange_naam = "4ea30bc9-dab2-40d9-ab58-5095140a495c.jpeg"
    post(f"/klus/{klus_id}/bijlage", {
        "bijlage": [
            (io.BytesIO(PNG), lange_naam),
            (io.BytesIO(b"%PDF-1.4 test"), "bon-gamma.pdf"),
        ],
    }, content_type="multipart/form-data")
    # Eerst de id's ophalen. De cursor open laten terwijl de app schrijft
    # zet de database op slot.
    bijlage_ids = [rij["id"] for rij in db.execute("SELECT id FROM bijlagen")]
    for bijlage_id in bijlage_ids:
        post(f"/bijlage/{bijlage_id}/meesturen")
    db.execute("UPDATE inkopen SET omschrijving='Tegellijm'")
    db.commit()
    inkoop_id = db.execute("SELECT id FROM inkopen").fetchone()[0]
    post("/nieuw", {
        "klant_naam": "Voorbeeld Bedrijf BV", "klant_email": "klant@example.com",
        "datum": "2026-08-14",
        "omschrijving": "Tegellijm", "type": "bon", "aantal": "1", "prijs": "12",
        "regel_inkoop": str(inkoop_id),
    })
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    ids = [rij["id"] for rij in db.execute("SELECT id FROM bijlagen ORDER BY id")]

    pagina = client.get(f"/factuur/{factuur_id}/mail").data.decode()
    assert "PDF van de rekening" in pagina
    assert f'href="/factuur/{factuur_id}/mail-pdf"' in pagina
    assert "Tegellijm" in pagina
    assert "Badkamer Voorbeeldstraat" in pagina
    assert "Foto" in pagina
    assert "PDF" in pagina
    assert "bytes" in pagina
    assert 'text-overflow: ellipsis' in pagina
    assert "white-space: nowrap" in pagina
    assert "width: 44px" in pagina
    assert "height: 44px" in pagina
    # Twee bonnen, niet ook een vinkje op de rekening-PDF: die gaat altijd mee.
    assert pagina.count('name="mee"') == 2
    foto_id, pdf_id = ids
    assert f'href="/bijlage/{foto_id}"' in pagina
    assert f'src="/bijlage/{foto_id}"' in pagina
    assert f'href="/bijlage/{pdf_id}"' in pagina
    assert f'src="/bijlage/{pdf_id}"' not in pagina

    regels = re.findall(r'<li class="bijlage-regel">.*?</li>', pagina, re.S)
    assert len(regels) == 3
    with_vink = [regel for regel in regels if 'name="mee"' in regel]
    assert len(with_vink) == 2
    for regel in with_vink:
        link = regel.index("<a ")
        assert regel.index('name="mee"') < link
        assert "</label>" in regel[:link]
        assert "<input" not in regel[link:]
    pdf_regel = next(regel for regel in regels if "PDF van de rekening" in regel)
    assert 'name="mee"' not in pdf_regel
    assert 'data-soort="pdf"' in pdf_regel
    # De bestandsnaam staat los, niet in de grijze zin die op een telefoon breekt.
    bestand = pdf_regel.split('class="bijlage-bestand"', 1)[1].split("</span>", 1)[0]
    assert "2026-001.pdf" in bestand
    assert "2026-001.pdf" not in pdf_regel.split('class="bijlage-info"', 1)[1]
    assert 'role="dialog"' in pagina
    assert 'aria-modal="true"' in pagina
    assert 'decoding="async"' in pagina
    foto_regel = next(regel for regel in regels if lange_naam in regel)
    assert 'data-soort="foto"' in foto_regel
    assert f'title="{lange_naam}"' in foto_regel
    # De lange naam is de titel, niet nog eens de grijze regel (die werd drie regels).
    info = foto_regel.split('class="bijlage-info"', 1)[1]
    assert lange_naam not in info


def test_de_lichtbak_houdt_de_focus_en_geeft_hem_terug(post, db, client):
    """Een foto opent als dialoog. Tab blijft daar, Escape sluit, en de focus
    keert terug naar de link die hem opende. Een tik op de foto zelf zet de
    focus op de pagina; Escape moet hem dan nog steeds sluiten."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    mailserver(db)
    post("/klussen/nieuw", {"naam": "Badkamer Voorbeeldstraat", "uurtarief": "45"})
    klus_id = db.execute("SELECT id FROM klussen").fetchone()[0]
    post(f"/klus/{klus_id}/bijlage",
         {"bijlage": (io.BytesIO(PNG), "voeg-badkamer.png")},
         content_type="multipart/form-data")
    bijlage_id = db.execute("SELECT id FROM bijlagen").fetchone()[0]
    post(f"/bijlage/{bijlage_id}/meesturen")
    inkoop_id = db.execute("SELECT id FROM inkopen").fetchone()[0]
    post("/nieuw", {
        "klant_naam": "Voorbeeld Klant", "klant_email": "klant@example.com",
        "datum": "2026-08-14",
        "omschrijving": "Tegellijm", "type": "bon", "aantal": "1", "prijs": "12",
        "regel_inkoop": str(inkoop_id),
    })
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    html = client.get(f"/factuur/{factuur_id}/mail").data.decode()

    with sync_playwright() as p:
        browser = p.webkit.launch()
        pagina = browser.new_page(
            viewport={"width": 390, "height": 844}, device_scale_factor=1)
        pagina.set_content(html)
        link = pagina.locator('a.bijlage-open[data-soort="foto"]')
        link.click()
        bak = pagina.locator("#lichtbak")
        assert bak.get_attribute("role") == "dialog"
        assert bak.get_attribute("aria-modal") == "true"
        assert bak.get_attribute("aria-label") == "voeg-badkamer.png"
        assert bak.get_attribute("hidden") is None
        assert pagina.evaluate(
            "() => document.activeElement.classList.contains('lichtbak-sluit')")
        pagina.keyboard.press("Tab")
        assert pagina.evaluate(
            "() => document.activeElement.classList.contains('lichtbak-sluit')")
        pagina.keyboard.press("Shift+Tab")
        assert pagina.evaluate(
            "() => document.activeElement.classList.contains('lichtbak-sluit')")
        pagina.keyboard.press("Escape")
        assert bak.get_attribute("hidden") is not None
        assert pagina.evaluate(
            "() => document.activeElement.getAttribute('data-soort') === 'foto'")

        # De foto is geen knop. Een tik erop haalt de focus uit de lichtbak;
        # Escape moet hem dan nog steeds dichtdoen en de focus terugzetten.
        link.click()
        assert bak.get_attribute("hidden") is None
        pagina.locator("#lichtbak-foto").click()
        assert bak.get_attribute("hidden") is None
        pagina.keyboard.press("Escape")
        assert bak.get_attribute("hidden") is not None
        assert pagina.evaluate(
            "() => document.activeElement.getAttribute('data-soort') === 'foto'")

        pagina.set_viewport_size({"width": 320, "height": 700})
        regels = pagina.locator(".bijlage-bestand").evaluate(
            "el => el.getClientRects().length")
        assert regels == 1
        assert "\n" not in pagina.locator(".bijlage-bestand").inner_text()
        browser.close()


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
