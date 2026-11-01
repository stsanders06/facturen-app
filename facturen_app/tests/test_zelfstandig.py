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


def _los_proces(env_extra, code, check=True):
    """Draai code na een import van main.py in een los proces, zoals de container."""
    env = {k: v for k, v in os.environ.items()
           if k not in {"ZELFSTANDIG", "INGRESS_NETWERK", "SESSION_COOKIE_SECURE"}}
    env.update(env_extra)
    return subprocess.run([sys.executable, "-c", "import main\n" + code], cwd=APP_DIR,
                          env=env, capture_output=True, text=True, check=check)


def _opstarten(env_extra):
    code = ("print(main.ZELFSTANDIG, [str(n) for n in main.INGRESS_NETWERKEN], "
            "main.app.jinja_env.globals['zelfstandig'])")
    return _los_proces(env_extra, code).stdout.strip()


# Wat de container als "aan" en "uit" begrijpt. Al het andere: niet starten.
AAN = ["1", "ja", "J", "true", "TRUE", "yes", "y", "on", "aan", " 1 "]
UIT = ["", "0", "nee", "n", "false", "False", "no", "off", "uit"]
ONZIN = ["2", "misschien", "enabled", "ture", "1;", "-1", "waar", "jaa"]

# Een verzonnen zijbalk-kop vanaf een adres in het Home Assistant-netwerk.
VERVALST = {"HTTP_X_INGRESS_PATH": "/api/hassio_ingress/verzonnen",
            "REMOTE_ADDR": "172.30.32.5"}

_VERVALSTE_KOP = f"""
conn = main.get_db()
conn.execute("INSERT OR IGNORE INTO gebruikers (naam, wachtwoord, aangemaakt) "
             "VALUES ('tester', 'x', '2026-01-01')")
conn.commit()
conn.close()
a = main.app.test_client().get("/", environ_overrides={VERVALST!r})
print(a.status_code, a.headers.get("Location", ""))
"""


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


@pytest.mark.parametrize("waarde", AAN)
def test_bij_het_opstarten_elke_aan_waarde_is_zelfstandig(waarde):
    assert _opstarten({"ZELFSTANDIG": waarde}) == "True [] True"


@pytest.mark.parametrize("waarde", UIT)
def test_bij_het_opstarten_elke_uit_waarde_is_de_add_on(waarde):
    assert _opstarten({"ZELFSTANDIG": waarde}) == "False ['172.30.32.0/23'] False"


@pytest.mark.parametrize("waarde", ONZIN)
def test_bij_het_opstarten_onbekende_waarde_start_niet(waarde):
    """Een tikfout mag nooit stilletjes de add-on-stand geven: daar laat een
    zijbalk-kop iedereen zonder inlog binnen."""
    uit = _los_proces({"ZELFSTANDIG": waarde}, "print('gestart')", check=False)
    assert uit.returncode != 0
    assert "gestart" not in uit.stdout
    assert "ZELFSTANDIG=" in uit.stderr and "begrijp ik niet" in uit.stderr


@pytest.mark.parametrize("waarde", ["yes", "on", "true", "ja", "1"])
def test_vervalste_zijbalk_kop_komt_niet_langs_de_inlog_bij_elke_aan_waarde(waarde, tmp_path):
    """172.30.32.0/23 is het Ingress-netwerk van de add-on. Als losse container
    mag een kop vanaf zo'n adres niets betekenen, hoe ZELFSTANDIG ook gespeld is."""
    uit = _los_proces({"ZELFSTANDIG": waarde, "DATA_DIR": str(tmp_path)}, _VERVALSTE_KOP)
    status, waarheen = uit.stdout.split()
    assert status == "302"
    assert waarheen.startswith("/inloggen")


def test_add_on_vertrouwt_de_zijbalk_kop_nog_steeds(tmp_path):
    """De andere kant: zonder ZELFSTANDIG blijft de add-on zoals hij was."""
    uit = _los_proces({"DATA_DIR": str(tmp_path)}, _VERVALSTE_KOP)
    assert uit.stdout.split() == ["200"]


@pytest.mark.parametrize("waarde,verwacht", [("", "False"), ("0", "False"), ("1", "True"),
                                             ("ja", "True")])
def test_secure_cookie_alleen_als_je_het_aanzet(waarde, verwacht):
    """Standaard uit: thuis is het http://, en een Secure-cookie komt daar nooit terug."""
    env = {"SESSION_COOKIE_SECURE": waarde} if waarde else {}
    uit = _los_proces(env, "print(main.app.config['SESSION_COOKIE_SECURE'])")
    assert uit.stdout.strip() == verwacht


def test_secure_cookie_onbekende_waarde_start_niet():
    uit = _los_proces({"SESSION_COOKIE_SECURE": "misschien"}, "print('gestart')", check=False)
    assert uit.returncode != 0
    assert "SESSION_COOKIE_SECURE=" in uit.stderr


def test_secure_cookie_staat_echt_op_de_cookie(app, monkeypatch, uitgelogde_client):
    monkeypatch.setitem(app.config, "SESSION_COOKIE_SECURE", True)
    antwoord = uitgelogde_client.post("/inloggen", data={
        "csrf_token": "test-token", "naam": TEST_GEBRUIKER, "wachtwoord": TEST_WACHTWOORD})
    assert "Secure" in antwoord.headers["Set-Cookie"]


# --- Elke pagina achter de inlog ---------------------------------------------

def _naar_inloggen(antwoord):
    """De add-on zet een meegestuurd Ingress-pad voor elke link, ook voor deze
    doorverwijzing (zo was het altijd); het gaat erom dat het /inloggen is."""
    pad = antwoord.headers["Location"].split("?", 1)[0]
    return pad == "/inloggen" or (pad.startswith("/api/hassio_ingress/")
                                  and pad.endswith("/inloggen"))


def _voorbeeld_url(regel):
    """Vul elk stukje van de route met iets geldigs, zodat de route zelf matcht."""
    waarden = {}
    for naam, omzetter in regel._converters.items():
        soort = type(omzetter).__name__
        waarden[naam] = 1 if soort in {"IntegerConverter", "NumberConverter"} else "x"
    with facturen.app.test_request_context():
        return facturen.url_for(regel.endpoint, **waarden)


def _alle_routes():
    for regel in facturen.app.url_map.iter_rules():
        if regel.endpoint in facturen.OPEN_PAGINAS:
            continue
        for methode in sorted(regel.methods - {"HEAD", "OPTIONS"}):
            yield pytest.param(regel, methode, id=f"{methode} {regel.rule}")


def test_open_paginas_zijn_precies_deze_vier():
    """Wie hier iets bij zet, zet die pagina open voor iedereen op het netwerk."""
    assert facturen.OPEN_PAGINAS == {"inloggen", "account_instellen", "static",
                                     "scans_ontvangen"}


def test_er_zijn_genoeg_routes_om_te_controleren():
    assert len(list(_alle_routes())) >= 90
    eindpunten = {r.endpoint for r in facturen.app.url_map.iter_rules()}
    assert {"download_pdf", "bekijk_pdf"} <= eindpunten


@pytest.mark.parametrize("regel,methode", _alle_routes())
def test_elke_route_buiten_open_paginas_vraagt_om_inloggen(regel, methode, zelfstandig,
                                                           uitgelogde_client):
    url = _voorbeeld_url(regel)
    for extra in ({}, VERVALST):
        antwoord = uitgelogde_client.open(url, method=methode, environ_overrides=extra)
        assert antwoord.status_code == 302, (methode, url, extra)
        assert _naar_inloggen(antwoord), (methode, url, extra)


@pytest.mark.parametrize("regel,methode", _alle_routes())
def test_elke_route_vraagt_ook_als_add_on_op_poort_8099_om_inloggen(regel, methode,
                                                                   uitgelogde_client):
    """Poort 8099 van de add-on: zonder kop, of met een kop van buiten het
    Ingress-netwerk, ook geen toegang."""
    url = _voorbeeld_url(regel)
    for extra in ({}, {"HTTP_X_INGRESS_PATH": "/api/hassio_ingress/x",
                       "REMOTE_ADDR": "192.168.1.66"}):
        antwoord = uitgelogde_client.open(url, method=methode, environ_overrides=extra)
        assert antwoord.status_code == 302, (methode, url, extra)
        assert _naar_inloggen(antwoord), (methode, url, extra)


# --- Sessies na een nieuw wachtwoord -----------------------------------------

def _ander_apparaat(app):
    """Een tweede browser, ingelogd met het huidige wachtwoord."""
    return _log_in(app.test_client())


def _log_in(c, **extra):
    with c.session_transaction() as sessie:
        sessie["csrf_token"] = "ander-token"
    c.post("/inloggen", data={"csrf_token": "ander-token", "naam": TEST_GEBRUIKER,
                              "wachtwoord": TEST_WACHTWOORD, **extra})
    assert c.get("/").status_code == 200
    return c


def test_na_wachtwoord_herstellen_is_elk_apparaat_uitgelogd(app, client):
    telefoon = _ander_apparaat(app)
    wachtwoord_herstellen.herstel(facturen.DB_PATH, "hersteld-wachtwoord")
    try:
        for c in (client, telefoon):
            antwoord = c.get("/")
            assert antwoord.status_code == 302
            assert antwoord.headers["Location"].startswith("/inloggen")
    finally:
        _zet_testwachtwoord_terug()


def test_na_wachtwoord_herstellen_ook_een_onthouden_sessie_eruit(app):
    c = _log_in(app.test_client(), onthoud="ja")
    wachtwoord_herstellen.herstel(facturen.DB_PATH, "hersteld-wachtwoord")
    try:
        assert c.get("/").status_code == 302
    finally:
        _zet_testwachtwoord_terug()


def test_na_wachtwoord_wijzigen_blijft_dit_apparaat_binnen_en_de_rest_eruit(app, client,
                                                                           post):
    telefoon = _ander_apparaat(app)
    try:
        antwoord = post("/wachtwoord", data={
            "huidig": TEST_WACHTWOORD, "nieuw": "nog-een-geheim", "nogmaals": "nog-een-geheim"})
        assert antwoord.status_code == 302
        assert client.get("/").status_code == 200
        assert telefoon.get("/").status_code == 302
    finally:
        _zet_testwachtwoord_terug()


def test_met_het_nieuwe_wachtwoord_kom_je_weer_binnen(app, uitgelogde_client):
    wachtwoord_herstellen.herstel(facturen.DB_PATH, "hersteld-wachtwoord")
    try:
        uitgelogde_client.post("/inloggen", data={
            "csrf_token": "test-token", "naam": TEST_GEBRUIKER,
            "wachtwoord": "hersteld-wachtwoord"})
        assert uitgelogde_client.get("/").status_code == 200
    finally:
        _zet_testwachtwoord_terug()


def test_oude_sessie_zonder_kenmerk_moet_opnieuw_inloggen(app):
    """Zo zag een sessie er vóór 2.1.1 uit. Eén keer opnieuw inloggen na de update."""
    with app.test_client() as c:
        with c.session_transaction() as sessie:
            sessie["gebruiker"] = TEST_GEBRUIKER
        assert c.get("/").status_code == 302
        with c.session_transaction() as sessie:
            assert "gebruiker" not in sessie


def test_sessie_van_een_verwijderd_account_telt_niet(app, client):
    conn = facturen.get_db()
    conn.execute("UPDATE gebruikers SET naam='iemand-anders' WHERE naam=?", (TEST_GEBRUIKER,))
    conn.commit()
    conn.close()
    try:
        assert client.get("/").status_code == 302
    finally:
        conn = facturen.get_db()
        conn.execute("UPDATE gebruikers SET naam=? WHERE naam='iemand-anders'",
                     (TEST_GEBRUIKER,))
        conn.commit()
        conn.close()


def test_kenmerk_lekt_de_hash_niet(app):
    kenmerk = facturen.wachtwoord_kenmerk(TEST_HASH)
    assert len(kenmerk) == 32
    assert kenmerk not in TEST_HASH
    assert kenmerk != facturen.wachtwoord_kenmerk(generate_password_hash(TEST_WACHTWOORD))


def test_uitloggen_haalt_ook_het_kenmerk_weg(client, post):
    post("/uitloggen")
    with client.session_transaction() as sessie:
        assert "gebruiker" not in sessie and "wachtwoord_kenmerk" not in sessie
    assert client.get("/").status_code == 302


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
    assert dienst["ports"] == ["${LUISTER_ADRES:+${LUISTER_ADRES}:}${POORT:-8099}:${POORT:-8099}"]
    omgeving = dienst["environment"]
    assert omgeving["TZ"] == "${TZ:-Europe/Amsterdam}"
    # Binnen de container dezelfde poort, zodat de healthcheck en de startmelding kloppen.
    assert omgeving["PORT"] == "${POORT:-8099}"
    assert omgeving["SESSION_COOKIE_SECURE"] == "${SESSION_COOKIE_SECURE:-0}"
    assert dienst["mem_limit"]


def test_compose_zet_zelfstandig_vast_en_niet_uit_env():
    """Uit .env zou een tikfout of een lege regel de add-on-stand kunnen geven."""
    dienst = yaml.safe_load((REPO / "docker-compose.yml").read_text())["services"]["facturen"]
    assert dienst["environment"]["ZELFSTANDIG"] == "1"
    assert "ZELFSTANDIG" not in (REPO / ".env.example").read_text()


def test_compose_heeft_init_zodat_stoppen_meteen_gaat():
    """Als PID 1 negeert Python SIGTERM zonder handler voor de kernel; dan wacht
    docker 10 s en volgt SIGKILL, midden in een schrijfactie."""
    dienst = yaml.safe_load((REPO / "docker-compose.yml").read_text())["services"]["facturen"]
    assert dienst["init"] is True


def test_compose_poort_met_en_zonder_luisteradres():
    regel = yaml.safe_load((REPO / "docker-compose.yml").read_text())["services"]["facturen"]["ports"][0]

    def vul_in(env):
        def vervang(m):
            naam, soort, rest = m.group(1), m.group(2), m.group(3)
            if soort == ":-":
                return env.get(naam) or rest
            return rest.replace("${%s}" % naam, env[naam]) if env.get(naam) else ""
        tekst = regel
        while "${" in tekst:
            tekst = re.sub(r"\$\{([A-Z_]+)(:[-+])((?:[^{}]|\$\{[A-Z_]+\})*)\}", vervang, tekst)
        return tekst
    assert vul_in({}) == "8099:8099"
    assert vul_in({"POORT": "8123"}) == "8123:8123"
    assert vul_in({"LUISTER_ADRES": "192.168.1.20"}) == "192.168.1.20:8099:8099"


def test_env_voorbeeld_heeft_alles_wat_compose_gebruikt():
    voorbeeld = (REPO / ".env.example").read_text()
    sleutels = set(re.findall(r"^([A-Z_]+)=", voorbeeld, re.M))
    # ${X:-...} en ${X-...}: die tweede vorm laat een léég antwoord staan in
    # plaats van de standaard, en dat is hoe BON_OCR_URL uit te zetten is.
    gebruikt = set(re.findall(r"\$\{([A-Z_]+):?[-+]",
                              (REPO / "docker-compose.yml").read_text()))
    assert gebruikt == sleutels
    assert "TZ=Europe/Amsterdam" in voorbeeld


def test_eigen_instellingen_en_administratie_komen_niet_in_git():
    regels = (REPO / ".gitignore").read_text().split()
    assert "/.env" in regels
    assert "/data/" in regels
    # De datamap van het ontwikkelen zonder Docker (main.py valt daarop terug).
    assert "facturen_app/app/data/" in regels


def test_dockerignore_houdt_administratie_en_geheimen_uit_het_image():
    regels = [r.strip() for r in (ADDON_DIR / ".dockerignore").read_text().splitlines()
              if r.strip() and not r.startswith("#")]
    for nodig in ["app/data/", "data/", "**/secret_key", "**/*.db", "**/*.db-journal",
                  "**/*.db-wal", "**/.env", "**/.env.*", "tests/", "**/__pycache__/",
                  "**/.pytest_cache/", "**/test-results/", "**/.git"]:
        assert nodig in regels, nodig
    # De add-on en de losse Dockerfile kopiëren deze mappen; die moeten erin blijven.
    for weg in ["app", "app/", "rootfs", "zelfstandig", "*"]:
        assert weg not in regels, weg


def test_losse_dockerfile_houdt_de_data_map_als_volume():
    tekst = (ADDON_DIR / "Dockerfile.standalone").read_text()
    assert '\nVOLUME ["/data"]\n' in tekst


def test_losse_dockerfile_healthcheck_op_de_poort_uit_env():
    tekst = (ADDON_DIR / "Dockerfile.standalone").read_text()
    assert "\nHEALTHCHECK " in tekst
    assert 'CMD ["/usr/local/bin/facturen-gezond"]' in tekst
    assert "COPY zelfstandig/gezond.sh /usr/local/bin/facturen-gezond" in tekst
    gezond = (ADDON_DIR / "zelfstandig" / "gezond.sh").read_text()
    assert "os.environ.get('PORT', '8099')" in gezond
    assert "/inloggen" in gezond
    assert "su-exec nobody" in gezond


def test_requirements_zijn_allemaal_vastgezet():
    """Ook pillow en chardet: anders bouwt de Pi over een maand iets anders."""
    regels = [r.strip() for r in (APP_DIR / "requirements.txt").read_text().splitlines()
              if r.strip() and not r.startswith("#")]
    assert all(re.fullmatch(r"[A-Za-z0-9_.\-\[\]]+==[0-9][0-9A-Za-z.]*", r) for r in regels), regels
    namen = {r.split("==")[0].lower() for r in regels}
    assert {"pillow", "chardet"} <= namen


def _start_sh(env, tmp_path):
    """start.sh tot aan het moment dat hij de app zou starten."""
    nep = tmp_path / "bin"
    nep.mkdir(exist_ok=True)
    for naam in ["su-exec", "chown", "python3"]:
        (nep / naam).write_text("#!/bin/sh\necho \"$0 $*\"\n")
        (nep / naam).chmod(0o755)
    tekst = (ADDON_DIR / "zelfstandig" / "start.sh").read_text().replace(
        "/opt/app/main.py", "main.py")
    script = tmp_path / "start.sh"
    script.write_text(tekst)
    omgeving = {"PATH": f"{nep}:/usr/bin:/bin", "DATA_DIR": str(tmp_path / "data"),
                "HOME": str(tmp_path)}
    omgeving.update(env)
    return subprocess.run(["sh", str(script)], env=omgeving, capture_output=True, text=True)


@pytest.mark.parametrize("env", [{"PUID": "0"}, {"PGID": "0"}, {"PUID": "abc"},
                                 {"PGID": "1000;id"}, {"PUID": "-1"}, {"PUID": "00"}])
def test_start_weigert_root_en_onzin_als_puid(env, tmp_path):
    uit = _start_sh(env, tmp_path)
    assert uit.returncode == 1
    assert "su-exec" not in uit.stdout
    assert "id -u" in uit.stdout + uit.stderr


@pytest.mark.skipif(os.geteuid() != 0, reason="start.sh als root: alleen in de container")
def test_start_als_root_zakt_naar_puid(tmp_path):
    uit = _start_sh({"PUID": "1234", "PGID": "5678"}, tmp_path)
    assert uit.returncode == 0, uit.stderr
    assert "su-exec 1234:5678 python3 main.py" in uit.stdout


def test_start_als_gewone_gebruiker_slaat_chown_over(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("deze test hoort bij een gewone gebruiker")
    uit = _start_sh({"PUID": "1000", "PGID": "1000"}, tmp_path)
    assert uit.returncode == 0, uit.stderr
    assert "chown" not in uit.stdout and "su-exec" not in uit.stdout
    assert "python3 main.py" in uit.stdout


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
    for naam in ["start.sh", "wachtwoord-herstellen.sh", "gezond.sh"]:
        tekst = (ADDON_DIR / "zelfstandig" / naam).read_text()
        assert tekst.startswith("#!/bin/sh\n")
        assert "su-exec" in tekst


def test_versie_klopt_met_config_yaml():
    config = yaml.safe_load((ADDON_DIR / "config.yaml").read_text())
    tekst = (APP_DIR / "main.py").read_text()
    assert f'os.environ.get("ADDON_VERSION") or "{config["version"]}"' in tekst


# --- De bonnenlezer als eigen container ---------------------------------------

def test_de_bonnenlezer_staat_niet_op_het_netwerk():
    """Dit is de belangrijkste eis aan die container: hij heeft geen inlog, dus
    hij mag alleen vanuit de app bereikbaar zijn en nooit via een poort op de Pi."""
    dienst = yaml.safe_load((REPO / "docker-compose.yml").read_text())["services"]["bon-ocr"]
    assert "ports" not in dienst
    assert dienst["expose"] == ["8099"]
    assert dienst["restart"] == "unless-stopped"
    # Geen datamap: de bon komt in het verzoek mee en wordt niet bewaard.
    assert "volumes" not in dienst
    # Zonder init blijft de container tien seconden hangen bij elke stop.
    assert dienst["init"] is True
    # Gemeten op een Pi 5: piek 626 MB bij een bon van 2 MB. 768 was te krap.
    assert dienst["mem_limit"] == "1g"


def test_de_app_weet_het_adres_van_de_lezer():
    dienst = yaml.safe_load((REPO / "docker-compose.yml").read_text())["services"]["facturen"]
    # Zonder dubbele punt: een lege waarde in .env hoort leeg te blijven, want
    # dat is hoe je het lezen uitzet.
    assert dienst["environment"]["BON_OCR_URL"] == "${BON_OCR_URL-http://bon-ocr:8099/lees}"


def test_de_lezer_draait_niet_als_root_en_heeft_de_modellen_in_het_image():
    inhoud = (REPO / "bon_ocr" / "Dockerfile").read_text()
    assert "USER bonocr" in inhoud
    # De modellen tijdens de build binnenhalen: anders moet de Pi aan het
    # internet kunnen en staat de eerste scan stil op een download.
    assert "RapidOCR(" in inhoud
    assert "HEALTHCHECK" in inhoud
    # Debian en niet Alpine: onnxruntime heeft geen wheels voor musl.
    assert "slim-bookworm" in inhoud


def test_de_lezer_heeft_vaste_pakketversies():
    regels = [r.strip() for r in (REPO / "bon_ocr" / "requirements.txt").read_text()
              .splitlines() if r.strip() and not r.startswith("#")]
    assert regels
    for regel in regels:
        assert "==" in regel, regel
