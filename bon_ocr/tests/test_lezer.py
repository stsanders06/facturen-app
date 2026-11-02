"""De keuze die de lezer maakt: tekstlaag, ingesloten plaatje, of natekenen.

Hier draait geen OCR. Deze tests gaan over de vraag wélke invoer de parser
krijgt, en dat is precies waar het misging: een bon die je rechtstreeks uit de
app van een winkel deelt is geen scan maar een PDF met echte tekst, en het
enige plaatje erin is het logo. Dat logo werd gelezen, en dus kwam er alleen
een winkelnaam uit en verder niets.
"""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# lezer.py importeren trekt Flask en waitress mee; die horen niet bij de
# parsertests, dus overslaan als ze er niet zijn.
lezer = pytest.importorskip("lezer", reason="lezer.py heeft Flask en waitress nodig")

BONNEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bonnen")
HEEFT_POPPLER = all(
    subprocess.run(["which", naam], capture_output=True).returncode == 0
    for naam in ("pdftotext", "pdftoppm")
)
poppler = pytest.mark.skipif(not HEEFT_POPPLER, reason="poppler niet geïnstalleerd")


def _maak_pdf(pad, tekst):
    """Een PDF met echte tekst erin, zonder extra pakketten."""
    regels = tekst.splitlines()
    inhoud = "BT /F1 10 Tf 40 760 Td 12 TL\n"
    for regel in regels:
        veilig = regel.replace("\\", "").replace("(", "").replace(")", "")
        inhoud += f"({veilig}) Tj T*\n"
    inhoud += "ET"
    stukken = [
        "%PDF-1.4\n",
        "1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n",
        "2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n",
        "3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]"
        "/Resources<</Font<</F1 5 0 R>>>>/Contents 4 0 R>>endobj\n",
        f"4 0 obj<</Length {len(inhoud)}>>stream\n{inhoud}\nendstream endobj\n",
        "5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Courier>>endobj\n",
    ]
    ruw = "".join(stukken)
    ruw += ("trailer<</Root 1 0 R/Size 6>>\n%%EOF\n")
    with open(pad, "wb") as doel:
        doel.write(ruw.encode("latin-1"))
    return pad


@poppler
def test_een_pdf_met_tekst_wordt_niet_door_de_ocr_gehaald(tmp_path):
    """Dit is de bon uit de winkel-app: foutloos én in een oogwenk klaar."""
    with open(os.path.join(BONNEN, "echt02_karwei_tekstlaag.txt"), encoding="utf-8") as bron:
        tekst = bron.read()
    pad = _maak_pdf(str(tmp_path / "bon.pdf"), tekst)
    uit = lezer.tekst_uit_pdf(pad)
    assert uit is not None
    assert "Totaal" in uit
    assert "KABELDOOS" in uit


@poppler
def test_een_lege_pdf_levert_geen_tekstlaag(tmp_path):
    """Een gescande bon heeft geen tekst; dan moet de OCR eraan te pas komen."""
    pad = _maak_pdf(str(tmp_path / "leeg.pdf"), "x")
    assert lezer.tekst_uit_pdf(pad) is None


@poppler
def test_een_paar_losse_tekens_tellen_niet_als_tekstlaag(tmp_path):
    """Een scanner zet er soms een enkel teken in. Dat is geen bon."""
    pad = _maak_pdf(str(tmp_path / "bijna.pdf"), "KAR\nWEI\n12,50")
    assert lezer.tekst_uit_pdf(pad) is None


def test_een_logo_is_niet_de_bon(tmp_path):
    """Het logo van de Karwei-bon is 114x87 en de barcode 350x25. Werden die
    als bon gelezen, dan kwam er alleen een winkelnaam uit."""
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2", reason="opencv zit alleen in de lezer")

    logo = str(tmp_path / "logo.png")
    cv2.imwrite(logo, np.full((87, 114, 3), 255, dtype="uint8"))
    barcode = str(tmp_path / "barcode.png")
    cv2.imwrite(barcode, np.full((25, 350, 3), 255, dtype="uint8"))
    bon = str(tmp_path / "bon.png")
    cv2.imwrite(bon, np.full((1600, 900, 3), 255, dtype="uint8"))

    assert lezer._groot_genoeg(logo) is False
    assert lezer._groot_genoeg(barcode) is False
    assert lezer._groot_genoeg(bon) is True


def test_een_onmeetbaar_plaatje_wordt_toch_geprobeerd(tmp_path):
    """Liever een lege bon die opvalt dan een bestand dat stil wordt genegeerd."""
    pytest.importorskip("cv2", reason="opencv zit alleen in de lezer")
    stuk = str(tmp_path / "stuk.png")
    with open(stuk, "wb") as doel:
        doel.write(b"dit is geen plaatje")
    assert lezer._groot_genoeg(stuk) is False


@pytest.mark.parametrize("soort,ext", [
    ("image/jpeg", ".jpg"), ("image/png", ".png"), ("application/pdf", ".pdf"),
])
def test_de_soorten_die_de_lezer_aanneemt(soort, ext):
    assert lezer.SOORTEN[soort] == ext


def test_heic_wordt_niet_aangenomen():
    """opencv leest het niet; een belofte die misgaat is erger dan een weigering."""
    assert "image/heic" not in lezer.SOORTEN
