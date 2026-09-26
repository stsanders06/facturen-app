"""Prototype C: regels staan compact ingeklapt op rekening- en offerteformulier."""


def test_nieuw_formulier_heeft_regel_samenvatting(client):
    inhoud = client.get("/nieuw").data.decode()
    assert 'class="regel-samenvatting"' in inhoud
    # Op de rekening geen type-badge: omschrijving zegt al genoeg.
    assert 'class="regel-badge"' not in inhoud
    assert 'class="regel-detail"' in inhoud
    assert 'data-open="false"' in inhoud
    assert 'role="button"' in inhoud
    # Velden blijven in de HTML (voor POST), in het detailblok.
    assert 'name="omschrijving"' in inhoud
    assert 'name="type"' in inhoud
    assert 'name="aantal"' in inhoud
    assert 'name="prijs"' in inhoud
    assert 'class="weg"' in inhoud


def test_offerte_formulier_heeft_zelfde_regels_structuur(client):
    inhoud = client.get("/offertes/nieuw").data.decode()
    assert 'class="regel-samenvatting"' in inhoud
    assert 'class="regel-detail"' in inhoud
    # Offerte behoudt de type-badge (gedeelde template, alleen bij is_offerte).
    assert 'class="regel-badge"' in inhoud
    assert 'id="meer-regels"' in inhoud
    assert 'id="regel-erbij"' in inhoud


def test_bewerken_met_regels_toont_samenvatting_chrome(client, maak_factuur):
    factuur_id = maak_factuur()
    inhoud = client.get(f"/factuur/{factuur_id}/bewerk").data.decode()
    assert 'class="regel-samenvatting"' in inhoud
    assert 'class="regel-badge"' not in inhoud
    assert 'class="regel-detail"' in inhoud
    assert 'name="omschrijving"' in inhoud
    # Bestaande omschrijving blijft in het invoerveld staan voor POST.
    assert "Kraan vervangen" in inhoud


def test_script_heeft_prototype_c_gedrag(client):
    """Zonder JS-runner: bewaken dat open/dicht en badges in het script zitten.

    Badge-map blijft (voor offerte); pasSamenvattingAan raakt de badge alleen
    als het element bestaat (rekening heeft geen .regel-badge).
    """
    inhoud = client.get("/nieuw").data.decode()
    assert "function zetOpen(" in inhoud
    assert "function pasSamenvattingAan(" in inhoud
    assert "badge: 'M'" in inhoud
    assert "badge: 'U'" in inhoud
    assert "badge: 'D'" in inhoud
    assert "badge: 'V'" in inhoud
    assert "badge: 'B'" in inhoud
    assert "if (badge) badge.textContent" in inhoud
    assert "Nieuwe regel" in inhoud
