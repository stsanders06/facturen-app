"""Leest de nepbonnen uit `bonnen/` en vergelijkt met de juiste antwoorden.

In `bonnen/` staat per bon de tekst die RapidOCR eruit haalde (`.txt`) en wat
er had moeten uitkomen (`.json`). De tekst is eenmalig gemaakt en meegeleverd,
zodat deze tests draaien zonder onnxruntime: op een PR hoeft de CI geen
OCR-container te bouwen. Allemaal nepbonnen; echte bonnen horen hier niet.

De bonnen `bon01` tot `bon08` zijn de set waarop de parser is gemaakt, de zes
`blind`-bonnen zijn er pas achteraf op gezet. Die blinde zes zijn dus de
eerlijke score: daar hoort 5 van de 6 helemaal goed te zijn, en de zesde moet
zijn eigen fout melden.
"""
import difflib
import json
import os
import sys

import pytest

# De parser ligt een map hoger en is geen pakket. Geen conftest.py hiervoor:
# die zou botsen met de conftest van facturen_app (zelfde modulenaam, en dan
# verliest de hele suite zijn fixtures).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bon_parser import lees_bon  # noqa: E402  (moet ná het zetten van sys.path)

BONNEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bonnen")
NAMEN = sorted(n[:-4] for n in os.listdir(BONNEN) if n.endswith(".txt"))

# Eén bekende afwijking, en waarom hij blijft staan: de OCR maakt van de
# 9%-regel op deze bon ':96BTT  2,81'. Daar is geen tarief uit te halen zonder
# te gokken, en gokken op een btw-bedrag is erger dan het niet weten. De
# parser hoort het btw-veld dan als onzeker te melden; dat test
# test_een_fout_veld_staat_altijd_in_onzeker.
BEKENDE_AFWIJKING = {"blind03_groene_erf": {"btw_totaal"}}


def _lees(naam):
    with open(os.path.join(BONNEN, naam + ".txt"), encoding="utf-8") as bron:
        tekst = bron.read()
    with open(os.path.join(BONNEN, naam + ".json"), encoding="utf-8") as bron:
        goed = json.load(bron)
    return lees_bon(tekst), goed


def _plat(waarde):
    return "".join(teken for teken in str(waarde or "").lower() if teken.isalnum())


def _zelfde_naam(gelezen, verwacht):
    """De winkelnaam hoeft niet woord voor woord te kloppen.

    'GROTHAUS' is goed genoeg voor 'GROTHAUS Bouw- en Tuinmarkt': de bon zet
    de handelsnaam groot bovenaan en de volledige naam in het briefhoofd.
    """
    a, b = _plat(gelezen), _plat(verwacht)
    return bool(a) and (a in b or b in a)


def _afwijkingen(uit, goed):
    """Welke velden niet kloppen. Leeg betekent: deze bon is helemaal goed."""
    fout = set()
    if not _zelfde_naam(uit["winkel"], goed["winkel"]):
        fout.add("winkel")
    if uit["datum"] != goed["datum"]:
        fout.add("datum")
    if uit["totaal"] != goed["totaal"]:
        fout.add("totaal")
    if uit["btw_totaal"] != goed["btw_totaal"]:
        fout.add("btw_totaal")
    if uit["betaalwijze"] != goed["betaalwijze"]:
        fout.add("betaalwijze")
    if len(uit["regels"]) != len(goed["regels"]):
        fout.add("regels")
        return fout
    for gelezen, verwacht in zip(uit["regels"], goed["regels"]):
        gelijkenis = difflib.SequenceMatcher(
            None, _plat(gelezen["omschrijving"]), _plat(verwacht["omschrijving"])).ratio()
        if gelijkenis < 0.85:
            fout.add("omschrijving")
        if gelezen["aantal"] != verwacht["aantal"]:
            fout.add("aantal")
        if gelezen["stukprijs"] != verwacht["stukprijs"]:
            fout.add("stukprijs")
        if gelezen["regelbedrag"] != verwacht["regelbedrag"]:
            fout.add("regelbedrag")
    return fout


@pytest.mark.parametrize("naam", NAMEN)
def test_elke_nepbon_komt_er_goed_uit(naam):
    uit, goed = _lees(naam)
    assert _afwijkingen(uit, goed) <= BEKENDE_AFWIJKING.get(naam, set())


@pytest.mark.parametrize("naam", NAMEN)
def test_een_fout_veld_staat_altijd_in_onzeker(naam):
    """De belangrijkste eis: een leesfout mag niet stil doorglippen.

    Stone kijkt elke bon na, maar alleen als hij weet waar hij moet kijken.
    Klopt een veld niet, dan hoort het in `onzeker` te staan.
    """
    uit, goed = _lees(naam)
    fout = _afwijkingen(uit, goed)
    # De regelvelden worden samen gemeld, want ze komen uit dezelfde regel.
    gemeld = set(uit["onzeker"])
    if {"omschrijving", "aantal", "stukprijs", "regelbedrag"} & fout:
        gemeld |= {"omschrijving", "aantal", "stukprijs", "regelbedrag"}
    if "btw" in gemeld:
        gemeld.add("btw_totaal")
    assert fout <= gemeld


@pytest.mark.parametrize("naam", [n for n in NAMEN if n.startswith("bon")])
def test_de_eigen_set_is_helemaal_goed(naam):
    """Op de acht bonnen waarop de parser is gemaakt mag niets missen."""
    uit, goed = _lees(naam)
    assert _afwijkingen(uit, goed) == set()


def test_vijf_van_de_zes_blinde_bonnen_zijn_helemaal_goed():
    """De eerlijke score. Zakt dit, dan is er een regel stukgegaan.

    In de proef was dit 4 van de 6; de datum met de maand als woord en de
    kolomopmaak met het aantal maakten er 5 van. Omhoog mag, omlaag niet.
    """
    goed_geteld = sum(not _afwijkingen(*_lees(naam))
                      for naam in NAMEN if naam.startswith("blind"))
    assert goed_geteld >= 5


def test_de_regels_tellen_op_tot_het_totaal():
    """Op een bon zonder korting is dit de controle die een leesfout vangt."""
    for naam in NAMEN:
        uit, _ = _lees(naam)
        if uit["korting"] or not uit["regels"] or uit["totaal"] is None:
            continue
        if "regels" in uit["onzeker"]:
            continue
        assert uit["controles"]["regels_som_klopt"], naam


# Hieronder de losse vormen uit de proef, elk met zijn eigen test. Zo zie je
# bij een wijziging welke vorm stukgaat in plaats van alleen dat een bon
# niet meer klopt.

def test_de_aantalregel_onder_het_artikel_hoort_bij_dat_artikel():
    """Action zet het aantal op een eigen regel: '= 2 x 7,95'."""
    uit = lees_bon("Action\n"
                   "02-10-2026\n"
                   "123456 Schuurspons\n"
                   "15,90\n"
                   "= 2 x 7,95\n"
                   "TOTAAL   15,90\n"
                   "MAESTRO   15,90\n")
    assert len(uit["regels"]) == 1
    regel = uit["regels"][0]
    assert regel["omschrijving"] == "Schuurspons"
    assert (regel["aantal"], regel["stukprijs"], regel["regelbedrag"]) == (2, 7.95, 15.9)


def test_het_sterretje_van_bauhaus_is_een_maalteken():
    """Bauhaus drukt '4 * 5,79' af, en plakt de btw-code tegen het bedrag."""
    uit = lees_bon("Bauhaus\n"
                   "02-10-2026\n"
                   "Houtschroef 4x40\n"
                   "23,161\n"
                   "4 * 5,79\n"
                   "TOTAAL   23,16\n"
                   "V PAY   23,16\n")
    assert len(uit["regels"]) == 1
    assert uit["regels"][0]["aantal"] == 4
    assert uit["regels"][0]["regelbedrag"] == 23.16


def test_een_vervolgregel_zonder_bedrag_hoort_bij_de_omschrijving():
    uit = lees_bon("Action\n"
                   "02-10-2026\n"
                   "123456 Schuurspons\n"
                   "15,90\n"
                   "= 2 x 7,95\n"
                   "grof geel\n"
                   "TOTAAL   15,90\n"
                   "PIN   15,90\n")
    assert uit["regels"][0]["omschrijving"] == "Schuurspons grof geel"


def test_de_nettoprijs_van_karwei_vervangt_de_prijs_van_het_artikel():
    uit = lees_bon("KAR\n"
                   "WEI\n"
                   "02-10-2026\n"
                   "Spaanplaatschroef\n"
                   "8,99   8,99\n"
                   "Nettoprijs   7,19\n"
                   "TOTAAL   7,19\n"
                   "Bankpas   7,19\n")
    assert uit["winkel"] == "Karwei"
    assert uit["regels"][0]["regelbedrag"] == 7.19
    assert uit["regels"][0]["stukprijs"] == 7.19


def test_een_prijscorrectie_is_geen_eigen_regel():
    uit = lees_bon("Karwei\n"
                   "02-10-2026\n"
                   "Plint wit\n"
                   "12,50   12,50\n"
                   "Prijscorrectie   -2,50\n"
                   "TOTAAL   10,00\n"
                   "Maestro   10,00\n")
    assert len(uit["regels"]) == 1


def test_de_personeelskorting_na_het_subtotaal_wordt_gevonden():
    uit = lees_bon("Karwei\n"
                   "02-10-2026\n"
                   "Plint wit\n"
                   "12,50   12,50\n"
                   "Subtotaal   12,50\n"
                   "Personeelskorting   1,25\n"
                   "TOTAAL   11,25\n"
                   "Maestro   11,25\n")
    assert uit["korting"] == -1.25
    assert uit["controles"]["regels_som_klopt"]


def test_een_artikelnummer_hoort_niet_in_de_omschrijving():
    uit = lees_bon("Gamma\n"
                   "02-10-2026\n"
                   "ART. 884412 Ducttape zwart   6,49\n"
                   "TOTAAL   6,49\n"
                   "PIN   6,49\n")
    assert uit["regels"][0]["omschrijving"] == "Ducttape zwart"


def test_vier_kolommen_met_het_aantal_in_het_midden():
    """Een tuincentrum zet omschrijving, aantal, stukprijs en bedrag naast elkaar."""
    uit = lees_bon("Tuincentrum\n"
                   "02-10-2026\n"
                   "Artikel   Aant   Prijs   Bedrag\n"
                   "Buxus bol 30cm   2   12,50   25,00\n"
                   "TOTAAL   25,00\n"
                   "Bankpas   25,00\n")
    regel = uit["regels"][0]
    assert regel["omschrijving"] == "Buxus bol 30cm"
    assert (regel["aantal"], regel["stukprijs"], regel["regelbedrag"]) == (2, 12.5, 25.0)


def test_twee_losse_bedragen_worden_geen_aantal():
    """De kolomvorm mag niet toeslaan als het niet uitkomt.

    '8,99  8,99' met een 1 ervoor is stukprijs en bedrag, geen aantal 1 maal
    een ander bedrag. De rekencontrole in de parser houdt dat uit elkaar.
    """
    uit = lees_bon("Bouwmax\n"
                   "02-10-2026\n"
                   "Vulmiddel 750g   1   8,99   8,99\n"
                   "TOTAAL   8,99\n"
                   "PIN   8,99\n")
    assert uit["regels"][0]["aantal"] == 1
    assert uit["regels"][0]["regelbedrag"] == 8.99


def test_het_totaal_volgt_de_meerderheid():
    """Een bon noemt het totaal vaker; één leesfout mag niet winnen.

    Dit geval komt uit de proef: één regel las 29,97 waar de andere twee
    29,95 zeiden.
    """
    uit = lees_bon("Bouwmax\n"
                   "02-10-2026\n"
                   "Verfroller   29,95\n"
                   "TOTAAL   29,97\n"
                   "TE BETALEN   29,95\n"
                   "Totaal betaald   29,95\n"
                   "PIN   29,95\n")
    assert uit["totaal"] == 29.95
    assert uit["controles"]["totaal_stemmen"] == [29.97, 29.95, 29.95]
    assert "totaal" not in uit["onzeker"]


def test_bij_een_gelijke_stand_is_het_totaal_onzeker():
    """Twee totaalregels die het niet eens zijn: dan kiest de parser de eerste,
    maar Stone hoort ernaar te kijken."""
    uit = lees_bon("Bouwmax\n"
                   "02-10-2026\n"
                   "Verfroller   29,95\n"
                   "TOTAAL   29,97\n"
                   "TE BETALEN   29,95\n")
    assert uit["totaal"] == 29.97
    assert "totaal" in uit["onzeker"]


@pytest.mark.parametrize("regel,verwacht", [
    ("MAESTRO   10,00", "pin"),
    ("V PAY   10,00", "pin"),
    ("Bankpas   10,00", "pin"),
    ("PIN   10,00", "pin"),
    ("CONTANT   10,00", "contant"),
    ("MASTERCARD   10,00", "creditcard"),
    ("VISA   10,00", "creditcard"),
])
def test_de_betaalwijze_wordt_herkend(regel, verwacht):
    uit = lees_bon(f"Gamma\n02-10-2026\nSchroef   10,00\nTOTAAL   10,00\n{regel}\n")
    assert uit["betaalwijze"] == verwacht


def test_contactloos_is_alleen_een_laatste_redmiddel():
    """Een Mastercard gaat ook contactloos, dus het woord alleen zegt niets.

    Staat er niets anders, dan is pin de beste gok, maar dan hoort de
    betaalwijze wel als onzeker gemeld te worden.
    """
    uit = lees_bon("Gamma\n02-10-2026\nSchroef   10,00\nTOTAAL   10,00\nCONTACTLOOS\n")
    assert uit["betaalwijze"] == "pin"
    assert "betaalwijze" in uit["onzeker"]

    naast_mastercard = lees_bon("Gamma\n02-10-2026\nSchroef   10,00\n"
                                "TOTAAL   10,00\nMASTERCARD CONTACTLOOS\n")
    assert naast_mastercard["betaalwijze"] == "creditcard"


@pytest.mark.parametrize("regel,verwacht", [
    ("Datum 02-10-2026", "2026-10-02"),
    ("Datum 02.10.26", "2026-10-02"),
    ("2026-10-02", "2026-10-02"),
    ("Datum 30/06/2619:22", "2026-06-30"),
    ("02okt 2026   12:40", "2026-10-02"),
    ("2 oktober 2026", "2026-10-02"),
])
def test_de_datum_wordt_herkend(regel, verwacht):
    uit = lees_bon(f"Gamma\n{regel}\nSchroef   10,00\nTOTAAL   10,00\nPIN   10,00\n")
    assert uit["datum"] == verwacht


@pytest.mark.parametrize("regel", [
    "BTW 21%   1,74",
    "BTW 21,00%   1,74",
    "BTW 21,00 % over 8,26 = 1,74",
])
def test_de_btw_wordt_herkend(regel):
    uit = lees_bon(f"Gamma\n02-10-2026\nSchroef   10,00\nTOTAAL   10,00\n"
                   f"{regel}\nPIN   10,00\n")
    assert uit["btw"] == [{"tarief": 21, "bedrag": 1.74}]
    assert uit["controles"]["btw_klopt"]


def test_negen_procent_btw_wordt_ook_herkend():
    uit = lees_bon("Hema\n02-10-2026\nKoffie   10,00\nTOTAAL   10,00\n"
                   "BTW 9%   0,83\nPIN   10,00\n")
    assert uit["btw"] == [{"tarief": 9, "bedrag": 0.83}]
    assert uit["controles"]["btw_klopt"]


def test_een_btw_die_niet_klopt_wordt_gemeld():
    uit = lees_bon("Gamma\n02-10-2026\nSchroef   10,00\nTOTAAL   10,00\n"
                   "BTW 21%   5,00\nPIN   10,00\n")
    assert uit["controles"]["btw_klopt"] is False
    assert "btw" in uit["onzeker"]


def test_een_postcode_wordt_nooit_de_winkelnaam():
    uit = lees_bon("2345 CD Proefdorp\n"
                   "Testweg 3\n"
                   "Houthandel Van Dijk\n"
                   "02-10-2026\n"
                   "Vuren plank   10,00\n"
                   "TOTAAL   10,00\nPIN   10,00\n")
    assert uit["winkel"] == "Houthandel Van Dijk"
    # Onbekende winkel: altijd nakijken, want dit is een gok uit de opmaak.
    assert "winkel" in uit["onzeker"]


def test_een_lege_bon_levert_lege_velden_en_geen_fout():
    """De app moet hier gewoon een leeg formulier van maken."""
    uit = lees_bon("")
    assert uit["winkel"] is None and uit["totaal"] is None and uit["regels"] == []
    assert "totaal" in uit["onzeker"]
