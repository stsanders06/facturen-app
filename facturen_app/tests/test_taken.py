"""Takenlijst onder een notitie.

De tekst en de foto's van de notitie blijven zoals ze waren. De lijst hangt
ernaast, zodat je bij een klus kunt afvinken wat er nog moet gebeuren.
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
    schrijf_op(post, klus_id, tekst)
    return db.execute("SELECT id FROM notities").fetchone()[0]


def _taak(post, notitie_id, tekst):
    return post(
        f"/notitie/{notitie_id}/taak",
        {"tekst": tekst},
        follow_redirects=True,
    )


def _namen(db, notitie_id):
    return [
        rij["tekst"] for rij in db.execute(
            "SELECT tekst FROM taken WHERE notitie_id=? ORDER BY volgorde, id",
            (notitie_id,),
        )
    ]


def test_een_taak_komt_onder_de_notitie(post, db, klus_id, client):
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, notitie_id, "Kitrand snijden")
    _taak(post, notitie_id, "Tegels voegen")
    assert _namen(db, notitie_id) == ["Kitrand snijden", "Tegels voegen"]
    assert db.execute("SELECT tekst FROM notities").fetchone()[0] == "Te doen in de badkamer"
    pagina = client.get(f"/klus/{klus_id}").data.decode()
    assert "Kitrand snijden" in pagina
    assert "0 van 2 klaar" in pagina
    assert "Nog een taak" in pagina


def test_afvinken_blijft_in_de_lijst(post, db, klus_id, client):
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, notitie_id, "Kitrand snijden")
    taak_id = db.execute("SELECT id FROM taken").fetchone()[0]
    post(f"/notitie/{notitie_id}/taak/{taak_id}/vink", follow_redirects=True)
    assert db.execute("SELECT klaar FROM taken").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM taken").fetchone()[0] == 1
    pagina = client.get(f"/klus/{klus_id}").data.decode()
    assert "1 van 1 klaar" in pagina
    assert "is-klaar" in pagina
    post(f"/notitie/{notitie_id}/taak/{taak_id}/vink", follow_redirects=True)
    assert db.execute("SELECT klaar FROM taken").fetchone()[0] == 0


def test_een_tik_bewaart_meteen(client, post, db, klus_id):
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, notitie_id, "Tegels voegen")
    taak_id = db.execute("SELECT id FROM taken").fetchone()[0]
    antwoord = client.post(
        f"/notitie/{notitie_id}/taak/{taak_id}/vink",
        data={"csrf_token": "test-token"},
        headers={"Accept": "application/json"},
    )
    assert antwoord.status_code == 200
    assert antwoord.is_json
    gegevens = antwoord.get_json()
    assert gegevens["klaar"] == 1
    assert gegevens["voortgang"] == "1 van 1 klaar"
    assert gegevens["klus_voortgang"] == "1 van 1 klaar"


def test_de_tekst_van_een_taak_aanpassen(post, db, klus_id):
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, notitie_id, "Kit")
    taak_id = db.execute("SELECT id FROM taken").fetchone()[0]
    post(f"/notitie/{notitie_id}/taak/{taak_id}/tekst",
         {"tekst": "Kitrand bij de douche"}, follow_redirects=True)
    assert db.execute("SELECT tekst FROM taken").fetchone()[0] == "Kitrand bij de douche"


def test_een_lege_taak_komt_er_niet_bij(post, db, klus_id):
    notitie_id = _notitie(post, db, klus_id)
    antwoord = _taak(post, notitie_id, "   ")
    assert db.execute("SELECT COUNT(*) FROM taken").fetchone()[0] == 0
    assert "Schrijf eerst wat er gedaan moet worden" in antwoord.get_data(as_text=True)


def test_verwijderen_haalt_alleen_die_taak_weg(post, db, klus_id):
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, notitie_id, "Eerst")
    _taak(post, notitie_id, "Daarna")
    eerste = db.execute("SELECT id FROM taken ORDER BY volgorde").fetchone()[0]
    post(f"/notitie/{notitie_id}/taak/{eerste}/verwijder", follow_redirects=True)
    assert _namen(db, notitie_id) == ["Daarna"]
    assert db.execute("SELECT tekst FROM notities").fetchone()[0] == "Te doen in de badkamer"


def test_omhoog_en_omlaag(post, db, klus_id):
    notitie_id = _notitie(post, db, klus_id)
    for tekst in ("A", "B", "C"):
        _taak(post, notitie_id, tekst)
    c = db.execute("SELECT id FROM taken WHERE tekst='C'").fetchone()[0]
    post(f"/notitie/{notitie_id}/taak/{c}/verplaats",
         {"richting": "omhoog"}, follow_redirects=True)
    assert _namen(db, notitie_id) == ["A", "C", "B"]
    a = db.execute("SELECT id FROM taken WHERE tekst='A'").fetchone()[0]
    post(f"/notitie/{notitie_id}/taak/{a}/verplaats",
         {"richting": "omlaag"}, follow_redirects=True)
    assert _namen(db, notitie_id) == ["C", "A", "B"]
    post(f"/notitie/{notitie_id}/taak/{a}/verplaats",
         {"richting": "nergens"}, follow_redirects=True)
    assert _namen(db, notitie_id) == ["C", "A", "B"]


def test_zonder_csrf_komt_er_geen_taak_bij(client, post, db, klus_id):
    notitie_id = _notitie(post, db, klus_id)
    antwoord = client.post(f"/notitie/{notitie_id}/taak", data={"tekst": "Stiekem"})
    assert antwoord.status_code == 400
    assert db.execute("SELECT COUNT(*) FROM taken").fetchone()[0] == 0


def test_een_notitie_met_taken_mag_een_lege_tekst(post, db, klus_id):
    """De lijst is dan de inhoud. De tekst leegmaken mag de taken niet wissen."""
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, notitie_id, "Kitrand snijden")
    post(f"/notitie/{notitie_id}/bewerk", {"tekst": ""}, follow_redirects=True)
    assert db.execute("SELECT tekst FROM notities").fetchone()[0] == ""
    assert _namen(db, notitie_id) == ["Kitrand snijden"]


def test_verwijderen_van_de_notitie_neemt_de_taken_mee(post, db, klus_id):
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, notitie_id, "Kitrand snijden")
    post(f"/notitie/{notitie_id}/verwijder", follow_redirects=True)
    assert db.execute("SELECT COUNT(*) FROM taken").fetchone()[0] == 0
    prul = db.execute("SELECT inhoud FROM prullenbak").fetchone()[0]
    assert "Kitrand snijden" in prul
    assert "taken" in prul


def test_de_voortgang_staat_op_de_klus_en_bij_de_notitie(post, client, klus_id):
    schrijf_op(post, klus_id, "Badkamer")
    eerste = facturen.get_db().execute("SELECT id FROM notities").fetchone()[0]
    _taak(post, eerste, "Een")
    _taak(post, eerste, "Twee")
    taak = facturen.get_db().execute("SELECT id FROM taken").fetchone()[0]
    post(f"/notitie/{eerste}/taak/{taak}/vink", follow_redirects=True)
    schrijf_op(post, klus_id, "Keuken")
    tweede = facturen.get_db().execute(
        "SELECT id FROM notities ORDER BY id DESC"
    ).fetchone()[0]
    _taak(post, tweede, "Drie")
    pagina = client.get(f"/klus/{klus_id}").data.decode()
    assert pagina.count("1 van 2 klaar") >= 1
    assert "1 van 3 klaar" in pagina
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
    _taak(post, notitie_id, "Na de migratie")
    assert _namen(db, notitie_id) == ["Na de migratie"]


def test_een_foto_notitie_krijgt_ook_een_lijst(post, db, klus_id, client):
    schrijf_op(post, klus_id, tekst="", fotos=["situatie.png"])
    notitie_id = db.execute("SELECT id FROM notities").fetchone()[0]
    _taak(post, notitie_id, "Foto nalopen")
    pagina = client.get(f"/klus/{klus_id}").data.decode()
    assert "Foto nalopen" in pagina
    assert "situatie.png" in pagina
    assert "0 van 1 klaar" in pagina


def test_een_lange_taak_duwt_de_klus_niet_van_het_scherm(client, post, db, klus_id):
    """Op 320 px mag een taak zonder spatie de knoppen niet buiten beeld duwen."""
    from browser import meetbrowser, playwright_verplicht

    sync_playwright = playwright_verplicht()
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, notitie_id, "a" * 120)
    html = client.get(f"/klus/{klus_id}").data.decode()
    # De tekst staat één keer in het vak, niet nog eens in elk aria-label.
    assert html.count("a" * 120) == 1
    assert "min-height: 44px" in html

    with sync_playwright() as p:
        browser = meetbrowser(p)
        pagina = browser.new_page(device_scale_factor=1)
        try:
            pagina.set_viewport_size({"width": 320, "height": 800})
            pagina.set_content(html)
            gemeten = pagina.evaluate(
                """() => {
                  const doc = document.documentElement;
                  const vink = document.querySelector('.vink');
                  const knop = document.querySelector('.taak-knoppen .btn');
                  const veld = document.querySelector('.taak-tekst textarea');
                  const verberg = document.querySelector('.taken-verberg');
                  return {
                    scroll: doc.scrollWidth - doc.clientWidth,
                    vink: vink.getBoundingClientRect().height,
                    rechts: knop.getBoundingClientRect().right,
                    veld: veld.getBoundingClientRect().width,
                    verborgen: getComputedStyle(verberg).display,
                  };
                }"""
            )
            assert gemeten["scroll"] <= 1
            assert gemeten["vink"] >= 44
            assert gemeten["rechts"] <= 321
            assert gemeten["veld"] > 180
            assert gemeten["verborgen"] == "none"
        finally:
            browser.close()


def test_een_taak_mag_hooguit_vijfhonderd_tekens(post, db, klus_id):
    notitie_id = _notitie(post, db, klus_id)
    antwoord = _taak(post, notitie_id, "b" * 501)
    assert "500 tekens" in antwoord.get_data(as_text=True)
    assert db.execute("SELECT COUNT(*) FROM taken").fetchone()[0] == 0
    _taak(post, notitie_id, "b" * 500)
    assert db.execute("SELECT length(tekst) FROM taken").fetchone()[0] == 500


def test_een_notitie_mag_hooguit_tweehonderd_taken(post, db, klus_id, monkeypatch):
    monkeypatch.setattr(facturen, "TAAK_MAX_AANTAL", 2)
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, notitie_id, "Een")
    _taak(post, notitie_id, "Twee")
    antwoord = _taak(post, notitie_id, "Drie")
    assert "200 taken" not in antwoord.get_data(as_text=True)
    assert "hooguit 2 taken" in antwoord.get_data(as_text=True)
    assert db.execute("SELECT COUNT(*) FROM taken").fetchone()[0] == 2


def test_json_antwoord_laat_zich_niet_raden(client, post, db, klus_id):
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, notitie_id, "Kit")
    taak_id = db.execute("SELECT id FROM taken").fetchone()[0]
    antwoord = client.post(
        f"/notitie/{notitie_id}/taak/{taak_id}/vink",
        data={"csrf_token": "test-token"},
        headers={"Accept": "application/json"},
    )
    assert antwoord.headers["X-Content-Type-Options"] == "nosniff"


def test_cascade_wist_taken_als_de_notitie_weggaat(db, post, klus_id):
    """1.42.1 wist alleen de notitie. Zonder CASCADE bleef de database op slot."""
    notitie_id = _notitie(post, db, klus_id, "Bestaat al")
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
    db.execute(
        "INSERT INTO taken (notitie_id, tekst, klaar, volgorde) VALUES (?, 'Kit', 0, 0)",
        (notitie_id,),
    )
    db.commit()
    facturen.init_db()
    db.execute("DELETE FROM notities WHERE id=?", (notitie_id,))
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM taken").fetchone()[0] == 0


def _dialoog_pagina(client, klus_id):
    from browser import meetbrowser, playwright_verplicht
    html = client.get(f"/klus/{klus_id}").data.decode()
    html = html.replace("<head>", '<head><base href="http://facturen.test/">', 1)
    return playwright_verplicht(), meetbrowser, html


def test_annuleren_laat_de_taak_staan(client, post, db, klus_id):
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, notitie_id, "Kitrand snijden")
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
    _taak(post, notitie_id, "Kitrand snijden")
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
    _taak(post, notitie_id, "Kitrand snijden")
    taak_id = db.execute("SELECT id FROM taken").fetchone()[0]
    antwoord = client.post(
        f"/notitie/{notitie_id}/taak/{taak_id}/tekst",
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
    _taak(post, notitie_id, "Kit\r\nrand")
    assert db.execute("SELECT tekst FROM taken").fetchone()[0] == "Kit rand"
    taak_id = db.execute("SELECT id FROM taken").fetchone()[0]
    post(f"/notitie/{notitie_id}/taak/{taak_id}/tekst", {"tekst": "douche\nbak"})
    assert db.execute("SELECT tekst FROM taken").fetchone()[0] == "douche bak"


def test_zonder_script_heeft_de_taak_een_bewaarknop(client, post, db, klus_id):
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, notitie_id, "Kitrand snijden")
    html = client.get(f"/klus/{klus_id}").data.decode()
    stuk = html.split('class="taak-tekst"', 1)[1].split("</form>", 1)[0]
    assert 'type="submit"' in stuk
    assert "Bewaar" in stuk


def test_enter_bewaart_de_taak_zonder_nieuwe_regel(client, post, db, klus_id):
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, notitie_id, "Kit")
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
    _taak(post, notitie_id, "Kit")
    taak_id = db.execute("SELECT id FROM taken").fetchone()[0]
    geweigerd = client.post(
        f"/notitie/{notitie_id}/taak/{taak_id}/tekst",
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
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, notitie_id, "Kitrand snijden")
    taak_id = db.execute("SELECT id FROM taken").fetchone()[0]
    post(f"/notitie/{notitie_id}/verwijder", follow_redirects=True)
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
    nieuwe = _notitie(post, db, klus_id, "Nog een notitie")
    _taak(post, nieuwe, "Nieuwe taak")
    nieuw_id = db.execute("SELECT id FROM taken").fetchone()[0]
    assert nieuw_id != taak_id
    prul = db.execute("SELECT id FROM prullenbak ORDER BY id DESC").fetchone()[0]
    post(f"/prullenbak/{prul}/terug", follow_redirects=True)
    teksten = [rij[0] for rij in db.execute("SELECT tekst FROM taken")]
    assert "Kitrand snijden" in teksten
    assert "Nieuwe taak" in teksten


def test_een_bezet_taaknummer_komt_toch_terug(post, db, klus_id):
    notitie_id = _notitie(post, db, klus_id)
    _taak(post, notitie_id, "Kitrand snijden")
    taak_id = db.execute("SELECT id FROM taken").fetchone()[0]
    post(f"/notitie/{notitie_id}/verwijder", follow_redirects=True)
    andere = _notitie(post, db, klus_id, "Andere notitie")
    db.execute(
        """INSERT INTO taken (id, notitie_id, tekst, klaar, volgorde)
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
        _taak(post, notitie_id, "Gewoon een taak")
    assert "Traceback" not in caplog.text
