"""Uren op een rekening horen bij dezelfde klant, net als de bonnen.

De verkeerde klussen blijven wel in de pagina: wisselen van klant moet ze kunnen
tonen zonder opnieuw te laden.
"""
from conftest import facturen


def _klant(db, naam):
    return db.execute(
        "INSERT INTO klanten (naam, email) VALUES (?, ?)",
        (naam, f"{naam.lower()}@example.com"),
    ).lastrowid


def _klus_met_uren(post, db, naam, klant_id=None):
    gegevens = {"naam": naam, "uurtarief": "40"}
    if klant_id:
        gegevens["klant_id"] = str(klant_id)
    post("/klussen/nieuw", gegevens)
    klus_id = db.execute("SELECT id FROM klussen WHERE naam=?", (naam,)).fetchone()[0]
    post(f"/klus/{klus_id}/dag", {"datum": "2026-08-14", "van": "09:00", "tot": "12:00"})
    return klus_id


def _knop(html, naam):
    plek = html.find(f'data-naam="{naam}"')
    assert plek != -1, naam
    begin = html.rfind("<button", 0, plek)
    einde = html.find(">", plek)
    return html[begin:einde]


def _menu_kop(html):
    return html.split('id="klus-menu"', 1)[1].split(">", 1)[0]


def test_geboekte_klussen_markeert_wie_bij_de_klant_hoort(post, db):
    anna = _klant(db, "Anna")
    piet = _klant(db, "Piet")
    db.commit()
    van_anna = _klus_met_uren(post, db, "Keuken Anna", anna)
    van_piet = _klus_met_uren(post, db, "Dak Piet", piet)
    los = _klus_met_uren(post, db, "Losse klus")

    bij_anna = {k["id"]: k for k in facturen.geboekte_klussen(klant_id=anna)}
    assert bij_anna[van_anna]["past"] is True
    assert bij_anna[van_piet]["past"] is False
    assert bij_anna[los]["past"] is False

    # Zonder klant op de rekening alleen klussen die ook geen klant hebben.
    zonder = {k["id"]: k for k in facturen.geboekte_klussen()}
    assert zonder[los]["past"] is True
    assert zonder[van_anna]["past"] is False
    assert zonder[van_piet]["past"] is False


def test_uren_op_deze_rekening_blijven_passen_bij_haar_klant(post, db):
    """Bij bewerken horen de uren die er al op staan weer in de lijst, mét past."""
    anna = _klant(db, "Anna")
    piet = _klant(db, "Piet")
    db.commit()
    klus_id = _klus_met_uren(post, db, "Keuken Anna", anna)
    post("/nieuw", {
        "klant_id": str(anna), "klant_naam": "Anna", "datum": "2026-08-14",
        "omschrijving": "Uren keuken", "type": "arbeid_uur", "aantal": "3",
        "prijs": "40", "regel_klus": str(klus_id),
    })
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]

    assert facturen.geboekte_klussen(klant_id=anna) == []
    terug = facturen.geboekte_klussen(factuur_id, anna)
    assert [k["id"] for k in terug] == [klus_id]
    assert terug[0]["past"] is True
    assert facturen.geboekte_klussen(factuur_id, piet)[0]["past"] is False


def test_rekening_voor_klant_toont_alleen_diens_uren(post, db, client):
    anna = _klant(db, "Anna")
    piet = _klant(db, "Piet")
    db.commit()
    _klus_met_uren(post, db, "Keuken Anna", anna)
    _klus_met_uren(post, db, "Dak Piet", piet)
    _klus_met_uren(post, db, "Losse klus")

    post("/nieuw", {
        "klant_id": str(anna), "klant_naam": "Anna", "datum": "2026-08-14",
        "omschrijving": "Werk", "type": "arbeid_klus", "aantal": "1", "prijs": "10",
    })
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    pagina = client.get(f"/factuur/{factuur_id}/bewerk").data.decode()

    assert "hidden" not in _knop(pagina, "Keuken Anna")
    assert "hidden" in _knop(pagina, "Dak Piet")
    assert "hidden" in _knop(pagina, "Losse klus")
    # Piet blijft in de pagina, met zijn klant, zodat een wissel hem kan tonen.
    assert f'data-klant="{piet}"' in _knop(pagina, "Dak Piet")
    assert 'id="klus-menu"' in pagina
    assert "hidden" not in _menu_kop(pagina)


def test_nieuwe_rekening_voor_een_klant_filtert_uren_meteen(post, db, client):
    anna = _klant(db, "Anna")
    piet = _klant(db, "Piet")
    db.commit()
    _klus_met_uren(post, db, "Keuken Anna", anna)
    _klus_met_uren(post, db, "Dak Piet", piet)

    pagina = client.get(f"/nieuw?klant={anna}").data.decode()
    assert "hidden" not in _knop(pagina, "Keuken Anna")
    assert "hidden" in _knop(pagina, "Dak Piet")


def test_zonder_klant_alleen_uren_van_klussen_zonder_klant(post, db, client):
    anna = _klant(db, "Anna")
    db.commit()
    _klus_met_uren(post, db, "Keuken Anna", anna)
    _klus_met_uren(post, db, "Losse klus")

    pagina = client.get("/nieuw").data.decode()
    assert "hidden" in _knop(pagina, "Keuken Anna")
    assert "hidden" not in _knop(pagina, "Losse klus")
    assert "hidden" not in _menu_kop(pagina)


def test_urenmenu_verdwijnt_als_geen_enkele_klus_past(post, db, client):
    anna = _klant(db, "Anna")
    db.commit()
    _klus_met_uren(post, db, "Keuken Anna", anna)

    pagina = client.get("/nieuw").data.decode()
    assert "hidden" in _menu_kop(pagina)
    # De knop blijft, anders kan kiezen van Anna haar uren niet terughalen.
    assert "hidden" in _knop(pagina, "Keuken Anna")


def test_een_klantwissel_filtert_de_uren_opnieuw(client):
    """De lijst wordt bij een andere klant opnieuw gezet, met dezelfde regel als
    de bonnen. Zonder die aanroep blijft de lijst van het laden staan."""
    pagina = client.get("/nieuw").data.decode()
    wijzig = pagina.split("klantKeuze.addEventListener('change'", 1)[1].split("});", 1)[0]
    assert "pasKlantAan(true)" in wijzig

    klant_fn = pagina.split("function pasKlantAan", 1)[1].split("function bonPastBijKlant", 1)[0]
    assert "pasKlussenAan();" in klant_fn

    lijst = pagina.split("function pasLijstAan", 1)[1].split("function pasBonnenAan", 1)[0]
    assert "bonPastBijKlant" in lijst
    assert "knop.hidden = !past" in lijst
    assert "menu.hidden = zichtbaar === 0" in lijst

    klus_fn = pagina.split("function pasKlussenAan()", 1)[1].split("document.getElementById('klant-bewerken')", 1)[0]
    assert "'.klus-keuze'" in klus_fn
    assert "'klus-menu'" in klus_fn

    # .menu-item zet display:block en dat wint van het hidden-attribuut.
    assert ".klus-keuze[hidden]" in pagina
    assert "#klus-menu[hidden]" in pagina
    assert "display: none" in pagina.split(".klus-keuze[hidden]", 1)[1].split("}", 1)[0]
