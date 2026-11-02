"""Leest een bon en geeft de velden terug. Alleen bereikbaar vanuit de app.

Draait als eigen container, zodat de facturen-app klein en snel blijft: de
OCR-modellen en onnxruntime wegen samen een paar honderd megabyte en zijn maar
een paar seconden per bon nodig. Valt deze container weg, dan werkt de app
gewoon door en vult Stone de bon met de hand in.

Geen inlog, want deze container staat niet op het netwerk: in
docker-compose.yml staat geen `ports`, dus alleen de app kan erbij via het
interne netwerk. Zet er nooit een poort op.
"""
import logging
import os
import subprocess
import tempfile

from flask import Flask, jsonify, request
from waitress import serve

from bon_parser import lees_bon

# Groter dan dit is geen bon. Dezelfde grens als de app per scan aanhoudt.
MAX_BYTES = 25 * 1024 * 1024
# Wat de app aanlevert. Al het andere weigeren we meteen: een onbekend soort
# door de OCR duwen kost alleen tijd. HEIC staat er expres niet bij, net als in
# BON_OCR_TYPES aan de kant van de app: opencv leest het niet, en dan is een
# duidelijke weigering beter dan een poging die altijd misgaat.
SOORTEN = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "application/pdf": ".pdf",
}
# Poppler mag niet blijven hangen op een kapotte PDF.
PDF_TIJD = 20

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "info").upper(),
                    format="[%(asctime)s] %(levelname)s: %(message)s")
log = logging.getLogger("bon-ocr")
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_BYTES

_ocr = None


def ocr():
    """De OCR één keer opbouwen en daarna hergebruiken.

    Het opbouwen leest de modellen in en duurt een seconde of twee. Dat hoort
    niet bij elke bon opnieuw te gebeuren.
    """
    global _ocr
    if _ocr is None:
        from rapidocr import RapidOCR
        draden = int(os.environ.get("OCR_DRADEN", "4"))
        _ocr = RapidOCR(params={
            "Global.log_level": "error",
            "EngineConfig.onnxruntime.intra_op_num_threads": draden,
        })
    return _ocr


def regels_uit_vakken(vakken, teksten):
    """Zet de losse tekstvakken om in regels, van boven naar beneden.

    De OCR geeft per stukje tekst een vakje terug, niet een regel. Wat op
    dezelfde hoogte ligt hoort bij elkaar. Een bon ligt zelden recht op de
    scanner, dus eerst de scheefstand eruit: de mediaan van de hoek van alle
    bovenranden is de scheefstand van de hele bon, en daarmee rekenen we de
    middelpunten terug. Zonder die correctie lopen de regels van een schuine
    foto door elkaar heen.
    """
    import numpy as np

    hoeken = [np.arctan2(np.array(v)[1][1] - np.array(v)[0][1],
                         np.array(v)[1][0] - np.array(v)[0][0]) for v in vakken]
    hoek = -float(np.median(hoeken))
    cos, sin = np.cos(hoek), np.sin(hoek)
    stukken = []
    for vak, tekst in zip(vakken, teksten):
        vak = np.array(vak, dtype=float)
        midden_x, midden_y = vak[:, 0].mean(), vak[:, 1].mean()
        y = sin * midden_x + cos * midden_y
        x = cos * vak[:, 0].min() - sin * midden_y
        stukken.append((y, x, vak[:, 1].max() - vak[:, 1].min(), tekst))
    stukken.sort()
    uit = []
    for y, x, hoogte, tekst in stukken:
        # Minder dan een halve regelhoogte verschil is dezelfde regel.
        if uit and abs(uit[-1]["y"] - y) < 0.5 * hoogte:
            uit[-1]["delen"].append((x, tekst))
        else:
            uit.append({"y": y, "delen": [(x, tekst)]})
    return ["   ".join(t for x, t in sorted(r["delen"])) for r in uit]


# Een bon met een tekstlaag levert honderden tekens. Een gescande PDF levert
# niets, of een enkel los teken dat de scanner erbij heeft verzonnen.
TEKSTLAAG_MINIMUM = 120
TEKSTLAAG_REGELS = 6
# Kleiner dan dit is geen bon maar een logo of een barcode. De bon van de
# Karwei-app heeft een logo van 114x87 en een barcode van 350x25; die eruit
# halen en lezen gaf alleen de winkelnaam en verder niets.
PLAATJE_MINIMUM = 500


def tekst_uit_pdf(pad):
    """De tekstlaag van een PDF, als die er is.

    Een bon die je rechtstreeks uit de app van een winkel deelt is geen scan
    maar een PDF met echte tekst erin. Die tekst gebruiken is niet alleen veel
    sneller dan OCR (milliseconden tegen tien tot twintig seconden op een Pi),
    hij is ook foutloos: geen 'Subtoteal' en geen verwisselde letters.
    """
    try:
        klaar = subprocess.run(["pdftotext", "-layout", pad, "-"],
                               capture_output=True, text=True, timeout=PDF_TIJD)
    except (OSError, subprocess.SubprocessError):
        return None
    tekst = klaar.stdout or ""
    regels = [r for r in tekst.splitlines() if r.strip()]
    if len(tekst.strip()) < TEKSTLAAG_MINIMUM or len(regels) < TEKSTLAAG_REGELS:
        return None
    return "\n".join(regels)


def plaatje_uit_pdf(pad):
    """Het plaatje uit een PDF, voor de OCR.

    Een scan van de iPhone is een PDF met een foto erin en zonder tekstlaag.
    Dat ingesloten plaatje eruit halen is sneller en zuiniger dan de bladzijde
    op 300 dpi natekenen, en de OCR leest het net zo goed. Lukt dat niet, dan
    alsnog natekenen: een PDF van een andere bron heeft soms echte opmaak.
    """
    stam = pad + "-uit"
    try:
        subprocess.run(["pdfimages", "-png", "-f", "1", "-l", "1", pad, stam],
                       check=True, timeout=PDF_TIJD,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        gevonden = [p for p in _naast(stam) if p.endswith(".png")
                    and _groot_genoeg(p)]
        # Een bon met een logo levert meer plaatjes; het grootste is de bon.
        if gevonden:
            return max(gevonden, key=lambda p: os.path.getsize(p))
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        subprocess.run(["pdftoppm", "-r", "200", "-png", "-f", "1", "-l", "1",
                        pad, stam],
                       check=True, timeout=PDF_TIJD,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    gevonden = sorted(p for p in _naast(stam) if p.endswith(".png"))
    return gevonden[0] if gevonden else None


def _groot_genoeg(pad):
    """Of dit plaatje de bon kan zijn, of alleen een logo of barcode."""
    try:
        import cv2
        beeld = cv2.imread(pad)
        if beeld is None:
            return False
        hoogte, breedte = beeld.shape[:2]
        return max(hoogte, breedte) >= PLAATJE_MINIMUM
    except Exception:
        # Kunnen we het niet meten, dan maar proberen: een te klein plaatje
        # levert hooguit een lege bon op, en die valt op.
        return True


def _naast(stam):
    """De bestanden die poppler naast `stam` heeft gezet."""
    map_naam = os.path.dirname(stam) or "."
    begin = os.path.basename(stam)
    return [os.path.join(map_naam, naam) for naam in os.listdir(map_naam)
            if naam.startswith(begin)]


@app.route("/lees", methods=["POST"])
def lees():
    """De bon staat in de body, het soort in Content-Type."""
    soort = (request.content_type or "").split(";")[0].strip().lower()
    if soort not in SOORTEN:
        return jsonify({"fout": f"Dit soort bestand kan ik niet lezen: {soort}"}), 415
    ruw = request.get_data()
    if not ruw:
        return jsonify({"fout": "Er zat geen bestand bij het verzoek."}), 400

    tekst = None
    via = "ocr"
    with tempfile.TemporaryDirectory(prefix="bon-") as tijdelijk:
        pad = os.path.join(tijdelijk, "bon" + SOORTEN[soort])
        with open(pad, "wb") as doel:
            doel.write(ruw)
        if SOORTEN[soort] == ".pdf":
            # Heeft de PDF echte tekst, dan is dat beter dan wat OCR ervan
            # maakt: foutloos en in een oogwenk klaar.
            tekst = tekst_uit_pdf(pad)
            if tekst is not None:
                via = "tekstlaag"
            else:
                pad = plaatje_uit_pdf(pad)
                if not pad:
                    return jsonify({"fout": "Uit deze PDF komt geen plaatje."}), 422
        if tekst is None:
            try:
                gelezen = ocr()(pad)
            except Exception:
                # Wat de OCR ook overkomt: de app hoort hier geen 500 op te
                # krijgen, want dan lijkt het of de scan stuk is in plaats van
                # onleesbaar.
                log.exception("De OCR liep vast op een bon")
                return jsonify({"fout": "De bon is niet te lezen."}), 422
            tekst = ("" if gelezen.boxes is None
                     else "\n".join(regels_uit_vakken(gelezen.boxes, gelezen.txts)))

    uit = lees_bon(tekst)
    # De regels gaan mee terug: de app bewaart ze bij een correctie, zodat er
    # later te meten is waar de parser misging. Zie DOCKER.md.
    uit["ocr_regels"] = tekst.splitlines()
    log.info("Bon gelezen via %s: %s regels tekst, %s artikelen, onzeker: %s",
             via, len(uit["ocr_regels"]), len(uit["regels"]),
             ",".join(uit["onzeker"]) or "-")
    return jsonify(uit)


@app.route("/gezond")
def gezond():
    """Voor de healthcheck. Zegt niets over de modellen: die worden pas bij de
    eerste bon ingelezen, en een healthcheck mag dat niet uitlokken."""
    return "ok", 200


if __name__ == "__main__":
    poort = int(os.environ.get("PORT", "8099"))
    log.info("bon-ocr luistert op %s", poort)
    # Eén draad: twee bonnen tegelijk lezen verdubbelt het geheugen en de Pi
    # heeft er maar één nodig. De app stuurt ze toch achter elkaar.
    serve(app, host="0.0.0.0", port=poort, threads=1, ident=None)
