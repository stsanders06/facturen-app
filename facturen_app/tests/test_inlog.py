"""Het account en de inlog op poort 8099."""
from conftest import facturen, TEST_GEBRUIKER, TEST_WACHTWOORD

# Zo doet een verzoek zich voor als komend uit de zijbalk van Home Assistant.
VIA_SIDEBAR = {"HTTP_X_INGRESS_PATH": "/api/hassio_ingress/abc123"}


def test_zonder_inlog_kom_je_op_het_inlogscherm(uitgelogde_client):
    antwoord = uitgelogde_client.get("/")
    assert antwoord.status_code == 302
    assert "/inloggen" in antwoord.headers["Location"]


def test_ingelogd_kom_je_gewoon_binnen(client):
    assert client.get("/").status_code == 200


def test_via_de_zijbalk_hoef_je_niet_in_te_loggen(uitgelogde_client):
    """Daar zit de login van Home Assistant al voor; twee keer inloggen is onzin."""
    assert uitgelogde_client.get("/", environ_overrides=VIA_SIDEBAR).status_code == 200


def test_inloggen_met_het_juiste_wachtwoord(uitgelogde_client):
    antwoord = uitgelogde_client.post("/inloggen", data={
        "csrf_token": "test-token", "naam": TEST_GEBRUIKER, "wachtwoord": TEST_WACHTWOORD,
    })
    assert antwoord.status_code == 302
    assert uitgelogde_client.get("/").status_code == 200


def test_inloggen_met_het_verkeerde_wachtwoord(uitgelogde_client):
    uitgelogde_client.post("/inloggen", data={
        "csrf_token": "test-token", "naam": TEST_GEBRUIKER, "wachtwoord": "fout",
    })
    assert uitgelogde_client.get("/").status_code == 302


def test_de_melding_verklapt_niet_wat_er_fout_was(uitgelogde_client):
    antwoord = uitgelogde_client.post("/inloggen", data={
        "csrf_token": "test-token", "naam": "bestaatniet", "wachtwoord": "fout",
    }, follow_redirects=True)
    assert "Gebruikersnaam of wachtwoord klopt niet" in antwoord.data.decode()


def test_hoofdletters_in_de_gebruikersnaam_maken_niet_uit(uitgelogde_client):
    uitgelogde_client.post("/inloggen", data={
        "csrf_token": "test-token", "naam": TEST_GEBRUIKER.upper(),
        "wachtwoord": TEST_WACHTWOORD,
    })
    assert uitgelogde_client.get("/").status_code == 200


def test_na_vijf_pogingen_gaat_de_deur_dicht(uitgelogde_client):
    for _ in range(facturen.MAX_POGINGEN):
        uitgelogde_client.post("/inloggen", data={
            "csrf_token": "test-token", "naam": TEST_GEBRUIKER, "wachtwoord": "fout",
        })

    # Ook met het júiste wachtwoord kom je er nu even niet in.
    uitgelogde_client.post("/inloggen", data={
        "csrf_token": "test-token", "naam": TEST_GEBRUIKER, "wachtwoord": TEST_WACHTWOORD,
    })
    assert uitgelogde_client.get("/").status_code == 302


def test_een_geslaagde_inlog_wist_de_teller(uitgelogde_client):
    uitgelogde_client.post("/inloggen", data={
        "csrf_token": "test-token", "naam": TEST_GEBRUIKER, "wachtwoord": "fout",
    })
    uitgelogde_client.post("/inloggen", data={
        "csrf_token": "test-token", "naam": TEST_GEBRUIKER, "wachtwoord": TEST_WACHTWOORD,
    })
    assert facturen.MISLUKTE_POGINGEN == {}


def test_je_komt_terug_op_de_pagina_waar_je_heen_wilde(uitgelogde_client):
    antwoord = uitgelogde_client.post("/inloggen", data={
        "csrf_token": "test-token", "naam": TEST_GEBRUIKER,
        "wachtwoord": TEST_WACHTWOORD, "verder": "/klanten",
    })
    assert antwoord.headers["Location"] == "/klanten"


def test_doorsturen_naar_een_andere_website_wordt_genegeerd(uitgelogde_client):
    """Anders kan een link je na het inloggen zomaar ergens anders naartoe sturen."""
    for adres in ["//kwaadaardig.example.com", "https://kwaadaardig.example.com"]:
        antwoord = uitgelogde_client.post("/inloggen", data={
            "csrf_token": "test-token", "naam": TEST_GEBRUIKER,
            "wachtwoord": TEST_WACHTWOORD, "verder": adres,
        })
        assert antwoord.headers["Location"] == "/"


def test_uitloggen(client):
    client.post("/uitloggen", data={"csrf_token": "test-token"})
    assert client.get("/").status_code == 302


def test_zonder_account_kom_je_op_het_instelscherm(zonder_account):
    antwoord = zonder_account.get("/")
    assert antwoord.status_code == 302
    assert "/instellen" in antwoord.headers["Location"]


def test_account_aanmaken(zonder_account, db):
    antwoord = zonder_account.post("/instellen", data={
        "csrf_token": "test-token", "naam": "stone",
        "wachtwoord": "een-goed-wachtwoord", "nogmaals": "een-goed-wachtwoord",
    })
    assert antwoord.status_code == 302
    assert db.execute("SELECT naam FROM gebruikers").fetchone()[0] == "stone"
    # En je bent meteen ingelogd.
    assert zonder_account.get("/").status_code == 200


def test_wachtwoord_moet_lang_genoeg_zijn(zonder_account, db):
    zonder_account.post("/instellen", data={
        "csrf_token": "test-token", "naam": "stone",
        "wachtwoord": "kort", "nogmaals": "kort",
    })
    assert db.execute("SELECT COUNT(*) FROM gebruikers").fetchone()[0] == 0


def test_de_twee_wachtwoorden_moeten_gelijk_zijn(zonder_account, db):
    zonder_account.post("/instellen", data={
        "csrf_token": "test-token", "naam": "stone",
        "wachtwoord": "een-goed-wachtwoord", "nogmaals": "iets-anders",
    })
    assert db.execute("SELECT COUNT(*) FROM gebruikers").fetchone()[0] == 0


def test_er_komt_geen_tweede_account_bij(client, db):
    """Het instelscherm is eenmalig; anders kan iemand er zelf een account bij zetten."""
    antwoord = client.post("/instellen", data={
        "csrf_token": "test-token", "naam": "indringer",
        "wachtwoord": "een-goed-wachtwoord", "nogmaals": "een-goed-wachtwoord",
    })
    assert antwoord.status_code == 302
    assert db.execute("SELECT COUNT(*) FROM gebruikers WHERE naam='indringer'").fetchone()[0] == 0


def test_het_wachtwoord_staat_niet_leesbaar_in_de_database(db):
    hash = db.execute("SELECT wachtwoord FROM gebruikers").fetchone()[0]
    assert TEST_WACHTWOORD not in hash
    assert len(hash) > 40


def test_wachtwoord_wijzigen(client, db):
    antwoord = client.post("/wachtwoord", data={
        "csrf_token": "test-token", "huidig": TEST_WACHTWOORD,
        "nieuw": "nog-een-goed-wachtwoord", "nogmaals": "nog-een-goed-wachtwoord",
    }, follow_redirects=True)
    assert "gewijzigd" in antwoord.data.decode()

    hash = db.execute("SELECT wachtwoord FROM gebruikers WHERE naam=?", (TEST_GEBRUIKER,)).fetchone()[0]
    assert facturen.check_password_hash(hash, "nog-een-goed-wachtwoord")

    # Terugzetten, want dit account wordt door alle andere tests gebruikt.
    db.execute("UPDATE gebruikers SET wachtwoord=? WHERE naam=?",
               (facturen.generate_password_hash(TEST_WACHTWOORD), TEST_GEBRUIKER))
    db.commit()


def test_wijzigen_lukt_niet_zonder_het_huidige_wachtwoord(client, db):
    client.post("/wachtwoord", data={
        "csrf_token": "test-token", "huidig": "fout",
        "nieuw": "nog-een-goed-wachtwoord", "nogmaals": "nog-een-goed-wachtwoord",
    })
    hash = db.execute("SELECT wachtwoord FROM gebruikers WHERE naam=?", (TEST_GEBRUIKER,)).fetchone()[0]
    assert facturen.check_password_hash(hash, TEST_WACHTWOORD)


def test_de_inlogpagina_is_zonder_inlog_te_bereiken(uitgelogde_client):
    assert uitgelogde_client.get("/inloggen").status_code == 200


def test_ingelogd_stuurt_de_inlogpagina_je_door(client):
    assert client.get("/inloggen").status_code == 302


def test_instellingen_toont_wie_er_is_ingelogd(client):
    inhoud = client.get("/instellingen").data.decode()
    assert TEST_GEBRUIKER in inhoud
    assert "Wachtwoord wijzigen" in inhoud


def test_ook_een_post_komt_er_niet_langs_zonder_inlog(uitgelogde_client, db, maak_factuur):
    factuur_id = maak_factuur()
    uitgelogde_client.post(f"/factuur/{factuur_id}/verwijder", data={"csrf_token": "test-token"})
    assert db.execute("SELECT COUNT(*) FROM facturen").fetchone()[0] == 1


def test_de_url_waar_je_heen_wilde_wordt_netjes_bewaard(uitgelogde_client):
    kaal = uitgelogde_client.get("/klanten")
    assert kaal.headers["Location"].endswith("verder=/klanten")

    met_zoekterm = uitgelogde_client.get("/?status=openstaand")
    assert "status%3Dopenstaand" in met_zoekterm.headers["Location"]
