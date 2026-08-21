"""Het overzicht bovenaan de rekeningenpagina en de knoppen op de kaarten."""
from datetime import date, timedelta


def dagen_geleden(aantal):
    return (date.today() - timedelta(days=aantal)).isoformat()


def test_openstaand_bedrag_telt_alleen_onbetaalde_rekeningen(db, client, maak_factuur):
    maak_factuur(nummer="2026-001", totaal=100.0, status="verzonden")
    maak_factuur(nummer="2026-002", totaal=250.0, status="betaald")
    inhoud = client.get("/").data.decode()
    assert "100,00" in inhoud
    assert "1 rekening" in inhoud


def test_rekening_over_de_vervaldatum_heet_te_laat(db, client, maak_factuur):
    """De betaaltermijn is veertien dagen, dus twintig dagen oud is te laat."""
    maak_factuur(nummer="2026-001", datum=dagen_geleden(20), status="verzonden")
    inhoud = client.get("/").data.decode()
    assert "Te laat" in inhoud


def test_verse_rekening_is_niet_te_laat(db, client, maak_factuur):
    maak_factuur(nummer="2026-001", datum=dagen_geleden(2), status="verzonden")
    assert "Te laat" not in client.get("/").data.decode()


def test_betaalde_rekening_is_nooit_te_laat(db, client, maak_factuur):
    maak_factuur(nummer="2026-001", datum=dagen_geleden(200), status="betaald")
    assert "Te laat" not in client.get("/").data.decode()


def test_filter_te_laat_toont_alleen_die(db, client, maak_factuur):
    maak_factuur(nummer="2026-001", datum=dagen_geleden(30), klant="Oude Klant",
                 status="verzonden")
    maak_factuur(nummer="2026-002", datum=dagen_geleden(1), klant="Verse Klant",
                 status="verzonden")
    inhoud = client.get("/?status=verlopen").data.decode()
    assert "Oude Klant" in inhoud
    assert "Verse Klant" not in inhoud


def test_openstaand_zet_de_oudste_bovenaan(db, client, maak_factuur):
    """Wie het langst op zijn geld wacht, hoort als eerste in beeld te komen."""
    maak_factuur(nummer="2026-001", datum=dagen_geleden(2), klant="Verse Klant",
                 status="verzonden")
    maak_factuur(nummer="2026-002", datum=dagen_geleden(60), klant="Oude Klant",
                 status="verzonden")
    inhoud = client.get("/?status=openstaand").data.decode()
    assert inhoud.index("Oude Klant") < inhoud.index("Verse Klant")


def test_de_lijst_zet_normaal_de_nieuwste_bovenaan(db, client, maak_factuur):
    maak_factuur(nummer="2026-001", klant="Eerste Klant")
    maak_factuur(nummer="2026-002", klant="Tweede Klant")
    inhoud = client.get("/").data.decode()
    assert inhoud.index("Tweede Klant") < inhoud.index("Eerste Klant")


def test_een_kaart_heeft_hooguit_een_losse_knop_naast_het_menu(db, client, maak_factuur):
    """Er stonden acht knoppen op elke kaart; dat is waar het menu voor is."""
    maak_factuur(status="verzonden")
    inhoud = client.get("/").data.decode()
    assert inhoud.count('class="btn btn-text"') <= 1
    assert 'class="menu"' in inhoud


def test_het_menu_bevat_de_minder_gebruikte_acties(db, client, maak_factuur):
    maak_factuur(status="verzonden")
    inhoud = client.get("/").data.decode()
    for actie in ["Bekijken", "Bewerken", "Downloaden", "PDF vernieuwen", "Verwijderen"]:
        assert actie in inhoud, actie


def test_concept_met_mailadres_zet_mailen_vooraan(db, client, maak_factuur):
    maak_factuur(status="concept", email="jan@example.com")
    inhoud = client.get("/").data.decode()
    assert '<button type="submit" class="btn btn-text">Mailen</button>' in inhoud


def test_concept_zonder_mailadres_zet_bewerken_vooraan(db, client, maak_factuur):
    maak_factuur(status="concept", email="")
    inhoud = client.get("/").data.decode()
    assert 'class="btn btn-text" href="/factuur/' in inhoud


def test_verzonden_rekening_zet_betaald_vooraan(db, client, maak_factuur):
    maak_factuur(status="verzonden")
    inhoud = client.get("/").data.decode()
    assert '<button type="submit" class="btn btn-text">Betaald</button>' in inhoud


def test_offertekaart_heeft_ook_een_menu(db, client, maak_offerte):
    maak_offerte(status="verzonden")
    inhoud = client.get("/offertes").data.decode()
    assert 'class="menu"' in inhoud
    assert inhoud.count('class="btn btn-text"') <= 1
