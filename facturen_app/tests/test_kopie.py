"""Een kopie van elke mail naar jezelf, als BCC. De klant ziet dat adres niet."""
from conftest import facturen


class _NepSmtp:
    verstuurd = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, *a):
        pass

    def send_message(self, msg, to_addrs=None, **k):
        _NepSmtp.verstuurd.append((msg, list(to_addrs or [])))


def _server(db, bcc_aan=1, bcc_adres="kopie@example.org", smtp_van="afzender@example.org"):
    db.execute(
        """UPDATE settings SET smtp_host='smtp.example.com', smtp_port=587,
           smtp_user='info@example.org', smtp_pass='geheim', smtp_van=?,
           naam='Voorbeeld Bedrijf BV', bcc_aan=?, bcc_adres=? WHERE id=1""",
        (smtp_van, bcc_aan, bcc_adres),
    )
    db.commit()


def test_de_kopie_zit_in_de_envelop_van_elke_mail(monkeypatch, db, maak_factuur, maak_offerte):
    _server(db)
    monkeypatch.setattr(facturen.smtplib, "SMTP", _NepSmtp)
    _NepSmtp.verstuurd.clear()

    factuur_id = maak_factuur(status="verzonden", email="klant@example.org")
    assert facturen.verstuur_email(factuur_id)[0]
    assert facturen.herinnering_email(factuur_id)[0]
    assert facturen.verstuur_offerte_email(maak_offerte())[0]

    assert len(_NepSmtp.verstuurd) == 3
    for msg, envelop in _NepSmtp.verstuurd:
        assert envelop[0] != "kopie@example.org"
        assert "kopie@example.org" in envelop
        assert "Bcc" not in msg.keys()
        assert "kopie@example.org" not in msg.as_string()


def test_zonder_eigen_adres_gaat_de_kopie_naar_de_afzender(monkeypatch, db, maak_factuur):
    _server(db, bcc_adres="")
    monkeypatch.setattr(facturen.smtplib, "SMTP", _NepSmtp)
    _NepSmtp.verstuurd.clear()
    factuur_id = maak_factuur(status="verzonden", email="klant@example.org")
    assert facturen.verstuur_email(factuur_id)[0]
    _, envelop = _NepSmtp.verstuurd[-1]
    assert envelop == ["klant@example.org", "afzender@example.org"]


def test_geen_kopie_als_het_adres_de_ontvanger_is(monkeypatch, db, maak_factuur):
    _server(db, bcc_adres="Klant@Example.org")
    monkeypatch.setattr(facturen.smtplib, "SMTP", _NepSmtp)
    _NepSmtp.verstuurd.clear()
    factuur_id = maak_factuur(status="verzonden", email="klant@example.org")
    assert facturen.verstuur_email(factuur_id)[0]
    _, envelop = _NepSmtp.verstuurd[-1]
    assert envelop == ["klant@example.org"]


def test_uitzetten_stuurt_geen_kopie(monkeypatch, db, maak_factuur):
    _server(db, bcc_aan=0)
    monkeypatch.setattr(facturen.smtplib, "SMTP", _NepSmtp)
    _NepSmtp.verstuurd.clear()
    factuur_id = maak_factuur(status="verzonden", email="klant@example.org")
    assert facturen.verstuur_email(factuur_id)[0]
    _, envelop = _NepSmtp.verstuurd[-1]
    assert envelop == ["klant@example.org"]


def test_een_regeleinde_in_het_adres_wordt_geen_kop(db, maak_factuur, monkeypatch):
    _server(db, bcc_adres="kopie@example.org\r\nBcc: hidden@example.org")
    assert facturen.kopie_adres("klant@example.org") == ""
    monkeypatch.setattr(facturen.smtplib, "SMTP", _NepSmtp)
    _NepSmtp.verstuurd.clear()
    factuur_id = maak_factuur(status="verzonden", email="klant@example.org")
    assert facturen.verstuur_email(factuur_id)[0]
    msg, envelop = _NepSmtp.verstuurd[-1]
    assert envelop == ["klant@example.org"]
    assert "hidden@example.org" not in msg.as_string()


def test_het_scherm_noemt_de_kopie(client, db, maak_factuur, maak_offerte):
    _server(db)
    factuur_id = maak_factuur(email="klant@example.org")
    pagina = client.get(f"/factuur/{factuur_id}/mail").data.decode()
    assert "Kopie (BCC) naar kopie@example.org" in pagina

    offerte_id = maak_offerte()
    pagina = client.get(f"/offerte/{offerte_id}/mail").data.decode()
    assert "Kopie (BCC) naar kopie@example.org" in pagina

    factuur_id = maak_factuur(nummer="2026-009", status="verzonden", email="klant@example.org")
    pagina = client.get(f"/factuur/{factuur_id}/herinnering").data.decode()
    assert "Kopie (BCC) naar kopie@example.org" in pagina


def test_het_scherm_verzwijgt_de_kopie_als_die_uit_staat(client, db, maak_factuur):
    _server(db, bcc_aan=0)
    factuur_id = maak_factuur(email="klant@example.org")
    assert "Kopie (BCC)" not in client.get(f"/factuur/{factuur_id}/mail").data.decode()


def test_instellingen_bewaart_de_kopie(post, client, db):
    post("/instellingen", {
        "naam": "Voorbeeld Bedrijf BV",
        "smtp_van": "afzender@example.org",
        "bcc_aan": "ja",
        "bcc_adres": "kopie@example.org",
    })
    rij = db.execute("SELECT bcc_aan, bcc_adres FROM settings").fetchone()
    assert rij["bcc_aan"] == 1
    assert rij["bcc_adres"] == "kopie@example.org"
    pagina = client.get("/instellingen").data.decode()
    assert "BCC naar mezelf" in pagina
    assert 'id="bcc_adres"' in pagina
    assert "kopie@example.org" in pagina
    assert 'id="bcc_aan"' in pagina
    assert "checked" in pagina


def test_hetzelfde_adres_als_de_afzender_volgt_de_afzender(post, db):
    post("/instellingen", {
        "naam": "Voorbeeld Bedrijf BV",
        "smtp_van": "afzender@example.org",
        "bcc_aan": "ja",
        "bcc_adres": "Afzender@Example.org",
    })
    rij = db.execute("SELECT bcc_aan, bcc_adres FROM settings").fetchone()
    assert rij["bcc_aan"] == 1
    assert rij["bcc_adres"] == ""


def test_uitvinken_zet_de_kopie_uit(post, db):
    post("/instellingen", {
        "naam": "Voorbeeld Bedrijf BV",
        "smtp_van": "afzender@example.org",
        "bcc_adres": "kopie@example.org",
    })
    rij = db.execute("SELECT bcc_aan, bcc_adres FROM settings").fetchone()
    assert rij["bcc_aan"] == 0


def test_een_kapot_adres_blijft_niet_bewaard(post, client, db):
    post("/instellingen", {
        "naam": "Voorbeeld Bedrijf BV",
        "smtp_van": "afzender@example.org",
        "bcc_aan": "ja",
        "bcc_adres": "kopie@example.org",
    })
    post("/instellingen", {
        "naam": "Voorbeeld Bedrijf BV",
        "smtp_van": "afzender@example.org",
        "bcc_aan": "ja",
        "bcc_adres": "kopie@example.org\nBcc: hidden@example.org",
    })
    pagina = client.get("/instellingen").data.decode()
    assert "klopt niet" in pagina
    rij = db.execute("SELECT bcc_aan, bcc_adres FROM settings").fetchone()
    assert rij["bcc_adres"] == "kopie@example.org"
    assert rij["bcc_aan"] == 1


def test_een_oude_database_krijgt_de_kopie_aan(db):
    db.execute("ALTER TABLE settings DROP COLUMN bcc_aan")
    db.execute("ALTER TABLE settings DROP COLUMN bcc_adres")
    db.commit()
    facturen.init_db()
    vers = facturen.get_db()
    rij = vers.execute("SELECT bcc_aan, bcc_adres FROM settings WHERE id=1").fetchone()
    vers.close()
    assert rij["bcc_aan"] == 1
    assert (rij["bcc_adres"] or "") == ""
