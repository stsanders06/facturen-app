"""Een kopie van elke mail naar jezelf, als BCC. De klant ziet dat adres niet."""
from email.message import EmailMessage

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


def test_twee_adressen_in_een_veld_worden_twee_ontvangers(monkeypatch, db, maak_factuur):
    """'a, b' is twee ontvangers. Als één lange RCPT weigert de server hem,
    terwijl de kopie wél weggaat — en de rekening stond dan op verstuurd."""
    _server(db)
    monkeypatch.setattr(facturen.smtplib, "SMTP", _NepSmtp)
    _NepSmtp.verstuurd.clear()
    factuur_id = maak_factuur(
        status="verzonden", email="klant-m@example.org, tweede@example.org",
    )
    assert facturen.verstuur_email(factuur_id)[0]
    _, envelop = _NepSmtp.verstuurd[-1]
    assert envelop == [
        "klant-m@example.org", "tweede@example.org", "kopie@example.org",
    ]


def test_een_naam_voor_het_adres_gaat_niet_mee_als_ontvanger(monkeypatch, db, maak_factuur):
    _server(db)
    monkeypatch.setattr(facturen.smtplib, "SMTP", _NepSmtp)
    _NepSmtp.verstuurd.clear()
    factuur_id = maak_factuur(
        status="verzonden", email="Voorbeeld Klant <klant@example.org>",
    )
    assert facturen.verstuur_email(factuur_id)[0]
    _, envelop = _NepSmtp.verstuurd[-1]
    assert envelop == ["klant@example.org", "kopie@example.org"]


def test_een_naam_voor_de_afzender_wordt_alleen_het_adres(monkeypatch, db, maak_factuur):
    """'Naam <afzender@…>' is geen geldig adres als je hem heel laat. Dan viel
    de kopie stil weg, of er ging een tweede exemplaar naar hetzelfde adres."""
    _server(db, bcc_adres="", smtp_van="Voorbeeld Bedrijf <afzender@example.org>")
    monkeypatch.setattr(facturen.smtplib, "SMTP", _NepSmtp)
    _NepSmtp.verstuurd.clear()
    factuur_id = maak_factuur(status="verzonden", email="klant@example.org")
    assert facturen.verstuur_email(factuur_id)[0]
    _, envelop = _NepSmtp.verstuurd[-1]
    assert envelop == ["klant@example.org", "afzender@example.org"]

    _NepSmtp.verstuurd.clear()
    zelf = maak_factuur(
        nummer="2026-002", status="verzonden",
        email="Naam <Afzender@Example.org>",
    )
    assert facturen.verstuur_email(zelf)[0]
    _, envelop = _NepSmtp.verstuurd[-1]
    assert envelop == ["Afzender@Example.org"]


def test_de_envelop_knipt_aan_en_cc_en_slaat_dubbele_over():
    msg = EmailMessage()
    msg["To"] = "klant-m@example.org, tweede@example.org, "
    msg["Cc"] = "Voorbeeld <tweede@example.org>, Naam <cc@example.org>"
    verplicht, envelop = facturen._envelop_adressen(msg, "Tweede@Example.org")
    assert verplicht == [
        "klant-m@example.org", "tweede@example.org", "cc@example.org",
    ]
    assert envelop == verplicht


def test_een_geweigerde_aan_zet_de_rekening_niet_op_verstuurd(
        monkeypatch, db, post, maak_factuur):
    """De server nam de kopie aan en de klant niet. Dat is geen gelukte mail:
    anders staat de rekening op verstuurd en heeft de klant niets."""

    class WeigertAan(_NepSmtp):
        def send_message(self, msg, to_addrs=None, **k):
            super().send_message(msg, to_addrs, **k)
            return {to_addrs[0]: (550, b"User unknown")}

    _server(db)
    monkeypatch.setattr(facturen.smtplib, "SMTP", WeigertAan)
    _NepSmtp.verstuurd.clear()
    factuur_id = maak_factuur(nummer="", status="concept", email="klant@example.org")
    antwoord = post(f"/factuur/{factuur_id}/verstuur", follow_redirects=True)
    assert "weigert het adres klant@example.org" in antwoord.data.decode()
    rij = db.execute(
        "SELECT nummer, status FROM facturen WHERE id=?", (factuur_id,),
    ).fetchone()
    assert rij["status"] == "concept"
    assert rij["nummer"] == ""
    _, envelop = _NepSmtp.verstuurd[-1]
    assert "klant@example.org" in envelop
    assert "kopie@example.org" in envelop


def test_alle_ontvangers_geweigerd_is_ook_geen_gelukte_mail(
        monkeypatch, db, post, maak_factuur):
    class WeigertIedereen(_NepSmtp):
        def send_message(self, msg, to_addrs=None, **k):
            raise facturen.smtplib.SMTPRecipientsRefused(
                {adres: (550, b"no") for adres in (to_addrs or [])}
            )

    _server(db)
    monkeypatch.setattr(facturen.smtplib, "SMTP", WeigertIedereen)
    factuur_id = maak_factuur(nummer="", status="concept", email="klant@example.org")
    antwoord = post(f"/factuur/{factuur_id}/verstuur", follow_redirects=True)
    assert "weigert" in antwoord.data.decode()
    rij = db.execute(
        "SELECT nummer, status FROM facturen WHERE id=?", (factuur_id,),
    ).fetchone()
    assert rij["status"] == "concept"
    assert rij["nummer"] == ""


def test_een_geweigerde_kopie_houdt_de_mail_naar_de_klant_niet_tegen(
        monkeypatch, db, maak_factuur):
    class WeigertKopie(_NepSmtp):
        def send_message(self, msg, to_addrs=None, **k):
            super().send_message(msg, to_addrs, **k)
            return {"kopie@example.org": (550, b"no")}

    _server(db)
    monkeypatch.setattr(facturen.smtplib, "SMTP", WeigertKopie)
    factuur_id = maak_factuur(nummer="", status="concept", email="klant@example.org")
    assert facturen.verstuur_email(factuur_id)[0]
    rij = db.execute(
        "SELECT status FROM facturen WHERE id=?", (factuur_id,),
    ).fetchone()
    assert rij["status"] == "verzonden"


def test_het_kopieveld_blijft_leeg_en_toont_de_afzender(client, db, post):
    db.execute(
        """UPDATE settings SET smtp_van='afzender@example.org', bcc_aan=1,
           bcc_adres='' WHERE id=1"""
    )
    db.commit()
    pagina = client.get("/instellingen").data.decode()
    veld = pagina.split('id="bcc_adres"', 1)[1].split(">", 1)[0]
    assert 'value=""' in veld
    assert 'placeholder="afzender@example.org"' in veld
    post("/instellingen", {
        "naam": "Voorbeeld Bedrijf BV",
        "smtp_van": "afzender@example.org",
        "bcc_aan": "ja",
        "bcc_adres": "",
    })
    rij = db.execute("SELECT bcc_aan, bcc_adres FROM settings").fetchone()
    assert rij["bcc_aan"] == 1
    assert rij["bcc_adres"] == ""


def test_uitvinken_blijft_uit_ook_als_het_adres_niet_klopt(post, client, db):
    post("/instellingen", {
        "naam": "Voorbeeld Bedrijf BV",
        "smtp_van": "afzender@example.org",
        "bcc_aan": "ja",
        "bcc_adres": "kopie@example.org",
    })
    antwoord = post("/instellingen", {
        "naam": "Voorbeeld Bedrijf BV",
        "smtp_van": "afzender@example.org",
        "bcc_adres": "niet-geldig",
    }, follow_redirects=True)
    assert "klopt niet" not in antwoord.data.decode()
    rij = db.execute("SELECT bcc_aan, bcc_adres FROM settings").fetchone()
    assert rij["bcc_aan"] == 0
    assert rij["bcc_adres"] == "kopie@example.org"
    assert "niet-geldig" not in (rij["bcc_adres"] or "")


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
