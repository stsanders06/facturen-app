"""Gewerkte dagen, notities en inkopen delen één kaart.

Eerder had elke sectie een eigen opzet: dagen over twee kaarten, notities als
één lijst, inkopen met een kaart in de kaart. De macro en de klasse invoerrij
houden ze bij elkaar, zodat een volgende wijziging niet in één sectie blijft
hangen.
"""
from test_smalle_weergave import css_van, regel_met


def _klus(post, db):
    post("/klussen/nieuw", {"naam": "Badkamer", "uurtarief": "45"})
    return db.execute("SELECT id FROM klussen").fetchone()[0]


def _sectie(html, kop):
    """De kaart vanaf de kop tot de volgende kop."""
    return html.split(f"<h2>{kop}</h2>", 1)[1].split("<h2>", 1)[0]


def test_de_drie_secties_zijn_dezelfde_kaart(post, client, db):
    klus_id = _klus(post, db)
    post(f"/klus/{klus_id}/dag", {
        "datum": "2026-08-14", "van": "09:00", "tot": "17:00", "notitie": "voegen",
    })
    post(f"/klus/{klus_id}/dag", {
        "datum": "2026-08-15", "van": "08:00", "tot": "12:00",
    })
    vast = db.execute("SELECT id FROM uren WHERE datum='2026-08-15'").fetchone()[0]
    db.execute("UPDATE uren SET factuur_id=1 WHERE id=?", (vast,))
    db.commit()
    post(f"/klus/{klus_id}/notitie", {"tekst": "Achtergevel meenemen"})
    post(f"/klus/{klus_id}/inkoop", {
        "omschrijving": "Gamma — tegellijm", "bedrag": "34,95",
    })

    pagina = client.get(f"/klus/{klus_id}").data.decode()
    # Info en 'Klus verwijderen' blijven gewone kaarten; alleen deze drie delen de macro.
    assert pagina.count('class="card invoerkaart"') == 3
    assert "<h2>Dag erbij</h2>" not in pagina
    assert "inkoop-nieuw" not in pagina
    assert 'class="inkoop"' not in pagina

    dagen = _sectie(pagina, "Gewerkte dagen")
    notities = _sectie(pagina, "Notities")
    inkopen = _sectie(pagina, "Inkopen")
    for deel in (dagen, notities, inkopen):
        assert "invoerform" in deel
        assert 'class="invoerlijst' in deel
        assert 'class="invoerrij-hoofd"' in deel
        assert 'class="invoerrij-weg"' in deel
        # Eerst invullen, dan de lijn met de lijst eronder.
        assert deel.index("invoerform") < deel.index('class="invoerlijst')

    # Een dag die al op een rekening staat kun je niet weghalen of aanpassen.
    assert dagen.count('aria-label="Dag verwijderen"') == 1
    vast_rij = dagen.split('class="invoerrij vast"', 1)[1].split("</li>", 1)[0]
    assert "data-dag-bewerk" not in vast_rij
    assert "Dag verwijderen" not in vast_rij
    assert "chip-betaald" in vast_rij
    # De open dag blijft te bewerken, met dezelfde velden als voorheen.
    assert 'data-dag-bewerk' in dagen
    assert 'name="van"' in dagen
    assert 'value="09:00"' in dagen

    assert 'aria-label="Notitie verwijderen"' in notities
    assert 'data-bewerkt="notitie-' in notities
    assert 'aria-label="Bon verwijderen"' in inkopen
    assert "Gamma — tegellijm" in inkopen
    assert "34,95" in inkopen


def test_het_kruisje_van_een_regel_heeft_het_grijze_vak(client, post, db):
    """Zelfde rand, hoek en vulling als het kruisje bij een materiaalregel,
    anders is weghalen per sectie weer een andere knop."""
    klus_id = _klus(post, db)
    css = css_van(client.get(f"/klus/{klus_id}").data.decode())
    vak = regel_met(css, ".invoerrij-weg .weg")
    assert "border: 1px solid var(--border)" in vak
    assert "border-radius: var(--radius-sm)" in vak
    assert "background: var(--card)" in vak
    assert "border-top: 1px solid var(--divider)" in regel_met(
        css, ".invoerlijst > .invoerrij")
    # Materiaal toevoegen zit niet meer in een eigen kader.
    assert "border" not in regel_met(css, ".invoerrij .mat-toevoegen")


def test_een_lege_sectie_houdt_het_formulier_en_zegt_dat_er_niets_staat(client, post, db):
    klus_id = _klus(post, db)
    pagina = client.get(f"/klus/{klus_id}").data.decode()
    dagen = _sectie(pagina, "Gewerkte dagen")
    assert "Dag toevoegen" in dagen
    assert "Nog geen uren bijgehouden voor deze klus." in dagen
    assert 'class="invoerlijst' not in dagen
    assert "Nog niets opgeschreven bij deze klus." in _sectie(pagina, "Notities")
    assert "Nog geen inkopen bij deze klus." in _sectie(pagina, "Inkopen")
