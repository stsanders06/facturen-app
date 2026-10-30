"""Foto's en bonnetjes bij een klus."""
import io
import os
import pathlib

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
    post("/klussen/nieuw", {"naam": "Badkamer Voorbeeldstraat", "uurtarief": "45"})
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
    assert antwoord.mimetype == "image/png"
    # Inline, zodat een tik op het voorbeeld de foto in beeld zet en niet
    # meteen een download start.
    assert antwoord.headers["Content-Disposition"].startswith("inline")
    assert antwoord.headers["X-Content-Type-Options"] == "nosniff"
    assert antwoord.headers["Cache-Control"] == "private, no-store"


def test_een_pdf_bijlage_komt_inline_als_pdf(post, db, client, klus_id):
    upload(post, klus_id, "bon.pdf", b"%PDF-1.4 test")
    bijlage_id = db.execute("SELECT id FROM bijlagen").fetchone()[0]
    antwoord = client.get(f"/bijlage/{bijlage_id}")
    assert antwoord.status_code == 200
    assert antwoord.mimetype == "application/pdf"
    assert antwoord.headers["Content-Disposition"].startswith("inline")


def test_bijlage_zonder_inlog_gaat_naar_inloggen(post, db, uitgelogde_client, klus_id):
    upload(post, klus_id)
    bijlage_id = db.execute("SELECT id FROM bijlagen").fetchone()[0]
    antwoord = uitgelogde_client.get(f"/bijlage/{bijlage_id}")
    assert antwoord.status_code == 302
    assert "/inloggen" in antwoord.headers["Location"]
    assert antwoord.data != PNG


def test_bidi_in_de_weergavenaam_komt_niet_in_de_kop(post, db, client, klus_id):
    """U+202E en de andere bidi-stuurtekens draaien filename* om. Ze gaan eruit."""
    upload(post, klus_id)
    db.execute(
        "UPDATE bijlagen SET naam=?",
        ("bon\u202e\u200f\u202a\u2066.png",),
    )
    db.commit()
    bijlage_id = db.execute("SELECT id FROM bijlagen").fetchone()[0]
    antwoord = client.get(f"/bijlage/{bijlage_id}")
    assert antwoord.status_code == 200
    beschikking = antwoord.headers["Content-Disposition"]
    assert "bon.png" in beschikking
    assert "\u202e" not in beschikking
    assert "202e" not in beschikking.lower()
    assert "200f" not in beschikking.lower()


def test_een_regeleinde_in_de_weergavenaam_opent_de_bijlage_toch(post, db, client, klus_id):
    """De weergavenaam komt in Content-Disposition. Een regeleinde daarin
    liet de server met 500 stuklopen; de foto zelf is wel gewoon te tonen."""
    upload(post, klus_id)
    db.execute("UPDATE bijlagen SET naam=?", ("bon\r\n.png",))
    db.commit()
    bijlage_id = db.execute("SELECT id FROM bijlagen").fetchone()[0]
    antwoord = client.get(f"/bijlage/{bijlage_id}")
    assert antwoord.status_code == 200
    assert antwoord.data == PNG
    beschikking = antwoord.headers["Content-Disposition"]
    assert "\r" not in beschikking
    assert "\n" not in beschikking
    assert "bon.png" in beschikking


def test_stuurtekens_in_de_bestandsnaam_geven_404(client, db, klus_id):
    """Een NUL of een regeleinde in de opgeslagen naam mag geen 500 geven.
    Zo'n naam wijst niet naar een bestand dat we willen tonen."""
    for naam in ("plaat\x00je.png", "plaat\r\nje.png", "plaat\nje.png"):
        assert facturen.veilig_pad(facturen.BIJLAGE_DIR, naam) is None
        db.execute("DELETE FROM bijlagen")
        db.execute(
            """INSERT INTO bijlagen (klus_id, bestand, naam, toegevoegd)
               VALUES (?, ?, 'plaatje.png', '2026-08-14')""",
            (klus_id, naam),
        )
        db.commit()
        bijlage_id = db.execute("SELECT id FROM bijlagen").fetchone()[0]
        antwoord = client.get(f"/bijlage/{bijlage_id}")
        assert antwoord.status_code == 404, naam
        assert antwoord.headers["X-Content-Type-Options"] == "nosniff"
        assert antwoord.headers["Cache-Control"] == "private, no-store"


def test_een_pad_buiten_de_bijlagenmap_geeft_404(client, db, klus_id):
    """Een naam met .. of een snelkoppeling mag niet buiten de map wijzen."""
    buiten = pathlib.Path(facturen.BIJLAGE_DIR).parent / "geheim.txt"
    buiten.write_text("geheim", encoding="utf-8")
    try:
        db.execute(
            """INSERT INTO bijlagen (klus_id, bestand, naam, toegevoegd)
               VALUES (?, '../geheim.txt', 'geheim.txt', '2026-08-14')""",
            (klus_id,),
        )
        db.commit()
        bijlage_id = db.execute("SELECT id FROM bijlagen").fetchone()[0]
        antwoord = client.get(f"/bijlage/{bijlage_id}")
        assert antwoord.status_code == 404
        assert b"geheim" not in antwoord.data

        koppeling = pathlib.Path(facturen.BIJLAGE_DIR) / "via.png"
        koppeling.symlink_to(buiten)
        db.execute(
            "UPDATE bijlagen SET bestand='via.png', naam='via.png' WHERE id=?",
            (bijlage_id,),
        )
        db.commit()
        antwoord = client.get(f"/bijlage/{bijlage_id}")
        assert antwoord.status_code == 404
        assert b"geheim" not in antwoord.data
    finally:
        buiten.unlink(missing_ok=True)


def test_bijlage_verwijderen_bewaart_het_bestand_voor_het_geval_je_terugwilt(post, db, klus_id):
    """Een foto is niet opnieuw te maken, dus hij blijft staan zolang de bijlage in
    de prullenbak zit. Pas als die wordt opgeruimd gaat het bestand er echt uit."""
    upload(post, klus_id)
    rij = db.execute("SELECT * FROM bijlagen").fetchone()
    pad = os.path.join(facturen.BIJLAGE_DIR, rij["bestand"])

    post(f"/bijlage/{rij['id']}/verwijder")
    assert db.execute("SELECT COUNT(*) FROM bijlagen").fetchone()[0] == 0
    assert os.path.exists(pad)


def test_een_verwijderde_bijlage_is_terug_te_halen(post, db, klus_id):
    upload(post, klus_id)
    rij = db.execute("SELECT * FROM bijlagen").fetchone()
    post(f"/bijlage/{rij['id']}/verwijder")

    prullenbak_id = db.execute("SELECT id FROM prullenbak").fetchone()[0]
    post(f"/prullenbak/{prullenbak_id}/terug")

    terug = db.execute("SELECT * FROM bijlagen").fetchone()
    assert terug["id"] == rij["id"]
    assert terug["bestand"] == rij["bestand"]
    assert os.path.exists(os.path.join(facturen.BIJLAGE_DIR, terug["bestand"]))


def test_klus_verwijderen_neemt_de_bijlagen_mee(post, db, klus_id):
    upload(post, klus_id)
    pad = os.path.join(facturen.BIJLAGE_DIR,
                       db.execute("SELECT bestand FROM bijlagen").fetchone()[0])

    post(f"/klus/{klus_id}/verwijder")
    assert db.execute("SELECT COUNT(*) FROM bijlagen").fetchone()[0] == 0
    # Het bestand blijft staan tot de prullenbak wordt opgeruimd.
    assert os.path.exists(pad)


def test_de_klus_heeft_geen_meesturen_knop_meer(post, client, db, klus_id):
    """Een bon op de rekening gaat mee. Het vinkje per bestand is weg."""
    upload(post, klus_id)
    bijlage_id = db.execute("SELECT id FROM bijlagen").fetchone()[0]
    assert db.execute("SELECT meesturen FROM bijlagen").fetchone()[0] == 0

    pagina = client.get(f"/klus/{klus_id}").data.decode()
    assert "Gaat mee" not in pagina
    assert "Meesturen met de rekening" not in pagina
    assert f'action="/bijlage/{bijlage_id}/meesturen"' not in pagina

    # Een oude knop die nog ergens openstaat mag de vlag niet meer omzetten.
    post(f"/bijlage/{bijlage_id}/meesturen")
    assert db.execute("SELECT meesturen FROM bijlagen").fetchone()[0] == 0


def test_de_kluspagina_toont_de_bijlage(post, db, client, klus_id):
    upload(post, klus_id, "bonnetje-gamma.png")
    assert "bonnetje-gamma.png" in client.get(f"/klus/{klus_id}").data.decode()


def test_alleen_een_bon_op_de_rekening_gaat_mee(post, db, klus_id):
    """Ook als het oude vinkje uit stond. Een andere bon van de klus niet."""
    upload(post, klus_id, "meesturen.png")
    upload(post, klus_id, "prive.png")
    eerste = db.execute("SELECT id FROM bijlagen ORDER BY id").fetchone()[0]
    db.execute("UPDATE bijlagen SET meesturen=0")
    db.commit()

    inkoop_id = db.execute(
        "SELECT inkoop_id FROM bijlagen WHERE id=?", (eerste,)
    ).fetchone()[0]
    post("/nieuw", {"klant_naam": "Jan", "datum": "2026-08-14",
                    "omschrijving": "Bon", "type": "bon", "aantal": "1",
                    "prijs": "10", "regel_inkoop": str(inkoop_id)})
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]

    namen = [bon["naam"] for bon in facturen.bonnen_bij_factuur(db, factuur_id)]
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
    bijlage_id = db.execute("SELECT id FROM bijlagen").fetchone()[0]
    db.execute("UPDATE bijlagen SET meesturen=0 WHERE id=?", (bijlage_id,))
    db.commit()
    inkoop_id = db.execute(
        "SELECT inkoop_id FROM bijlagen WHERE id=?", (bijlage_id,)
    ).fetchone()[0]
    post("/nieuw", {"klant_naam": "Jan", "klant_email": "jan@example.com",
                    "datum": "2026-08-14", "omschrijving": "Bon", "type": "bon",
                    "aantal": "1", "prijs": "12", "regel_inkoop": str(inkoop_id)})
    # Opslaan mailt niet. De bon gaat mee zodra je de rekening zelf mailt.
    assert "extra" not in opgevangen
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    post(f"/factuur/{factuur_id}/verstuur")

    assert [naam for _, naam in opgevangen["extra"]] == ["bon.png"]
    assert "bonnetjes zitten erbij" in opgevangen["tekst"]


def _figuur(html, naam):
    """Het hele figure, inclusief de link. data-naam staat pas op de <a>."""
    marker = f'data-naam="{naam}"'
    kop, _staart = html.split(marker, 1)
    start = kop.rfind("<figure")
    return html[start:].split("</figure>", 1)[0]


def test_het_kruisje_vervangt_het_woord_weg(post, client, klus_id):
    """Onder de tegel stond een rode tekstlink. Het kruisje zit op het vak."""
    upload(post, klus_id, "scan.pdf", b"%PDF-1.4 test")
    upload(post, klus_id, "voeg.png")
    post(f"/klus/{klus_id}/notitie", {
        "tekst": "Voegen in de badkamer",
        "foto": (io.BytesIO(PNG), "voeg-notitie.png"),
    }, content_type="multipart/form-data")
    html = client.get(f"/klus/{klus_id}").data.decode()
    assert ">Weg<" not in html
    for naam in ("scan.pdf", "voeg.png", "voeg-notitie.png"):
        figuur = _figuur(html, naam)
        assert ">Weg<" not in figuur
        assert ">Verwijderen<" not in figuur
        assert f'aria-label="Bijlage {naam} verwijderen"' in figuur
        assert 'class="tegel-weg"' in figuur
        assert 'type="submit"' in figuur
        assert 'method="post"' in figuur
        assert "/verwijder" in figuur
        assert 'name="csrf_token"' in figuur
        assert f'data-bevestig="{naam} verwijderen?"' in figuur
        assert 'class="bestandsnaam"' in figuur
        # Buiten de link, anders opent verwijderen de foto.
        assert "<button" not in figuur.split("</a>", 1)[0]
        assert figuur.index("</a>") < figuur.index("tegel-weg")
    assert 'data-soort="pdf"' in _figuur(html, "scan.pdf")


def test_verwijderen_zonder_script_post_het_formulier(post, client, db, klus_id):
    """Zonder JavaScript is er geen vraag, maar de knop verstuurt wel."""
    upload(post, klus_id, "scan.pdf", b"%PDF-1.4 test")
    html = client.get(f"/klus/{klus_id}").data.decode()
    figuur = _figuur(html, "scan.pdf")
    assert 'class="tegel-weg"' in figuur
    bijlage_id = db.execute("SELECT id FROM bijlagen").fetchone()[0]
    assert f'action="/bijlage/{bijlage_id}/verwijder"' in figuur
    post(f"/bijlage/{bijlage_id}/verwijder")
    assert db.execute("SELECT COUNT(*) FROM bijlagen").fetchone()[0] == 0
    # De melding noemt de naam nog. De tegel zelf is weg.
    assert 'aria-label="Bijlage scan.pdf verwijderen"' not in client.get(
        f"/klus/{klus_id}").data.decode()


def test_het_kruisje_vraagt_toestemming_en_blijft_van_de_bon_af(post, client, klus_id):
    """Met script dezelfde vraag als vroeger. Het tikvlak raakt het rode kruis niet."""
    from browser import meetbrowser, playwright_verplicht

    upload(post, klus_id, "scan.pdf", b"%PDF-1.4 test")
    upload(post, klus_id, "voeg.png")
    post(f"/klus/{klus_id}/notitie", {
        "tekst": "Voegen in de badkamer",
        "foto": (io.BytesIO(PNG), "voeg-notitie.png"),
    }, content_type="multipart/form-data")
    html = client.get(f"/klus/{klus_id}").data.decode()
    sync_playwright = playwright_verplicht()
    with sync_playwright() as p:
        browser = meetbrowser(p)
        try:
            for breedte in (390, 320):
                pagina = browser.new_page(
                    viewport={"width": breedte, "height": 900},
                    device_scale_factor=1,
                )
                pagina.set_content(html)
                maten = pagina.evaluate("""() => {
                  function vak(el) {
                    const r = el.getBoundingClientRect();
                    return {l: r.left, r: r.right, t: r.top, b: r.bottom,
                            w: r.width, h: r.height};
                  }
                  function gat(a, b) {
                    const gx = Math.max(a.l - b.r, b.l - a.r);
                    const gy = Math.max(a.t - b.b, b.t - a.b);
                    if (gx < -0.5 && gy < -0.5) return -1;
                    if (gx < 0) return gy;
                    if (gy < 0) return gx;
                    return Math.hypot(gx, gy);
                  }
                  const uit = [];
                  document.querySelectorAll('.tegel-weg').forEach(knop => {
                    const rij = knop.closest('.invoerrij');
                    const bon = rij && rij.querySelector('.invoerrij-weg .weg');
                    const span = knop.querySelector('span');
                    const anker = knop.closest('.bijlage-vak').querySelector('a');
                    const naam = knop.closest('figure').querySelector('.bestandsnaam');
                    const stijl = getComputedStyle(naam);
                    knop.focus({focusVisible: true});
                    const ring = getComputedStyle(span);
                    uit.push({
                      label: knop.getAttribute('aria-label'),
                      knop: vak(knop),
                      rond: vak(span),
                      tegel: vak(knop.closest('.bijlage-vak')),
                      anker: vak(anker),
                      ankerHoog: getComputedStyle(anker).height,
                      gat: bon ? gat(vak(knop), vak(bon)) : null,
                      eenRegel: stijl.whiteSpace === 'nowrap'
                        && stijl.textOverflow === 'ellipsis',
                      focus: document.activeElement === knop
                        && ring.outlineStyle !== 'none',
                    });
                  });
                  return uit;
                }""")
                assert maten, breedte
                for stuk in maten:
                    assert stuk["knop"]["w"] >= 44 and stuk["knop"]["h"] >= 44, stuk
                    assert 24 <= stuk["rond"]["w"] <= 28, stuk
                    assert 24 <= stuk["rond"]["h"] <= 28, stuk
                    tegel, rond = stuk["tegel"], stuk["rond"]
                    assert rond["l"] >= tegel["l"] - 1 and rond["r"] <= tegel["r"] + 1, (
                        breedte, stuk)
                    assert rond["t"] >= tegel["t"] - 1 and rond["b"] <= tegel["b"] + 1, (
                        breedte, stuk)
                    assert stuk["gat"] is None or stuk["gat"] >= 8, (breedte, stuk)
                    assert stuk["eenRegel"]
                    assert stuk["focus"], stuk
                    assert stuk["label"].startswith("Bijlage ")
                    assert stuk["label"].endswith(" verwijderen")
                pagina.close()

            pagina = browser.new_page(viewport={"width": 390, "height": 900})
            pagina.set_content(html)
            pagina.evaluate("""() => {
              document.querySelectorAll('form.bijlage-weg').forEach(form => {
                form.addEventListener('submit', (e) => {
                  document.body.dataset.verstuurd = form.getAttribute('action');
                  e.preventDefault();
                });
              });
            }""")
            gezien = {}

            def bevestig(dialoog):
                gezien["tekst"] = dialoog.message
                dialoog.accept()

            pagina.on("dialog", bevestig)
            pagina.locator('button[aria-label="Bijlage scan.pdf verwijderen"]').click()
            assert gezien["tekst"] == "scan.pdf verwijderen?"
            assert pagina.evaluate("() => document.body.dataset.verstuurd").endswith(
                "/verwijder")
            assert pagina.locator("#lichtbak").get_attribute("hidden") is not None
        finally:
            browser.close()


def test_onbekende_bijlage_geeft_404(client, db):
    antwoord = client.get("/bijlage/9999")
    assert antwoord.status_code == 404
    assert antwoord.headers["X-Content-Type-Options"] == "nosniff"
    assert antwoord.headers["Cache-Control"] == "private, no-store"
