"""Eerste pagina van een PDF als klein voorbeeld.

Zonder pdftoppm, of bij een kapot bestand, blijft het grijze vak. De upload
wacht niet op het tekenen: het plaatje ontstaat pas als de tegel het vraagt.
"""
import io
import os
import shutil
import signal
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
    assert 'class="pdf-tegel"' in eerste
    _wacht()
    pagina = client.get(f"/klus/{klus_id}").data.decode()
    assert f"/bijlage/{bijlage_id}/miniatuur" in pagina
    assert 'loading="lazy"' in pagina
    assert 'data-soort="pdf"' in pagina
    assert 'class="is-pdf"' in pagina
    assert 'class="pdf-tegel"' in pagina
    # De regel moet winnen van .bijlage img, anders blijft het midden staan.
    assert "object-position: top center" in pagina

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
    echt = facturen._run_pdftoppm

    def tel(cmd):
        aanroepen["n"] += 1
        return echt(cmd)

    monkeypatch.setattr(facturen.shutil, "which", lambda _naam: "/usr/bin/pdftoppm")
    monkeypatch.setattr(facturen, "_run_pdftoppm", tel)
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
    echt = facturen._run_pdftoppm

    def tel(cmd):
        aanroepen["n"] += 1
        return echt(cmd)

    monkeypatch.setattr(facturen, "_run_pdftoppm", tel)
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

    def nep(_cmd):
        raise subprocess.TimeoutExpired(_cmd, facturen._MINI_TIJD)

    monkeypatch.setattr(facturen.shutil, "which", lambda _naam: "/usr/bin/pdftoppm")
    monkeypatch.setattr(facturen, "_run_pdftoppm", nep)
    begin = time.perf_counter()
    assert client.get(f"/bijlage/{bijlage_id}/miniatuur").status_code == 404
    assert time.perf_counter() - begin < 0.5
    _wacht()
    assert facturen._MINI_TIJD <= 5
    assert any(naam.endswith(".min.mislukt") for naam in os.listdir(facturen.BIJLAGE_DIR))


def test_een_traag_voorbeeld_houdt_de_pagina_niet_tegen(post, db, client, klus_id, monkeypatch):
    _bon(post, klus_id, b"%PDF-1.4\ntraag")
    bijlage_id = db.execute("SELECT id FROM bijlagen").fetchone()[0]

    def traag(_cmd):
        time.sleep(1.2)
        raise subprocess.TimeoutExpired(_cmd, facturen._MINI_TIJD)

    monkeypatch.setattr(facturen.shutil, "which", lambda _naam: "/usr/bin/pdftoppm")
    monkeypatch.setattr(facturen, "_run_pdftoppm", traag)
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
        assert 'class="pdf-wacht"' in eerste
        assert 'data-mini="' in eerste
    else:
        assert 'class="pdf-wacht"' not in eerste
    if shutil.which("pdftoppm"):
        _wacht()
        tweede = client.get(f"/factuur/{factuur_id}/mail").data.decode()
        assert teller["n"] == 1
        assert 'class="is-pdf"' in tweede
        assert 'loading="lazy"' in tweede
    else:
        assert client.get(f"/factuur/{factuur_id}/mail").status_code == 200
        assert teller["n"] == 1


def _tegel(html):
    start = html.find('class="pdf-tegel"')
    assert start != -1
    return html[start:start + 700]


def test_de_pagina_vraagt_het_voorbeeld_zelf_op(post, db, client, klus_id, monkeypatch):
    """Zonder script blijft het grijze vak. Met script komt het plaatje erin
    zodra het er is, zonder de pagina opnieuw te laden."""
    _bon(post, klus_id, _echte_pdf())

    def langzaam(cmd):
        time.sleep(0.3)
        with open(cmd[-1] + ".png", "wb") as uit:
            uit.write(b"\x89PNG\r\n\x1a\n")

    monkeypatch.setattr(facturen.shutil, "which", lambda _naam: "/usr/bin/pdftoppm")
    monkeypatch.setattr(facturen, "_run_pdftoppm", langzaam)
    eerste = client.get(f"/klus/{klus_id}").data.decode()
    tegel = _tegel(eerste)
    assert 'class="bestandsicoon pdf-wacht"' in tegel
    assert "data-mini=" in tegel
    assert 'data-beeld="is-pdf"' in tegel
    assert 'class="is-pdf"' not in tegel
    assert "onerror" not in tegel
    # De pauzes samen zijn een kwart minuut. Daarna blijft het grijze vak.
    assert "[400, 1200, 3400, 10000]" in eerste
    _wacht()
    tweede = client.get(f"/klus/{klus_id}").data.decode()
    tegel = _tegel(tweede)
    assert 'class="is-pdf"' in tegel
    assert "pdf-wacht" not in tegel
    assert "onerror" not in tegel
    assert 'data-soort="pdf"' in tegel


def test_oude_paginanamen_verdwijnen_met_het_voorbeeld(post, db, klus_id):
    """Zonder -singlefile bleef min-01.png of min-00001.png achter."""
    _bon(post, klus_id, b"%PDF-1.4\noud")
    rij = db.execute("SELECT id, bestand FROM bijlagen").fetchone()
    for extra in (".min-01.png", ".min-00001.png", ".min.png", ".min.stempel"):
        open(os.path.join(facturen.BIJLAGE_DIR, rij["bestand"] + extra), "wb").close()
    # Een andere PDF in dezelfde map mag zijn oude pagina houden.
    open(os.path.join(facturen.BIJLAGE_DIR, "andere.min-01.png"), "wb").close()
    post(f"/bijlage/{rij['id']}/verwijder", follow_redirects=True)
    namen = set(os.listdir(facturen.BIJLAGE_DIR))
    assert not any(naam.startswith(rij["bestand"] + ".min") for naam in namen)
    assert os.path.isfile(os.path.join(facturen.BIJLAGE_DIR, rij["bestand"]))
    assert "andere.min-01.png" in namen


def test_verwijderen_tijdens_tekenen_laat_geen_stempel_achter(
        post, db, client, klus_id, monkeypatch):
    _bon(post, klus_id, _echte_pdf())
    rij = db.execute("SELECT id, bestand FROM bijlagen").fetchone()
    bron = os.path.join(facturen.BIJLAGE_DIR, rij["bestand"])

    def wis_de_bron(cmd):
        os.remove(bron)
        with open(cmd[-1] + ".png", "wb") as uit:
            uit.write(b"\x89PNG\r\n\x1a\n")

    monkeypatch.setattr(facturen.shutil, "which", lambda _naam: "/usr/bin/pdftoppm")
    monkeypatch.setattr(facturen, "_run_pdftoppm", wis_de_bron)
    assert client.get(f"/bijlage/{rij['id']}/miniatuur").status_code == 404
    _wacht()
    namen = os.listdir(facturen.BIJLAGE_DIR)
    assert not any(naam.startswith(rij["bestand"]) for naam in namen)


def test_een_trage_pdftoppm_stopt_met_zijn_groep(monkeypatch):
    monkeypatch.setattr(facturen, "_MINI_TIJD", 0.3)
    gezien = {}
    echt = subprocess.Popen

    def popen(cmd, **kwargs):
        gezien["sessie"] = kwargs.get("start_new_session")
        proc = echt(cmd, **kwargs)
        gezien["pid"] = proc.pid
        return proc

    monkeypatch.setattr(facturen.subprocess, "Popen", popen)
    begin = time.perf_counter()
    with pytest.raises(subprocess.TimeoutExpired):
        facturen._run_pdftoppm(["sleep", "30"])
    assert time.perf_counter() - begin < 2
    assert gezien["sessie"] is True
    assert not os.path.exists(f"/proc/{gezien['pid']}")


def test_afsluiten_stopt_een_open_pdftoppm():
    proc = subprocess.Popen(
        ["sleep", "30"], start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    facturen._MINI_PROCESSEN.add(proc)
    klaar = False
    try:
        facturen._stop_open_miniaturen()
        proc.wait(timeout=2)
        klaar = proc.poll() is not None
    finally:
        facturen._MINI_PROCESSEN.discard(proc)
        if proc.poll() is None:
            proc.kill()
    assert klaar


def test_sigterm_stopt_eerst_de_kinderen(monkeypatch):
    gestopt = {"n": 0}
    gedood = {}
    monkeypatch.setattr(
        facturen, "_stop_open_miniaturen",
        lambda: gestopt.__setitem__("n", gestopt["n"] + 1),
    )
    monkeypatch.setattr(facturen.os, "kill", lambda pid, sig: gedood.update(pid=pid, sig=sig))
    monkeypatch.setattr(facturen.signal, "signal", lambda *_args: None)
    facturen._bij_sigterm(signal.SIGTERM, None)
    assert gestopt["n"] == 1
    assert gedood["pid"] == os.getpid()
    assert gedood["sig"] == signal.SIGTERM
