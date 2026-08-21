"""Hoe een PDF of foto opengaat, en dat verschilt per plek waar je binnenkomt.

In de zijbalk van Home Assistant kan een bestand niet in een eigen tabblad open: zo'n
venster heeft de cookie van Ingress niet en Home Assistant antwoordt met "401:
Unauthorized". In hetzelfde venster tonen is ook niets — de PDF-weergave van iOS laat
daar alleen het eerste blad zien. Daarom wordt het in de zijbalk een download.
Op poort 8099 speelt dat allemaal niet en blijft bekijken bekijken, in een apart
tabblad, zodat je lijst openstaat.
"""
# Zo doet een verzoek zich voor als komend uit de zijbalk van Home Assistant.
VIA_ZIJBALK = {"HTTP_X_INGRESS_PATH": "/api/hassio_ingress/abc123"}


def paginas_met_een_pdf_link(client, maak_factuur, maak_offerte, db, extra=None):
    """De pagina's waar een PDF, offerte of foto vanaf te openen is."""
    factuur_id = maak_factuur()
    maak_offerte()
    klant_id = db.execute("INSERT INTO klanten (naam) VALUES ('Jan Jansen')").lastrowid
    db.execute("UPDATE facturen SET klant_id=? WHERE id=?", (klant_id, factuur_id))
    db.commit()
    return ["/", "/offertes", f"/klant/{klant_id}"]


def test_op_poort_8099_opent_een_pdf_in_een_nieuw_tabblad(client, maak_factuur, maak_offerte, db):
    for pad in paginas_met_een_pdf_link(client, maak_factuur, maak_offerte, db):
        inhoud = client.get(pad).data.decode()
        assert 'target="_blank"' in inhoud, pad
        assert 'rel="noopener"' in inhoud, pad


def test_in_de_zijbalk_opent_een_pdf_in_hetzelfde_venster(client, maak_factuur, maak_offerte, db):
    """Anders antwoordt Home Assistant met 401 in plaats van de PDF te tonen."""
    for pad in paginas_met_een_pdf_link(client, maak_factuur, maak_offerte, db):
        inhoud = client.get(pad, environ_overrides=VIA_ZIJBALK).data.decode()
        assert 'target="_blank"' not in inhoud, pad


def test_een_foto_bij_een_klus_volgt_dezelfde_regel(client, db):
    klus_id = db.execute(
        """INSERT INTO klussen (naam, uurtarief, gestart)
           VALUES ('Vloer reinigen', 47.5, '2026-08-10')""").lastrowid
    db.execute(
        """INSERT INTO bijlagen (klus_id, bestand, naam, toegevoegd)
           VALUES (?, 'foto.png', 'Vloer voor het reinigen.png', '2026-08-10')""",
        (klus_id,))
    db.commit()

    assert 'target="_blank"' in client.get(f"/klus/{klus_id}").data.decode()
    assert 'target="_blank"' not in client.get(
        f"/klus/{klus_id}", environ_overrides=VIA_ZIJBALK).data.decode()


def test_in_de_zijbalk_komt_een_pdf_als_download_binnen(client, maak_factuur):
    """In het venster zelf toont iOS alleen het eerste blad van een PDF."""
    factuur_id = maak_factuur()
    antwoord = client.get(f"/factuur/{factuur_id}/bekijk", environ_overrides=VIA_ZIJBALK)
    assert antwoord.status_code == 200
    assert antwoord.headers["Content-Disposition"].startswith("attachment")


def test_op_poort_8099_blijft_bekijken_gewoon_bekijken(client, maak_factuur):
    factuur_id = maak_factuur()
    antwoord = client.get(f"/factuur/{factuur_id}/bekijk")
    assert antwoord.headers["Content-Disposition"].startswith("inline")


def test_een_offerte_volgt_dezelfde_regel(client, maak_offerte):
    offerte_id = maak_offerte()
    in_zijbalk = client.get(f"/offerte/{offerte_id}/bekijk", environ_overrides=VIA_ZIJBALK)
    assert in_zijbalk.headers["Content-Disposition"].startswith("attachment")
    assert client.get(f"/offerte/{offerte_id}/bekijk"
                      ).headers["Content-Disposition"].startswith("inline")
