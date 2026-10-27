"""Scans die vanaf de iPhone binnenkomen, en wat je ermee doet.

De opdracht op de telefoon heeft geen sessie. Hij authenticeert met een code,
niet met het CSRF-kenmerk van een formulier. De rest van de app blijft dat
kenmerk eisen.
"""
import hashlib
import io
import os
import re
import struct
import subprocess
import zlib

import pytest

from conftest import facturen
from test_smalle_weergave import css_van, regel_met

# Zoals Home Assistant het stuurt: de header én een afzender uit het interne netwerk.
ZIJBALK = {
    "HTTP_X_INGRESS_PATH": "/api/hassio_ingress/abc123",
    "REMOTE_ADDR": "172.30.32.2",
}

WEIGERING = "Deze scan is niet geaccepteerd. Maak in Instellingen een nieuwe code aan."


def png_bytes(breedte=8, hoogte=8, rgb=(40, 90, 160)):
    """Een echt PNG-bestand, klein genoeg om in een test te stoppen."""
    def stuk(merk, data):
        return (struct.pack(">I", len(data)) + merk + data
                + struct.pack(">I", zlib.crc32(merk + data) & 0xFFFFFFFF))

    ruw = b"".join(b"\x00" + bytes(rgb) * breedte for _ in range(hoogte))
    return (b"\x89PNG\r\n\x1a\n"
            + stuk(b"IHDR", struct.pack(">IIBBBBB", breedte, hoogte, 8, 2, 0, 0, 0))
            + stuk(b"IDAT", zlib.compress(ruw))
            + stuk(b"IEND", b""))


def bestand(inhoud, naam):
    return (io.BytesIO(inhoud), naam)


def code_maken(client, post):
    """Maak een code en geef hem terug. De pagina daarna toont hem niet meer."""
    assert post("/instellingen/scans/token", {"actie": "nieuw"}).status_code == 302
    pagina = client.get("/instellingen").data.decode()
    begin = pagina.index('<textarea id="scan-code"')
    open_einde = pagina.index(">", begin) + 1
    token = pagina[open_einde:pagina.index("</textarea>", open_einde)].strip()
    assert token
    assert token not in client.get("/instellingen").data.decode()
    return token


def code_in_de_database(db, token="code-voor-de-opdracht"):
    """Zet een code zonder langs de ingelogde browser te gaan.

    Twee testbrowsers tegelijk houden elkaars verzoek open; de app deelt
    die context, en dan raakt de opruiming de verkeerde aan."""
    db.execute(
        "UPDATE settings SET scan_token_hash=? WHERE id=1",
        (hashlib.sha256(token.encode("utf-8")).hexdigest(),),
    )
    db.commit()
    return token


def stuur(client, token, bestanden, kop=None):
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if kop:
        headers.update(kop)
    if not isinstance(bestanden, list):
        bestanden = [bestanden]
    return client.post(
        "/api/scans",
        data={"bestand": bestanden},
        headers=headers,
        content_type="multipart/form-data",
    )


def test_zonder_code_komt_er_niets_binnen(uitgelogde_client):
    antwoord = stuur(uitgelogde_client, None, bestand(png_bytes(), "bon.png"))
    assert antwoord.status_code == 401
    assert antwoord.data.decode().strip() == WEIGERING


def test_een_verkeerde_code_krijgt_dezelfde_weigering(uitgelogde_client, db):
    code_in_de_database(db)
    antwoord = stuur(uitgelogde_client, "dit-is-niet-de-code", bestand(png_bytes(), "bon.png"))
    assert antwoord.status_code == 401
    assert antwoord.data.decode().strip() == WEIGERING


def test_een_ingetrokken_code_doet_het_niet_meer(client, post, db):
    token = code_maken(client, post)
    assert post("/instellingen/scans/token", {"actie": "intrekken"}).status_code == 302
    assert db.execute("SELECT scan_token_hash FROM settings WHERE id=1").fetchone()[0] == ""
    antwoord = stuur(client, token, bestand(png_bytes(), "bon.png"))
    assert antwoord.status_code == 401
    assert antwoord.data.decode().strip() == WEIGERING
    assert db.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 0


def test_de_code_staat_alleen_als_hash_in_de_database(client, post, db):
    token = code_maken(client, post)
    opgeslagen = db.execute("SELECT scan_token_hash FROM settings WHERE id=1").fetchone()[0]
    assert opgeslagen == hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert token not in opgeslagen


def test_een_scan_mag_zonder_sessie_en_zonder_csrf(uitgelogde_client, db):
    """De opdracht heeft geen formulier. Het kenmerk eisen zou elke scan weigeren."""
    token = code_in_de_database(db)
    antwoord = stuur(uitgelogde_client, token, bestand(png_bytes(), "bon.png"))
    assert antwoord.status_code == 200
    assert antwoord.mimetype.startswith("text/plain")
    assert antwoord.data.decode().strip() == "Scan ontvangen: bon.png"
    assert db.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 1


def test_een_andere_opdracht_blijft_het_kenmerk_eisen(client, post, db):
    """De vrijstelling geldt alleen voor het scan-adres. Een bearer-kop erbij
    zetten mag een klant niet stil aanmaken."""
    token = code_maken(client, post)
    antwoord = client.post(
        "/klanten/nieuw",
        data={"naam": "Voorbeeld Klant"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert antwoord.status_code == 400
    assert db.execute("SELECT COUNT(*) FROM klanten").fetchone()[0] == 0


def test_de_extensie_telt_niet_de_inhoud_wel(client, post, db):
    token = code_maken(client, post)
    gif = stuur(client, token, bestand(b"GIF89a" + b"\x00" * 16, "foto.png"))
    assert gif.status_code == 415
    assert "geen PDF of foto" in gif.data.decode()
    webp = stuur(client, token,
                 bestand(b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 8, "plaat.webp"))
    assert webp.status_code == 415
    pdf = stuur(client, token, bestand(b"%PDF-1.4\n", "foto.jpg"))
    assert pdf.status_code == 200
    assert pdf.data.decode().strip() == "Scan ontvangen: foto.pdf"
    heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00"
    assert stuur(client, token, bestand(heic, "scan.heic")).status_code == 200
    jpeg = stuur(client, token, bestand(b"\xff\xd8\xff\xe0" + b"\x00" * 16, "a.jpg"))
    assert jpeg.status_code == 200
    soorten = {rij["soort"] for rij in db.execute("SELECT soort FROM scans")}
    assert soorten == {"pdf", "heic", "jpeg"}


def test_een_te_groot_bestand_komt_niet_in_de_inbox(client, post, db):
    token = code_maken(client, post)
    te_groot = b"\x89PNG\r\n\x1a\n" + b"\x00" * (facturen.SCAN_PER_BESTAND + 1 - 8)
    antwoord = stuur(client, token, bestand(te_groot, "groot.png"))
    assert antwoord.status_code == 413
    assert "25 MB" in antwoord.data.decode()
    assert db.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 0


def test_een_bestand_van_precies_de_grens_mag_wel(client, post, db):
    token = code_maken(client, post)
    precies = b"\x89PNG\r\n\x1a\n" + b"\x00" * (facturen.SCAN_PER_BESTAND - 8)
    antwoord = stuur(client, token, bestand(precies, "precies.png"))
    assert antwoord.status_code == 200
    assert db.execute("SELECT grootte FROM scans").fetchone()[0] == facturen.SCAN_PER_BESTAND


def test_meerdere_bestanden_in_een_verzoek(client, post, db):
    token = code_maken(client, post)
    antwoord = stuur(client, token, [
        bestand(png_bytes(), "a.png"),
        bestand(b"\xff\xd8\xff" + b"\x00" * 8, "b.jpg"),
        bestand(b"%PDF-1.4\n", "c.pdf"),
    ])
    assert antwoord.status_code == 200
    assert antwoord.data.decode().strip() == "Scans ontvangen: a.png, b.jpg en c.pdf"
    assert db.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 3


def test_een_slecht_bestand_houdt_de_rest_tegen(client, post, db):
    token = code_maken(client, post)
    antwoord = stuur(client, token, [
        bestand(png_bytes(), "goed.png"),
        bestand(b"GIF89a" + b"\x00" * 8, "fout.gif"),
    ])
    assert antwoord.status_code == 415
    assert db.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 0
    assert os.listdir(facturen.SCAN_DIR) == []


def test_een_naam_met_stuurtekens_wordt_niet_teruggestuurd(client, post):
    """Een regeleinde in de melding wordt een tweede kop. Een bidi-teken
    draait de naam om, zodat een png eruitziet als iets anders."""
    assert facturen.schone_scannaam("a\nb.png", ".png") == "ab.png"
    assert "\u202e" not in facturen.schone_scannaam("scan\u202e.png", ".png")
    token = code_maken(client, post)
    antwoord = stuur(client, token, bestand(png_bytes(), "scan\u202e.png"))
    tekst = antwoord.data.decode()
    assert antwoord.status_code == 200
    assert "\u202e" not in tekst
    assert tekst.strip() == "Scan ontvangen: scan.png"


def test_zonder_bestand_is_een_gewone_fout(uitgelogde_client, db):
    token = code_in_de_database(db)
    antwoord = uitgelogde_client.post(
        "/api/scans",
        data={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert antwoord.status_code == 400
    assert "geen bestand" in antwoord.data.decode()


def test_de_inbox_vraagt_om_inloggen(uitgelogde_client):
    assert uitgelogde_client.get("/scans").status_code == 302


def test_de_inbox_toont_de_scan_en_het_aantal(client, post):
    token = code_maken(client, post)
    stuur(client, token, [
        bestand(png_bytes(), "keuken.png"),
        bestand(b"%PDF-1.4\n", "bon.pdf"),
    ])
    pagina = client.get("/scans").data.decode()
    assert "Nieuwe scans" in pagina
    assert "keuken.png" in pagina
    assert "bon.pdf" in pagina
    assert 'data-bevestig="Deze scan verwijderen' in pagina
    # De PDF opent als bestand, niet in de lichtbak. De lichtbak zoekt zelf ook
    # naar data-soort="foto", dus tellen op de hele pagina zegt niets.
    tags = re.findall(r'<a class="scan-beeld"[^>]*>', pagina)
    png = next(tag for tag in tags if "keuken.png" in tag)
    pdf = next(tag for tag in tags if "bon.pdf" in tag)
    assert 'data-soort="foto"' in png
    assert "data-soort" not in pdf
    assert 'class="tab-tel">2<' in client.get("/").data.decode()


def test_een_scan_wordt_een_bon_en_verdwijnt_uit_de_inbox(client, post, db):
    token = code_maken(client, post)
    klant = db.execute(
        "INSERT INTO klanten (naam) VALUES ('Voorbeeld Klant')"
    ).lastrowid
    klus = db.execute(
        """INSERT INTO klussen (naam, klant_id, uurtarief, gestart)
           VALUES ('Badkamer', ?, 0, '2026-10-01')""",
        (klant,),
    ).lastrowid
    db.commit()
    stuur(client, token, bestand(png_bytes(), "bouwmarkt.png"))
    scan_id = db.execute("SELECT id FROM scans").fetchone()[0]
    formulier = client.get(f"/scans/{scan_id}/bon").data.decode()
    assert "Voorbeeld Klant" in formulier
    assert "Badkamer" in formulier

    antwoord = post(f"/scans/{scan_id}/bon", {
        "klus_id": klus, "omschrijving": "Bouwmarkt", "bedrag": "12,50",
    })
    assert antwoord.status_code == 302
    assert antwoord.headers["Location"].endswith(f"/klus/{klus}")
    assert db.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 0
    bon = db.execute("SELECT omschrijving, bedrag FROM inkopen").fetchone()
    assert bon["omschrijving"] == "Bouwmarkt"
    assert bon["bedrag"] == 12.5
    bijlage = db.execute(
        "SELECT naam, inkoop_id, notitie_id, bestand FROM bijlagen"
    ).fetchone()
    assert bijlage["naam"] == "bouwmarkt.png"
    assert bijlage["inkoop_id"]
    assert bijlage["notitie_id"] is None
    assert os.path.isfile(os.path.join(facturen.BIJLAGE_DIR, bijlage["bestand"]))
    assert "bouwmarkt.png" in client.get(f"/klus/{klus}").data.decode()
    assert 'class="tab-tel"' not in client.get("/scans").data.decode()


def test_een_scan_wordt_een_notitie(client, post, db):
    token = code_maken(client, post)
    klus = db.execute(
        """INSERT INTO klussen (naam, uurtarief, gestart)
           VALUES ('Zonder klant erbij', 0, '2026-10-01')"""
    ).lastrowid
    db.commit()
    stuur(client, token, bestand(png_bytes(), "muur.png"))
    scan_id = db.execute("SELECT id FROM scans").fetchone()[0]
    formulier = client.get(f"/scans/{scan_id}/notitie").data.decode()
    assert "Zonder klant" in formulier
    antwoord = post(f"/scans/{scan_id}/notitie", {
        "klus_id": klus, "tekst": "", "wanneer": "2026-10-18",
    })
    assert antwoord.status_code == 302
    notitie = db.execute("SELECT tekst, wanneer FROM notities").fetchone()
    assert notitie["tekst"] == ""
    assert notitie["wanneer"] == "2026-10-18"
    assert db.execute("SELECT notitie_id FROM bijlagen").fetchone()[0]
    assert db.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 0


def test_verwijderen_haalt_de_scan_weg(client, post, db):
    token = code_maken(client, post)
    stuur(client, token, bestand(png_bytes(), "weg.png"))
    scan_id = db.execute("SELECT id, bestand FROM scans").fetchone()
    zonder_kenmerk = client.post(f"/scans/{scan_id['id']}/verwijder")
    assert zonder_kenmerk.status_code == 400
    assert db.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 1
    assert post(f"/scans/{scan_id['id']}/verwijder").status_code == 302
    assert db.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 0
    assert not os.path.isfile(os.path.join(facturen.SCAN_DIR, scan_id["bestand"]))


def test_een_onbekende_klus_laat_de_scan_staan(client, post, db):
    token = code_maken(client, post)
    stuur(client, token, bestand(png_bytes(), "blijft.png"))
    scan_id = db.execute("SELECT id FROM scans").fetchone()[0]
    antwoord = post(f"/scans/{scan_id}/bon", {"klus_id": "999", "omschrijving": "Nee"})
    assert antwoord.status_code == 302
    assert db.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM inkopen").fetchone()[0] == 0


def test_opslaan_van_de_instellingen_wist_de_code_niet(client, post, db):
    token = code_maken(client, post)
    verwacht = db.execute("SELECT scan_token_hash FROM settings WHERE id=1").fetchone()[0]
    post("/instellingen", {
        "naam": "Voorbeeld Bedrijf BV",
        "scan_token_hash": "a" * 64,
    })
    assert db.execute(
        "SELECT scan_token_hash FROM settings WHERE id=1"
    ).fetchone()[0] == verwacht
    assert stuur(client, token, bestand(png_bytes(), "na.png")).status_code == 200


def test_het_adres_op_de_poort_is_het_adres_van_dit_verzoek(client):
    """De cookie hoort bij localhost. Een ander hostadres stuurt hem niet mee
    en je valt terug op het inlogscherm, dus de poort zit in dezelfde host."""
    pagina = client.get("/instellingen", headers={"Host": "localhost:8099"}).data.decode()
    assert ">http://localhost:8099/api/scans</textarea>" in pagina
    assert 'data-kopieer="scan-adres"' in pagina
    assert "zet dan eerst de VPN aan" in pagina


def test_via_de_zijbalk_leggen_we_het_adres_uit(client):
    """Ingress kent het adres op het netwerk niet. Een verzonnen host zou de
    opdracht naar de verkeerde plek sturen. Zonder exact adres is er ook
    niets om te kopiëren."""
    pagina = client.get("/instellingen", environ_overrides=ZIJBALK).data.decode()
    assert "adres-van-home-assistant" in pagina
    assert ":8099/api/scans" in pagina
    assert "zet dan eerst de VPN aan" in pagina
    assert 'id="scan-adres"' in pagina
    assert 'data-kopieer="scan-adres"' not in pagina
    assert 'value="http://' not in pagina


def test_de_uitleg_noemt_de_opdracht_en_het_veld(client):
    pagina = client.get("/instellingen").data.decode()
    assert "Naar Facturen" in pagina
    assert "Haal inhoud van URL op" in pagina
    assert "Toon meer" in pagina
    assert "Vraag om hoofdtekst" in pagina
    assert "Formulier" in pagina
    assert "type Bestand" in pagina
    assert "geen bestand" in pagina
    assert "Herhaal voor elk onderdeel" in pagina
    assert "Herhaal onderdeel" in pagina
    assert "Stop herhalen" in pagina
    assert "Authorization" in pagina
    assert "Toon melding" in pagina
    assert "Toon in deelpaneel" in pagina
    assert "deelblad" not in pagina
    assert "herhaal het veld" not in pagina


def test_de_getoonde_code_verdwijnt_uit_de_sessie(client, post):
    """Weghalen vóór het tekenen zou de code kwijt zijn als de pagina mislukt.
    Na een gelukte pagina mag een verversing hem niet nog eens tonen, en hij
    hoort ook niet in de sessie achter te blijven."""
    assert post("/instellingen/scans/token", {"actie": "nieuw"}).status_code == 302
    with client.session_transaction() as sessie:
        assert sessie.get("scan_token_eenmalig")
    pagina = client.get("/instellingen").data.decode()
    assert 'data-kopieer="scan-code"' in pagina
    assert "Kopieer" in pagina
    assert "Gekopieerd" in pagina
    assert "clipboard.writeText" in pagina
    assert 'execCommand("copy")' in pagina
    assert "min-height: 44px" in regel_met(css_van(pagina), ".kopieer-rij textarea.scan-code")
    with client.session_transaction() as sessie:
        assert "scan_token_eenmalig" not in sessie


def test_een_punt_pdf_krijgt_geen_dubbele_extensie(client, post, db):
    """'.pdf' heeft voor splitext geen stam. Daar '.pdf' achter plakken
    werd '.pdf.pdf' in de inbox."""
    assert facturen.schone_scannaam(".pdf", ".pdf") == "scan.pdf"
    token = code_maken(client, post)
    antwoord = stuur(client, token, bestand(b"%PDF-1.4\n", ".pdf"))
    assert antwoord.status_code == 200
    assert antwoord.data.decode().strip() == "Scan ontvangen: scan.pdf"
    assert db.execute("SELECT naam FROM scans").fetchone()[0] == "scan.pdf"


def test_het_eenentwintigste_bestand_wordt_geweigerd(client, post, db):
    token = code_maken(client, post)
    bestanden = [bestand(png_bytes(), f"s{i}.png") for i in range(21)]
    antwoord = stuur(client, token, bestanden)
    assert antwoord.status_code == 400
    assert "20" in antwoord.data.decode()
    assert db.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 0
    assert os.listdir(facturen.SCAN_DIR) == []


def test_twintig_bestanden_mogen_wel(client, post, db):
    token = code_maken(client, post)
    bestanden = [bestand(png_bytes(), f"s{i}.png") for i in range(20)]
    antwoord = stuur(client, token, bestanden)
    assert antwoord.status_code == 200
    assert db.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 20


def test_een_te_groot_verzoek_noemt_het_totaal_niet_een_bestand(client, post, monkeypatch):
    """De grens van het hele verzoek is een andere zin dan die van één bestand.
    Anders zoekt iemand een scan van 25 MB terwijl het totaal de deur dicht deed."""
    monkeypatch.setattr(facturen, "SCAN_VERZOEK_GRENS", 1024)
    token = code_maken(client, post)
    antwoord = stuur(client, token, bestand(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4000, "samen.png"))
    assert antwoord.status_code == 413
    tekst = antwoord.data.decode()
    assert "1 kB" in tekst
    assert "Eén scan" not in tekst
    assert "Samen mogen de scans" in tekst


def test_een_gewoon_formulier_blijft_op_de_kleine_grens(client, monkeypatch):
    """De ruimere grens hangt alleen aan /api/scans. Een ander formulier dat
    intussen 100 MB mag sturen, vult de schijf alsnog."""
    assert facturen.app.config["MAX_CONTENT_LENGTH"] == 32 * 1024 * 1024
    monkeypatch.setitem(facturen.app.config, "MAX_CONTENT_LENGTH", 1024)
    antwoord = client.post(
        "/klanten/nieuw",
        data={"naam": "x" * 2000, "csrf_token": "test-token"},
        follow_redirects=True,
    )
    assert antwoord.status_code == 200
    pagina = antwoord.data.decode()
    assert "Samen mogen de bestanden hooguit 1 kB zijn" in pagina
    assert "Samen mogen de scans" not in pagina


def test_een_mislukte_pdfminiatuur_wordt_niet_opnieuw_geprobeerd(client, post, db, monkeypatch):
    token = code_maken(client, post)
    assert stuur(client, token, bestand(b"%PDF-1.4\n", "kapot.pdf")).status_code == 200
    aanroepen = {"n": 0}

    def nep(*_args, **_kwargs):
        aanroepen["n"] += 1
        raise subprocess.CalledProcessError(1, "pdftoppm")

    monkeypatch.setattr(facturen.shutil, "which", lambda _naam: "/usr/bin/pdftoppm")
    monkeypatch.setattr(facturen.subprocess, "run", nep)
    assert client.get("/scans").status_code == 200
    assert client.get("/scans").status_code == 200
    assert aanroepen["n"] == 1
    assert any(naam.endswith(".min.mislukt") for naam in os.listdir(facturen.SCAN_DIR))
    scan_id = db.execute("SELECT id FROM scans").fetchone()[0]
    assert post(f"/scans/{scan_id}/verwijder").status_code == 302
    assert not any(naam.endswith(".min.mislukt") for naam in os.listdir(facturen.SCAN_DIR))


def test_zonder_pdftoppm_onthouden_we_de_mislukking_niet(client, post, monkeypatch):
    """Ontbreekt het programma, dan kan een update van de add-on het alsnog
    brengen. Een vlag 'mislukt' zou die eerste pagina voorgoed verbergen."""
    token = code_maken(client, post)
    assert stuur(client, token, bestand(b"%PDF-1.4\n", "later.pdf")).status_code == 200
    monkeypatch.setattr(facturen.shutil, "which", lambda _naam: None)
    assert client.get("/scans").status_code == 200
    assert client.get("/scans").status_code == 200
    assert not any(naam.endswith(".min.mislukt") for naam in os.listdir(facturen.SCAN_DIR))


def test_een_pdfminiatuur_blijft_heel_in_het_vak(client):
    css = css_van(client.get("/scans").data.decode())
    assert "object-fit: contain" in regel_met(css, ".scan-beeld img.is-pdf")
    # Foto's blijven het vak vullen. De contain-regel geldt alleen voor de PDF.
    assert "object-fit: cover" in css
    assert "is-pdf" in css


def _browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    return sync_playwright


def test_een_lange_scannaam_duwt_de_inbox_niet_van_het_scherm(client, db):
    """Op 320 en 390 werd de kaart 504 px breed: de naam had geen spatie en
    1fr mocht niet krimpen. De knoppen vielen rechts buiten beeld."""
    sync_playwright = _browser()
    naam = "a" * 120
    db.execute(
        """INSERT INTO scans (bestand, naam, soort, grootte, ontvangen)
           VALUES ('lang.png', ?, 'png', 12, '2026-10-27T12:00:00')""",
        (naam,),
    )
    db.commit()
    html = client.get("/scans").data.decode()
    assert naam in html
    css = css_van(html)
    smal = css[css.index("@media (max-width: 420px)"):]
    assert "minmax(0, 1fr)" in smal
    assert "grid-template-columns: 1fr;" not in smal
    assert "overflow-wrap: anywhere" in regel_met(css, ".scan-naam")
    assert "min-width: 0" in regel_met(css, ".scan-kaart")

    with sync_playwright() as p:
        browser = p.webkit.launch()
        pagina = browser.new_page(device_scale_factor=1)
        try:
            for breedte in (390, 320):
                pagina.set_viewport_size({"width": breedte, "height": 700})
                pagina.set_content(html)
                gemeten = pagina.evaluate(
                    """() => {
                      const doc = document.documentElement;
                      const knoppen = [...document.querySelectorAll('.scan-acties .btn')];
                      const rechts = Math.max(...knoppen.map((k) => k.getBoundingClientRect().right));
                      return {
                        scroll: doc.scrollWidth - doc.clientWidth,
                        knopBinnen: rechts <= window.innerWidth + 1,
                      };
                    }"""
                )
                assert gemeten["scroll"] <= 1, breedte
                assert gemeten["knopBinnen"] is True
        finally:
            browser.close()


def test_de_code_is_op_een_telefoon_helemaal_te_bereiken(client, post):
    sync_playwright = _browser()
    assert post("/instellingen/scans/token", {"actie": "nieuw"}).status_code == 302
    html = client.get("/instellingen").data.decode()
    begin = html.index('<textarea id="scan-code"')
    open_einde = html.index(">", begin) + 1
    token = html[open_einde:html.index("</textarea>", open_einde)].strip()
    assert len(token) > 20

    with sync_playwright() as p:
        browser = p.webkit.launch()
        pagina = browser.new_page(device_scale_factor=1)
        try:
            for breedte in (390, 320):
                pagina.set_viewport_size({"width": breedte, "height": 900})
                pagina.set_content(html)
                gemeten = pagina.evaluate(
                    """(verwacht) => {
                      const veld = document.getElementById('scan-code');
                      const doc = document.documentElement;
                      const doos = veld.getBoundingClientRect();
                      return {
                        hoogte: doos.height,
                        rechts: doos.right,
                        pagina: doc.scrollWidth - doc.clientWidth,
                        tekst: veld.value,
                        bereikbaar: veld.scrollWidth <= veld.clientWidth + 2
                          || veld.scrollHeight > veld.clientHeight,
                      };
                    }""",
                    token,
                )
                assert gemeten["tekst"] == token
                assert gemeten["hoogte"] >= 44
                assert gemeten["pagina"] <= 1, breedte
                assert gemeten["rechts"] <= breedte + 1
                assert gemeten["bereikbaar"] is True
            knop = pagina.locator('[data-kopieer="scan-code"]')
            knop.click()
            pagina.wait_for_function(
                """() => {
                  const veld = document.getElementById('scan-code');
                  const status = veld.parentElement.querySelector('.gekopieerd');
                  return status && !status.hidden;
                }"""
            )
        finally:
            browser.close()
