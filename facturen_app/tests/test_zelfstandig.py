"""De app als losse container (docker compose), zonder Home Assistant.

Zonder ZELFSTANDIG moet alles precies blijven zoals de add-on het altijd deed;
daarom test elk geval hier ook de add-on-kant."""
import os
import pathlib
import re
import sqlite3
import subprocess
import sys

import pytest
import yaml
from werkzeug.security import check_password_hash, generate_password_hash

from conftest import APP_DIR, facturen, TEST_GEBRUIKER, TEST_HASH, TEST_WACHTWOORD

import wachtwoord_herstellen

ADDON_DIR = APP_DIR.parent
REPO = ADDON_DIR.parent

VIA_SIDEBAR = {
    "HTTP_X_INGRESS_PATH": "/api/hassio_ingress/abc123",
    "REMOTE_ADDR": "172.30.32.2",
}


@pytest.fixture
def zelfstandig(app, monkeypatch):
    """Zet de app in de stand van Dockerfile.standalone, zonder opnieuw te importeren.

    Het importeren zelf staat in test_bij_het_opstarten_*; hier gaat het om wat
    de pagina's ermee doen."""
    monkeypatch.setattr(facturen, "ZELFSTANDIG", True)
    monkeypatch.setattr(facturen, "INGRESS_NETWERKEN", [])
    monkeypatch.setattr(facturen, "INLOG_PLEK", "")
    monkeypatch.setitem(facturen.app.jinja_env.globals, "zelfstandig", True)
    return app


def _zet_testwachtwoord_terug():
    """De app-fixture voegt het testaccount alleen toe als het er nog niet is; een
    gewijzigd wachtwoord zou dus in de volgende test blijven hangen."""
    conn = facturen.get_db()
    conn.execute("UPDATE gebruikers SET wachtwoord=? WHERE naam=?", (TEST_HASH, TEST_GEBRUIKER))
    conn.commit()
    conn.close()


def _opstarten(env_extra):
    """Importeer main.py in een los proces, zoals de container dat doet."""
    env = {k: v for k, v in os.environ.items()
           if k not in {"ZELFSTANDIG", "INGRESS_NETWERK"}}
    env.update(env_extra)
    code = ("import main; print(main.ZELFSTANDIG, [str(n) for n in main.INGRESS_NETWERKEN], "
            "main.app.jinja_env.globals['zelfstandig'])")
    uit = subprocess.run([sys.executable, "-c", code], cwd=APP_DIR, env=env,
                         capture_output=True, text=True, check=True)
    return uit.stdout.strip()


# --- Opstarten ---------------------------------------------------------------

def test_bij_het_opstarten_als_add_on_blijft_ingress_zoals_het_was():
    assert _opstarten({}) == "False ['172.30.32.0/23'] False"


def test_bij_het_opstarten_zelfstandig_staat_ingress_uit():
    assert _opstarten({"ZELFSTANDIG": "1"}) == "True [] True"


def test_bij_het_opstarten_zelfstandig_wint_van_ingress_netwerk_in_env():
    """Docker kan zijn eigen netwerk in 172.30.x leggen. Een INGRESS_NETWERK in
    .env mag de inlog dan nog steeds niet openzetten."""
    uit = _opstarten({"ZELFSTANDIG": "1", "INGRESS_NETWERK": "172.16.0.0/12"})
    assert uit == "True [] True"


# --- Inlog en Ingress --------------------------------------------------------

def test_add_on_zijbalk_komt_nog_steeds_zonder_inlog_binnen(uitgelogde_client):
    assert uitgelogde_client.get("/", environ_overrides=VIA_SIDEBAR).status_code == 200


def test_zelfstandig_helpt_een_ingress_header_niet_langs_de_inlog(zelfstandig):
    with zelfstandig.test_client() as c:
        antwoord = c.get("/", environ_overrides=VIA_SIDEBAR)
    assert antwoord.status_code == 302
    assert "/inloggen" in antwoord.headers["Location"]


def test_zelfstandig_zet_een_ingress_header_geen_pad_voor_de_links(zelfstandig, client):
    antwoord = client.get("/klanten", environ_overrides={
        "HTTP_X_INGRESS_PATH": "/api/hassio_ingress/verzonnen"})
    assert antwoord.status_code == 200
    assert "hassio_ingress" not in antwoord.data.decode()


def test_add_on_zet_het_ingress_pad_wel_voor_de_links(client):
    html = client.get("/klanten", environ_overrides=VIA_SIDEBAR).data.decode()
    assert "/api/hassio_ingress/abc123/" in html


def test_zelfstandig_toont_het_scanadres_van_de_container(zelfstandig):
    """Via de zijbalk is het adres niet te zien en blijft het veld leeg. Als losse
    container is er geen zijbalk, dus ook een verzonnen header levert het adres."""
    with zelfstandig.test_request_context("/instellingen", base_url="http://192.168.1.50:8099",
                                          environ_overrides=VIA_SIDEBAR):
        assert facturen.scan_upload_adres() == "http://192.168.1.50:8099/api/scans"


def test_add_on_zijbalk_laat_het_scanadres_leeg(app):
    with app.test_request_context("/instellingen", base_url="http://192.168.1.50:8099",
                                  environ_overrides=VIA_SIDEBAR):
        assert facturen.scan_upload_adres() == ""


# --- Teksten -----------------------------------------------------------------

def test_add_on_inlogscherm_noemt_de_zijbalk(zonder_account, uitgelogde_client):
    # zonder_account leegt de tabel; een account terug zodat /inloggen opent.
    conn = facturen.get_db()
    conn.execute("INSERT INTO gebruikers (naam, wachtwoord, aangemaakt) "
                 "VALUES (?, ?, '2026-01-01')", (TEST_GEBRUIKER, generate_password_hash("x" * 8)))
    conn.commit()
    conn.close()
    assert "zijbalk van Home Assistant" in uitgelogde_client.get("/inloggen").data.decode()


def test_zelfstandig_inlogscherm_noemt_home_assistant_niet(zelfstandig, uitgelogde_client):
    html = uitgelogde_client.get("/inloggen").data.decode()
    assert "Home Assistant" not in html
    assert "zijbalk" not in html


def test_add_on_instelscherm_noemt_poort_8099(zonder_account):
    html = zonder_account.get("/instellen").data.decode()
    assert "poort 8099" in html


def test_zelfstandig_instelscherm_zonder_poort_en_zijbalk(zelfstandig, zonder_account):
    html = zonder_account.get("/instellen").data.decode()
    assert "8099" not in html
    assert "Home Assistant" not in html
    assert "gebruikersnaam en een wachtwoord" in html


def test_zelfstandig_melding_na_account_maken_zonder_poort(zelfstandig, zonder_account):
    html = zonder_account.post("/instellen", data={
        "csrf_token": "test-token", "naam": TEST_GEBRUIKER, "wachtwoord": "lang-genoeg",
        "nogmaals": "lang-genoeg",
    }, follow_redirects=True).data.decode()
    _zet_testwachtwoord_terug()
    assert "Je account staat klaar. Vanaf nu log je hiermee in." in html


def test_add_on_melding_na_account_maken_noemt_de_poort(zonder_account):
    html = zonder_account.post("/instellen", data={
        "csrf_token": "test-token", "naam": TEST_GEBRUIKER, "wachtwoord": "lang-genoeg",
        "nogmaals": "lang-genoeg",
    }, follow_redirects=True).data.decode()
    _zet_testwachtwoord_terug()
    assert "Vanaf nu log je hiermee in op poort 8099." in html


def test_zelfstandig_instellingen_wijst_naar_wachtwoord_herstellen(zelfstandig, client):
    html = client.get("/instellingen").data.decode()
    assert "docker compose exec facturen wachtwoord-herstellen" in html
    assert "in de zijbalk" not in html


def test_add_on_instellingen_noemt_de_zijbalk(client):
    html = client.get("/instellingen").data.decode()
    assert "in de zijbalk" in html
    assert "wachtwoord-herstellen" not in html


def test_zelfstandig_melding_na_wachtwoord_wijzigen_zonder_poort(zelfstandig, post):
    html = post("/wachtwoord", data={
        "huidig": TEST_WACHTWOORD, "nieuw": "nog-een-geheim", "nogmaals": "nog-een-geheim",
    }, follow_redirects=True).data.decode()
    _zet_testwachtwoord_terug()
    assert "Je wachtwoord is gewijzigd. Vanaf nu log je hiermee in." in html
    assert "8099" not in html.split("Je wachtwoord is gewijzigd", 1)[1][:80]


# --- Wachtwoord herstellen ---------------------------------------------------

@pytest.fixture
def losse_db(tmp_path):
    pad = tmp_path / "facturen.db"
    conn = sqlite3.connect(pad)
    conn.execute("CREATE TABLE gebruikers (id INTEGER PRIMARY KEY, naam TEXT, "
                 "wachtwoord TEXT, aangemaakt TEXT)")
    conn.commit()
    conn.close()
    return pad


def _hash(pad):
    conn = sqlite3.connect(pad)
    try:
        return conn.execute("SELECT naam, wachtwoord FROM gebruikers").fetchall()
    finally:
        conn.close()


def test_herstellen_zet_een_nieuw_wachtwoord_en_noemt_de_naam(losse_db):
    conn = sqlite3.connect(losse_db)
    conn.execute("INSERT INTO gebruikers (naam, wachtwoord, aangemaakt) VALUES (?, ?, ?)",
                 ("testgebruiker", generate_password_hash("oud-wachtwoord"), "2026-01-01"))
    conn.commit()
    conn.close()
    assert wachtwoord_herstellen.herstel(str(losse_db), "nieuw-geheim") == "testgebruiker"
    [(naam, h)] = _hash(losse_db)
    assert check_password_hash(h, "nieuw-geheim")
    assert not check_password_hash(h, "oud-wachtwoord")


def test_herstellen_weigert_een_te_kort_wachtwoord(losse_db):
    with pytest.raises(ValueError, match="minstens 8"):
        wachtwoord_herstellen.herstel(str(losse_db), "kort")


def test_herstellen_zelfde_minimum_als_de_app():
    assert wachtwoord_herstellen.MINIMUM == facturen.WACHTWOORD_MINIMUM


def test_herstellen_zonder_account_verwijst_naar_de_browser(losse_db):
    with pytest.raises(ValueError, match="Open de app in je browser"):
        wachtwoord_herstellen.herstel(str(losse_db), "nieuw-geheim")
    assert _hash(losse_db) == []


def test_herstellen_zonder_database_verandert_niets(tmp_path):
    with pytest.raises(ValueError, match="geen administratie"):
        wachtwoord_herstellen.herstel(str(tmp_path / "facturen.db"), "nieuw-geheim")
    assert not (tmp_path / "facturen.db").exists()


def test_herstellen_via_de_opdracht(losse_db, monkeypatch, capsys):
    conn = sqlite3.connect(losse_db)
    conn.execute("INSERT INTO gebruikers (naam, wachtwoord, aangemaakt) VALUES (?, ?, ?)",
                 ("testgebruiker", generate_password_hash("oud-wachtwoord"), "2026-01-01"))
    conn.commit()
    conn.close()
    monkeypatch.setenv("DATA_DIR", str(losse_db.parent))
    antwoorden = iter(["nieuw-geheim", "nieuw-geheim"])
    monkeypatch.setattr(wachtwoord_herstellen.getpass, "getpass", lambda _: next(antwoorden))
    assert wachtwoord_herstellen.main() == 0
    assert "Log in als testgebruiker" in capsys.readouterr().out


def test_herstellen_via_de_opdracht_twee_keer_anders(losse_db, monkeypatch, capsys):
    monkeypatch.setenv("DATA_DIR", str(losse_db.parent))
    antwoorden = iter(["nieuw-geheim", "ander-geheim"])
    monkeypatch.setattr(wachtwoord_herstellen.getpass, "getpass", lambda _: next(antwoorden))
    assert wachtwoord_herstellen.main() == 1
    assert "niet hetzelfde" in capsys.readouterr().out


def test_herstellen_werkt_op_de_echte_database_van_de_app(app, uitgelogde_client):
    """Zelfde tabel als de app: na herstellen kom je met het nieuwe wachtwoord binnen."""
    naam = wachtwoord_herstellen.herstel(facturen.DB_PATH, "hersteld-wachtwoord")
    assert naam == TEST_GEBRUIKER
    uitgelogde_client.post("/inloggen", data={
        "csrf_token": "test-token", "naam": TEST_GEBRUIKER,
        "wachtwoord": "hersteld-wachtwoord"})
    assert uitgelogde_client.get("/").status_code == 200
    _zet_testwachtwoord_terug()


# --- De bestanden voor docker compose -----------------------------------------

def test_compose_bouwt_de_losse_dockerfile_met_data_map_en_herstart():
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text())
    dienst = compose["services"]["facturen"]
    assert dienst["build"] == {"context": "./facturen_app",
                               "dockerfile": "Dockerfile.standalone"}
    assert dienst["restart"] == "unless-stopped"
    assert "./data:/data" in dienst["volumes"]
    assert dienst["ports"] == ["${POORT:-8099}:8099"]
    assert dienst["environment"]["TZ"] == "${TZ:-Europe/Amsterdam}"
    assert dienst["mem_limit"]


def test_env_voorbeeld_heeft_alles_wat_compose_gebruikt():
    voorbeeld = (REPO / ".env.example").read_text()
    sleutels = set(re.findall(r"^([A-Z_]+)=", voorbeeld, re.M))
    gebruikt = set(re.findall(r"\$\{([A-Z_]+):-", (REPO / "docker-compose.yml").read_text()))
    assert gebruikt == sleutels
    assert "TZ=Europe/Amsterdam" in voorbeeld


def test_eigen_instellingen_en_administratie_komen_niet_in_git():
    regels = (REPO / ".gitignore").read_text().split()
    assert "/.env" in regels
    assert "/data/" in regels


def test_losse_dockerfile_zet_zelfstandig_en_de_data_map():
    tekst = (ADDON_DIR / "Dockerfile.standalone").read_text()
    assert tekst.startswith("#") and "\nFROM python:3.12-alpine3.20\n" in tekst
    assert "ZELFSTANDIG=1" in tekst
    assert "DATA_DIR=/data" in tekst
    assert "TZ=Europe/Amsterdam" in tekst
    # Zonder bashio: die bestaat alleen in het basisimage van Home Assistant.
    assert "with-contenv" not in tekst and "bashio::" not in tekst


def test_losse_dockerfile_installeert_wat_de_add_on_installeert():
    """Zelfde pakketten, anders draait de PDF of de miniatuur ergens anders anders."""
    def pakketten(pad):
        tekst = pad.read_text()
        blok = tekst.split("apk add --no-cache", 1)[1].split("&&", 1)[0]
        return set(blok.replace("\\", " ").split())
    los = pakketten(ADDON_DIR / "Dockerfile.standalone")
    assert pakketten(ADDON_DIR / "Dockerfile") <= los
    assert los - pakketten(ADDON_DIR / "Dockerfile") == {"su-exec"}


def test_add_on_zet_zelfstandig_nergens():
    """Met ZELFSTANDIG in de add-on zou de zijbalk ineens een inlog vragen."""
    for pad in [ADDON_DIR / "Dockerfile", ADDON_DIR / "config.yaml",
                ADDON_DIR / "rootfs/usr/bin/facturen-app"]:
        assert "ZELFSTANDIG" not in pad.read_text(), pad


def test_startscripts_zijn_uitvoerbaar_sh():
    for naam in ["start.sh", "wachtwoord-herstellen.sh"]:
        tekst = (ADDON_DIR / "zelfstandig" / naam).read_text()
        assert tekst.startswith("#!/bin/sh\n")
        assert "su-exec" in tekst


def test_versie_klopt_met_config_yaml():
    config = yaml.safe_load((ADDON_DIR / "config.yaml").read_text())
    tekst = (APP_DIR / "main.py").read_text()
    assert f'os.environ.get("ADDON_VERSION") or "{config["version"]}"' in tekst
