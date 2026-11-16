"""Een bestand uploaden op de pagina Scans, naast de opdracht op de iPhone."""
import re

from conftest import facturen
from test_scans import bestand, png_bytes


def _upload(post, *bestanden, **opties):
    return post("/scans/uploaden", {"bestand": list(bestanden)},
                content_type="multipart/form-data", **opties)


def test_een_geupload_bestand_staat_in_de_inbox(client, post, db):
    antwoord = _upload(post, bestand(png_bytes(), "bon-karwei.png"), follow_redirects=True)
    pagina = antwoord.data.decode()
    assert "Scan ontvangen: bon-karwei.png" in pagina
    rij = db.execute("SELECT naam, soort FROM scans").fetchone()
    assert tuple(rij) == ("bon-karwei.png", "png")


def test_meerdere_bestanden_tegelijk(client, post, db):
    _upload(post, bestand(png_bytes(), "a.png"), bestand(png_bytes(), "b.png"))
    assert db.execute("SELECT count(*) FROM scans").fetchone()[0] == 2


def test_een_geupload_bestand_wordt_gelezen(client, post, db, monkeypatch):
    gepland = []
    monkeypatch.setattr(facturen, "_plan_bonherkenning", lambda rij: gepland.append(rij))
    _upload(post, bestand(png_bytes(), "bon.png"))
    assert len(gepland) == 1


def test_geen_pdf_of_foto_wordt_geweigerd(client, post, db):
    antwoord = _upload(post, bestand(b"gewoon wat tekst", "lijstje.txt"), follow_redirects=True)
    assert "geen PDF of foto" in antwoord.data.decode()
    assert db.execute("SELECT count(*) FROM scans").fetchone()[0] == 0


def test_zonder_bestand_een_nette_melding(client, post, db):
    antwoord = post("/scans/uploaden", {}, follow_redirects=True)
    assert "Kies eerst een bestand." in antwoord.data.decode()


def test_zonder_inlog_kan_niemand_uploaden(uitgelogde_client, db):
    uitgelogde_client.post("/scans/uploaden", data={"bestand": bestand(png_bytes(), "x.png")},
                           content_type="multipart/form-data")
    assert db.execute("SELECT count(*) FROM scans").fetchone()[0] == 0


def test_de_lege_inbox_heeft_de_uploadknop_en_de_nieuwe_tekst(client):
    pagina = client.get("/scans").data.decode()
    assert "Ingescande bonnen belanden hier en worden automatisch uitgelezen." in pagina
    assert "Naar de uitleg" not in pagina
    assert re.search(r'<button[^>]*data-kiest="scan-uploaden"[^>]*>Uploaden</button>', pagina)
    assert 'id="scan-uploaden"' in pagina


def test_met_scans_staat_de_knop_bovenaan(client, post, db):
    _upload(post, bestand(png_bytes(), "bon.png"))
    pagina = client.get("/scans").data.decode()
    kop = re.search(r'<div class="page-head">.*?</div>\s*</div>', pagina, re.S).group(0)
    assert 'data-kiest="scan-uploaden"' in kop
