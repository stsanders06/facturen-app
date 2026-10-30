"""Eerste pagina van een PDF als klein voorbeeld.

Zonder pdftoppm, of bij een kapot bestand, blijft het grijze vak. De upload
wacht niet op het tekenen: het plaatje ontstaat pas als de tegel het vraagt.
"""
import io
import os
import shutil
import struct

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
        follow_redirects=True,
    )


def test_upload_tekent_de_pdf_nog_niet(post, db, klus_id):
    _bon(post, klus_id, _echte_pdf())
    assert not any(naam.endswith(".min.png") for naam in os.listdir(facturen.BIJLAGE_DIR))


@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="pdftoppm ontbreekt")
def test_een_pdf_krijgt_een_plaatje_van_de_eerste_pagina(post, db, client, klus_id):
    _bon(post, klus_id, _echte_pdf())
    bijlage_id = db.execute("SELECT id FROM bijlagen").fetchone()[0]
    pagina = client.get(f"/klus/{klus_id}").data.decode()
    assert f"/bijlage/{bijlage_id}/miniatuur" in pagina
    assert 'loading="lazy"' in pagina
    assert 'data-soort="pdf"' in pagina
    assert 'class="is-pdf"' in pagina
    # Het grijze vak blijft in de pagina, voor als het tekenen mislukt.
    assert ">PDF<" in pagina

    antwoord = client.get(f"/bijlage/{bijlage_id}/miniatuur")
    assert antwoord.status_code == 200
    assert antwoord.mimetype == "image/png"
    assert antwoord.headers["X-Content-Type-Options"] == "nosniff"
    assert "max-age=86400" in antwoord.headers["Cache-Control"]
    assert "inline" in antwoord.headers["Content-Disposition"]
    breedte, hoogte = _png_afmeting(antwoord.data)
    assert breedte > 40 and hoogte > 40
    assert any(naam.endswith(".min.png") for naam in os.listdir(facturen.BIJLAGE_DIR))

    # De tweede keer komt het van schijf.
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
    assert client.get(f"/bijlage/{rij['id']}/miniatuur").status_code == 200
    assert aanroepen["n"] == 1


def test_verwijderen_wist_het_voorbeeld(post, db, client, klus_id, monkeypatch):
    _bon(post, klus_id, b"%PDF-1.4\nkapot")
    rij = db.execute("SELECT id, bestand FROM bijlagen").fetchone()
    monkeypatch.setattr(facturen.shutil, "which", lambda _naam: "/usr/bin/pdftoppm")
    client.get(f"/bijlage/{rij['id']}/miniatuur")
    assert any(naam.startswith(rij["bestand"] + ".min.") for naam in os.listdir(facturen.BIJLAGE_DIR))
    post(f"/bijlage/{rij['id']}/verwijder", follow_redirects=True)
    # Het bestand zelf blijft in de prullenbak. Het voorbeeld niet.
    assert os.path.isfile(os.path.join(facturen.BIJLAGE_DIR, rij["bestand"]))
    assert not any(
        naam.startswith(rij["bestand"] + ".min.")
        for naam in os.listdir(facturen.BIJLAGE_DIR)
    )
