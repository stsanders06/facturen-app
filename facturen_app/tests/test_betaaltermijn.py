"""De betaaltermijn is in te stellen; dat stuurt vervaldatum, te-laat en herinnering."""
from datetime import date, timedelta

import pytest

from conftest import facturen


def dagen_geleden(aantal):
    return (date.today() - timedelta(days=aantal)).isoformat()


def test_standaard_is_veertien_dagen(db):
    """Zonder eigen keuze blijft de vervaldatum veertien dagen na de factuurdatum."""
    assert facturen.vervaldatum("2026-08-14") == "2026-08-28"
    assert facturen.betaaltermijn_dagen() == 14


def test_instellingenrij_met_alleen_id_krijgt_veertien_dagen(db):
    """INSERT settings (id) moet blijven werken; de kolom heeft een default."""
    db.execute("DELETE FROM settings")
    db.execute("INSERT INTO settings (id) VALUES (1)")
    db.commit()
    rij = db.execute("SELECT betaaltermijn_dagen FROM settings WHERE id=1").fetchone()
    assert rij["betaaltermijn_dagen"] == 14


def test_eigen_termijn_verandert_vervaldatum_en_te_laat(db, client, maak_factuur):
    db.execute("UPDATE settings SET betaaltermijn_dagen=30 WHERE id=1")
    db.commit()

    assert facturen.vervaldatum("2026-08-14") == "2026-09-13"

    # Twintig dagen oud is bij dertig dagen termijn nog niet te laat.
    maak_factuur(nummer="2026-001", datum=dagen_geleden(20), status="verzonden")
    pagina = client.get("/").data.decode()
    assert "Te laat" not in pagina

    # Eenenvijftig dagen oud wél (30 dagen termijn + 21 dagen te laat).
    db.execute("DELETE FROM regels")
    db.execute("DELETE FROM facturen")
    db.commit()
    maak_factuur(nummer="2026-002", datum=dagen_geleden(51), status="verzonden")
    pagina = client.get("/").data.decode()
    assert "Te laat" in pagina


@pytest.mark.parametrize("invoer, verwacht", [
    ("30", 30),
    ("0", 1),       # korter dan een dag is geen termijn
    ("999", 365),   # langer dan een jaar hoort niet bij een rekening
    ("", 14),       # leeg: terug naar de standaard
])
def test_instellingen_bewaart_en_begrenst_de_termijn(post, db, invoer, verwacht):
    post("/instellingen", {"naam": "Stone", "betaaltermijn_dagen": invoer})
    rij = db.execute("SELECT betaaltermijn_dagen FROM settings WHERE id=1").fetchone()
    assert rij["betaaltermijn_dagen"] == verwacht


def test_herinnering_met_termijn_van_dertig_dagen(monkeypatch, db, maak_factuur):
    """Bij dertig dagen termijn is een rekening van veertig dagen oud tien dagen te laat."""
    opgevangen = {}

    def nep_mail(s, ontvanger, onderwerp, tekst, pad, bestandsnaam):
        opgevangen.update(tekst=tekst, onderwerp=onderwerp)
        return True, ""

    monkeypatch.setattr(facturen, "_mail_pdf", nep_mail)
    db.execute(
        """UPDATE settings SET smtp_host='smtp.example.com', naam='Hogedruk Venlo',
           betaaltermijn_dagen=30 WHERE id=1"""
    )
    db.commit()

    factuur_id = maak_factuur(nummer="2026-001", datum=dagen_geleden(40), status="verzonden")
    gelukt, _ = facturen.herinnering_email(factuur_id)
    assert gelukt
    assert "10 dagen geleden" in opgevangen["tekst"]
