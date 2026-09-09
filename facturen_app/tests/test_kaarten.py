"""Wat een klik op een kaart doet, en wat er achter de drie puntjes zit.

Klikken opende eerst de PDF. Dat is niet wat je vanuit een lijst wilt: je klikt om
iets aan te passen. De PDF is er niet uit, die staat nu in het menu.
"""
import re


def menu_van(inhoud):
    return "".join(re.findall(r'<details class="menu".*?</details>', inhoud, re.S))


def test_een_offertekaart_opent_het_bewerkscherm(client, maak_offerte):
    offerte_id = maak_offerte()
    inhoud = client.get("/offertes").data.decode()
    assert f'class="kaartlink" href="/offerte/{offerte_id}/bewerk"' in inhoud


def test_de_offerte_pdf_zit_achter_de_drie_puntjes(client, maak_offerte):
    offerte_id = maak_offerte()
    assert f'href="/offerte/{offerte_id}/bekijk"' in menu_van(
        client.get("/offertes").data.decode())


def test_bewerken_staat_niet_dubbel_in_het_offertemenu(client, maak_offerte):
    maak_offerte()
    assert ">Bewerken<" not in menu_van(client.get("/offertes").data.decode())


def test_op_de_klantpagina_openen_de_regels_ook_het_bewerkscherm(client, db, maak_factuur):
    """Dezelfde rekening hoort overal hetzelfde te doen als je erop klikt."""
    klant_id = db.execute("INSERT INTO klanten (naam) VALUES ('Jan Jansen')").lastrowid
    factuur_id = maak_factuur()
    db.execute("UPDATE facturen SET klant_id=? WHERE id=?", (klant_id, factuur_id))
    db.commit()
    inhoud = client.get(f"/klant/{klant_id}").data.decode()
    assert f'class="kaartlink" href="/factuur/{factuur_id}/bewerk"' in inhoud
    assert f'href="/factuur/{factuur_id}/bekijk"' in inhoud


def test_de_waarschuwing_bij_een_betaalde_rekening_is_weg(client, maak_factuur):
    """Stond bovenaan het bewerkscherm en was in de weg; je mag zelf weten of je een
    betaalde rekening nog aanpast."""
    factuur_id = maak_factuur(status="betaald")
    inhoud = client.get(f"/factuur/{factuur_id}/bewerk").data.decode()
    assert "staat op betaald" not in inhoud


def test_de_csv_knop_is_een_gewone_knop_geworden(client):
    """Er stond een heel vak omheen met uitleg; dat nam meer plaats in dan de regels."""
    inhoud = client.get("/nieuw").data.decode()
    assert 'id="csv-knop"' in inhoud
    assert 'id="regels_csv" accept=".csv,text/csv,text/plain" hidden' in inhoud
    assert "urenkiezer" not in inhoud


def test_uren_van_een_klus_staan_achter_een_knop(client, db):
    klus_id = db.execute(
        """INSERT INTO klussen (naam, uurtarief, gestart)
           VALUES ('Gevel reinigen', 55.0, '2026-09-01')""").lastrowid
    db.execute("""INSERT INTO uren (klus_id, datum, van, tot)
                  VALUES (?, '2026-09-01', '08:00', '12:00')""", (klus_id,))
    db.commit()
    inhoud = client.get("/nieuw").data.decode()
    assert 'class="menu-item klus-keuze"' in inhoud
    assert f'data-klus="{klus_id}"' in inhoud
    # De keuzelijst met een uitlegblok eromheen is weg.
    assert 'id="klus_uren"' not in inhoud


def test_zonder_klussen_staat_die_knop_er_niet(client):
    """Op de knop zelf zoeken en niet op het woord: dat staat ook in de uitleg en
    in het script eronder."""
    assert 'class="menu-item klus-keuze"' not in client.get("/nieuw").data.decode()


def test_bij_meer_dan_tien_regels_komt_er_een_toon_alles_knop(client):
    """Een lange materiaallijst maakte van het formulier één muur met velden."""
    inhoud = client.get("/nieuw").data.decode()
    assert 'id="meer-regels"' in inhoud
    assert "var MAX_ZICHTBAAR = 10;" in inhoud


def test_dezelfde_knoppen_staan_bij_een_offerte(client):
    """Rekening en offerte gebruiken hetzelfde formulier; wat je bij de een opruimt
    hoort bij de ander ook opgeruimd te zijn."""
    inhoud = client.get("/offertes/nieuw").data.decode()
    assert 'id="csv-knop"' in inhoud
    assert 'id="meer-regels"' in inhoud
    assert "urenkiezer" not in inhoud
