"""Suggesties tijdens het typen van een adres.

Twee bronnen: adressen die je al eens hebt ingevoerd, en de adressenlijst van het
Kadaster (PDOK). Die laatste wordt hier nagebootst — de tests horen niet van een
internetverbinding af te hangen, en al helemaal niet bij elke keer draaien een
externe server te bevragen.
"""
import json
import urllib.error

import pytest

from conftest import facturen


@pytest.fixture
def kadaster(monkeypatch):
    """Doet zich voor als PDOK en onthoudt waar het naar gevraagd is."""
    gevraagd = []

    def nep(term, aantal=5):
        gevraagd.append(term)
        return ["Voorbeeldstraat 1\n1234 AB Voorbeeldstad",
                "Voorbeeldstraat 21\n1234 AC Voorbeeldstad"]

    monkeypatch.setattr(facturen, "zoek_adressen", nep)
    return gevraagd


def test_er_komt_niets_terug_bij_een_te_korte_term(client, kadaster):
    assert client.get("/adressen?q=na").get_json() == {"suggesties": []}
    assert kadaster == []


def test_de_officiele_adressen_komen_terug(client, kadaster):
    suggesties = client.get("/adressen?q=voorbeeldstraat 1").get_json()["suggesties"]
    assert [s["adres"] for s in suggesties] == [
        "Voorbeeldstraat 1\n1234 AB Voorbeeldstad",
        "Voorbeeldstraat 21\n1234 AC Voorbeeldstad",
    ]


def test_een_eigen_klant_staat_bovenaan(client, db, kadaster):
    """Een adres dat je al eens hebt ingevoerd is meestal degene die je bedoelt."""
    db.execute("""INSERT INTO klanten (naam, adres)
                  VALUES ('Voorbeeld Bakkerij', 'Voorbeeldstraat 9
1234 AA Voorbeeldstad')""")
    db.commit()

    suggesties = client.get("/adressen?q=voorbeeldstraat").get_json()["suggesties"]
    assert suggesties[0]["bron"] == "Voorbeeld Bakkerij"
    assert suggesties[0]["adres"].startswith("Voorbeeldstraat 9")
    assert suggesties[1]["bron"] == ""


def test_je_kunt_ook_op_de_klantnaam_zoeken(client, db, kadaster):
    db.execute("""INSERT INTO klanten (naam, adres)
                  VALUES ('Voorbeeld Bakkerij', 'Voorbeeldstraat 14
1234 AB Voorbeeldstad')""")
    db.commit()

    suggesties = client.get("/adressen?q=bakkerij").get_json()["suggesties"]
    assert suggesties[0]["adres"] == "Voorbeeldstraat 14\n1234 AB Voorbeeldstad"


def test_twee_klanten_op_hetzelfde_adres_geven_een_regel(client, db, kadaster):
    """Anders staan er twee regels die er precies hetzelfde uitzien."""
    for naam in ("Voorbeeld Bakkerij", "Voorbeeld Kapper"):
        db.execute("INSERT INTO klanten (naam, adres) VALUES (?, 'Voorbeeldstraat 14\n1234 AB Voorbeeldstad')",
                   (naam,))
    db.commit()

    suggesties = client.get("/adressen?q=voorbeeldstraat").get_json()["suggesties"]
    eigen = [s for s in suggesties if s["bron"]]
    assert len(eigen) == 1


def test_een_klant_zonder_adres_levert_geen_lege_suggestie(client, db, kadaster):
    db.execute("INSERT INTO klanten (naam, adres) VALUES ('Zonder adres', '')")
    db.commit()

    suggesties = client.get("/adressen?q=zonder adres").get_json()["suggesties"]
    assert all(s["adres"] for s in suggesties)


def test_zonder_internet_krijg_je_gewoon_je_eigen_klanten(client, db, monkeypatch):
    """Suggesties zijn een gemak; zonder moet je gewoon door kunnen typen."""
    def stuk(*_args, **_kwargs):
        raise urllib.error.URLError("geen netwerk")

    monkeypatch.setattr(facturen, "urlopen", stuk)
    db.execute("""INSERT INTO klanten (naam, adres)
                  VALUES ('Voorbeeld Bakkerij', 'Voorbeeldstraat 14
1234 AB Voorbeeldstad')""")
    db.commit()

    antwoord = client.get("/adressen?q=voorbeeldstraat")
    assert antwoord.status_code == 200
    assert antwoord.get_json()["suggesties"][0]["bron"] == "Voorbeeld Bakkerij"


def test_dezelfde_term_wordt_maar_een_keer_opgezocht(app, monkeypatch):
    """Tijdens het typen komt dezelfde beginletterreeks steeds terug; dat hoeft niet
    elke keer een verzoek naar buiten te zijn."""
    keren = []

    class NepAntwoord:
        def read(self):
            keren.append(1)
            return json.dumps({"response": {"docs": [
                {"weergavenaam": "Voorbeeldstraat 14, 1234AB Voorbeeldstad"}]}}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(facturen, "urlopen", lambda *a, **k: NepAntwoord())

    eerst = facturen.zoek_adressen("voorbeeldstraat 14")
    daarna = facturen.zoek_adressen("voorbeeldstraat 14")
    assert eerst == daarna == ["Voorbeeldstraat 14\n1234 AB Voorbeeldstad"]
    assert len(keren) == 1


def test_een_antwoord_dat_nergens_op_slaat_laat_de_app_niet_klappen(app, monkeypatch):
    class Rommel:
        def read(self):
            return b"<html>foutpagina</html>"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(facturen, "urlopen", lambda *a, **k: Rommel())
    assert facturen.zoek_adressen("voorbeeldstraat 14") == []
