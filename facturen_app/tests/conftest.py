"""Gemeenschappelijke opzet voor de tests.

De app leest DATA_DIR bij het importeren en maakt daar meteen de database aan, dus
die moet vóór de import naar een tijdelijke map wijzen. Anders schrijven de tests
in de echte /data van de add-on.
"""
import os
import pathlib
import sys
import tempfile

import pytest

APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(APP_DIR))

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="facturen-tests-")
os.environ["SECRET_KEY"] = "sleutel-voor-de-tests"

import main as facturen  # noqa: E402  (moet ná het zetten van DATA_DIR)

# Tabellen in de volgorde waarin ze leeg mogen: eerst wat naar iets anders verwijst.
TABELLEN = [
    "regels", "offerte_regels", "uren", "betalingen", "bijlagen", "facturen",
    "offertes", "klussen", "klanten",
]


@pytest.fixture
def app():
    """De Flask-app, met na afloop een schone database."""
    facturen.app.config["TESTING"] = True
    yield facturen.app

    conn = facturen.get_db()
    for tabel in TABELLEN:
        conn.execute(f"DELETE FROM {tabel}")
    # De hele instellingenrij terug naar leeg. Alleen een paar velden wissen liet
    # bijvoorbeeld een ingestelde mailserver naar de volgende test lekken.
    conn.execute("DELETE FROM settings")
    conn.execute("INSERT INTO settings (id) VALUES (1)")
    conn.commit()
    conn.close()
    for pdf in pathlib.Path(facturen.PDF_DIR).glob("*.pdf"):
        pdf.unlink()
    for bestand in pathlib.Path(facturen.BIJLAGE_DIR).iterdir():
        bestand.unlink()


@pytest.fixture
def client(app):
    """Testbrowser met een geldig CSRF-kenmerk in de sessie, zodat POSTs erdoor komen."""
    with app.test_client() as c:
        with c.session_transaction() as sessie:
            sessie["csrf_token"] = "test-token"
        yield c


@pytest.fixture
def post(client):
    """POST met het CSRF-kenmerk er automatisch bij."""
    def _post(url, data=None, **kwargs):
        gegevens = dict(data or {})
        gegevens.setdefault("csrf_token", "test-token")
        return client.post(url, data=gegevens, **kwargs)
    return _post


@pytest.fixture
def db(app):
    """Open verbinding met de testdatabase."""
    conn = facturen.get_db()
    yield conn
    conn.commit()
    conn.close()


@pytest.fixture
def maak_factuur(db):
    """Zet een rekening met één regel in de database en geeft het id terug."""
    def _maak(nummer="2026-001", datum="2026-08-14", klant="Jan Jansen", totaal=121.0,
              status="concept", email="jan@example.com"):
        factuur_id = db.execute(
            """INSERT INTO facturen (nummer, datum, klant_naam, klant_adres, klant_email,
               status, totaal) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (nummer, datum, klant, "Kerkstraat 1\n5900 AA Venlo", email, status, totaal),
        ).lastrowid
        db.execute(
            """INSERT INTO regels (factuur_id, omschrijving, type, aantal, prijs, subtotaal)
               VALUES (?, 'Kraan vervangen', 'arbeid_uur', 2, 60.5, 121.0)""",
            (factuur_id,),
        )
        db.commit()
        return factuur_id
    return _maak


@pytest.fixture
def maak_offerte(db):
    """Zet een offerte met één regel in de database en geeft het id terug."""
    def _maak(nummer="OFF-2026-001", datum="2026-08-14", klant="Jan Jansen",
              totaal=250.0, status="concept", geldig="2026-09-13"):
        offerte_id = db.execute(
            """INSERT INTO offertes (nummer, datum, geldig_tot, klant_naam, klant_adres,
               klant_email, status, totaal, toelichting)
               VALUES (?, ?, ?, ?, '', 'jan@example.com', ?, ?, 'Inclusief materiaal.')""",
            (nummer, datum, geldig, klant, status, totaal),
        ).lastrowid
        db.execute(
            """INSERT INTO offerte_regels (offerte_id, omschrijving, type, aantal, prijs,
               subtotaal) VALUES (?, 'Badkamer betegelen', 'arbeid_klus', 1, 250.0, 250.0)""",
            (offerte_id,),
        )
        db.commit()
        return offerte_id
    return _maak
