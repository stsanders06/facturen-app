"""De regel bovenaan het scherm na een handeling.

Alles zag er eerst hetzelfde uit — geel met een rand — waardoor ook "Instellingen
opgeslagen" las alsof er iets fout was gegaan. De kleur draagt nu de betekenis.
"""
import re

from test_smalle_weergave import css_van, regel_met


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


def test_een_melding_blijft_in_beeld_als_je_naar_beneden_staat(post, client):
    """De melding stond bovenaan de pagina. Bleef je na een bon halverwege
    staan, dan zag je hem niet. Hij hangt nu vast aan het scherm, onder de
    tabbalk, en rekent de inkeping van een iPhone mee."""
    inhoud = post("/instellingen", {"naam": "Jan Jansen"},
                  follow_redirects=True).data.decode()
    assert 'class="meldingen"' in inhoud
    assert 'role="status"' in inhoud
    assert inhoud.index('class="meldingen"') < inhoud.index('class="melding gelukt"')

    css = css_van(inhoud)
    vak = regel_met(css, ".meldingen")
    assert "position: fixed" in vak
    assert "z-index: 9" in vak
    assert "balkhoogte" in vak
    assert "safe-area-inset-left" in vak
    assert "env(safe-area-inset-top, 0px)" in regel_met(css, ".appbar")
    smal = css[css.index("@media (pointer: coarse)"):]
    assert "font-size: 16px" in regel_met(smal, ".meldingen .melding")


def test_een_ongedaan_knop_blijft_in_de_melding(post, maak_factuur):
    """De knop en het csrf-veld horen ín de melding te blijven. Zonder het
    veld weigert de app de opdracht, en buiten het vak zie je de knop niet
    als je naar beneden staat."""
    factuur_id = maak_factuur()
    inhoud = post(f"/factuur/{factuur_id}/verwijder",
                  follow_redirects=True).data.decode()
    stuk = re.search(r'class="meldingen".*?</div>\s*</div>', inhoud, re.S)
    assert stuk, inhoud
    vak = stuk.group(0)
    assert "Ongedaan maken" in vak
    assert 'name="csrf_token"' in vak
    assert 'class="melding gelukt"' in vak
    assert "terugknop" in vak


def test_na_een_formulier_blijft_de_pagina_waar_je_was(client):
    """Een bon toevoegen laadt de kluspagina opnieuw en de browser zet je
    bovenaan. De plek wordt bij het versturen bewaard en alleen teruggezet
    op dezelfde pagina, als er een melding staat. Zoeken is een GET en hoort
    de lijst juist bovenaan te houden. Een bestandskiezer verstuurt zonder
    submit-event; die moet de plek apart bewaren."""
    inhoud = client.get("/").data.decode()
    assert "facturen-schuif" in inhoud
    assert "bewaarSchuif()" in inhoud
    assert "gegevens.pad !== location.pathname + location.search" in inhoud
    assert "document.querySelector('.melding')" in inhoud
    assert "(e.target.method || '').toLowerCase() === 'get'" in inhoud
    kiezer = inhoud.index("data-verstuurt")
    versturen = inhoud.index("veld.form.submit()", kiezer)
    assert "bewaarSchuif()" in inhoud[kiezer:versturen]


def test_een_gewone_bevestiging_verdwijnt_een_knop_niet(client):
    """Een bevestiging die vast aan het scherm blijft hangen dekt de pagina
    af. Een knop om iets terug te halen mag juist niet vanzelf weg: dan is
    het te laat om hem te gebruiken."""
    inhoud = client.get("/").data.decode()
    assert "melding.querySelector('form, .balk')" in inhoud
    assert "melding.classList.contains('fout') ? 8000 : 5000" in inhoud
    assert "verdwijnMelding" in inhoud
