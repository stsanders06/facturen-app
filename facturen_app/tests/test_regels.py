"""Regels uit het formulier lezen en optellen."""
from werkzeug.datastructures import MultiDict

from conftest import facturen


def form(**velden):
    gegevens = MultiDict()
    for sleutel, waarden in velden.items():
        for waarde in waarden:
            gegevens.add(sleutel, waarde)
    return gegevens


def test_regels_worden_opgeteld(db):
    regels, totaal = facturen.lees_regels(form(
        omschrijving=["Kraan", "Arbeid"],
        type=["materiaal", "arbeid_uur"],
        aantal=["2", "3"],
        prijs=["25,50", "60"],
    ))
    assert len(regels) == 2
    assert regels[0][4] == 51.0
    assert totaal == 231.0


def test_regel_zonder_omschrijving_valt_af(db):
    regels, totaal = facturen.lees_regels(form(
        omschrijving=["Kraan", ""],
        type=["materiaal", "materiaal"],
        aantal=["1", "5"],
        prijs=["10", "99"],
    ))
    assert len(regels) == 1
    assert totaal == 10.0


def test_vaste_prijs_telt_het_bedrag_en_niet_het_aantal(db):
    """Bij 'arbeid, vaste prijs' zegt het aantal niets; anders zou 3 × 250 op de
    rekening komen terwijl je 250 hebt afgesproken."""
    regels, totaal = facturen.lees_regels(form(
        omschrijving=["Badkamer"], type=["arbeid_klus"], aantal=["3"], prijs=["250"],
    ))
    assert regels[0][2] == 1.0
    assert totaal == 250.0


def test_lege_lijst_levert_niets_op(db):
    assert facturen.lees_regels(form()) == ([], 0.0)


def test_klusnummer_gaat_mee_als_het_een_getal_is(db):
    regels, _ = facturen.lees_regels(form(
        omschrijving=["Uren badkamer", "Kraan"],
        type=["arbeid_uur", "materiaal"],
        aantal=["8", "1"],
        prijs=["45", "20"],
        regel_klus=["3", ""],
    ))
    assert regels[0][5] == 3
    assert regels[1][5] is None


def test_totaal_wordt_op_centen_afgerond(db):
    _, totaal = facturen.lees_regels(form(
        omschrijving=["Iets"], type=["materiaal"], aantal=["3"], prijs=["0,105"],
    ))
    assert totaal == 0.32
