"""Adressen en telefoonnummers krijgen overal dezelfde vorm.

Wat je intikt komt terug op de rekening, in de mail en op de klantpagina. Of je nu
"1234ab voorbeeldstad" of "1234 AB Voorbeeldstad" typt, er hoort hetzelfde uit te komen. Het
gebeurt bij het opslaan, dus ook voor wat er uit een CSV komt.
"""
import io

import pytest

from conftest import facturen

net_adres = facturen.net_adres
net_telefoon = facturen.net_telefoon


@pytest.mark.parametrize("invoer, verwacht", [
    # Al goed: blijft zoals het is.
    ("Voorbeeldstraat 1\n1234 AB Voorbeeldstad", "Voorbeeldstraat 1\n1234 AB Voorbeeldstad"),
    # Op één regel met een komma.
    ("Voorbeeldstraat 1, 1234 AB Voorbeeldstad", "Voorbeeldstraat 1\n1234 AB Voorbeeldstad"),
    # Alles aan elkaar en klein getypt.
    ("voorbeeldstraat 1 1234ab voorbeeldstad", "Voorbeeldstraat 1\n1234 AB Voorbeeldstad"),
    # Rommel ertussen: lege regels en dubbele spaties.
    ("Voorbeeldstraat 14\n\n1234AB   voorbeeldstad", "Voorbeeldstraat 14\n1234 AB Voorbeeldstad"),
    # Tussenvoegsels blijven klein.
    ("voorbeeldstraat 1, 1234AB, dorp aan den berg",
     "Voorbeeldstraat 1\n1234 AB Dorp aan den Berg"),
    # Na een koppelteken hoort weer een hoofdletter.
    ("voorbeeldstraat 3, 1234ab voorbeeld-stad", "Voorbeeldstraat 3\n1234 AB Voorbeeld-Stad"),
    ("", ""),
])
def test_een_adres_krijgt_de_vaste_vorm(invoer, verwacht):
    assert net_adres(invoer) == verwacht


def test_eigen_hoofdletters_blijven_staan():
    """Wie zelf hoofdletters zet, bedoelt dat waarschijnlijk zo."""
    assert net_adres("Voorbeeldstraat 1, 1234EA 's-Voorbeeld").endswith("'s-Voorbeeld")


def test_zonder_postcode_blijft_de_inhoud_ongemoeid():
    """Een buitenlands adres wordt niet verbouwd: geen hoofdletters bijwerken, geen
    postcode verzinnen. Alleen de komma wordt een regelovergang, want die scheidt de
    straat van de plaats."""
    assert net_adres("Baker Street 221B, London NW1 6XE") == \
        "Baker Street 221B\nLondon NW1 6XE"
    assert net_adres("Alleen een straatnaam 5") == "Alleen een straatnaam 5"


def test_een_adres_uit_de_adressenlijst_zonder_postcode(client):
    """Niet elk adres bij het Kadaster heeft een postcode; dan hoort de komma toch
    een regelovergang te worden in plaats van in het veld te blijven staan."""
    assert net_adres("Voorbeeldstraat 14, Voorbeeldstad") == "Voorbeeldstraat 14\nVoorbeeldstad"


def test_een_adres_zonder_postcode_wordt_wel_opgeschoond():
    assert net_adres("  Voorbeeldweg   3 \n\n  Voorbeeldstad  ") == "Voorbeeldweg 3\nVoorbeeldstad"


@pytest.mark.parametrize("invoer, verwacht", [
    ("0612345678", "06 12345678"),
    ("06 12345678", "06 12345678"),
    ("06-12345678", "06 12345678"),
    ("+31612345678", "+31 6 12345678"),
    ("0031 6 12345678", "+31 6 12345678"),
    ("+31 (0)6 12345678", "+31 6 12345678"),
    # Vaste nummers: het netnummer is drie of vier cijfers lang.
    ("010-1234567", "010 1234567"),
    ("0201234567", "020 1234567"),
    ("0161234567", "0161 234567"),
    ("+31 (0)10 1234567", "+31 10 1234567"),
    ("", ""),
])
def test_een_telefoonnummer_krijgt_de_vaste_vorm(invoer, verwacht):
    assert net_telefoon(invoer) == verwacht


@pytest.mark.parametrize("invoer", ["+49 30 12345678", "onbekend", "06123"])
def test_wat_geen_nederlands_nummer_is_blijft_staan(invoer):
    """Een buitenlands nummer verkeerd opdelen is erger dan het laten staan."""
    assert net_telefoon(invoer) == invoer


def test_een_klant_wordt_netjes_opgeslagen(post, db):
    post("/klanten/nieuw", {
        "naam": "Jan Jansen",
        "adres": "voorbeeldstraat 1, 1234ab voorbeeldstad",
        "telefoon": "06-12345678",
    })
    klant = db.execute("SELECT adres, telefoon FROM klanten").fetchone()
    assert klant["adres"] == "Voorbeeldstraat 1\n1234 AB Voorbeeldstad"
    assert klant["telefoon"] == "06 12345678"


def test_het_adres_bij_een_rekening_ook(post, db):
    post("/nieuw", {
        "klant_naam": "Jan Jansen",
        "klant_adres": "voorbeeldstraat 14, 1234ab voorbeeldstad",
        "datum": "2026-08-14",
        "omschrijving": "Kraan vervangen", "type": "arbeid_uur",
        "aantal": "2", "prijs": "60,50",
    })
    assert db.execute("SELECT klant_adres FROM facturen").fetchone()[0] == \
        "Voorbeeldstraat 14\n1234 AB Voorbeeldstad"


def test_je_eigen_gegevens_ook(post, db):
    post("/instellingen", {"naam": "Testbedrijf", "adres": "voorbeeldstraat 1 1234ab voorbeeldstad",
                           "telefoon": "0101234567"})
    rij = db.execute("SELECT adres, telefoon FROM settings").fetchone()
    assert rij["adres"] == "Voorbeeldstraat 1\n1234 AB Voorbeeldstad"
    assert rij["telefoon"] == "010 1234567"


def test_uit_een_csv_ook(post, db):
    """Juist daar staat het vaak slordig."""
    bestand = (io.BytesIO(
        "naam;adres;telefoon\nJan Jansen;voorbeeldstraat 14 1234ab voorbeeldstad;06-12345678\n"
        .encode()), "klanten.csv")
    post("/klanten/import", {"bestand": bestand}, content_type="multipart/form-data")

    klant = db.execute("SELECT adres, telefoon FROM klanten").fetchone()
    assert klant["adres"] == "Voorbeeldstraat 14\n1234 AB Voorbeeldstad"
    assert klant["telefoon"] == "06 12345678"


def test_wat_er_al_stond_wordt_eenmalig_opgeschoond(db):
    """Anders zou alleen wat je ná deze versie opslaat netjes worden en blijft je
    lijst er door elkaar uitzien."""
    db.execute("""INSERT INTO klanten (naam, adres, telefoon)
                  VALUES ('Slordig', 'voorbeeldstraat 14, 1234ab voorbeeldstad', '06-12345678')""")
    db.execute("""INSERT INTO klanten (naam, adres, telefoon)
                  VALUES ('Buitenland', 'Baker Street 221B, London NW1 6XE',
                          '+49 30 12345678')""")
    db.commit()

    aangepast = facturen.net_bestaande_gegevens()

    slordig = db.execute("SELECT * FROM klanten WHERE naam='Slordig'").fetchone()
    assert slordig["adres"] == "Voorbeeldstraat 14\n1234 AB Voorbeeldstad"
    assert slordig["telefoon"] == "06 12345678"

    buiten = db.execute("SELECT * FROM klanten WHERE naam='Buitenland'").fetchone()
    # De komma wordt een regelovergang, verder blijft het buitenlandse adres heel.
    assert buiten["adres"] == "Baker Street 221B\nLondon NW1 6XE"
    assert buiten["telefoon"] == "+49 30 12345678"
    assert aangepast == 2


def test_een_tweede_keer_opschonen_verandert_niets_meer(db):
    db.execute("""INSERT INTO klanten (naam, adres) VALUES ('Slordig', '1234ab voorbeeldstad')""")
    db.commit()

    facturen.net_bestaande_gegevens()
    assert facturen.net_bestaande_gegevens() == 0


def test_ook_het_adres_op_een_bestaande_rekening(db, maak_factuur):
    factuur_id = maak_factuur()
    db.execute("UPDATE facturen SET klant_adres='voorbeeldstraat 14 1234ab voorbeeldstad' WHERE id=?",
               (factuur_id,))
    db.commit()

    facturen.net_bestaande_gegevens()
    assert db.execute("SELECT klant_adres FROM facturen WHERE id=?",
                      (factuur_id,)).fetchone()[0] == "Voorbeeldstraat 14\n1234 AB Voorbeeldstad"
