"""De gaten uit de doorloop van 1.29.1 blijven dicht.

Elk punt hier is iets dat de app stilletjes verkeerd deed: uren op de verkeerde
klant, alle dagen afvinken voor nul euro, een nummer weggeven aan een mail die
niet wegging, of een aanbetaling die de rest niet aftrekt.
"""
import io
import os
import pathlib
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

from conftest import facturen, TEST_GEBRUIKER, TEST_WACHTWOORD
from test_beveiliging import ECHTE_ZIJBALK, NAGEMAAKT
from test_pdf import tekst_uit_pdf, zet_bedrijf

ROOT = pathlib.Path(__file__).resolve().parents[2]

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


def _klant(db, naam):
    return db.execute(
        "INSERT INTO klanten (naam, email) VALUES (?, ?)",
        (naam, f"{naam.lower()}@example.com"),
    ).lastrowid


def _klus(post, db, naam, klant_id=None, tarief="40"):
    gegevens = {"naam": naam, "uurtarief": tarief}
    if klant_id:
        gegevens["klant_id"] = str(klant_id)
    post("/klussen/nieuw", gegevens)
    return db.execute("SELECT id FROM klussen WHERE naam=?", (naam,)).fetchone()[0]


def _dag(post, klus_id, datum, van, tot):
    post(f"/klus/{klus_id}/dag", {"datum": datum, "van": van, "tot": tot})


def _rekening(post, klant_id, klant_naam, klus_id, uren, prijs, extra=None):
    gegevens = {
        "klant_id": str(klant_id),
        "klant_naam": klant_naam,
        "klant_email": "jan@example.com",
        "datum": "2026-08-14",
        "omschrijving": "Uren",
        "type": "arbeid_uur",
        "aantal": str(uren),
        "prijs": str(prijs),
        "regel_klus": str(klus_id),
    }
    if extra:
        gegevens.update(extra)
    return post("/nieuw", gegevens, follow_redirects=True)


def _gekoppeld(db, klus_id):
    return [
        rij["id"] for rij in db.execute(
            "SELECT id FROM uren WHERE klus_id=? AND factuur_id IS NOT NULL ORDER BY datum, id",
            (klus_id,),
        )
    ]


def _tekst(pad_of_bytes):
    if isinstance(pad_of_bytes, (bytes, bytearray)):
        fd, pad = tempfile.mkstemp(suffix=".pdf")
        os.write(fd, pad_of_bytes)
        os.close(fd)
        try:
            return tekst_uit_pdf(pad)
        finally:
            os.remove(pad)
    return tekst_uit_pdf(pad_of_bytes)


def _mailserver(db):
    db.execute(
        "UPDATE settings SET smtp_host='smtp.example.com', naam='Jansen' WHERE id=1"
    )
    db.commit()


def _vang_mail(monkeypatch):
    opgevangen = {}

    def nep_mail(s, ontvanger, onderwerp, tekst, pad, bestandsnaam, extra=None):
        opgevangen.update(
            ontvanger=ontvanger, onderwerp=onderwerp, tekst=tekst,
            pad=pad, bestandsnaam=bestandsnaam, extra=extra or [],
            pdf=_tekst(pad) if pad and os.path.exists(pad) else "",
        )
        return True, ""

    monkeypatch.setattr(facturen, "_mail_pdf", nep_mail)
    return opgevangen


# ---------- P1.1 uren en bonnen horen bij de klant van de rekening ----------

def test_post_met_uren_van_een_andere_klant_wordt_geweigerd(post, db):
    """Het menu verbergt andermans uren. Een nagemaakt formulier mag ze niet
    alsnog op deze rekening zetten."""
    anna = _klant(db, "Anna")
    piet = _klant(db, "Piet")
    db.commit()
    van_piet = _klus(post, db, "Dak Piet", piet)
    _dag(post, van_piet, "2026-08-14", "09:00", "12:00")

    antwoord = _rekening(post, anna, "Anna", van_piet, 3, 40)

    assert "andere klant" in antwoord.data.decode()
    assert db.execute("SELECT COUNT(*) FROM facturen").fetchone()[0] == 0
    assert _gekoppeld(db, van_piet) == []


def test_post_met_een_bon_van_een_andere_klant_wordt_geweigerd(post, db):
    anna = _klant(db, "Anna")
    piet = _klant(db, "Piet")
    db.commit()
    van_piet = _klus(post, db, "Dak Piet", piet)
    inkoop_id = db.execute(
        """INSERT INTO inkopen (klus_id, omschrijving, bedrag, toegevoegd)
           VALUES (?, 'Tegels', 80, '2026-08-14')""",
        (van_piet,),
    ).lastrowid
    db.commit()

    antwoord = post("/nieuw", {
        "klant_id": str(anna), "klant_naam": "Anna", "datum": "2026-08-14",
        "omschrijving": "Bon", "type": "bon", "aantal": "1", "prijs": "80",
        "regel_inkoop": str(inkoop_id),
    }, follow_redirects=True)

    assert "andere klant" in antwoord.data.decode()
    assert db.execute("SELECT COUNT(*) FROM facturen").fetchone()[0] == 0
    assert db.execute("SELECT factuur_id FROM inkopen").fetchone()[0] is None


def test_bewerken_met_de_verkeerde_klus_laat_de_rekening_met_rust(post, db):
    anna = _klant(db, "Anna")
    piet = _klant(db, "Piet")
    db.commit()
    van_anna = _klus(post, db, "Keuken Anna", anna)
    van_piet = _klus(post, db, "Dak Piet", piet)
    _dag(post, van_anna, "2026-08-14", "09:00", "12:00")
    _rekening(post, anna, "Anna", van_anna, 3, 40)
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]

    antwoord = post(f"/factuur/{factuur_id}/bewerk", {
        "klant_id": str(anna), "klant_naam": "Anna", "datum": "2026-08-14",
        "omschrijving": "Uren van Piet", "type": "arbeid_uur",
        "aantal": "3", "prijs": "40", "regel_klus": str(van_piet),
    }, follow_redirects=True)

    assert "andere klant" in antwoord.data.decode()
    klus_op_regel = db.execute(
        "SELECT klus_id FROM regels WHERE factuur_id=?", (factuur_id,)
    ).fetchone()[0]
    assert klus_op_regel == van_anna


# ---------- P1.2 alleen de dagen boeken die in de regel passen ----------

def test_een_deel_van_de_dagen_blijft_open(post, db):
    """Hele dagen, oudste eerst. Een dag die niet meer past blijft factureerbaar."""
    anna = _klant(db, "Anna")
    db.commit()
    klus_id = _klus(post, db, "Keuken", anna)
    _dag(post, klus_id, "2026-08-10", "08:00", "12:00")
    _dag(post, klus_id, "2026-08-11", "08:00", "12:00")

    antwoord = _rekening(post, anna, "Anna", klus_id, 4, 40)

    geboekt = db.execute(
        """SELECT datum FROM uren WHERE klus_id=? AND factuur_id IS NOT NULL
           ORDER BY datum""",
        (klus_id,),
    ).fetchall()
    open_dagen = db.execute(
        "SELECT datum FROM uren WHERE klus_id=? AND factuur_id IS NULL",
        (klus_id,),
    ).fetchall()
    assert [rij["datum"] for rij in geboekt] == ["2026-08-10"]
    assert [rij["datum"] for rij in open_dagen] == ["2026-08-11"]
    assert "blijft" in antwoord.data.decode()


def test_een_dag_die_niet_in_de_regel_past_blijft_helemaal_open(post, db):
    """Een dag van 8 uur splitsen we niet. Liever opnieuw factureerbaar dan
    half afgevinkt."""
    anna = _klant(db, "Anna")
    db.commit()
    klus_id = _klus(post, db, "Keuken", anna)
    _dag(post, klus_id, "2026-08-10", "08:00", "16:00")

    antwoord = _rekening(post, anna, "Anna", klus_id, 4, 40)

    assert _gekoppeld(db, klus_id) == []
    assert "passen niet" in antwoord.data.decode()


def test_nul_euro_markeert_geen_enkele_dag(post, db):
    anna = _klant(db, "Anna")
    db.commit()
    klus_id = _klus(post, db, "Keuken", anna)
    _dag(post, klus_id, "2026-08-10", "08:00", "16:00")
    _dag(post, klus_id, "2026-08-11", "08:00", "12:00")

    antwoord = _rekening(post, anna, "Anna", klus_id, 12, 0)

    assert _gekoppeld(db, klus_id) == []
    tekst = antwoord.data.decode()
    assert "nul" in tekst
    assert "open" in tekst


def test_passen_alle_uren_dan_gaan_alle_dagen_mee(post, db):
    anna = _klant(db, "Anna")
    db.commit()
    klus_id = _klus(post, db, "Keuken", anna)
    _dag(post, klus_id, "2026-08-10", "08:00", "12:00")
    _dag(post, klus_id, "2026-08-11", "13:00", "17:00")

    _rekening(post, anna, "Anna", klus_id, 8, 40)

    assert db.execute(
        "SELECT COUNT(*) FROM uren WHERE klus_id=? AND factuur_id IS NULL",
        (klus_id,),
    ).fetchone()[0] == 0


# ---------- P1.3 prullenbak koppelt uren en bonnen terug ----------

def test_terugzetten_koppelt_de_geboekte_uren_weer(post, db):
    anna = _klant(db, "Anna")
    db.commit()
    klus_id = _klus(post, db, "Keuken", anna)
    _dag(post, klus_id, "2026-08-10", "08:00", "12:00")
    _dag(post, klus_id, "2026-08-11", "08:00", "12:00")
    _rekening(post, anna, "Anna", klus_id, 4, 40)
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    geboekt = _gekoppeld(db, klus_id)
    assert len(geboekt) == 1

    post(f"/factuur/{factuur_id}/verwijder")
    assert _gekoppeld(db, klus_id) == []

    prullenbak_id = db.execute("SELECT id FROM prullenbak ORDER BY id DESC").fetchone()[0]
    post(f"/prullenbak/{prullenbak_id}/terug")

    assert _gekoppeld(db, klus_id) == geboekt
    assert db.execute(
        "SELECT datum FROM uren WHERE factuur_id IS NULL"
    ).fetchone()["datum"] == "2026-08-11"


def test_terugzetten_pakt_uren_niet_af_van_een_nieuwere_rekening(post, db):
    anna = _klant(db, "Anna")
    db.commit()
    klus_id = _klus(post, db, "Keuken", anna)
    _dag(post, klus_id, "2026-08-10", "08:00", "12:00")
    _rekening(post, anna, "Anna", klus_id, 4, 40)
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    post(f"/factuur/{factuur_id}/verwijder")

    # Intussen op een andere rekening gezet. Ongedaan maken mag die niet leeghalen.
    andere = db.execute(
        """INSERT INTO facturen (nummer, datum, klant_naam, status, totaal)
           VALUES ('2026-009', '2026-08-20', 'Anna', 'concept', 160)"""
    ).lastrowid
    db.execute("UPDATE uren SET factuur_id=? WHERE klus_id=?", (andere, klus_id))
    db.commit()

    prullenbak_id = db.execute("SELECT id FROM prullenbak ORDER BY id DESC").fetchone()[0]
    antwoord = post(f"/prullenbak/{prullenbak_id}/terug", follow_redirects=True)

    assert db.execute("SELECT factuur_id FROM uren").fetchone()[0] == andere
    assert "andere rekening" in antwoord.data.decode()


def test_terugzetten_koppelt_de_bon_weer(post, db):
    klus_id = _klus(post, db, "Badkamer")
    inkoop_id = db.execute(
        """INSERT INTO inkopen (klus_id, omschrijving, bedrag, toegevoegd)
           VALUES (?, 'Kit', 12, '2026-08-14')""",
        (klus_id,),
    ).lastrowid
    db.commit()
    post("/nieuw", {
        "klant_naam": "Jan", "datum": "2026-08-14",
        "omschrijving": "Kit", "type": "bon", "aantal": "1", "prijs": "12",
        "regel_inkoop": str(inkoop_id),
    })
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    assert db.execute("SELECT factuur_id FROM inkopen").fetchone()[0] == factuur_id

    post(f"/factuur/{factuur_id}/verwijder")
    assert db.execute("SELECT factuur_id FROM inkopen").fetchone()[0] is None

    prullenbak_id = db.execute("SELECT id FROM prullenbak ORDER BY id DESC").fetchone()[0]
    post(f"/prullenbak/{prullenbak_id}/terug")
    assert db.execute("SELECT factuur_id FROM inkopen").fetchone()[0] == factuur_id


# ---------- P1.4 PDF opnieuw vóór de mail en bij betaald ----------

def test_de_mail_stuurt_de_pdf_van_nu_niet_van_gisteren(monkeypatch, post, db, maak_factuur):
    opgevangen = _vang_mail(monkeypatch)
    _mailserver(db)
    zet_bedrijf(db, iban="NL00OUD0000000000")
    factuur_id = maak_factuur(nummer="", status="concept")
    facturen.maak_pdf(factuur_id)

    zet_bedrijf(db, iban="NL99NIEUW000000000")
    post(f"/factuur/{factuur_id}/verstuur")

    assert "NL99NIEUW000000000" in opgevangen["pdf"]
    assert "NL00OUD0000000000" not in opgevangen["pdf"]


def test_betaald_tekent_de_pdf_opnieuw(post, db, maak_factuur):
    zet_bedrijf(db)
    factuur_id = maak_factuur(nummer="2026-001", status="verzonden", totaal=121.0)
    facturen.maak_pdf(factuur_id)
    vooraf = _tekst(facturen.maak_pdf(factuur_id))
    assert "Al betaald" not in vooraf

    post(f"/factuur/{factuur_id}/betaald")
    pad = os.path.join(facturen.PDF_DIR, "2026-001.pdf")
    assert "Al betaald" in _tekst(pad)

    post(f"/factuur/{factuur_id}/niet-betaald")
    assert "Al betaald" not in _tekst(pad)


# ---------- P1.5 en P1.6 nummer pas na een gelukte mail ----------

def test_mislukte_smtp_geeft_het_nummer_terug(monkeypatch, db, post, maak_factuur):
    """Tijdens het versturen heeft de rekening al een nummer, anders kan de PDF
    hem niet noemen. Weigert de server, dan is dat nummer weer vrij."""
    gezien = {}

    def weigert(s, ontvanger, onderwerp, tekst, pad, bestandsnaam, extra=None):
        conn = facturen.get_db()
        gezien["nummer"] = conn.execute(
            "SELECT nummer FROM facturen"
        ).fetchone()[0]
        gezien["pdf"] = _tekst(pad)
        conn.close()
        return False, "De mailserver weigert je gebruikersnaam of wachtwoord."

    monkeypatch.setattr(facturen, "_mail_pdf", weigert)
    _mailserver(db)
    factuur_id = maak_factuur(nummer="", status="concept")
    antwoord = post(f"/factuur/{factuur_id}/verstuur", follow_redirects=True)

    factuur = db.execute("SELECT nummer, status FROM facturen").fetchone()
    assert gezien["nummer"] != ""
    assert "CONCEPT" not in gezien["pdf"]
    assert factuur["nummer"] == ""
    assert factuur["status"] == "concept"
    assert facturen.volgend_nummer(db) == f"{facturen.vandaag().year}-001"
    assert "weigert" in antwoord.data.decode()


def test_een_rekening_die_al_een_nummer_had_houdt_het_bij_een_mislukte_mail(
        monkeypatch, db, post, maak_factuur):
    monkeypatch.setattr(
        facturen, "_mail_pdf",
        lambda *a, **k: (False, "De mailserver weigert het adres jan@example.com."),
    )
    _mailserver(db)
    factuur_id = maak_factuur(nummer="2026-007", status="verzonden")
    post(f"/factuur/{factuur_id}/verstuur")
    factuur = db.execute("SELECT nummer, status FROM facturen").fetchone()
    assert factuur["nummer"] == "2026-007"
    assert factuur["status"] == "verzonden"


def test_het_mailvoorbeeld_toont_het_voorlopige_nummer_zonder_het_op_te_slaan(
        client, db, maak_factuur):
    _mailserver(db)
    zet_bedrijf(db)
    factuur_id = maak_factuur(nummer="", status="concept")
    jaar = facturen.vandaag().year
    pagina = client.get(f"/factuur/{factuur_id}/mail").data.decode()
    assert f"{jaar}-001" in pagina
    assert "voorlopig" in pagina
    assert db.execute("SELECT nummer FROM facturen").fetchone()[0] == ""

    pdf = client.get(f"/factuur/{factuur_id}/mail-pdf")
    tekst = _tekst(pdf.data)
    assert f"{jaar}-001" in tekst
    assert "CONCEPT" not in tekst
    assert db.execute("SELECT nummer FROM facturen").fetchone()[0] == ""
    # De bewaarde concept-PDF zegt nog CONCEPT; alleen het voorbeeld niet.
    concept = _tekst(facturen.maak_pdf(factuur_id))
    assert "CONCEPT" in concept


def test_eigen_zin_komt_na_de_aanhef_en_houdt_de_standaardtekst(
        monkeypatch, post, db, maak_factuur):
    opgevangen = _vang_mail(monkeypatch)
    _mailserver(db)
    factuur_id = maak_factuur(nummer="", status="concept", klant="Jan Jansen")
    post(f"/factuur/{factuur_id}/verstuur", {"eigen_zin": "Tot volgende week."})

    tekst = opgevangen["tekst"]
    assert tekst.startswith("Beste Jan Jansen,")
    assert tekst.index("Tot volgende week.") < tekst.index("Hierbij de rekening")
    assert "Met vriendelijke groet" in tekst


# ---------- P1.7 offerte en herinnering via hetzelfde scherm ----------

def test_offerte_mailen_opent_eerst_het_controlescherm(client, db, maak_offerte):
    _mailserver(db)
    offerte_id = maak_offerte()
    lijst = client.get("/offertes").data.decode()
    assert f'href="/offerte/{offerte_id}/mail"' in lijst
    assert f'action="/offerte/{offerte_id}/verstuur"' not in lijst

    pagina = client.get(f"/offerte/{offerte_id}/mail").data.decode()
    assert "Mail controleren" in pagina
    assert "Offerte OFF-2026-001" in pagina
    assert "Hierbij de offerte (OFF-2026-001)" in pagina
    assert 'name="eigen_zin"' in pagina
    assert f'action="/offerte/{offerte_id}/verstuur"' in pagina
    assert 'name="verstuur"' not in pagina


def test_offerte_opslaan_stuurt_geen_mail(monkeypatch, post, db):
    verstuurd = []
    monkeypatch.setattr(
        facturen, "_mail_pdf", lambda *a, **k: verstuurd.append(a) or (True, "")
    )
    _mailserver(db)
    post("/offertes/nieuw", {
        "klant_naam": "Jan", "klant_email": "jan@example.com", "datum": "2026-08-14",
        "omschrijving": "Tegelwerk", "type": "arbeid_klus", "aantal": "1", "prijs": "250",
        "verstuur": "ja",
    })
    assert verstuurd == []
    assert db.execute("SELECT status FROM offertes").fetchone()[0] == "concept"


def test_herinnering_opent_eerst_het_controlescherm(client, db, maak_factuur):
    _mailserver(db)
    factuur_id = maak_factuur(nummer="2026-004", status="verzonden")
    lijst = client.get("/").data.decode()
    assert f'href="/factuur/{factuur_id}/herinnering"' in lijst

    pagina = client.get(f"/factuur/{factuur_id}/herinnering").data.decode()
    assert "Mail controleren" in pagina
    assert "Herinnering: rekening 2026-004" in pagina
    assert "staat nog open" in pagina
    assert 'name="eigen_zin"' in pagina
    assert f'action="/factuur/{factuur_id}/herinnering"' in pagina


def test_herinnering_neemt_de_eigen_zin_mee(monkeypatch, post, db, maak_factuur):
    opgevangen = _vang_mail(monkeypatch)
    _mailserver(db)
    factuur_id = maak_factuur(nummer="2026-004", status="verzonden")
    post(f"/factuur/{factuur_id}/herinnering", {"eigen_zin": "De tegelzetter is klaar."})
    tekst = opgevangen["tekst"]
    assert tekst.startswith("Beste Jan Jansen,")
    assert "De tegelzetter is klaar." in tekst
    assert "staat nog open" in tekst


# ---------- P1.8 toelichting gaat door op een volgend vel ----------

def test_een_lange_toelichting_breekt_niet_door_de_voet_heen(db, maak_offerte):
    zet_bedrijf(db)
    offerte_id = maak_offerte()
    # Elke zin op een eigen regel, anders past de lap nog op één vel en
    # bewijst de test niet dat er wordt afgebroken.
    db.execute(
        "UPDATE offertes SET toelichting=? WHERE id=?",
        ("De voeg is inbegrepen.\n" * 80, offerte_id),
    )
    db.commit()
    tekst = tekst_uit_pdf(facturen.maak_offerte_pdf(offerte_id))
    assert tekst.count("voeg") >= 80
    assert "vervolg" in tekst


# ---------- P1.9 openstaand op de klantenlijst ----------

def test_klantenlijst_trekt_deelbetalingen_af_van_openstaand(post, db, client):
    klant_id = _klant(db, "Anna")
    factuur_id = db.execute(
        """INSERT INTO facturen (nummer, datum, klant_id, klant_naam, status, totaal)
           VALUES ('2026-003', '2026-08-14', ?, 'Anna', 'verzonden', 121)""",
        (klant_id,),
    ).lastrowid
    db.commit()
    post(f"/factuur/{factuur_id}/betalingen", {"datum": "2026-08-20", "bedrag": "21,00"})

    rij = {k["id"]: k for k in facturen.klantenlijst()}[klant_id]
    assert rij["openstaand"] == 100
    pagina = client.get("/klanten").data.decode()
    assert "100,00" in pagina
    assert "121,00 openstaand" not in pagina


# ---------- P1.10 aanbetaling en restant ----------

def _offerte_van(db, maak_offerte, totaal=1000.0, inkoop_id=None):
    offerte_id = maak_offerte(totaal=totaal, status="geaccepteerd")
    db.execute(
        """UPDATE offerte_regels SET prijs=?, subtotaal=?, inkoop_id=?
           WHERE offerte_id=?""",
        (totaal, totaal, inkoop_id, offerte_id),
    )
    db.commit()
    return offerte_id


def test_aanbetaling_hangt_aan_de_offerte_zonder_de_bon_af_te_vinken(post, db, maak_offerte):
    klus_id = _klus(post, db, "Badkamer")
    inkoop_id = db.execute(
        """INSERT INTO inkopen (klus_id, omschrijving, bedrag, toegevoegd)
           VALUES (?, 'Tegels', 200, '2026-08-01')""",
        (klus_id,),
    ).lastrowid
    db.commit()
    offerte_id = _offerte_van(db, maak_offerte, inkoop_id=inkoop_id)

    post(f"/offerte/{offerte_id}/aanbetaling", {"percentage": "30"})

    factuur = db.execute("SELECT * FROM facturen").fetchone()
    assert factuur["offerte_id"] == offerte_id
    assert factuur["totaal"] == 300
    offerte = db.execute("SELECT factuur_id FROM offertes").fetchone()
    assert offerte["factuur_id"] is None
    assert db.execute("SELECT factuur_id FROM inkopen").fetchone()[0] is None


def test_een_vierde_aanbetaling_van_dertig_procent_mag_niet(post, db, maak_offerte):
    """Drie keer 30% kan. De vierde zou boven de offerte uitkomen; die weigeren
    we, anders staat er 120% op rekeningen."""
    offerte_id = _offerte_van(db, maak_offerte)
    for _ in range(3):
        post(f"/offerte/{offerte_id}/aanbetaling", {"percentage": "30"})
    assert db.execute("SELECT COUNT(*) FROM facturen").fetchone()[0] == 3

    antwoord = post(
        f"/offerte/{offerte_id}/aanbetaling",
        {"percentage": "30"},
        follow_redirects=True,
    )
    assert db.execute("SELECT COUNT(*) FROM facturen").fetchone()[0] == 3
    assert "meer dan de offerte" in antwoord.data.decode()


def test_naar_rekening_rekent_het_restant_en_neemt_de_bon_mee(post, db, maak_offerte):
    klus_id = _klus(post, db, "Badkamer")
    inkoop_id = db.execute(
        """INSERT INTO inkopen (klus_id, omschrijving, bedrag, toegevoegd)
           VALUES (?, 'Tegels', 200, '2026-08-01')""",
        (klus_id,),
    ).lastrowid
    db.commit()
    offerte_id = _offerte_van(db, maak_offerte, inkoop_id=inkoop_id)
    post(f"/offerte/{offerte_id}/aanbetaling", {"percentage": "30"})

    antwoord = post(f"/offerte/{offerte_id}/naar-rekening", follow_redirects=True)
    tekst = antwoord.data.decode()
    assert "700,00" in tekst or "700.00" in tekst or "restant" in tekst

    rest = db.execute(
        "SELECT * FROM facturen WHERE id != (SELECT MIN(id) FROM facturen)"
    ).fetchone()
    assert rest["totaal"] == 700
    assert rest["offerte_id"] == offerte_id
    regels = db.execute(
        "SELECT * FROM regels WHERE factuur_id=? ORDER BY id", (rest["id"],)
    ).fetchall()
    assert regels[0]["inkoop_id"] == inkoop_id
    assert regels[1]["subtotaal"] == -300
    assert "aanbetaald" in regels[1]["omschrijving"].lower()
    assert db.execute("SELECT factuur_id FROM inkopen").fetchone()[0] == rest["id"]

    nog_eens = post(f"/offerte/{offerte_id}/naar-rekening", follow_redirects=True)
    assert "al omgezet" in nog_eens.data.decode()
    assert db.execute("SELECT COUNT(*) FROM facturen").fetchone()[0] == 2


# ---------- P1.11 alleen bonnen van deze rekening, geen notitiefoto ----------

def _bijlage(db, klus_id, naam, meesturen=1, notitie_id=None, inkoop_id=None):
    return db.execute(
        """INSERT INTO bijlagen (klus_id, bestand, naam, toegevoegd, meesturen,
           notitie_id, inkoop_id)
           VALUES (?, ?, ?, '2026-08-14', ?, ?, ?)""",
        (klus_id, f"opslag-{naam}", naam, meesturen, notitie_id, inkoop_id),
    ).lastrowid


def test_een_notitiefoto_gaat_nooit_mee_ook_niet_als_meesturen_aan_staat(db, maak_factuur):
    klus_id = db.execute(
        "INSERT INTO klussen (naam, uurtarief, gestart) VALUES ('Keuken', 40, '2026-08-01')"
    ).lastrowid
    notitie_id = db.execute(
        """INSERT INTO notities (klus_id, wanneer, tekst)
           VALUES (?, '2026-08-14', 'Voor de verbouwing')""",
        (klus_id,),
    ).lastrowid
    _bijlage(db, klus_id, "voor.jpg", notitie_id=notitie_id)
    factuur_id = maak_factuur()
    db.execute(
        "UPDATE regels SET klus_id=? WHERE factuur_id=?", (klus_id, factuur_id)
    )
    db.commit()
    assert facturen.bonnen_bij_factuur(db, factuur_id) == []


def test_uren_alleen_halen_de_fotos_van_de_klus_niet_mee(db, post, maak_factuur):
    klus_id = db.execute(
        "INSERT INTO klussen (naam, uurtarief, gestart) VALUES ('Keuken', 40, '2026-08-01')"
    ).lastrowid
    _bijlage(db, klus_id, "overzicht.jpg")
    factuur_id = maak_factuur()
    db.execute(
        "UPDATE regels SET klus_id=? WHERE factuur_id=?", (klus_id, factuur_id)
    )
    db.commit()
    assert facturen.bonnen_bij_factuur(db, factuur_id) == []


def test_uitgevinkte_bon_gaat_niet_mee(monkeypatch, post, db):
    opgevangen = _vang_mail(monkeypatch)
    _mailserver(db)
    klus_id = _klus(post, db, "Badkamer")
    inkoop_id = db.execute(
        """INSERT INTO inkopen (klus_id, omschrijving, bedrag, toegevoegd)
           VALUES (?, 'Kit', 12, '2026-08-14')""",
        (klus_id,),
    ).lastrowid
    bijlage_id = _bijlage(db, klus_id, "bon.png", inkoop_id=inkoop_id)
    with open(os.path.join(facturen.BIJLAGE_DIR, "opslag-bon.png"), "wb") as bestand:
        bestand.write(PNG)
    db.commit()
    post("/nieuw", {
        "klant_naam": "Jan", "klant_email": "jan@example.com", "datum": "2026-08-14",
        "omschrijving": "Kit", "type": "bon", "aantal": "1", "prijs": "12",
        "regel_inkoop": str(inkoop_id),
    })
    factuur_id = db.execute("SELECT id FROM facturen").fetchone()[0]
    assert [b["id"] for b in facturen.bonnen_bij_factuur(db, factuur_id)] == [bijlage_id]

    post(f"/factuur/{factuur_id}/verstuur", {"bonnen_gekozen": "1"})
    assert opgevangen["extra"] == []
    assert "bonnetjes" not in opgevangen["tekst"]


# ---------- P1.12 wachtwoord via de zijbalk ----------

def _zet_wachtwoord_terug(db):
    db.execute(
        "UPDATE gebruikers SET wachtwoord=? WHERE naam=?",
        (facturen.generate_password_hash(TEST_WACHTWOORD), TEST_GEBRUIKER),
    )
    db.commit()


def test_via_de_zijbalk_wijzig_je_het_wachtwoord_zonder_het_oude(uitgelogde_client, db):
    # Niet follow_redirects: de Ingress-header blijft dan aan de volgende
    # aanvraag hangen en de testclient loopt vast in een omleiding.
    antwoord = uitgelogde_client.post("/wachtwoord", data={
        "csrf_token": "test-token",
        "nieuw": "herstel-wachtwoord",
        "nogmaals": "herstel-wachtwoord",
    }, environ_overrides=ECHTE_ZIJBALK)
    try:
        assert antwoord.status_code == 302
        assert "inloggen" not in antwoord.headers["Location"]
        hash = db.execute(
            "SELECT wachtwoord FROM gebruikers WHERE naam=?", (TEST_GEBRUIKER,)
        ).fetchone()[0]
        assert facturen.check_password_hash(hash, "herstel-wachtwoord")
    finally:
        _zet_wachtwoord_terug(db)


def test_een_nagemaakte_zijbalk_wijzigt_het_wachtwoord_niet(uitgelogde_client, db):
    uitgelogde_client.post("/wachtwoord", data={
        "csrf_token": "test-token",
        "nieuw": "herstel-wachtwoord",
        "nogmaals": "herstel-wachtwoord",
    }, environ_overrides=NAGEMAAKT)
    hash = db.execute(
        "SELECT wachtwoord FROM gebruikers WHERE naam=?", (TEST_GEBRUIKER,)
    ).fetchone()[0]
    assert facturen.check_password_hash(hash, TEST_WACHTWOORD)


def test_het_instellingenscherm_via_de_zijbalk_vraagt_niet_om_het_oude_wachtwoord(
        uitgelogde_client):
    pagina = uitgelogde_client.get(
        "/instellingen", environ_overrides=ECHTE_ZIJBALK
    ).data.decode()
    assert 'name="nieuw"' in pagina
    assert 'name="huidig"' not in pagina


def test_op_poort_8099_blijft_het_huidige_wachtwoord_verplicht(client):
    assert 'name="huidig"' in client.get("/instellingen").data.decode()


# ---------- P1.13 tijdzone ----------

def test_de_klok_valt_terug_op_amsterdam(monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    assert str(facturen.actieve_tijdzone()) == "Europe/Amsterdam"


def test_vandaag_volgt_de_gezette_tijdzone_niet_utc(monkeypatch):
    """Nummering en 'te laat' lezen vandaag(). Die hoort de klok van Home
    Assistant te volgen, ook als de container zelf op UTC staat."""
    monkeypatch.setenv("TZ", "Pacific/Kiritimati")
    assert facturen.vandaag() == datetime.now(ZoneInfo("Pacific/Kiritimati")).date()


def test_het_startscript_zet_amsterdam_als_home_assistant_geen_tz_meegeeft():
    script = (ROOT / "facturen_app/rootfs/usr/bin/facturen-app").read_text()
    assert 'export TZ="Europe/Amsterdam"' in script
    assert "tzdata" in (ROOT / "facturen_app/Dockerfile").read_text()


def test_het_rekeningnummer_gebruikt_het_jaar_van_vandaag(monkeypatch, db):
    class NepDatum:
        year = 2031

    monkeypatch.setattr(facturen, "vandaag", lambda: NepDatum())
    assert facturen.volgend_nummer(db) == "2031-001"


# ---------- P1.14 HEIC ----------

def test_een_heic_foto_krijgt_een_melding_en_geen_kapot_voorbeeld(post, db, client):
    klus_id = _klus(post, db, "Badkamer")
    antwoord = post(
        f"/klus/{klus_id}/bijlage",
        {"bijlage": (io.BytesIO(b"geen-echte-heic"), "IMG_0001.heic")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert "HEIC" in antwoord.data.decode()
    assert "JPG" in antwoord.data.decode()
    pagina = client.get(f"/klus/{klus_id}").data.decode()
    assert "Geen voorbeeld" in pagina
    assert "JPG of PNG" in pagina
