"""De PDF: komt hij eruit, staat het juiste erop, en blijft hij heel bij randgevallen."""
import base64
import os
import re
import zlib

import pytest
from reportlab.lib.units import mm

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


def _tekstregels_met_y(pad):
    """Per pagina de tekststukken met hun y, in punten vanaf de onderkant."""
    paginas = []
    for data in _stromen_uit_pdf(pad):
        inhoud = data.decode("latin-1")
        if "Tj" not in inhoud and "TJ" not in inhoud:
            continue
        stukken = []
        for gevonden in re.finditer(
            r"1 0 0 1 ([\d.]+) ([\d.]+) Tm \(((?:[^()\\]|\\.)*)\) Tj", inhoud
        ):
            stukken.append((
                float(gevonden.group(2)),
                _ontsnap(gevonden.group(3)),
            ))
        paginas.append(stukken)
    return paginas


# Een volle pagina eindigt hooguit één regel boven de bodem. Bij een rekening
# ligt die basislijn zo'n 23 mm boven de 72 mm; bij een offerte, met een lagere
# bodem, hooguit zo'n 26 mm. Meer dan 28 mm betekent dat er een regel is
# meegenomen terwijl hij hier nog paste. De check geldt alleen als de volgende
# pagina nog regels heeft: het slot mag eerder op, en dan blijft er ruimte over
# omdat de laatste regel niet meegaat.
LEGE_RUIMTE_MAX_MM = 28


def _document_met_regels(db, aantal, soort, toelichting="Inclusief materiaal."):
    """Een rekening of offerte met `aantal` korte regels. Korte namen, zodat
    elke regel één regel tekst is en de lege ruimte niet van het afbreken komt."""
    zet_bedrijf(db, email="info@example.org", telefoon="012 345 67 89")
    if soort == "rekening":
        doc_id = db.execute(
            """INSERT INTO facturen (nummer, datum, vervalt_op, klant_naam, klant_adres,
               klant_email, status, totaal)
               VALUES (?, '2026-10-01', '2026-10-15', 'Voorbeeld Klant',
               'Voorbeeldstraat 2\n1234 AB Voorbeeldstad', 'klant@example.org',
               'verzonden', ?)""",
            (f"2026-{aantal:03d}", aantal * 10),
        ).lastrowid
        tabel, sleutel = "regels", "factuur_id"
        teken = facturen.maak_pdf
    else:
        doc_id = db.execute(
            """INSERT INTO offertes (nummer, datum, geldig_tot, klant_naam, klant_adres,
               klant_email, status, totaal, toelichting)
               VALUES (?, '2026-10-01', '2026-11-01', 'Voorbeeld Klant',
               'Voorbeeldstraat 2\n1234 AB Voorbeeldstad', 'klant@example.org',
               'concept', ?, ?)""",
            (f"OFF-2026-{aantal:03d}", aantal * 10, toelichting),
        ).lastrowid
        tabel, sleutel = "offerte_regels", "offerte_id"
        teken = facturen.maak_offerte_pdf
    for nummer in range(1, aantal + 1):
        db.execute(
            f"""INSERT INTO {tabel} ({sleutel}, omschrijving, type, aantal, prijs, subtotaal)
                VALUES (?, ?, 'materiaal', 1, 10, 10)""",
            (doc_id, f"Voorbeeldregel {nummer:02d}"),
        )
    db.commit()
    return teken(doc_id)


def _aantal_regels(stukken):
    return sum(1 for _y, deel in stukken if deel.startswith("Voorbeeldregel "))


def _verdeling_rekening(aantal):
    """Hoe de korte regels van een rekening over de pagina's horen te liggen.

    Pagina 1 houdt er 8, een vol vervolgvel 12. Het totaal en de strook passen
    nog bij 11 regels op een vervolgvel; bij 12 niet meer, en dan komt er een
    blad met alleen dat blok. Dit is de indeling van 1.41.1 en moet zo blijven."""
    if aantal <= 8:
        return [aantal, 0]
    rest = aantal - 8
    paginas = [8]
    while rest:
        if rest <= 11:
            paginas.append(rest)
            break
        paginas.append(12)
        rest -= 12
        if rest == 0:
            paginas.append(0)
    return paginas


def _verdeling_offerte(aantal):
    """Hoe de korte regels van een offerte over de pagina's horen te liggen.

    Pagina 1 houdt er 12, een vol vervolgvel 15. Het slot (totaal en
    toelichting) past nog bij 10 regels op pagina 1 en bij 14 op een
    vervolgvel. Daarboven schuift alleen het slot; de laatste regel blijft."""
    if aantal <= 10:
        return [aantal]
    # 11 en 12 passen nog op pagina 1; het slot niet meer.
    if aantal <= 12:
        return [aantal, 0]
    rest = aantal - 12
    paginas = [12]
    while rest:
        if rest <= 14:
            paginas.append(rest)
            break
        paginas.append(15)
        rest -= 15
        if rest == 0:
            paginas.append(0)
    return paginas


@pytest.mark.parametrize("soort", ["rekening", "offerte"])
@pytest.mark.parametrize("aantal", list(range(8, 31)))
def test_regels_vullen_de_pagina_het_totaal_schuift_alleen(db, soort, aantal):
    """Regels lopen door tot de bodem. Het totaal (en de strook of de
    toelichting) gaat alleen zelf naar de volgende pagina als het er niet
    meer onder past. Een rekening houdt de indeling van 1.41.1."""
    pad = _document_met_regels(db, aantal, soort)
    paginas = _tekstregels_met_y(pad)
    assert paginas

    namen = []
    bodem = facturen.OFFERTE_REGEL_BODEM if soort == "offerte" else facturen.REGEL_BODEM
    for index, stukken in enumerate(paginas):
        van_deze = sorted(
            deel for _y, deel in stukken if deel.startswith("Voorbeeldregel ")
        )
        namen.extend(van_deze)
        if not van_deze:
            continue
        laagste = min(y for y, deel in stukken if deel.startswith("Voorbeeldregel "))
        # Een rekeningregel mag niet in de strook zakken. Een offerte mag
        # lager, tot haar eigen bodem.
        assert laagste >= bodem, (
            f"{soort} met {aantal} regels, pagina {index + 1}: "
            f"regel op {laagste / mm:.0f} mm, bodem is {bodem / mm:.0f} mm"
        )
        volgende_heeft_regels = index + 1 < len(paginas) and any(
            deel.startswith("Voorbeeldregel ") for _y, deel in paginas[index + 1]
        )
        if not volgende_heeft_regels:
            continue
        leeg = (laagste - bodem) / mm
        assert leeg <= LEGE_RUIMTE_MAX_MM, (
            f"{soort} met {aantal} regels, pagina {index + 1}: {leeg:.0f} mm leeg"
        )

    assert namen == [f"Voorbeeldregel {nummer:02d}" for nummer in range(1, aantal + 1)]
    verwacht = _verdeling_rekening(aantal) if soort == "rekening" else _verdeling_offerte(aantal)
    assert [_aantal_regels(stukken) for stukken in paginas] == verwacht

    volledig = [" ".join(deel for _y, deel in stukken) for stukken in paginas]
    for nummer, tekst in enumerate(volledig, start=1):
        assert f"Pagina {nummer} van {len(volledig)}" in tekst
    met_totaal = [i for i, tekst in enumerate(volledig) if "T O T A A L" in tekst]
    assert met_totaal == [len(volledig) - 1]
    alleen_slot = not any(deel.startswith("Voorbeeldregel ") for _y, deel in paginas[-1])
    if soort == "rekening":
        met_strook = [i for i, tekst in enumerate(volledig) if "B E T A L E N" in tekst]
        assert met_strook == met_totaal
        # Een blad met alleen het totaal heeft de vervolgkop, geen kolomkop:
        # die hoort bij regels.
        if alleen_slot:
            assert "vervolg" in volledig[-1]
            assert "O M S C H R I J V I N G" not in volledig[-1]
    else:
        assert "B E T A L E N" not in " ".join(volledig)
        # Totaal, toelichting en voetregel blijven op het laatste vel. De
        # laatste regel is daar niet mee naartoe gesleept: die staat in
        # `verwacht`, en een leeg slotblad heeft geen kolomkop.
        assert "Inclusief materiaal." in volledig[-1]
        assert "vrijblijvend" in volledig[-1]
        assert "Inclusief materiaal." not in " ".join(volledig[:-1])
        if alleen_slot:
            assert "vervolg" in volledig[-1]
            assert "O M S C H R I J V I N G" not in volledig[-1]


def test_offerte_zonder_toelichting_houdt_totaal_en_voet_bij_elkaar(db):
    """Zonder toelichting is het slot alleen het totaal en de voetregel.

    Bij 11 regels past dat nog op pagina 1. Bij 12 niet meer: dan gaan ze
    samen naar het volgende vel en blijft regel 12 staan."""
    pad = _document_met_regels(db, 11, "offerte", toelichting="")
    paginas = _tekstregels_met_y(pad)
    assert [_aantal_regels(stukken) for stukken in paginas] == [11]
    tekst = " ".join(deel for _y, deel in paginas[0])
    assert "T O T A A L" in tekst
    assert "vrijblijvend" in tekst
    assert "Pagina 1 van 1" in tekst
    assert "Inclusief materiaal." not in tekst

    pad = _document_met_regels(db, 12, "offerte", toelichting="")
    paginas = _tekstregels_met_y(pad)
    assert [_aantal_regels(stukken) for stukken in paginas] == [12, 0]
    volledig = [" ".join(deel for _y, deel in stukken) for stukken in paginas]
    assert "T O T A A L" not in volledig[0]
    assert "vrijblijvend" not in volledig[0]
    assert "T O T A A L" in volledig[1]
    assert "vrijblijvend" in volledig[1]
    assert "vervolg" in volledig[1]
    assert "O M S C H R I J V I N G" not in volledig[1]
    assert "Pagina 1 van 2" in volledig[0]
    assert "Pagina 2 van 2" in volledig[1]


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

