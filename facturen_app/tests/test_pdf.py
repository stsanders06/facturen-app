"""De PDF: komt hij eruit, staat het juiste erop, en blijft hij heel bij randgevallen."""
import base64
import os
import re
import zlib

from conftest import facturen


def _stromen_uit_pdf(pad):
    """De inhoudsstromen van een reportlab-PDF, al uitgepakt.

    reportlab schrijft een pagina als ASCII85 met daaronder zlib. Beide lagen
    moeten eraf voordat er iets leesbaars overblijft."""
    with open(pad, "rb") as f:
        rauw = f.read()
    stukken = []
    for stroom in re.findall(rb"stream\r?\n(.*?)endstream", rauw, re.S):
        data = stroom.strip().removesuffix(b"~>")
        try:
            data = base64.a85decode(data)
        except ValueError:
            pass
        try:
            data = zlib.decompress(data)
        except zlib.error:
            pass
        stukken.append(data)
    return stukken


def tekst_uit_pdf(pad):
    """Haalt de leesbare tekst uit een reportlab-PDF. Genoeg om te controleren dat
    een naam of bedrag erop staat, zonder een extra pakket te hoeven installeren."""
    inhoud = b"\n".join(_stromen_uit_pdf(pad)).decode("latin-1")
    # Tekst staat in de inhoudsstroom als (letters) Tj, met \( voor een echte haak.
    losse = re.findall(r"\((?:[^()\\]|\\.)*\)", inhoud)
    return " ".join(_ontsnap(deel[1:-1]) for deel in losse)


def paginas_uit_pdf(pad):
    """Tekst per pagina. Een stroom zonder tekst (een lettertype) telt niet mee."""
    paginas = []
    for data in _stromen_uit_pdf(pad):
        inhoud = data.decode("latin-1")
        if "Tj" not in inhoud and "TJ" not in inhoud:
            continue
        losse = re.findall(r"\((?:[^()\\]|\\.)*\)", inhoud)
        paginas.append(" ".join(_ontsnap(deel[1:-1]) for deel in losse))
    return paginas


def _ontsnap(tekst):
    """Zet de PDF-notatie terug om in gewone letters: \\( wordt (, en \\205 wordt het
    beletselteken. reportlab schrijft accenten als WinAnsi-bytes, dus cp1252."""
    tekst = re.sub(r"\\([0-7]{1,3})", lambda m: chr(int(m.group(1), 8)), tekst)
    tekst = re.sub(r"\\(.)", r"\1", tekst)
    return tekst.encode("latin-1", "replace").decode("cp1252", "replace")


def zet_bedrijf(db, **velden):
    velden.setdefault("naam", "Voorbeeld Bedrijf BV")
    velden.setdefault("adres", "Voorbeeldstraat 1\n1234 AB Voorbeeldstad")
    velden.setdefault("iban", "NL91ABNA0417164300")
    kolommen = ", ".join(f"{k}=?" for k in velden)
    db.execute(f"UPDATE settings SET {kolommen} WHERE id=1", tuple(velden.values()))
    db.commit()


def test_rekening_levert_een_leesbare_pdf_op(db, maak_factuur):
    zet_bedrijf(db)
    pad = facturen.maak_pdf(maak_factuur())
    assert os.path.exists(pad)
    assert os.path.getsize(pad) > 1000
    with open(pad, "rb") as f:
        assert f.read(5) == b"%PDF-"


def test_op_de_rekening_staan_de_klant_het_bedrag_en_de_iban(db, maak_factuur):
    zet_bedrijf(db)
    tekst = tekst_uit_pdf(facturen.maak_pdf(maak_factuur(klant="Jan Jansen", totaal=121.0)))
    assert "Jan Jansen" in tekst
    assert "121,00" in tekst
    assert "NL91ABNA0417164300" in tekst
    assert "Rekening" in tekst


def test_contante_rekening_toont_voldaan_en_geen_bankgegevens(db, maak_factuur):
    zet_bedrijf(db)
    factuur_id = maak_factuur()
    db.execute("UPDATE facturen SET betaalmethode='cash' WHERE id=?", (factuur_id,))
    db.commit()
    tekst = tekst_uit_pdf(facturen.maak_pdf(factuur_id))
    assert "Contant afgehandeld." in tekst
    assert "NL91ABNA0417164300" not in tekst


def test_offerte_heeft_geen_betaalstrook_maar_wel_de_toelichting(db, maak_offerte):
    zet_bedrijf(db)
    tekst = tekst_uit_pdf(facturen.maak_offerte_pdf(maak_offerte()))
    assert "Offerte" in tekst
    assert "Inclusief materiaal." in tekst
    assert "NL91ABNA0417164300" not in tekst


def test_offerte_zonder_einddatum_zegt_niets_over_geldigheid(db, maak_offerte):
    zet_bedrijf(db)
    tekst = tekst_uit_pdf(facturen.maak_offerte_pdf(maak_offerte(geldig="")))
    assert "geldig tot" not in tekst


def test_offerte_met_einddatum_noemt_hem_bovenaan(db, maak_offerte):
    zet_bedrijf(db)
    tekst = tekst_uit_pdf(facturen.maak_offerte_pdf(maak_offerte(geldig="2026-09-13")))
    assert "geldig tot" in tekst


def test_tenaamstelling_wint_van_de_bedrijfsnaam(db, maak_factuur):
    zet_bedrijf(db, tenaamstelling="Test Gebruiker")
    tekst = tekst_uit_pdf(facturen.maak_pdf(maak_factuur()))
    assert "Test Gebruiker" in tekst


def test_veel_regels_lopen_door_op_een_vervolgpagina(db, maak_factuur):
    zet_bedrijf(db)
    factuur_id = maak_factuur()
    for nummer in range(40):
        db.execute(
            """INSERT INTO regels (factuur_id, omschrijving, type, aantal, prijs, subtotaal)
               VALUES (?, ?, 'materiaal', 1, 10, 10)""",
            (factuur_id, f"Onderdeel {nummer}"),
        )
    db.commit()
    tekst = tekst_uit_pdf(facturen.maak_pdf(factuur_id))
    assert "vervolg" in tekst
    assert "Onderdeel 39" in tekst


def test_een_hele_lange_omschrijving_loopt_niet_over_de_kolommen(db, maak_factuur):
    zet_bedrijf(db)
    factuur_id = maak_factuur()
    db.execute(
        """INSERT INTO regels (factuur_id, omschrijving, type, aantal, prijs, subtotaal)
           VALUES (?, ?, 'materiaal', 1, 10, 10)""",
        (factuur_id, "Kraan " * 60),
    )
    db.commit()
    tekst = tekst_uit_pdf(facturen.maak_pdf(factuur_id))
    # De hele omschrijving blijft leesbaar, verdeeld over meerdere regels.
    assert tekst.count("Kraan") >= 60
    assert "…" not in tekst


def test_ontbrekend_logo_laat_de_pdf_gewoon_doorgaan(db, maak_factuur):
    zet_bedrijf(db, logo_bestand="bestaat-niet.png")
    assert os.path.exists(facturen.maak_pdf(maak_factuur()))


def test_lege_instellingen_breken_de_pdf_niet(db, maak_factuur):
    """Een verse installatie waar nog niets is ingevuld hoort geen foutpagina te geven."""
    assert os.path.exists(facturen.maak_pdf(maak_factuur()))


def test_tekst_afbreken_houdt_zich_aan_de_breedte(db):
    from reportlab.pdfgen import canvas as rl_canvas
    c = rl_canvas.Canvas("/dev/null")
    regels = facturen._regels_afbreken(c, "een twee drie vier vijf zes zeven acht",
                                       "Helvetica", 9, 40)
    assert len(regels) > 1
    for regel in regels:
        assert c.stringWidth(regel, "Helvetica", 9) <= 40 or " " not in regel


def test_leeg_afbreken_geeft_een_lege_regel(db):
    from reportlab.pdfgen import canvas as rl_canvas
    assert facturen._regels_afbreken(rl_canvas.Canvas("/dev/null"), "", "Helvetica", 9, 40) == [""]

def test_rekening_pdf_toont_geen_type_naam_onder_regels(db, maak_factuur):
    """Op de rekening-PDF geen grijze typenaam: omschrijving + eenheid volstaan."""
    zet_bedrijf(db)
    tekst = tekst_uit_pdf(facturen.maak_pdf(maak_factuur()))
    assert "Kraan vervangen" in tekst
    assert "Arbeid per uur" not in tekst
    # Eenheid blijft wel staan (arbeid_uur → u).
    assert " u" in tekst or "2 u" in tekst


def test_offerte_pdf_toont_geen_type_naam_onder_regels(db, maak_offerte):
    """Op de offerte-PDF ook geen typenaam: zelfde als rekening."""
    zet_bedrijf(db)
    tekst = tekst_uit_pdf(facturen.maak_offerte_pdf(maak_offerte()))
    assert "Badkamer betegelen" in tekst
    assert "Arbeid, vaste prijs" not in tekst
    assert "Materiaal" not in tekst

