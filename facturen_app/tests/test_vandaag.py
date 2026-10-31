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
    # De zijbalk is op een telefoon weg. Zonder dit menu zijn offertes,
    # klanten en instellingen daar niet te bereiken.
    menu = tekst.split('class="meer-vel"', 1)
    assert len(menu) == 2
    assert 'href="/offertes"' in menu[1]
    assert 'href="/klanten"' in menu[1]
    assert 'href="/instellingen"' in menu[1]
    assert "min-height: 44px" in tekst.split(".tabbar .meer-vel a", 1)[1].split("}", 1)[0]


def test_vandaag_noemt_een_open_rekening(client, maak_factuur):
    maak_factuur(nummer="2026-004", status="verzonden", klant="Voorbeeld Klant", totaal=100.0)
    tekst = client.get("/vandaag").data.decode()
    assert "Voorbeeld Klant" in tekst
    assert "100,00" in tekst
