"""Adressen en telefoonnummers krijgen overal dezelfde vorm.

Wat je intikt komt terug op de rekening, in de mail en op de klantpagina. Of je nu
"5932ch tegelen" of "5932 CH Tegelen" typt, er hoort hetzelfde uit te komen. Het
gebeurt bij het opslaan, dus ook voor wat er uit een CSV komt.
"""
import io

import pytest

from conftest import facturen

net_adres = facturen.net_adres
net_telefoon = facturen.net_telefoon


@pytest.mark.parametrize("invoer, verwacht", [
    # Al goed: blijft zoals het is.
    ("Nachtegaalstraat 212\n5932 CH Tegelen", "Nachtegaalstraat 212\n5932 CH Tegelen"),
    # Op één regel met een komma.
    ("Nachtegaalstraat 212, 5932 CH Tegelen", "Nachtegaalstraat 212\n5932 CH Tegelen"),
    # Alles aan elkaar en klein getypt.
    ("nachtegaalstraat 212 5932ch tegelen", "Nachtegaalstraat 212\n5932 CH Tegelen"),
    # Rommel ertussen: lege regels en dubbele spaties.
    ("Molenstraat 14\n\n5911AB   venlo", "Molenstraat 14\n5911 AB Venlo"),
    # Tussenvoegsels blijven klein.
    ("kerkstraat 1, 5932CH, alphen aan den rijn",
     "Kerkstraat 1\n5932 CH Alphen aan den Rijn"),
    # Na een koppelteken hoort weer een hoofdletter.
    ("dorpsweg 3, 1234ab berkel-enschot", "Dorpsweg 3\n1234 AB Berkel-Enschot"),
    ("", ""),
])
def test_een_adres_krijgt_de_vaste_vorm(invoer, verwacht):
    assert net_adres(invoer) == verwacht


def test_eigen_hoofdletters_blijven_staan():
    """Wie zelf hoofdletters zet, bedoelt dat waarschijnlijk zo."""
    assert net_adres("Lange Voorhout 1, 2514EA 's-Gravenhage").endswith("'s-Gravenhage")


@pytest.mark.parametrize("invoer", [
    "Baker Street 221B, London NW1 6XE",
    "Alleen een straatnaam 5",
])
def test_zonder_nederlandse_postcode_blijft_het_adres_ongemoeid(invoer):
    """Een buitenlands of half ingevuld adres hoort niet verbouwd te worden."""
    assert net_adres(invoer) == invoer


def test_een_adres_zonder_postcode_wordt_wel_opgeschoond():
    assert net_adres("  Dorpsweg   3 \n\n  Tegelen  ") == "Dorpsweg 3\nTegelen"


@pytest.mark.parametrize("invoer, verwacht", [
    ("0612345678", "06 12345678"),
    ("06 12345678", "06 12345678"),
    ("06-12345678", "06 12345678"),
    ("+31612345678", "+31 6 12345678"),
    ("0031 6 12345678", "+31 6 12345678"),
    ("+31 (0)6 12345678", "+31 6 12345678"),
    # Vaste nummers: het netnummer is drie of vier cijfers lang.
    ("077-3512244", "077 3512244"),
    ("0201234567", "020 1234567"),
    ("0161234567", "0161 234567"),
    ("+31 (0)77 3512244", "+31 77 3512244"),
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
        "adres": "nachtegaalstraat 212, 5932ch tegelen",
        "telefoon": "06-12345678",
    })
    klant = db.execute("SELECT adres, telefoon FROM klanten").fetchone()
    assert klant["adres"] == "Nachtegaalstraat 212\n5932 CH Tegelen"
    assert klant["telefoon"] == "06 12345678"


def test_het_adres_bij_een_rekening_ook(post, db):
    post("/nieuw", {
        "klant_naam": "Jan Jansen",
        "klant_adres": "molenstraat 14, 5911ab venlo",
        "datum": "2026-08-14",
        "omschrijving": "Kraan vervangen", "type": "arbeid_uur",
        "aantal": "2", "prijs": "60,50",
    })
    assert db.execute("SELECT klant_adres FROM facturen").fetchone()[0] == \
        "Molenstraat 14\n5911 AB Venlo"


def test_je_eigen_gegevens_ook(post, db):
    post("/instellingen", {"naam": "Stone", "adres": "kerkstraat 1 5932ch tegelen",
                           "telefoon": "0773512244"})
    rij = db.execute("SELECT adres, telefoon FROM settings").fetchone()
    assert rij["adres"] == "Kerkstraat 1\n5932 CH Tegelen"
    assert rij["telefoon"] == "077 3512244"


def test_uit_een_csv_ook(post, db):
    """Juist daar staat het vaak slordig."""
    bestand = (io.BytesIO(
        "naam;adres;telefoon\nJan Jansen;molenstraat 14 5911ab venlo;06-12345678\n"
        .encode()), "klanten.csv")
    post("/klanten/import", {"bestand": bestand}, content_type="multipart/form-data")

    klant = db.execute("SELECT adres, telefoon FROM klanten").fetchone()
    assert klant["adres"] == "Molenstraat 14\n5911 AB Venlo"
    assert klant["telefoon"] == "06 12345678"


def test_wat_er_al_stond_wordt_eenmalig_opgeschoond(db):
    """Anders zou alleen wat je ná deze versie opslaat netjes worden en blijft je
    lijst er door elkaar uitzien."""
    db.execute("""INSERT INTO klanten (naam, adres, telefoon)
                  VALUES ('Slordig', 'molenstraat 14, 5911ab venlo', '06-12345678')""")
    db.execute("""INSERT INTO klanten (naam, adres, telefoon)
                  VALUES ('Buitenland', 'Baker Street 221B, London NW1 6XE',
                          '+49 30 12345678')""")
    db.commit()

    aangepast = facturen.net_bestaande_gegevens()

    slordig = db.execute("SELECT * FROM klanten WHERE naam='Slordig'").fetchone()
    assert slordig["adres"] == "Molenstraat 14\n5911 AB Venlo"
    assert slordig["telefoon"] == "06 12345678"

    buiten = db.execute("SELECT * FROM klanten WHERE naam='Buitenland'").fetchone()
    assert buiten["adres"] == "Baker Street 221B, London NW1 6XE"
    assert buiten["telefoon"] == "+49 30 12345678"
    assert aangepast == 1


def test_een_tweede_keer_opschonen_verandert_niets_meer(db):
    db.execute("""INSERT INTO klanten (naam, adres) VALUES ('Slordig', '5911ab venlo')""")
    db.commit()

    facturen.net_bestaande_gegevens()
    assert facturen.net_bestaande_gegevens() == 0


def test_ook_het_adres_op_een_bestaande_rekening(db, maak_factuur):
    factuur_id = maak_factuur()
    db.execute("UPDATE facturen SET klant_adres='molenstraat 14 5911ab venlo' WHERE id=?",
               (factuur_id,))
    db.commit()

    facturen.net_bestaande_gegevens()
    assert db.execute("SELECT klant_adres FROM facturen WHERE id=?",
                      (factuur_id,)).fetchone()[0] == "Molenstraat 14\n5911 AB Venlo"
