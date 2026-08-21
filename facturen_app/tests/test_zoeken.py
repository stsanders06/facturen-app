"""Zoeken en filteren op de lijstpagina's."""
from conftest import facturen


def test_zoeken_op_naam(db, client, maak_factuur):
    maak_factuur(nummer="2026-001", klant="Jan Jansen")
    maak_factuur(nummer="2026-002", klant="Piet Peters")

    inhoud = client.get("/?q=jansen").data.decode()
    assert "Jan Jansen" in inhoud
    assert "Piet Peters" not in inhoud


def test_zoeken_let_niet_op_hoofdletters(db, client, maak_factuur):
    maak_factuur(klant="Jan Jansen")
    assert b"Jan Jansen" in client.get("/?q=JANSEN").data


def test_zoeken_op_nummer(db, client, maak_factuur):
    maak_factuur(nummer="2026-001", klant="Jan Jansen")
    maak_factuur(nummer="2026-002", klant="Piet Peters")
    inhoud = client.get("/?q=2026-002").data.decode()
    assert "Piet Peters" in inhoud
    assert "Jan Jansen" not in inhoud


def test_losse_woorden_mogen_uit_verschillende_velden_komen(db, client, maak_factuur):
    maak_factuur(nummer="2026-007", klant="Jan Jansen")
    maak_factuur(nummer="2026-008", klant="Piet Peters")
    inhoud = client.get("/?q=jansen+2026-007").data.decode()
    assert "Jan Jansen" in inhoud
    assert "Piet Peters" not in inhoud


def test_niets_gevonden_zegt_waarnaar_je_zocht(db, client, maak_factuur):
    maak_factuur(klant="Jan Jansen")
    assert "Niets gevonden voor" in client.get("/?q=zzzz").data.decode()


def test_zoeken_blijft_binnen_het_gekozen_filter(db, client, maak_factuur):
    maak_factuur(nummer="2026-001", klant="Jan Jansen", status="betaald")
    maak_factuur(nummer="2026-002", klant="Jan Jansen", status="verzonden")
    inhoud = client.get("/?status=betaald&q=jansen").data.decode()
    assert "2026-001" in inhoud
    assert "2026-002" not in inhoud


def test_zoeken_op_de_offertepagina(db, client, maak_offerte):
    maak_offerte(nummer="OFF-2026-001", klant="Jan Jansen")
    maak_offerte(nummer="OFF-2026-002", klant="Piet Peters")
    inhoud = client.get("/offertes?q=peters").data.decode()
    assert "Piet Peters" in inhoud
    assert "Jan Jansen" not in inhoud


def test_zoeken_op_de_klantenpagina(db, client):
    db.execute("INSERT INTO klanten (naam, email) VALUES ('Jan Jansen', 'jan@example.com')")
    db.execute("INSERT INTO klanten (naam, email) VALUES ('Piet Peters', 'piet@example.com')")
    db.commit()
    inhoud = client.get("/klanten?q=piet@example.com").data.decode()
    assert "Piet Peters" in inhoud
    assert "Jan Jansen" not in inhoud


def test_zonder_zoekterm_blijft_alles_staan(db, client, maak_factuur):
    maak_factuur(nummer="2026-001", klant="Jan Jansen")
    maak_factuur(nummer="2026-002", klant="Piet Peters")
    inhoud = client.get("/?q=").data.decode()
    assert "Jan Jansen" in inhoud and "Piet Peters" in inhoud


def test_zoek_in_werkt_op_losse_rijen(db):
    rijen = [{"naam": "Jan Jansen", "email": "jan@example.com"},
             {"naam": "Piet Peters", "email": "piet@example.com"}]
    assert facturen.zoek_in(rijen, "jan", ["naam", "email"]) == [rijen[0]]
    assert facturen.zoek_in(rijen, "", ["naam"]) == rijen
    assert facturen.zoek_in(rijen, "zzz", ["naam"]) == []
