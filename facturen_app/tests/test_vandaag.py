"""Het startscherm Vandaag. Leeg mag, en het menu hoort erop te staan."""


def test_vandaag_is_leeg_te_openen(client):
    pagina = client.get("/vandaag")
    assert pagina.status_code == 200
    tekst = pagina.data.decode()
    assert "Vandaag" in tekst
    assert "Openstaand" in tekst
    assert 'href="/"' in tekst or "Rekeningen" in tekst
    assert "Klussen" in tekst
    assert "Scans" in tekst
    assert "Instellingen" in tekst
    assert 'aria-label="Nieuw"' in tekst


def test_vandaag_noemt_een_open_rekening(client, maak_factuur):
    maak_factuur(nummer="2026-004", status="verzonden", klant="Voorbeeld Klant", totaal=100.0)
    tekst = client.get("/vandaag").data.decode()
    assert "Voorbeeld Klant" in tekst
    assert "100,00" in tekst
