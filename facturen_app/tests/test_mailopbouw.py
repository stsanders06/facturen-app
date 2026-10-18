"""Hoe een uitgaande mail in elkaar zit.

Voorheen was het één multipart/mixed met platte tekst, en daarna de PDF als
application/pdf zónder name-parameter. Een foto ging er hetzelfde in, mét
filename* als de naam een vreemd teken had, maar zonder name. Geen Content-ID
en geen multipart/related — dat klopte al. Apple Mail zette de PDF toch op ware
grootte onder de brief, omdat de brief geen HTML-deel had.

Nu is de brief een multipart/alternative (platte tekst én HTML) en is elk
bestand een bijlage: Content-Disposition attachment, filename (filename* bij
een vreemd teken) en name op het Content-Type. Nog steeds geen Content-ID.
"""
from email.message import EmailMessage

from conftest import facturen


PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


def _koppen(msg):
    namen = list(msg.keys())
    if msg.is_multipart():
        for deel in msg.iter_parts():
            namen.extend(_koppen(deel))
    return namen


def test_de_brief_is_alternative_en_de_bestanden_zijn_bijlagen():
    s = {
        "smtp_van": "afzender@example.org",
        "smtp_user": "",
        "bcc_aan": 0,
        "bcc_adres": "",
    }
    msg = facturen.bouw_mail(
        s,
        "klant@example.org",
        "Rekening 2026-001",
        "Beste <klant>,\n\nHierbij de rekening.",
        b"%PDF-1.4\n",
        "2026-001.pdf",
        [
            (PNG, "bon café.png", "image", "png"),
            (b"%PDF-1.4 extra\n", "rekening ë.pdf", "application", "pdf"),
        ],
    )
    ruw = msg.as_string()

    assert msg.get_content_type() == "multipart/mixed"
    assert "multipart/related" not in ruw.lower()
    assert "Content-ID" not in _koppen(msg)

    brief, pdf, foto, extra = list(msg.iter_parts())
    assert brief.get_content_type() == "multipart/alternative"
    vlak, html = list(brief.iter_parts())
    assert vlak.get_content_type() == "text/plain"
    assert "Hierbij de rekening." in vlak.get_content()
    assert html.get_content_type() == "text/html"
    assert "Beste &lt;klant&gt;" in html.get_content()
    assert "<p>" in html.get_content()

    assert pdf.get_content_type() == "application/pdf"
    assert pdf.get_param("name") == "2026-001.pdf"
    assert pdf.get_filename() == "2026-001.pdf"
    assert pdf.get_content_disposition() == "attachment"
    assert 'name="2026-001.pdf"' in ruw
    assert 'filename="2026-001.pdf"' in ruw

    assert foto.get_content_type() == "image/png"
    assert foto.get_content_disposition() == "attachment"
    assert foto.get_filename() == "bon café.png"
    assert foto.get_param("name") == "bon café.png"
    assert "filename*=utf-8''" in ruw
    assert "name*=utf-8''" in ruw
    assert "caf%C3%A9" in ruw

    assert extra.get_content_type() == "application/pdf"
    assert extra.get_content_disposition() == "attachment"
    assert extra.get_filename() == "rekening ë.pdf"
    assert "%C3%AB" in ruw


class _NepSmtp:
    bericht = None
    envelop = None

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
        _NepSmtp.bericht = msg
        _NepSmtp.envelop = list(to_addrs or [])


def test_versturen_stuurt_die_structuur_de_deur_uit(monkeypatch, tmp_path):
    pdf = tmp_path / "2026-001.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    foto = tmp_path / "bon.png"
    foto.write_bytes(PNG)
    monkeypatch.setattr(facturen.smtplib, "SMTP", _NepSmtp)
    s = {
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_user": "",
        "smtp_pass": "",
        "smtp_van": "afzender@example.org",
        "bcc_aan": 1,
        "bcc_adres": "kopie@example.org",
    }
    gelukt, tekst = facturen._mail_pdf(
        s, "klant@example.org", "Rekening", "Hierbij.",
        str(pdf), "2026-001.pdf", [(str(foto), "bon café.png")],
    )
    assert gelukt, tekst
    msg = _NepSmtp.bericht
    assert isinstance(msg, EmailMessage)
    assert msg.get_content_type() == "multipart/mixed"
    soorten = [deel.get_content_type() for deel in msg.iter_parts()]
    assert soorten[0] == "multipart/alternative"
    assert "text/html" in [d.get_content_type() for d in list(msg.iter_parts())[0].iter_parts()]
    assert all(deel.get_content_disposition() == "attachment" for deel in list(msg.iter_parts())[1:])
    assert "Content-ID" not in _koppen(msg)
    assert "multipart/related" not in msg.as_string().lower()
    assert _NepSmtp.envelop == ["klant@example.org", "kopie@example.org"]
    assert "Bcc" not in msg
    assert "kopie@example.org" not in msg.as_string()
