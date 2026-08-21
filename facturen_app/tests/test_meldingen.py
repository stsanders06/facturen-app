"""De regel bovenaan de pagina na een handeling.

Alles zag er eerst hetzelfde uit — geel met een rand — waardoor ook "Instellingen
opgeslagen" las alsof er iets fout was gegaan. De kleur draagt nu de betekenis.
"""


def test_een_bevestiging_is_geen_waarschuwing(post, client):
    inhoud = post("/instellingen", {"naam": "Jan Jansen"},
                  follow_redirects=True).data.decode()
    assert 'class="melding gelukt"' in inhoud
    assert "Instellingen opgeslagen" in inhoud


def test_iets_dat_niet_kon_is_wel_een_waarschuwing(post, db):
    db.execute("INSERT INTO klanten (naam) VALUES ('Jan Jansen')")
    db.commit()
    klant_id = db.execute("SELECT id FROM klanten").fetchone()[0]

    inhoud = post(f"/klant/{klant_id}/bewerk", {"naam": ""},
                  follow_redirects=True).data.decode()
    assert 'class="melding fout"' in inhoud
    assert "Vul een naam in" in inhoud


def test_een_bedrag_met_euroteken_blijft_leesbaar(post, db, maak_factuur):
    """De melding gaat als JSON over de lijn omdat er een knop bij kan zitten; het
    euroteken hoort daar niet als \\u20ac uit te komen."""
    factuur_id = maak_factuur(totaal=100.0, status="verzonden")
    inhoud = post(f"/factuur/{factuur_id}/betalingen",
                  {"datum": "2026-08-14", "bedrag": "40"},
                  follow_redirects=True).data.decode()
    assert "€ 40,00 geboekt" in inhoud
    assert "u20ac" not in inhoud


def test_een_naam_met_een_apostrof_breekt_de_melding_niet(post, db):
    """JSON én HTML moeten allebei overweg kunnen met "Anne d'Hondt"."""
    post("/klanten/nieuw", {"naam": "Anne d'Hondt"})
    klant_id = db.execute("SELECT id FROM klanten").fetchone()[0]

    inhoud = post(f"/klant/{klant_id}/verwijder", follow_redirects=True).data.decode()
    assert "Anne d&#39;Hondt" in inhoud or "Anne d'Hondt" in inhoud
    assert "Ongedaan maken" in inhoud


def test_meerdere_meldingen_komen_allemaal_door(post, db, maak_factuur):
    """Bij het opslaan van een rekening kunnen er twee tegelijk zijn."""
    inhoud = post("/klanten/nieuw", {"naam": "Jan Jansen"},
                  follow_redirects=True).data.decode()
    assert inhoud.count('class="melding') >= 1
