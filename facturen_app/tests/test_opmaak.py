"""Nederlandse notatie van bedragen, datums en uren."""
import pytest

from conftest import facturen


@pytest.mark.parametrize("waarde, verwacht", [
    (0, "0,00"),
    (12.5, "12,50"),
    (1287.5, "1.287,50"),
    (1234567.89, "1.234.567,89"),
    (None, "0,00"),
])
def test_bedragen_krijgen_een_komma_en_duizendtalpunten(waarde, verwacht):
    assert facturen.nl_bedrag(waarde) == verwacht


@pytest.mark.parametrize("waarde, verwacht", [
    ("2026-08-14", "14 aug 2026"),
    ("2026-01-01", "1 jan 2026"),
    ("2026-12-31", "31 dec 2026"),
])
def test_datums_worden_kort_nederlands(waarde, verwacht):
    assert facturen.filter_datum_nl(waarde) == verwacht


def test_onleesbare_datum_blijft_staan_zoals_hij_is():
    assert facturen.filter_datum_nl("later") == "later"


@pytest.mark.parametrize("van, tot, verwacht", [
    ("09:00", "17:00", 8.0),
    ("09:00", "09:30", 0.5),
    ("22:00", "01:30", 3.5),   # doorwerken tot na middernacht telt door
    ("09:00", "09:00", 0.0),
    ("kwart over", "17:00", 0.0),
])
def test_uren_tussen_twee_tijden(van, tot, verwacht):
    assert facturen.duur_in_uren(van, tot) == verwacht


@pytest.mark.parametrize("waarde, verwacht", [(8.5, "8,5"), (8.0, "8"), (None, "0")])
def test_uren_zonder_overbodige_nullen(waarde, verwacht):
    assert facturen.filter_uren(waarde) == verwacht


@pytest.mark.parametrize("van, tot, verwacht", [
    ("2026-08-10", "2026-08-12", "10 – 12 aug 2026"),
    ("2026-08-30", "2026-09-02", "30 aug – 2 sep 2026"),
    ("2025-12-30", "2026-01-02", "30 dec 2025 – 2 jan 2026"),
    ("2026-08-10", "2026-08-10", "10 aug 2026"),
    ("2026-08-10", None, "10 aug 2026"),
    (None, None, ""),
])
def test_periode_herhaalt_maand_en_jaar_alleen_als_het_moet(van, tot, verwacht):
    assert facturen.periode_nl(van, tot) == verwacht


def test_vervaldatum_is_veertien_dagen_later():
    assert facturen.vervaldatum("2026-08-14") == "2026-08-28"


def test_offerte_geldt_standaard_dertig_dagen():
    assert facturen.geldig_tot("2026-08-14") == "2026-09-13"


@pytest.mark.parametrize("waarde, verwacht", [
    ("12.50", 12.5),
    ("12,50", 12.5),
    ("1.287,50", 1287.5),   # met duizendtalpunt uit een CSV
    ("", 0.0),
    (None, 0.0),
    (" 60 ", 60.0),
])
def test_bedragen_lezen_met_komma_of_punt(waarde, verwacht):
    assert facturen._getal(waarde) == verwacht


def test_onleesbare_datum_valt_terug_op_vandaag():
    from datetime import date
    assert facturen.geldige_datum("gisteren") == date.today().isoformat()
    assert facturen.geldige_datum("gisteren", "2026-01-01") == "2026-01-01"
    assert facturen.geldige_datum("2026-08-14") == "2026-08-14"
