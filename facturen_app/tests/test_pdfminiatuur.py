"""Eerste pagina van een PDF als klein voorbeeld.

Zonder pdftoppm, of bij een kapot bestand, blijft het grijze vak. De upload
wacht niet op het tekenen: het plaatje ontstaat pas als de tegel het vraagt.
"""
import io
import os
import shutil
import struct
import subprocess
import time

import pytest

from conftest import facturen


def _echte_pdf(zin="Bon Voorbeeldstraat"):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    tekening = canvas.Canvas(buf, pagesize=A4)
    tekening.setFont("Helvetica", 18)
    tekening.drawString(72, 780, zin)
    tekening.showPage()
    tekening.save()
    return buf.getvalue()


def _png_afmeting(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


@pytest.fixture
def klus_id(post, db):
    post("/klussen/nieuw", {"naam": "Badkamer Voorbeeldstraat", "uurtarief": "45"})
    return db.execute("SELECT id FROM klussen").fetchone()[0]


def _bon(post, klus_id, inhoud, naam="scan.pdf"):
    return post(
        f"/klus/{klus_id}/bijlage",
        {"bijlage": (io.BytesIO(inhoud), naam)},
        content_type="multipart/form-data",
    )


def test_upload_tekent_de_pdf_nog_niet(post, db, klus_id):
    _bon(post, klus_id, _echte_pdf())
    assert not any(naam.endswith(".min.png") for naam in os.listdir(facturen.BIJLAGE_DIR))


def _wacht():
    facturen._wacht_op_miniaturen()


@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="pdftoppm ontbreekt")
def test_een_pdf_krijgt_een_plaatje_van_de_eerste_pagina(post, db, client, klus_id):
    _bon(post, klus_id, _echte_pdf())
    bijlage_id = db.execute("SELECT id FROM bijlagen").fetchone()[0]
    # Eerst het grijze vak: tekenen loopt op de achtergrond.
    eerste = client.get(f"/klus/{klus_id}").data.decode()
    assert ">PDF<" in eerste
    assert 'class="is-pdf"' not in eerste
    _wacht()
    pagina = client.get(f"/klus/{klus_id}").data.decode()
    assert f"/bijlage/{bijlage_id}/miniatuur" in pagina
    assert 'loading="lazy"' in pagina
    assert 'data-soort="pdf"' in pagina
    assert 'class="is-pdf"' in pagina

    antwoord = client.get(f"/bijlage/{bijlage_id}/miniatuur")
    assert antwoord.status_code == 200
    assert antwoord.mimetype == "image/png"
    assert antwoord.headers["X-Content-Type-Options"] == "nosniff"
    assert "max-age=86400" in antwoord.headers["Cache-Control"]
    assert "inline" in antwoord.headers["Content-Disposition"]
    breedte, hoogte = _png_afmeting(antwoord.data)
    assert breedte > 40 and hoogte > 40
    assert any(naam.endswith(".min.png") for naam in os.listdir(facturen.BIJLAGE_DIR))

    opnieuw = client.get(f"/bijlage/{bijlage_id}/miniatuur")
    assert opnieuw.data == antwoord.data


def test_een_kapotte_pdf_houdt_het_grijze_vak(post, db, client, klus_id, monkeypatch):
    _bon(post, klus_id, b"%PDF-1.4\nkapot")
    bijlage_id = db.execute("SELECT id FROM bijlagen").fetchone()[0]
    aanroepen = {"n": 0}
    echt = facturen.subprocess.run

    def tel(*args, **kwargs):
        aanroepen["n"] += 1
        return echt(*args, **kwargs)

    monkeypatch.setattr(facturen.shutil, "which", lambda _naam: "/usr/bin/pdftoppm")
    monkeypatch.setattr(facturen.subprocess, "run", tel)
    assert client.get(f"/bijlage/{bijlage_id}/miniatuur").status_code == 404
    _wacht()
    assert client.get(f"/bijlage/{bijlage_id}/miniatuur").status_code == 404
    assert aanroepen["n"] == 1
    assert any(naam.endswith(".min.mislukt") for naam in os.listdir(facturen.BIJLAGE_DIR))
    pagina = client.get(f"/klus/{klus_id}").data.decode()
    assert ">PDF<" in pagina


def test_de_miniatuur_vraagt_om_inloggen(uitgelogde_client):
    assert uitgelogde_client.get("/bijlage/1/miniatuur").status_code == 302


@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="pdftoppm ontbreekt")
def test_een_ander_bestand_maakt_het_voorbeeld_opnieuw(post, db, client, klus_id, monkeypatch):
    _bon(post, klus_id, _echte_pdf("Eerste bon"))
    rij = db.execute("SELECT id, bestand FROM bijlagen").fetchone()
    client.get(f"/bijlage/{rij['id']}/miniatuur")
    _wacht()
    assert client.get(f"/bijlage/{rij['id']}/miniatuur").status_code == 200
    aanroepen = {"n": 0}
    echt = facturen.subprocess.run

    def tel(*args, **kwargs):
        aanroepen["n"] += 1
        return echt(*args, **kwargs)

    monkeypatch.setattr(facturen.subprocess, "run", tel)
    assert client.get(f"/bijlage/{rij['id']}/miniatuur").status_code == 200
    assert aanroepen["n"] == 0
    pad = os.path.join(facturen.BIJLAGE_DIR, rij["bestand"])
    with open(pad, "wb") as uit:
        uit.write(_echte_pdf("Tweede bon"))
    client.get(f"/bijlage/{rij['id']}/miniatuur")
    _wacht()
    assert client.get(f"/bijlage/{rij['id']}/miniatuur").status_code == 200
    assert aanroepen["n"] == 1


def test_verwijderen_wist_het_voorbeeld(post, db, client, klus_id, monkeypatch):
    _bon(post, klus_id, b"%PDF-1.4\nkapot")
    rij = db.execute("SELECT id, bestand FROM bijlagen").fetchone()
    monkeypatch.setattr(facturen.shutil, "which", lambda _naam: "/usr/bin/pdftoppm")
    client.get(f"/bijlage/{rij['id']}/miniatuur")
    _wacht()
    assert any(naam.startswith(rij["bestand"] + ".min.") for naam in os.listdir(facturen.BIJLAGE_DIR))
    post(f"/bijlage/{rij['id']}/verwijder", follow_redirects=True)
    # Het bestand zelf blijft in de prullenbak. Het voorbeeld niet.
    assert os.path.isfile(os.path.join(facturen.BIJLAGE_DIR, rij["bestand"]))
    assert not any(
        naam.startswith(rij["bestand"] + ".min.")
        for naam in os.listdir(facturen.BIJLAGE_DIR)
    )


def _pdf_met_paginas(aantal):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    tekening = canvas.Canvas(buf, pagesize=A4)
    for nummer in range(1, aantal + 1):
        tekening.setFont("Helvetica", 18)
        tekening.drawString(72, 780, f"Blad {nummer} Voorbeeldstraat")
        tekening.showPage()
    tekening.save()
    return buf.getvalue()


@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="pdftoppm ontbreekt")
def test_twaalf_paginas_geven_een_voorbeeld_zonder_losse_png(post, db, client, klus_id):
    """pdftoppm zet bij 10+ pagina's -01.png. -singlefile houdt één naam."""
    _bon(post, klus_id, _pdf_met_paginas(12), "twaalf.pdf")
    rij = db.execute("SELECT id, bestand FROM bijlagen").fetchone()
    client.get(f"/bijlage/{rij['id']}/miniatuur")
    _wacht()
    antwoord = client.get(f"/bijlage/{rij['id']}/miniatuur")
    assert antwoord.status_code == 200
    assert antwoord.data[:8] == b"\x89PNG\r\n\x1a\n"
    namen = os.listdir(facturen.BIJLAGE_DIR)
    assert not any(naam.endswith("-1.png") or naam.endswith("-01.png") for naam in namen)
    post(f"/bijlage/{rij['id']}/verwijder", follow_redirects=True)
    namen = os.listdir(facturen.BIJLAGE_DIR)
    assert not any(".min." in naam or naam.endswith("-01.png") or naam.endswith("-1.png")
                   for naam in namen)


def test_tekenen_houdt_na_vijf_seconden_op(post, db, client, klus_id, monkeypatch):
    _bon(post, klus_id, b"%PDF-1.4\nlangzaam")
    bijlage_id = db.execute("SELECT id FROM bijlagen").fetchone()[0]
    gezien = {}

    def nep(*args, **kwargs):
        gezien["timeout"] = kwargs.get("timeout")
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout") or 0)

    monkeypatch.setattr(facturen.shutil, "which", lambda _naam: "/usr/bin/pdftoppm")
    monkeypatch.setattr(facturen.subprocess, "run", nep)
    begin = time.perf_counter()
    assert client.get(f"/bijlage/{bijlage_id}/miniatuur").status_code == 404
    assert time.perf_counter() - begin < 0.5
    _wacht()
    assert gezien["timeout"] <= 5
    assert any(naam.endswith(".min.mislukt") for naam in os.listdir(facturen.BIJLAGE_DIR))


def test_een_traag_voorbeeld_houdt_de_pagina_niet_tegen(post, db, client, klus_id, monkeypatch):
    _bon(post, klus_id, b"%PDF-1.4\ntraag")
    bijlage_id = db.execute("SELECT id FROM bijlagen").fetchone()[0]

    def traag(*args, **kwargs):
        time.sleep(1.2)
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout") or 5)

    monkeypatch.setattr(facturen.shutil, "which", lambda _naam: "/usr/bin/pdftoppm")
    monkeypatch.setattr(facturen.subprocess, "run", traag)
    begin = time.perf_counter()
    assert client.get(f"/bijlage/{bijlage_id}/miniatuur").status_code == 404
    assert time.perf_counter() - begin < 0.8
    begin = time.perf_counter()
    assert client.get(f"/klus/{klus_id}").status_code == 200
    assert time.perf_counter() - begin < 0.8
    _wacht()


def test_het_mailscherm_toont_de_pdf_als_tegel(client, maak_factuur, monkeypatch):
    factuur_id = maak_factuur(klant="Voorbeeld Klant", email="klant@example.com")
    teller = {"n": 0}
    echt = facturen.maak_pdf

    def tel(*args, **kwargs):
        teller["n"] += 1
        return echt(*args, **kwargs)

    monkeypatch.setattr(facturen, "maak_pdf", tel)
    eerste = client.get(f"/factuur/{factuur_id}/mail").data.decode()
    assert teller["n"] == 1
    assert "PDF van de rekening" in eerste
    assert 'class="is-pdf"' not in eerste
    assert "pdf-icoon" in eerste
    if shutil.which("pdftoppm"):
        _wacht()
        tweede = client.get(f"/factuur/{factuur_id}/mail").data.decode()
        assert teller["n"] == 1
        assert 'class="is-pdf"' in tweede
        assert 'loading="lazy"' in tweede
    else:
        assert client.get(f"/factuur/{factuur_id}/mail").status_code == 200
        assert teller["n"] == 1
