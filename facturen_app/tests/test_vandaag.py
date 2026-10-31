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
    # scans en instellingen daar niet te bereiken. Klanten staat los in de balk.
    menu = tekst.split('class="meer-vel"', 1)
    assert len(menu) == 2
    vel = menu[1].split("</div>", 1)[0]
    assert 'href="/offertes"' in vel
    assert 'href="/scans"' in vel
    assert 'href="/instellingen"' in vel
    assert 'href="/klanten"' not in vel
    assert "min-height: 44px" in tekst.split(".tabbar .meer-vel a", 1)[1].split("}", 1)[0]


def test_vandaag_noemt_een_open_rekening(client, maak_factuur):
    maak_factuur(nummer="2026-004", status="verzonden", klant="Voorbeeld Klant", totaal=100.0)
    tekst = client.get("/vandaag").data.decode()
    assert "Voorbeeld Klant" in tekst
    assert "100,00" in tekst
