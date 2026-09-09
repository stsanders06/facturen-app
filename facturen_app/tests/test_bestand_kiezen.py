"""Bestanden kiezen gaat overal via een eigen knop.

Het kale veld toont "No file chosen" — de enige Engelse tekst in de app — en laat
niet zien wat je hebt gekozen.
"""
import re


def bestandsvelden(pagina):
    return re.findall(r'<input[^>]*type="file"[^>]*>', pagina)


def test_het_logo_zit_achter_een_knop(client):
    pagina = client.get("/instellingen").data.decode()
    assert 'data-kiest="logo"' in pagina
    assert all("hidden" in veld for veld in bestandsvelden(pagina))


def test_het_csv_bestand_bij_klanten_ook(client):
    pagina = client.get("/klanten/import").data.decode()
    assert 'data-kiest="bestand"' in pagina
    assert all("hidden" in veld for veld in bestandsvelden(pagina))


def test_het_verborgen_veld_is_niet_required(client):
    """Chrome weigert een formulier te versturen met een verplicht veld dat het
    niet kan tonen, en zegt er niets bij. De route vangt het zelf netjes af."""
    pagina = client.get("/klanten/import").data.decode()
    assert not any("required" in veld for veld in bestandsvelden(pagina))


def test_importeren_zonder_bestand_zegt_wat_er_mis_is(post):
    antwoord = post("/klanten/import", {}, follow_redirects=True)
    assert "Kies eerst een CSV-bestand" in antwoord.get_data(as_text=True)
