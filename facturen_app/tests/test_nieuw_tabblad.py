"""Wanneer een PDF of foto in een nieuw tabblad mag openen.

In de zijbalk van Home Assistant mag dat niet. Home Assistant laat een verzoek aan
Ingress alleen door met een cookie die alleen binnen het paneel zelf geldt; de app van
Home Assistant opent een link met target="_blank" in een eigen browservenster, dat die
cookie niet heeft, en dan kreeg je "401: Unauthorized" te zien in plaats van de PDF.
Op poort 8099 is een apart tabblad juist prettig: je lijst blijft dan openstaan.
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
