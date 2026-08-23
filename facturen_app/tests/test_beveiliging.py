"""Gaten die bij een doorloop van de app zijn gevonden, en die dicht moeten blijven.

Stuk voor stuk dingen die geen enkele knop in de app laat zien: je moet ze met de
hand nabootsen. Juist daarom staan ze hier — een volgende wijziging mag ze niet
per ongeluk weer openzetten.
"""
import pytest

from conftest import facturen, TEST_GEBRUIKER, TEST_WACHTWOORD

# Zoals Home Assistant het stuurt: de header én een afzender uit het interne netwerk.
ECHTE_ZIJBALK = {
    "HTTP_X_INGRESS_PATH": "/api/hassio_ingress/abc123",
    "REMOTE_ADDR": "172.30.32.2",
}
# Zoals een willekeurige bezoeker op poort 8099 het zou proberen: de header nagemaakt,
# maar vanaf zijn eigen adres in het gewone netwerk.
NAGEMAAKT = {
    "HTTP_X_INGRESS_PATH": "/api/hassio_ingress/abc123",
    "REMOTE_ADDR": "192.168.1.77",
}


def test_een_nagemaakte_ingress_header_geeft_geen_toegang(uitgelogde_client):
    """Zonder deze controle kwam iedereen op het netwerk zonder wachtwoord binnen:
    één header meesturen was genoeg om de hele app te lezen én te wijzigen."""
    for pad in ("/", "/klanten", "/instellingen"):
        antwoord = uitgelogde_client.get(pad, environ_overrides=NAGEMAAKT)
        assert antwoord.status_code == 302, pad
        assert "/inloggen" in antwoord.headers["Location"], pad


def test_de_echte_zijbalk_komt_er_nog_gewoon_in(uitgelogde_client):
    assert uitgelogde_client.get("/", environ_overrides=ECHTE_ZIJBALK).status_code == 200


def test_een_nagemaakte_header_mag_ook_niets_veranderen(uitgelogde_client, db):
    antwoord = uitgelogde_client.post("/klanten/nieuw",
                                      data={"csrf_token": "test-token", "naam": "Ingebroken"},
                                      environ_overrides=NAGEMAAKT)
    assert antwoord.status_code == 302
    assert "/inloggen" in antwoord.headers["Location"]
    assert db.execute("SELECT COUNT(*) FROM klanten").fetchone()[0] == 0


@pytest.mark.parametrize("afzender", ["", "geen-ip", "10.0.0.5", "172.30.34.1"])
def test_afzenders_buiten_het_netwerk_van_home_assistant_tellen_niet(uitgelogde_client,
                                                                     afzender):
    overrides = {"HTTP_X_INGRESS_PATH": "/", "REMOTE_ADDR": afzender}
    assert uitgelogde_client.get("/", environ_overrides=overrides).status_code == 302


def test_het_mailwachtwoord_gaat_niet_mee_terug_naar_de_browser(client, db):
    """Stond het in de waarde van het invoerveld, dan staat het in de bron van de
    pagina en in elke kopie die daarvan wordt bewaard."""
    db.execute("UPDATE settings SET smtp_pass='geheimwachtwoord123' WHERE id=1")
    db.commit()

    inhoud = client.get("/instellingen").data.decode()
    assert "geheimwachtwoord123" not in inhoud


def test_een_leeg_wachtwoordveld_houdt_het_opgeslagen_wachtwoord(post, db):
    db.execute("UPDATE settings SET smtp_pass='geheimwachtwoord123' WHERE id=1")
    db.commit()

    post("/instellingen", {"naam": "Jan Jansen", "smtp_pass": ""})
    assert db.execute("SELECT smtp_pass FROM settings").fetchone()[0] == "geheimwachtwoord123"


def test_een_ingevuld_wachtwoordveld_vervangt_het_wel(post, db):
    db.execute("UPDATE settings SET smtp_pass='oud' WHERE id=1")
    db.commit()

    post("/instellingen", {"naam": "Jan Jansen", "smtp_pass": "nieuw"})
    assert db.execute("SELECT smtp_pass FROM settings").fetchone()[0] == "nieuw"


def test_een_knop_stuurt_je_niet_naar_een_website_van_iemand_anders(post, maak_factuur):
    """De Referer komt van de browser en is dus door een ander te bepalen. Hem blind
    volgen maakte van elke knop een doorgeefluik naar buiten."""
    factuur_id = maak_factuur()
    antwoord = post(f"/factuur/{factuur_id}/vernieuw",
                    headers={"Referer": "https://kwaadaardig.example.com/gepakt"})
    assert antwoord.status_code == 302
    assert "kwaadaardig.example.com" not in antwoord.headers["Location"]


def test_terugkomen_op_je_eigen_pagina_werkt_nog_wel(post, maak_factuur):
    factuur_id = maak_factuur()
    antwoord = post(f"/factuur/{factuur_id}/vernieuw",
                    headers={"Referer": "http://localhost/klanten?q=jansen"})
    assert antwoord.headers["Location"].endswith("/klanten?q=jansen")


def test_de_prullenbak_zet_niets_terug_in_een_tabel_die_er_niet_hoort(post, db):
    """De tabelnaam komt uit JSON en gaat rechtstreeks in de query. Vandaag schrijft
    alleen de app die JSON, maar de lijst met toegestane tabellen zorgt dat het ook
    veilig blijft als daar ooit iets tussenkomt."""
    db.execute(
        """INSERT INTO prullenbak (omschrijving, inhoud, wanneer)
           VALUES ('Iets', '{"rijen": {"gebruikers": [{"naam": "insluiper",
                   "wachtwoord": "x", "aangemaakt": "2026-01-01"}]}, "bestanden": []}',
                   '2026-08-21T12:00:00')""")
    db.commit()
    prullenbak_id = db.execute("SELECT id FROM prullenbak").fetchone()[0]

    post(f"/prullenbak/{prullenbak_id}/terug")
    namen = [r[0] for r in db.execute("SELECT naam FROM gebruikers")]
    assert "insluiper" not in namen


def test_de_prullenbak_negeert_kolommen_die_niet_bestaan(post, db):
    """Anders klapt het terugzetten op een oude bewaarde rij na een wijziging in het
    schema, en ben je alsnog kwijt wat je terug wilde halen."""
    db.execute(
        """INSERT INTO prullenbak (omschrijving, inhoud, wanneer)
           VALUES ('Klant', '{"rijen": {"klanten": [{"id": 7, "naam": "Jan Jansen",
                   "verzonnen_kolom": "x"}]}, "bestanden": []}', '2026-08-21T12:00:00')""")
    db.commit()
    prullenbak_id = db.execute("SELECT id FROM prullenbak").fetchone()[0]

    post(f"/prullenbak/{prullenbak_id}/terug")
    assert db.execute("SELECT naam FROM klanten WHERE id=7").fetchone()[0] == "Jan Jansen"


def test_de_sessiecookie_gaat_niet_mee_vanaf_een_andere_site(uitgelogde_client):
    antwoord = uitgelogde_client.post("/inloggen", data={
        "csrf_token": "test-token", "naam": TEST_GEBRUIKER, "wachtwoord": TEST_WACHTWOORD,
    })
    cookie = antwoord.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_de_inlogpagina_toont_een_nette_melding_en_geen_ruwe_json(uitgelogde_client):
    """Het inlogscherm gebruikt een eigen sjabloon; dat liep achter en liet de JSON
    van de melding letterlijk zien."""
    uitgelogde_client.post("/inloggen", data={
        "csrf_token": "test-token", "naam": TEST_GEBRUIKER, "wachtwoord": "fout",
    })
    inhoud = uitgelogde_client.get("/inloggen").data.decode()
    assert "klopt niet" in inhoud
    assert 'class="melding fout"' in inhoud
    # Zo zag de ruwe JSON eruit toen hij nog letterlijk op het scherm stond.
    assert "&#34;knop&#34;" not in inhoud
    assert '"tekst":' not in inhoud
