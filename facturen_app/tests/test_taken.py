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
    assert "a" * 120 in html
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
                  return {
                    scroll: doc.scrollWidth - doc.clientWidth,
                    vink: vink.getBoundingClientRect().height,
                    rechts: knop.getBoundingClientRect().right,
                  };
                }"""
            )
            assert gemeten["scroll"] <= 1
            assert gemeten["vink"] >= 44
            assert gemeten["rechts"] <= 321
        finally:
            browser.close()


def test_het_log_van_een_taak_staat_niet_in_de_melding(caplog, post, klus_id, db):
    """Een mislukte tik mag geen ruwe fout zijn. Dit houdt de logger stil."""
    notitie_id = _notitie(post, db, klus_id)
    with caplog.at_level(logging.ERROR):
        _taak(post, notitie_id, "Gewoon een taak")
    assert "Traceback" not in caplog.text
