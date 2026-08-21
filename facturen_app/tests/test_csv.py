"""Inlezen van CSV-bestanden: klanten en losse regels."""
import io

from conftest import facturen


def lees(tekst):
    return facturen._csv_lezer(tekst.encode("utf-8"))


def test_puntkomma_van_nederlandse_excel(db):
    rijen = list(lees("naam;email\nJan;jan@example.com\n"))
    assert rijen[0]["naam"] == "Jan"
    assert rijen[0]["email"] == "jan@example.com"


def test_komma_en_tab_werken_ook(db):
    assert list(lees("naam,email\nJan,jan@example.com\n"))[0]["naam"] == "Jan"
    assert list(lees("naam\temail\nJan\tjan@example.com\n"))[0]["naam"] == "Jan"


def test_byte_order_mark_hoort_niet_in_de_eerste_kolomnaam(db):
    rijen = list(facturen._csv_lezer("﻿naam;email\nJan;jan@example.com\n".encode("utf-8")))
    assert "naam" in rijen[0]


def test_windows_regeleindes(db):
    assert len(list(lees("naam\r\nJan\r\nPiet\r\n"))) == 2


def test_kolomnamen_worden_herkend_onder_verschillende_koppen(db):
    gevonden, extra = facturen._kolomnamen(["Bedrijf", "E-mail", "Mobiel", "Opmerkingen"])
    assert gevonden["naam"] == "Bedrijf"
    assert gevonden["email"] == "E-mail"
    assert gevonden["telefoon"] == "Mobiel"
    assert gevonden["notitie"] == "Opmerkingen"
    assert extra == []


def test_postcode_en_plaats_gaan_bij_het_adres(db):
    gevonden, extra = facturen._kolomnamen(["naam", "straat", "postcode", "woonplaats"])
    assert gevonden["adres"] == "straat"
    assert extra == ["postcode", "woonplaats"]


def test_onbekende_kolommen_worden_genegeerd(db):
    gevonden, extra = facturen._kolomnamen(["naam", "favoriete kleur"])
    assert set(gevonden) == {"naam"}
    assert extra == []


def test_klanten_importeren_slaat_bestaande_over(post, db):
    db.execute("INSERT INTO klanten (naam, email) VALUES ('Jan Jansen', 'oud@example.com')")
    db.commit()

    bestand = (io.BytesIO(b"naam;email\nJan Jansen;nieuw@example.com\nPiet Peters;piet@example.com\n"),
               "klanten.csv")
    antwoord = post("/klanten/import", {"bestand": bestand},
                    content_type="multipart/form-data", follow_redirects=True)
    assert antwoord.status_code == 200

    namen = {r["naam"]: r["email"] for r in db.execute("SELECT naam, email FROM klanten")}
    assert namen["Jan Jansen"] == "oud@example.com"   # niet overschreven
    assert namen["Piet Peters"] == "piet@example.com"


def test_klanten_importeren_werkt_bij_met_het_vinkje(post, db):
    db.execute("INSERT INTO klanten (naam, email) VALUES ('Jan Jansen', 'oud@example.com')")
    db.commit()

    bestand = (io.BytesIO(b"naam;email\nJan Jansen;nieuw@example.com\n"), "klanten.csv")
    post("/klanten/import", {"bestand": bestand, "bijwerken": "ja"},
         content_type="multipart/form-data", follow_redirects=True)

    email = db.execute("SELECT email FROM klanten WHERE naam='Jan Jansen'").fetchone()[0]
    assert email == "nieuw@example.com"


def test_voorbeeldbestanden_zijn_te_downloaden(client):
    for url in ["/klanten/voorbeeld.csv", "/regels/voorbeeld.csv"]:
        antwoord = client.get(url)
        assert antwoord.status_code == 200
        assert b"omschrijving" in antwoord.data or b"naam" in antwoord.data
