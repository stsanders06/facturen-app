"""Eén takenlijst per klus, onder de notities.

Tot 2.2.1 had elke notitie een eigen lijstje. Wat er nog moet gebeuren hoort
bij de klus, niet bij de aantekening waarin het toevallig is opgeschreven, dus
staat er nu één lijst onder de notities.
"""
import logging

import pytest

from conftest import facturen
from test_notities import schrijf_op


@pytest.fixture
def klus_id(post, db):
    post("/klussen/nieuw", {"naam": "Badkamer Voorbeeldstraat", "uurtarief": "45"})
    return db.execute("SELECT id FROM klussen").fetchone()[0]


def _notitie(post, db, klus_id, tekst="Te doen in de badkamer"):
    """Een notitie bij de klus. De takenlijst staat er los van, maar een paar
    tests kijken of de notitie zelf ongemoeid blijft."""
    schrijf_op(post, klus_id, tekst)
    return db.execute("SELECT id FROM notities").fetchone()[0]


def _taak(post, klus_id, tekst):
    return post(
        f"/klus/{klus_id}/taak",
        {"tekst": tekst},
        follow_redirects=True,
    )


def _namen(db, klus_id):
    return [
        rij["tekst"] for rij in db.execute(
            "SELECT tekst FROM taken WHERE klus_id=? ORDER BY volgorde, id",
            (klus_id,),
        )
    ]


def test_een_taak_komt_onder_de_notitie(post, db, klus_id, client):
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, klus_id, "Kitrand snijden")
    _taak(post, klus_id, "Tegels voegen")
    assert _namen(db, klus_id) == ["Kitrand snijden", "Tegels voegen"]
    assert db.execute("SELECT tekst FROM notities").fetchone()[0] == "Te doen in de badkamer"
    pagina = client.get(f"/klus/{klus_id}").data.decode()
    assert "Kitrand snijden" in pagina
    assert "0 van 2 klaar" in pagina
    assert "Nog een taak" in pagina


def test_afvinken_blijft_in_de_lijst(post, db, klus_id, client):
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, klus_id, "Kitrand snijden")
    taak_id = db.execute("SELECT id FROM taken").fetchone()[0]
    post(f"/klus/{klus_id}/taak/{taak_id}/vink", follow_redirects=True)
    assert db.execute("SELECT klaar FROM taken").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM taken").fetchone()[0] == 1
    pagina = client.get(f"/klus/{klus_id}").data.decode()
    assert "1 van 1 klaar" in pagina
    assert "is-klaar" in pagina
    post(f"/klus/{klus_id}/taak/{taak_id}/vink", follow_redirects=True)
    assert db.execute("SELECT klaar FROM taken").fetchone()[0] == 0


def test_een_tik_bewaart_meteen(client, post, db, klus_id):
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, klus_id, "Tegels voegen")
    taak_id = db.execute("SELECT id FROM taken").fetchone()[0]
    antwoord = client.post(
        f"/klus/{klus_id}/taak/{taak_id}/vink",
        data={"csrf_token": "test-token"},
        headers={"Accept": "application/json"},
    )
    assert antwoord.status_code == 200
    assert antwoord.is_json
    gegevens = antwoord.get_json()
    assert gegevens["klaar"] == 1
    assert gegevens["voortgang"] == "1 van 1 klaar"


def test_de_tekst_van_een_taak_aanpassen(post, db, klus_id):
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, klus_id, "Kit")
    taak_id = db.execute("SELECT id FROM taken").fetchone()[0]
    post(f"/klus/{klus_id}/taak/{taak_id}/tekst",
         {"tekst": "Kitrand bij de douche"}, follow_redirects=True)
    assert db.execute("SELECT tekst FROM taken").fetchone()[0] == "Kitrand bij de douche"


def test_een_lege_taak_komt_er_niet_bij(post, db, klus_id):
    notitie_id = _notitie(post, db, klus_id)
    antwoord = _taak(post, klus_id, "   ")
    assert db.execute("SELECT COUNT(*) FROM taken").fetchone()[0] == 0
    assert "Schrijf eerst wat er gedaan moet worden" in antwoord.get_data(as_text=True)


def test_verwijderen_haalt_alleen_die_taak_weg(post, db, klus_id):
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, klus_id, "Eerst")
    _taak(post, klus_id, "Daarna")
    eerste = db.execute("SELECT id FROM taken ORDER BY volgorde").fetchone()[0]
    post(f"/klus/{klus_id}/taak/{eerste}/verwijder", follow_redirects=True)
    assert _namen(db, klus_id) == ["Daarna"]
    assert db.execute("SELECT tekst FROM notities").fetchone()[0] == "Te doen in de badkamer"


def test_omhoog_en_omlaag(post, db, klus_id):
    notitie_id = _notitie(post, db, klus_id)
    for tekst in ("A", "B", "C"):
        _taak(post, klus_id, tekst)
    c = db.execute("SELECT id FROM taken WHERE tekst='C'").fetchone()[0]
    post(f"/klus/{klus_id}/taak/{c}/verplaats",
         {"richting": "omhoog"}, follow_redirects=True)
    assert _namen(db, klus_id) == ["A", "C", "B"]
    a = db.execute("SELECT id FROM taken WHERE tekst='A'").fetchone()[0]
    post(f"/klus/{klus_id}/taak/{a}/verplaats",
         {"richting": "omlaag"}, follow_redirects=True)
    assert _namen(db, klus_id) == ["C", "A", "B"]
    post(f"/klus/{klus_id}/taak/{a}/verplaats",
         {"richting": "nergens"}, follow_redirects=True)
    assert _namen(db, klus_id) == ["C", "A", "B"]


def test_zonder_csrf_komt_er_geen_taak_bij(client, post, db, klus_id):
    notitie_id = _notitie(post, db, klus_id)
    antwoord = client.post(f"/klus/{klus_id}/taak", data={"tekst": "Stiekem"})
    assert antwoord.status_code == 400
    assert db.execute("SELECT COUNT(*) FROM taken").fetchone()[0] == 0


def test_een_notitie_leegmaken_raakt_de_taken_niet(post, db, klus_id):
    """De taken staan los van de notitie; aan de notitie sleutelen laat ze staan.

    Een notitie zonder tekst en zonder foto's mag niet meer bestaan: vóór 2.2.1
    hielden de taken zo'n lege notitie overeind, en nu is daar geen reden meer
    voor."""
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, klus_id, "Kitrand snijden")
    post(f"/notitie/{notitie_id}/bewerk", {"tekst": ""}, follow_redirects=True)
    assert db.execute("SELECT tekst FROM notities").fetchone()[0] == "Te doen in de badkamer"
    assert _namen(db, klus_id) == ["Kitrand snijden"]


def test_verwijderen_van_de_notitie_laat_de_taken_staan(post, db, klus_id):
    """De taken horen bij de klus. Een aantekening weggooien hoort je lijstje
    niet mee te nemen; vóór 2.2.1 gebeurde dat wel."""
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, klus_id, "Kitrand snijden")
    post(f"/notitie/{notitie_id}/verwijder", follow_redirects=True)
    assert _namen(db, klus_id) == ["Kitrand snijden"]
    prul = db.execute("SELECT inhoud FROM prullenbak").fetchone()[0]
    assert "Kitrand snijden" not in prul


def test_verwijderen_van_de_klus_neemt_de_taken_wel_mee(post, db, klus_id):
    _taak(post, klus_id, "Kitrand snijden")
    post(f"/klus/{klus_id}/verwijder", follow_redirects=True)
    assert db.execute("SELECT COUNT(*) FROM taken").fetchone()[0] == 0
    prul = db.execute("SELECT inhoud FROM prullenbak").fetchone()[0]
    assert "Kitrand snijden" in prul


def test_de_voortgang_telt_de_hele_klus(post, client, klus_id):
    """Eén lijst, dus ook maar één teller. Vóór 2.2.1 stond er een teller per
    notitie én een voor de klus, en op een telefoon werd dat twee regels."""
    schrijf_op(post, klus_id, "Badkamer")
    _taak(post, klus_id, "Een")
    _taak(post, klus_id, "Twee")
    _taak(post, klus_id, "Drie")
    taak = facturen.get_db().execute("SELECT id FROM taken").fetchone()[0]
    post(f"/klus/{klus_id}/taak/{taak}/vink", follow_redirects=True)
    pagina = client.get(f"/klus/{klus_id}").data.decode()
    assert pagina.count("1 van 3 klaar") == 1
    assert 'id="taken-klus"' in pagina


def test_de_tabel_komt_erbij_zonder_notities_te_wissen(db, post, klus_id):
    """Een database van voor deze lijst heeft de tabel nog niet. De notities
    die er al staan mogen bij het bijzetten niet verdwijnen."""
    _notitie(post, db, klus_id, "Bestaande aantekening")
    db.execute("DROP TABLE taken")
    db.commit()
    facturen.init_db()
    assert db.execute(
        "SELECT tekst FROM notities"
    ).fetchone()[0] == "Bestaande aantekening"
    assert db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='taken'"
    ).fetchone()
    notitie_id = db.execute("SELECT id FROM notities").fetchone()[0]
    _taak(post, klus_id, "Na de migratie")
    assert _namen(db, klus_id) == ["Na de migratie"]


def test_een_foto_notitie_krijgt_ook_een_lijst(post, db, klus_id, client):
    schrijf_op(post, klus_id, tekst="", fotos=["situatie.png"])
    notitie_id = db.execute("SELECT id FROM notities").fetchone()[0]
    _taak(post, klus_id, "Foto nalopen")
    pagina = client.get(f"/klus/{klus_id}").data.decode()
    assert "Foto nalopen" in pagina
    assert "situatie.png" in pagina
    assert "0 van 1 klaar" in pagina


def _meet_taakrij(pagina, breedte):
    pagina.set_viewport_size({"width": breedte, "height": 800})
    return pagina.evaluate(
        """(breedte) => {
          document.querySelectorAll('.invoerkaart').forEach(function (kaart) {
            kaart.classList.add('is-toevoegen');
          });
          const doc = document.documentElement;
          const rij = document.querySelector('.taak');
          const vink = rij.querySelector('.vink');
          const veld = rij.querySelector('.taak-tekst textarea');
          const knoppen = rij.querySelector('.taak-knoppen');
          const knop = knoppen.querySelector('.btn');
          const nieuw = document.querySelector('.taak-nieuw input[name="tekst"]');
          const bewaar = rij.querySelector('.taak-bewaar');
          const ops = [...document.querySelectorAll('button')].find(
            (b) => b.textContent.trim() === 'Opschrijven'
          );
          const box = (el) => el.getBoundingClientRect();
          const midden = (el) => (box(el).top + box(el).bottom) / 2;
          return {
            scroll: doc.scrollWidth - doc.clientWidth,
            vink: box(vink).height,
            knop: box(knop).width,
            knopHoog: box(knop).height,
            rechts: Math.max(box(knoppen).right, box(veld).right),
            veld: box(veld).width,
            veldRechts: box(veld).right,
            knopRechts: box(knoppen).right,
            knopBoven: box(knoppen).top,
            veldOnder: box(veld).bottom,
            nieuwRechts: box(nieuw).right,
            opsRechts: box(ops).right,
            zelfdeRij: Math.abs(midden(vink) - midden(veld)),
            bewaar: getComputedStyle(bewaar).display,
            verborgen: getComputedStyle(document.querySelector('.taken-verberg')).display,
            breedte,
          };
        }""",
        breedte,
    )


def test_een_lange_taak_duwt_de_klus_niet_van_het_scherm(client, post, db, klus_id):
    """Op een telefoon blijft een taak één rij en binnen de kaart."""
    from browser import meetbrowser, playwright_verplicht

    sync_playwright = playwright_verplicht()
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, klus_id, "a" * 120)
    html = client.get(f"/klus/{klus_id}").data.decode()
    # De naam van de knop noemt de taak, anders klinken ze allemaal hetzelfde.
    assert f"Afvinken: {'a' * 120}" in html
    assert f"Omhoog: {'a' * 120}" in html
    assert f"Taak verwijderen: {'a' * 120}" in html
    # Eén teller, in de kop van de kaart.
    assert html.count("0 van 1 klaar") == 1
    assert "min-height: 44px" in html
    assert 'class="btn btn-text taak-bewaar"' in html

    with sync_playwright() as p:
        browser = meetbrowser(p)
        pagina = browser.new_page(device_scale_factor=1)
        try:
            pagina.set_content(html)
            for breedte in (320, 390):
                gemeten = _meet_taakrij(pagina, breedte)
                assert gemeten["scroll"] <= 1, gemeten
                assert gemeten["vink"] >= 44, gemeten
                assert gemeten["veld"] > 180, gemeten
                assert gemeten["knop"] >= 44 and gemeten["knopHoog"] >= 44, gemeten
                assert gemeten["rechts"] <= breedte + 1, gemeten
                assert gemeten["zelfdeRij"] < 8, gemeten
                assert gemeten["knopBoven"] >= gemeten["veldOnder"] - 2, gemeten
                assert abs(gemeten["knopRechts"] - gemeten["veldRechts"]) <= 2, gemeten
                assert abs(gemeten["nieuwRechts"] - gemeten["opsRechts"]) <= 2, gemeten
                assert gemeten["bewaar"] == "none", gemeten
                assert gemeten["verborgen"] == "none", gemeten
        finally:
            browser.close()


def _afstand(a, b):
    """Kleinste opening tussen twee rechthoeken. 0 als ze elkaar raken."""
    horizontaal = max(0, max(a["left"], b["left"]) - min(a["right"], b["right"]))
    verticaal = max(0, max(a["top"], b["top"]) - min(a["bottom"], b["bottom"]))
    if horizontaal and verticaal:
        return (horizontaal ** 2 + verticaal ** 2) ** 0.5
    return horizontaal or verticaal


def test_het_wegkruis_van_de_taak_raakt_de_notitie_niet(client, post, db, klus_id):
    """Bij een fotonotitie stond het prullenbakje in de kolom van het kruis."""
    from browser import meetbrowser, playwright_verplicht

    sync_playwright = playwright_verplicht()
    schrijf_op(post, klus_id, tekst="", fotos=["situatie.png"])
    notitie_id = db.execute("SELECT id FROM notities").fetchone()[0]
    _taak(post, klus_id, "Foto nalopen")
    html = client.get(f"/klus/{klus_id}").data.decode()
    with sync_playwright() as p:
        browser = meetbrowser(p)
        pagina = browser.new_page(device_scale_factor=1)
        try:
            pagina.set_content(html)
            for breedte in (320, 390):
                pagina.set_viewport_size({"width": breedte, "height": 800})
                dozen = pagina.evaluate(
                    """() => {
                      const vak = (el) => {
                        const b = el.getBoundingClientRect();
                        return {left: b.left, right: b.right, top: b.top, bottom: b.bottom};
                      };
                      return {
                        notitie: vak(document.querySelector('.invoerrij-weg .weg')),
                        taak: vak(document.querySelector('.taak-prullen')),
                      };
                    }"""
                )
                assert _afstand(dozen["notitie"], dozen["taak"]) >= 8, (breedte, dozen)
        finally:
            browser.close()


def test_zonder_script_blijft_de_tekst_leesbaar(client, post, db, klus_id):
    from browser import meetbrowser, playwright_verplicht

    sync_playwright = playwright_verplicht()
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, klus_id, "Kitrand langs de douchewand")
    html = client.get(f"/klus/{klus_id}").data.decode()
    with sync_playwright() as p:
        browser = meetbrowser(p)
        pagina = browser.new_page(device_scale_factor=1)
        try:
            pagina.set_viewport_size({"width": 390, "height": 800})
            pagina.set_content(html)
            gemeten = pagina.evaluate(
                """() => {
                  document.documentElement.className = '';
                  const veld = document.querySelector('.taak-tekst textarea');
                  veld.style.height = '';
                  const stijl = getComputedStyle(veld);
                  return {
                    hoogte: veld.getBoundingClientRect().height,
                    overloop: stijl.overflowY,
                  };
                }"""
            )
            assert gemeten["hoogte"] > 80, gemeten
            assert gemeten["overloop"] == "auto", gemeten
        finally:
            browser.close()


def test_een_taak_mag_hooguit_vijfhonderd_tekens(post, db, klus_id):
    notitie_id = _notitie(post, db, klus_id)
    antwoord = _taak(post, klus_id, "b" * 501)
    assert "500 tekens" in antwoord.get_data(as_text=True)
    assert db.execute("SELECT COUNT(*) FROM taken").fetchone()[0] == 0
    _taak(post, klus_id, "b" * 500)
    assert db.execute("SELECT length(tekst) FROM taken").fetchone()[0] == 500


def test_een_notitie_mag_hooguit_tweehonderd_taken(post, db, klus_id, monkeypatch):
    monkeypatch.setattr(facturen, "TAAK_MAX_AANTAL", 2)
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, klus_id, "Een")
    _taak(post, klus_id, "Twee")
    antwoord = _taak(post, klus_id, "Drie")
    assert "200 taken" not in antwoord.get_data(as_text=True)
    assert "hooguit 2 taken" in antwoord.get_data(as_text=True)
    assert db.execute("SELECT COUNT(*) FROM taken").fetchone()[0] == 2


def test_json_antwoord_laat_zich_niet_raden(client, post, db, klus_id):
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, klus_id, "Kit")
    taak_id = db.execute("SELECT id FROM taken").fetchone()[0]
    antwoord = client.post(
        f"/klus/{klus_id}/taak/{taak_id}/vink",
        data={"csrf_token": "test-token"},
        headers={"Accept": "application/json"},
    )
    assert antwoord.headers["X-Content-Type-Options"] == "nosniff"


def test_cascade_wist_taken_als_de_klus_weggaat(db, post, klus_id):
    """Een klus verwijderen hoort de database niet op slot te zetten."""
    _taak(post, klus_id, "Kit")
    db.execute("DELETE FROM klussen WHERE id=?", (klus_id,))
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM taken").fetchone()[0] == 0


def test_oude_taken_verhuizen_naar_hun_klus(db, post, klus_id):
    """Een database van vóór 2.2.1 heeft taken per notitie. Ze horen in één
    lijst per klus te belanden, op volgorde van de notitie waar ze bij stonden,
    en afgevinkte taken gaan mee."""
    eerste = _notitie(post, db, klus_id, "Eerste aantekening")
    schrijf_op(post, klus_id, "Tweede aantekening")
    tweede = db.execute(
        "SELECT id FROM notities ORDER BY id DESC").fetchone()[0]
    db.execute("DROP TABLE taken")
    db.execute(
        """CREATE TABLE taken (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notitie_id INTEGER NOT NULL,
            tekst TEXT NOT NULL,
            klaar INTEGER NOT NULL DEFAULT 0,
            volgorde INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (notitie_id) REFERENCES notities (id) ON DELETE CASCADE
        )"""
    )
    for notitie_id, tekst, klaar, volgorde in (
        (eerste, "Kit snijden", 1, 0),
        (eerste, "Voegen", 0, 1),
        (tweede, "Plint zagen", 0, 0),
    ):
        db.execute(
            "INSERT INTO taken (notitie_id, tekst, klaar, volgorde) VALUES (?, ?, ?, ?)",
            (notitie_id, tekst, klaar, volgorde),
        )
    db.commit()
    facturen.init_db()
    assert _namen(db, klus_id) == ["Kit snijden", "Voegen", "Plint zagen"]
    # Wat af was blijft af.
    klaar = db.execute(
        "SELECT klaar FROM taken WHERE tekst='Kit snijden'").fetchone()[0]
    assert klaar == 1
    # En de volgorde begint bij nul en loopt door over de notities heen.
    volgordes = [rij["volgorde"] for rij in db.execute(
        "SELECT volgorde FROM taken ORDER BY volgorde")]
    assert volgordes == [0, 1, 2]


def _dialoog_pagina(client, klus_id):
    from browser import meetbrowser, playwright_verplicht
    html = client.get(f"/klus/{klus_id}").data.decode()
    html = html.replace("<head>", '<head><base href="http://facturen.test/">', 1)
    return playwright_verplicht(), meetbrowser, html


def test_annuleren_laat_de_taak_staan(client, post, db, klus_id):
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, klus_id, "Kitrand snijden")
    sync_playwright, meetbrowser, html = _dialoog_pagina(client, klus_id)
    vragen = []
    with sync_playwright() as p:
        browser = meetbrowser(p)
        pagina = browser.new_page()
        def bij_dialoog(dialoog):
            vragen.append(dialoog.message)
            dialoog.dismiss()
        pagina.on("dialog", bij_dialoog)
        try:
            pagina.set_content(html)
            pagina.locator(".taak-weg button").click()
            assert pagina.locator(".taak").count() == 1
            assert len(vragen) == 1
        finally:
            browser.close()
    assert db.execute("SELECT COUNT(*) FROM taken").fetchone()[0] == 1


def _formuliervelden(ruw):
    """FormData is multipart, geen querystring. parse_qs knipt op het =-teken."""
    import re
    return dict(re.findall(r'name="([^"]+)"\r\n\r\n([^\r\n]*)', ruw or ""))


def test_bevestigen_verwijdert_de_taak(client, post, db, klus_id):
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, klus_id, "Kitrand snijden")
    sync_playwright, meetbrowser, html = _dialoog_pagina(client, klus_id)
    # De browser mag de testclient niet zelf aanroepen: die deelt de
    # verzoekcontext niet. We vangen het formulier en sturen het daarna
    # alsnog, zodat de database hetzelfde verzoek krijgt.
    gevangen = {}
    vragen = []

    def afhandelen(route):
        from urllib.parse import urlsplit
        gevangen["pad"] = urlsplit(route.request.url).path
        gevangen["velden"] = _formuliervelden(route.request.post_data)
        route.fulfill(
            status=200,
            body=(
                '{"weg": true, "gedaan": 0, "totaal": 0, "voortgang": "", '
                '"klus_gedaan": 0, "klus_totaal": 0, "klus_voortgang": ""}'
            ),
            content_type="application/json",
        )

    with sync_playwright() as p:
        browser = meetbrowser(p)
        pagina = browser.new_page()
        pagina.route("http://facturen.test/**", afhandelen)

        def bij_dialoog(dialoog):
            vragen.append(dialoog.message)
            dialoog.accept()

        pagina.on("dialog", bij_dialoog)
        try:
            pagina.set_content(html)
            pagina.locator(".taak-weg button").click()
            # De klik keert terug zodra het verzoek vertrekt. De rij gaat
            # pas weg als het JSON-antwoord binnen is.
            pagina.locator(".taak").wait_for(state="detached", timeout=3000)
            assert pagina.locator(".taak").count() == 0
            assert vragen == ["Deze taak verwijderen?"]
        finally:
            browser.close()
    assert gevangen["pad"].endswith("/verwijder")
    assert gevangen["velden"]["csrf_token"] == "test-token"
    antwoord = client.post(
        gevangen["pad"],
        data=gevangen["velden"],
        headers={"Accept": "application/json"},
    )
    assert antwoord.status_code == 200
    assert db.execute("SELECT COUNT(*) FROM taken").fetchone()[0] == 0


def test_leegmaken_vraagt_of_de_taak_weg_mag(client, post, db, klus_id):
    """Een leeg vak stil laten mislukken leek alsof de tekst weg was."""
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, klus_id, "Kitrand snijden")
    taak_id = db.execute("SELECT id FROM taken").fetchone()[0]
    antwoord = client.post(
        f"/klus/{klus_id}/taak/{taak_id}/tekst",
        data={"csrf_token": "test-token", "tekst": "   "},
        headers={"Accept": "application/json"},
    )
    assert antwoord.status_code == 400
    assert "tekst nodig" in antwoord.get_json()["fout"]
    assert antwoord.headers["X-Content-Type-Options"] == "nosniff"
    assert db.execute("SELECT tekst FROM taken").fetchone()[0] == "Kitrand snijden"

    sync_playwright, meetbrowser, html = _dialoog_pagina(client, klus_id)
    vragen = []
    with sync_playwright() as p:
        browser = meetbrowser(p)
        pagina = browser.new_page()

        def bij_dialoog(dialoog):
            vragen.append(dialoog.message)
            dialoog.dismiss()

        pagina.on("dialog", bij_dialoog)
        try:
            pagina.set_content(html)
            veld = pagina.locator(".taak-tekst textarea")
            veld.fill("")
            veld.blur()
            assert veld.input_value() == "Kitrand snijden"
            assert vragen == ["Deze taak verwijderen?"]
            assert pagina.locator(".taak").count() == 1
        finally:
            browser.close()
    assert db.execute("SELECT COUNT(*) FROM taken").fetchone()[0] == 1


def test_een_regeleinde_in_een_taak_wordt_een_spatie(post, db, klus_id):
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, klus_id, "Kit\r\nrand")
    assert db.execute("SELECT tekst FROM taken").fetchone()[0] == "Kit rand"
    taak_id = db.execute("SELECT id FROM taken").fetchone()[0]
    post(f"/klus/{klus_id}/taak/{taak_id}/tekst", {"tekst": "douche\nbak"})
    assert db.execute("SELECT tekst FROM taken").fetchone()[0] == "douche bak"


def test_zonder_script_heeft_de_taak_een_bewaarknop(client, post, db, klus_id):
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, klus_id, "Kitrand snijden")
    html = client.get(f"/klus/{klus_id}").data.decode()
    stuk = html.split('class="taak-tekst"', 1)[1].split("</form>", 1)[0]
    assert 'type="submit"' in stuk
    assert "Bewaar" in stuk


def test_enter_bewaart_de_taak_zonder_nieuwe_regel(client, post, db, klus_id):
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, klus_id, "Kit")
    sync_playwright, meetbrowser, html = _dialoog_pagina(client, klus_id)
    gevangen = {}

    def afhandelen(route):
        from urllib.parse import urlsplit
        if "/tekst" not in urlsplit(route.request.url).path:
            route.fallback()
            return
        gevangen["velden"] = _formuliervelden(route.request.post_data)
        route.fulfill(
            status=200,
            body=(
                '{"id": 1, "tekst": "Kitrand", "gedaan": 0, "totaal": 1, '
                '"voortgang": "", "klus_gedaan": 0, "klus_totaal": 1, '
                '"klus_voortgang": ""}'
            ),
            content_type="application/json",
        )

    with sync_playwright() as p:
        browser = meetbrowser(p)
        pagina = browser.new_page()
        pagina.route("http://facturen.test/**", afhandelen)
        try:
            pagina.set_content(html)
            veld = pagina.locator(".taak-tekst textarea")
            veld.fill("Kitrand")
            veld.press("Enter")
            pagina.wait_for_function(
                "() => document.querySelector('.taak-tekst textarea').defaultValue === 'Kitrand'"
            )
            assert "\n" not in veld.input_value()
        finally:
            browser.close()
    assert gevangen["velden"]["tekst"] == "Kitrand"
    assert db.execute("SELECT tekst FROM taken").fetchone()[0] == "Kit"


def test_een_net_toegevoegde_lange_taak_groeit_meteen(client, post, db, klus_id):
    """scrollHeight is 0 zolang de regel nog niet op de pagina staat."""
    _notitie(post, db, klus_id)
    sync_playwright, meetbrowser, html = _dialoog_pagina(client, klus_id)
    lange = "a" * 120

    def afhandelen(route):
        from urllib.parse import urlsplit
        if not urlsplit(route.request.url).path.rstrip("/").endswith("/taak"):
            route.fallback()
            return
        import json
        route.fulfill(
            status=200,
            body=json.dumps({
                "id": 50, "tekst": lange, "klaar": 0,
                "gedaan": 0, "totaal": 1, "voortgang": "0 van 1 klaar",
                "klus_gedaan": 0, "klus_totaal": 1, "klus_voortgang": "0 van 1 klaar",
            }),
            content_type="application/json",
        )

    with sync_playwright() as p:
        browser = meetbrowser(p)
        pagina = browser.new_page()
        pagina.set_viewport_size({"width": 320, "height": 800})
        pagina.route("http://facturen.test/**", afhandelen)
        try:
            pagina.set_content(html)
            nieuw = pagina.locator(".taak-nieuw input[name=tekst]")
            nieuw.fill(lange)
            nieuw.press("Enter")
            pagina.locator(".taak-tekst textarea").wait_for()
            gemeten = pagina.evaluate(
                """() => {
                  const veld = document.querySelector('.taak-tekst textarea');
                  return {
                    client: veld.clientHeight,
                    scroll: veld.scrollHeight,
                  };
                }"""
            )
            assert gemeten["client"] > 60
            assert gemeten["scroll"] <= gemeten["client"] + 4
        finally:
            browser.close()


def test_een_te_lange_aanpassing_springt_terug(client, post, db, klus_id):
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, klus_id, "Kit")
    taak_id = db.execute("SELECT id FROM taken").fetchone()[0]
    geweigerd = client.post(
        f"/klus/{klus_id}/taak/{taak_id}/tekst",
        data={"csrf_token": "test-token", "tekst": "b" * 501},
        headers={"Accept": "application/json"},
    )
    assert geweigerd.status_code == 400
    assert "500 tekens" in geweigerd.get_json()["fout"]
    assert db.execute("SELECT tekst FROM taken").fetchone()[0] == "Kit"

    sync_playwright, meetbrowser, html = _dialoog_pagina(client, klus_id)
    meldingen = []

    def afhandelen(route):
        from urllib.parse import urlsplit
        if "/tekst" not in urlsplit(route.request.url).path:
            route.fallback()
            return
        route.fulfill(
            status=400,
            body='{"fout": "Een taak mag hooguit 500 tekens zijn."}',
            content_type="application/json",
        )

    with sync_playwright() as p:
        browser = meetbrowser(p)
        pagina = browser.new_page()
        pagina.route("http://facturen.test/**", afhandelen)
        pagina.on("dialog", lambda dialoog: meldingen.append(dialoog.message) or dialoog.accept())
        try:
            pagina.set_content(html)
            veld = pagina.locator(".taak-tekst textarea")
            # blur() doet niets als het vak geen focus heeft, en dan gaat
            # de te lange tekst ook niet terug.
            veld.focus()
            veld.evaluate(
                """el => {
                  el.removeAttribute('maxlength');
                  el.value = 'x'.repeat(501);
                }"""
            )
            veld.blur()
            pagina.wait_for_function(
                "() => document.querySelector('.taak-tekst textarea').value === 'Kit'"
            )
        finally:
            browser.close()
    assert any("500 tekens" in melding for melding in meldingen)


def test_herbouw_geeft_geen_taaknummer_uit_de_prullenbak(db, post, klus_id):
    """De teller van een nagebouwde tabel begon weer bij de gekopieerde rijen."""
    _taak(post, klus_id, "Kitrand snijden")
    taak_id = db.execute("SELECT id FROM taken").fetchone()[0]
    post(f"/klus/{klus_id}/verwijder", follow_redirects=True)
    db.execute("DROP TABLE taken")
    db.execute(
        """CREATE TABLE taken (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notitie_id INTEGER NOT NULL,
            tekst TEXT NOT NULL,
            klaar INTEGER NOT NULL DEFAULT 0,
            volgorde INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (notitie_id) REFERENCES notities (id)
        )"""
    )
    db.execute("DELETE FROM sqlite_sequence WHERE name='taken'")
    db.commit()
    facturen.init_db()
    seq = db.execute(
        "SELECT seq FROM sqlite_sequence WHERE name='taken'"
    ).fetchone()
    assert seq is not None and seq[0] >= taak_id
    post("/klussen/nieuw", {"naam": "Keuken", "uurtarief": "45"})
    nieuwe_klus = db.execute(
        "SELECT id FROM klussen ORDER BY id DESC").fetchone()[0]
    _taak(post, nieuwe_klus, "Nieuwe taak")
    nieuw_id = db.execute("SELECT id FROM taken").fetchone()[0]
    assert nieuw_id != taak_id
    prul = db.execute("SELECT id FROM prullenbak ORDER BY id DESC").fetchone()[0]
    post(f"/prullenbak/{prul}/terug", follow_redirects=True)
    teksten = [rij[0] for rij in db.execute("SELECT tekst FROM taken")]
    assert "Kitrand snijden" in teksten
    assert "Nieuwe taak" in teksten


def test_een_bezet_taaknummer_komt_toch_terug(post, db, klus_id):
    _taak(post, klus_id, "Kitrand snijden")
    taak_id = db.execute("SELECT id FROM taken").fetchone()[0]
    post(f"/klus/{klus_id}/verwijder", follow_redirects=True)
    post("/klussen/nieuw", {"naam": "Keuken", "uurtarief": "45"})
    andere = db.execute("SELECT id FROM klussen ORDER BY id DESC").fetchone()[0]
    db.execute(
        """INSERT INTO taken (id, klus_id, tekst, klaar, volgorde)
           VALUES (?, ?, 'Andere taak', 0, 0)""",
        (taak_id, andere),
    )
    db.commit()
    prul = db.execute("SELECT id FROM prullenbak ORDER BY id DESC").fetchone()[0]
    post(f"/prullenbak/{prul}/terug", follow_redirects=True)
    teksten = [rij[0] for rij in db.execute("SELECT tekst FROM taken")]
    assert "Kitrand snijden" in teksten
    assert "Andere taak" in teksten


def test_het_log_van_een_taak_staat_niet_in_de_melding(caplog, post, klus_id, db):
    """Een mislukte tik mag geen ruwe fout zijn. Dit houdt de logger stil."""
    notitie_id = _notitie(post, db, klus_id)
    with caplog.at_level(logging.ERROR):
        _taak(post, klus_id, "Gewoon een taak")
    assert "Traceback" not in caplog.text
