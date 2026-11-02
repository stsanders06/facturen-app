"""Maakt velden van de tekst die de OCR van een bon heeft gelezen.

De OCR zelf leest bonnen goed: in de proef kwamen alle zes echte bonnen er
leesbaar uit, ook de oude telefoonfoto's. Alle fouten zaten in het omzetten
naar velden, en dus zit hier het werk. Een taalmodel is als alternatief
gemeten en viel af: RapidOCR met Qwen2.5-3B had 0 van de 6 blinde bonnen goed
in 38 seconden en 2,5 GB, tegen 4 van de 6 in 0,7 seconde met deze regels.
Erger dan de score is de soort fout: een regel die een bedrag niet snapt laat
het veld leeg en dat zie je, een taalmodel vult 29,95 in waar 29,97 staat en
dat glipt erdoor.

De regels hier zijn gegroeid op echte bonnen van Action, Karwei en Bauhaus en
daarna op nepbonnen nagerekend. Ze zijn dus na het zien van die bonnen
geschreven: op een winkel die er niet bij zat gaat dit minder goed. Vandaar
`onzeker`: elk veld dat leeg bleef of door een rekencontrole viel, komt daar
in te staan, en de app markeert het zodat Stone er langs moet.
"""
import difflib
import re

# Winkels die we bij naam kennen. De eerste zes zijn de nepbonnen uit de
# testset, de rest zijn de bouwmarkten waar in de praktijk gekocht wordt.
# Uitbreiden mag: de lijst wordt alleen gebruikt om een naam te herkennen.
BEKENDE_WINKELS = [
    "KLUSWERK", "BOUWMAX", "DOEMARKT", "GROTHAUS", "De Kwast",
    "IJzerwarenhandel Brons",
    "Action", "Karwei", "Bauhaus", "Gamma", "Praxis", "Hornbach", "Hema",
]

# Een bedrag heeft altijd twee cijfers achter de komma. De blik vooruit houdt
# '12,345' buiten de deur: dat is geen bedrag maar een artikelnummer met komma.
BEDRAG = r"-?\d{1,5}[,.]\d{2}(?!\d)"

# Een bedrag dat los staat, niet tegen letters of cijfers aan. Zo blijft een
# maat in een omschrijving ('Laminaat eiken 2,13m2') buiten beschouwing.
LOS_BEDRAG = r"(?<![A-Za-z0-9])" + BEDRAG + r"(?![A-Za-z0-9])"

# Regels die geld van het totaal afhalen, na de artikelen.
AFTREKPOSTEN = ("korting", "aanbieding", "actie", "voordeel")

# Aftrek die al in de prijs van het artikel zit. Karwei drukt
# 'Aanbieding t/m 16-08-2026  -13,35' af en zet de uitkomst eronder als
# 'Nettoprijs  31,14'; die regel nog eens van het totaal aftrekken zou het
# bedrag dubbel verlagen. Zonder dit werd de aanbieding bovendien een artikel
# met een eigen bedrag, en telde de bon helemaal niet meer op.
AL_VERREKEND = re.compile(r"prijs\s?correctie|prijsvoordeel|aanbieding|^actie\b", re.I)

# Wat op de bon staat, en wat wij het noemen. De volgorde telt: 'maestro' moet
# vóór 'pin' staan, want op een Maestro-bon staat vaak beide.
BETAALWIJZEN = [
    ("contant", "contant"), ("cash", "contant"),
    ("maestro", "pin"), ("v ?pay", "pin"), ("pin", "pin"), ("bankpas", "pin"),
    ("mastercard", "creditcard"), ("visa", "creditcard"),
    ("creditcard", "creditcard"), ("amex", "creditcard"),
]


def _normaliseer(regel):
    """Poetst de ruis weg die de OCR er standaard in legt."""
    r = (regel.replace("×", "x").replace("à", "@").replace("á", "@")
         .replace("€", " ").replace("：", ":"))
    # Een o tussen cijfers is een nul die als letter is gelezen.
    r = re.sub(r"(?<=\d)[oO](?=[\d,])|(?<=[\d,])[oO](?=\d)", "0", r)
    r = re.sub(r"(?<=\d) ?, ?(?=\d\d(?!\d))", ",", r)
    r = re.sub(r"^\s*a\s+(?=\d)", "@ ", r)
    # Bauhaus zet de btw-code tegen het bedrag aan: '7,751' is 7,75 met code 1.
    r = re.sub(r"(\d[,.]\d\d)[0-9]\s*$", r"\1", r)
    # Action begint de aantalregel met een isgelijkteken: '= 2 x 7,95'.
    r = re.sub(r"^\s*=\s*", "", r)
    # Bauhaus schrijft het maalteken als sterretje: '4 * 5,79'.
    r = re.sub(r"(?<=\d)\s*\*\s*(?=\d)", " x ", r)
    r = re.sub(r"\s*EUR/\S*\s*$", "", r, flags=re.I)
    return r.strip()


def _lijkt_op(woord, regel):
    """Of `woord` ongeveer in `regel` staat, ondanks leesfouten.

    De OCR haalt letters door elkaar: op een echte Karwei-bon stond
    'Subtoteal:' in plaats van 'Subtotaal', en dan ziet een exacte vergelijking
    het subtotaal over het hoofd — waarna de korting en het subtotaal zelf als
    artikelregels in de bon belanden. Eén verkeerde letter op de tien mag.
    """
    laag = regel.lower()
    if woord in laag:
        return True
    n = len(woord)
    return any(difflib.SequenceMatcher(None, woord, laag[i:i + n]).ratio() >= 0.85
               for i in range(max(1, len(laag) - n + 1)))


def _bedragen(regel):
    """Alle bedragen op een regel, als getal."""
    return [float(b.replace(",", ".")) for b in re.findall(BEDRAG, regel)]


def _ongeveer_in(naam, regel):
    """Hoe goed `naam` in `regel` zit, van 0 tot 1.

    Niet met == vergelijken: de OCR leest 'KARWEI' net zo vaak als 'KARWEl' of
    met een streep ernaast. Het hoogste stuk van de regel dat op de naam lijkt
    bepaalt de score.
    """
    n = re.sub(r"\W", "", naam.lower())
    h = re.sub(r"\W", "", regel.lower())
    if not h:
        return 0
    if n in h:
        return 1.0
    if len(h) < len(n):
        return difflib.SequenceMatcher(None, n, h).ratio()
    return max(difflib.SequenceMatcher(None, n, h[i:i + len(n)]).ratio()
               for i in range(len(h) - len(n) + 1))


def _schone_naam(regel):
    """Leestekens en ruis van de randen halen, letters en cijfers houden."""
    return re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9.)]+$", "", regel).strip()


def _winkel(regels):
    """De winkelnaam, en of hij uit de bekende lijst komt.

    Drie plekken worden bekeken: de eerste regels, diezelfde regels aan elkaar
    geplakt, en de laatste regels. Het aan elkaar plakken is voor Karwei: dat
    logo leest als 'KAR' en 'WEI' op twee regels. De laatste regels zijn voor
    bonnen die bovenaan alleen een logo hebben en de echte naam in het
    briefhoofd onderaan zetten.
    """
    beste = (0, None)
    kandidaten = regels[:6] + ["".join(regels[:3])] + regels[-8:]
    for winkel in BEKENDE_WINKELS:
        for regel in kandidaten:
            # Een regel die korter is dan de naam kan hem niet bevatten; zonder
            # deze grens matcht 'Hema' op elke korte regel met een h en een m.
            if len(re.sub(r"\W", "", regel)) < len(winkel) - 1:
                continue
            score = _ongeveer_in(winkel, regel)
            if score > beste[0]:
                beste = (score, winkel)
    if beste[0] >= 0.8:
        return beste[1], True
    # Onbekende winkel: de eerste regel die een naam kan zijn. Een adresregel
    # is dat niet, en op een bon staat het adres pal onder de naam: '2345 CD
    # Proefdorp' begint met cijfers, 'Testweg 3' eindigt op een huisnummer.
    gok = [_schone_naam(r) for r in regels[:6]
           if len(re.findall(r"[A-Za-z]", r)) >= 3
           and not re.match(r"^\W*\d", r)
           and not re.search(r"\s\d+\s?[a-zA-Z]?$", r)
           and not re.search(r"\b\d{4}\s?[A-Z]{2}\b", r)]
    return (gok[0] if gok else None), False


# Maanden zoals ze op een bon staan. De eerste drie letters zijn genoeg: dat
# dekt 'okt', 'okt.' en 'oktober' in één keer.
MAANDEN = {"jan": 1, "feb": 2, "mrt": 3, "maa": 3, "apr": 4, "mei": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12}


# Een datum op zo'n regel is niet de dag van de aankoop maar een geldigheid:
# 'Aanbieding t/m 16-08-2026'. Die stond op een echte bon bóven de bondatum, en
# won het daardoor — stil, want aan het veld was niets te zien.
GELDIGHEID = re.compile(r"\bt[/.]?[mn]\b|geldig|aanbieding|actie|houdbaar|vervalt", re.I)


def _datum(regels):
    """De datum van de bon, en of we daar zeker van zijn.

    In volgorde van betrouwbaarheid: een regel die zichzelf 'datum' noemt, dan
    een gewone regel, en pas als laatste een regel met een geldigheidsdatum.
    Dat laatste is een gok en wordt als onzeker gemeld.
    """
    met_kop, gewoon, geldigheid = None, None, None
    for regel in regels:
        gevonden = _datum_op_regel(regel)
        if gevonden is None:
            continue
        if GELDIGHEID.search(regel):
            geldigheid = geldigheid or gevonden
        elif _lijkt_op("datum", regel):
            met_kop = met_kop or gevonden
        else:
            gewoon = gewoon or gevonden
    if met_kop:
        return met_kop, True
    if gewoon:
        return gewoon, True
    return geldigheid, False


def _datum_op_regel(regel):
    """De eerste datum op deze regel, als jjjj-mm-dd.

    Bonnen zetten de datum soms tegen de tijd aan ('02-10-2026 14:33') en
    Duitse kassabonnen gebruiken punten met twee cijfers jaar ('02.10.26').
    Een tuincentrum schrijft de maand als woord, en de OCR plakt dag en maand
    dan aan elkaar ('02okt 2026'); daarom staat er geen \\b of spatie tussen.
    """
    for _ in (0,):
        m = re.search(r"\b(20\d\d)-(\d\d)-(\d\d)\b", regel)
        if m:
            jaar, maand, dag = m.groups()
        else:
            m = re.search(r"\b(\d{1,2})\s?[-./]\s?(\d{1,2})\s?[-./]\s?(\d{4}|\d{2})\b",
                          regel)
            if m:
                dag, maand, jaar = m.groups()
                jaar = ("20" + jaar) if len(jaar) == 2 else jaar
                # '30/06/2619:22' is een datum met de tijd eraan vast: het jaar
                # leest dan als 2619. De eerste twee cijfers zijn het jaar.
                if not 2000 <= int(jaar) <= 2099:
                    jaar = "20" + jaar[:2]
            else:
                m = re.search(r"\b(\d{1,2})\s*([a-zA-Z]{3,9})\.?,?\s*(20\d\d|\d{2})\b",
                              regel)
                if not m or m.group(2)[:3].lower() not in MAANDEN:
                    continue
                dag, jaar = m.group(1), m.group(3)
                maand = MAANDEN[m.group(2)[:3].lower()]
                jaar = ("20" + jaar) if len(jaar) == 2 else jaar
        if 1 <= int(dag) <= 31 and 1 <= int(maand) <= 12 and 2000 <= int(jaar) <= 2099:
            return f"{jaar}-{int(maand):02d}-{int(dag):02d}"
    return None


def _totaal(regels):
    """Het totaalbedrag, de regel waar het stond, en alle stemmen.

    Een bon noemt het totaal vaak drie keer: 'TOTAAL', 'TE BETALEN' en nog een
    keer in het betaaldeel. Wij nemen wat de meerderheid zegt in plaats van de
    eerste. Dat ving in de proef een leesfout op waarbij één regel 29,97 gaf
    waar de andere twee 29,95 zeiden.
    """
    index = None
    eerste = None
    stemmen = []
    for i, regel in enumerate(regels):
        laag = regel.lower()
        if not re.search(r"(?<!sub)(totaal|te betalen|total\b|bruto-?omzet|tot\.?\s?omzet)",
                         laag):
            continue
        if "btw" in laag or "subtotaal" in laag or "voordeel" in laag:
            continue
        gevonden = _bedragen(regel) or (_bedragen(regels[i + 1])
                                        if i + 1 < len(regels) else [])
        # 'TOT.OMZET' is een kopregel; het bedrag staat eronder.
        if "tot.omzet" in laag.replace(" ", "") and i + 1 < len(regels):
            gevonden = _bedragen(regels[i + 1])[:1]
        if not gevonden:
            continue
        stemmen.append(gevonden[-1] if "omzet" not in laag else gevonden[0])
        if index is None and "omzet" not in laag:
            index = i
            eerste = gevonden[-1]
    if not stemmen:
        return None, None, []
    top = max(set(stemmen), key=lambda b: (stemmen.count(b), b == eerste))
    # Staken de stemmen, dan weegt de eerste echte totaalregel het zwaarst.
    totaal = top if stemmen.count(top) > 1 else (eerste if index is not None else top)
    return totaal, index, stemmen


def _betaalwijze(regels, index_totaal):
    """Hoe er betaald is, en of dat uit een redmiddel kwam.

    Eerst onder het totaal kijken: daar staat de pinregel. 'Contactloos' is
    expres het laatste redmiddel, want een Mastercard gaat ook contactloos en
    dan zou die bon onterecht als pin binnenkomen.
    """
    for deel in (regels[(index_totaal or 0):], regels):
        for regel in deel:
            laag = regel.lower()
            for woord, wijze in BETAALWIJZEN:
                if re.search(r"\b" + woord + r"\b", laag):
                    return wijze, False
    if any(re.search(r"contactl(ess|oos|oze)", r.lower()) for r in regels):
        return "pin", True
    return None, False


def _btw(regels, index_totaal):
    """De btw per tarief. Alleen 21, 9, 6 en 0 procent gelden als tarief.

    De tariefregel heeft veel vormen: '21%', '21,00%', '21,00 % over ... = 5,20'
    en een losse TOT.BTW-kop met het bedrag op de regel eronder. Een regel met
    'korting' valt af: daar staat een percentage dat geen btw is.
    """
    gevonden = {}
    for regel in regels[(index_totaal or 0):]:
        laag = regel.lower()
        if "korting" in laag:
            continue
        m = (re.search(r"(?<![\d,.])(\d{1,2})(?:[,.]\d\d)?\s?%", regel)
             or re.search(r"bt[wvu].*?(?<![\d,.])(\d{1,2})[,.]00\s*\S?\s*over", laag))
        if not m:
            continue
        tarief = int(m.group(1))
        if tarief not in (21, 9, 6, 0):
            continue
        achter = _bedragen(regel[m.end():])
        if not achter:
            continue
        if "=" in regel:
            na_isgelijk = _bedragen(regel.split("=")[-1])
            bedrag = na_isgelijk[-1] if na_isgelijk else achter[-1]
        elif len(achter) >= 3:
            # 'tarief netto btw bruto': het middelste is de btw.
            bedrag = achter[1]
        else:
            bedrag = achter[-1]
        gevonden.setdefault(tarief, bedrag)
    if gevonden:
        return gevonden
    # Action zet 'TOT.OMZET  TOT.BTW' als kop met de bedragen eronder.
    for i, regel in enumerate(regels[:-1]):
        if re.search(r"tot\.?\s?bt[wvu]", regel.lower()):
            achter = _bedragen(regels[i + 1])
            if len(achter) >= 2:
                return {21: achter[-1]}
    return {}


def _bonregels(regels, start, eind):
    """De artikelen: omschrijving, aantal, stukprijs en regelbedrag.

    Dit is het lastigste deel, omdat elke winkel het anders afdrukt. Vier
    vormen komen voor, en ze staan hieronder in de volgorde waarin ze worden
    geprobeerd: een aantalregel onder het artikel ('2 x 7,95'), een @-regel
    met stukprijs en bedrag, een artikel met het bedrag op dezelfde regel, en
    Karwei die de omschrijving en de prijzen op twee aparte regels zet.
    """
    AANTAL = re.compile(r"^(\d{1,3})\s*(?:x|st\s*x|stk?\s*x)\s*(" + BEDRAG + r")"
                        r"(?:\s*=)?(?:\s+(" + BEDRAG + r"))?", re.I)
    STUK = re.compile(r"^@\s*(" + BEDRAG + r")\s+(" + BEDRAG + r")")
    # Vier kolommen op één regel: omschrijving, aantal, stukprijs, bedrag.
    # Alleen geldig als aantal maal stukprijs het bedrag oplevert; zonder die
    # rekencontrole zou dit ook toeslaan op een regel met twee losse bedragen.
    KOLOMMEN = re.compile(r"^(.*?)\s+(\d{1,3})\s+(" + BEDRAG + r")\s+("
                          + BEDRAG + r")\s*[A-Z0-9]?$")
    # Regels die nooit een artikel zijn: kopjes, nummers en adresgegevens.
    OVERSLAAN = re.compile(
        r"(tel\b|kvk|btw|datum|bonnr|kassa|omschrijving|bedrag\b|aantal art|filiaal"
        r"|kaartnr|ean\s*:|^\(|duplicaat|leveringsbo|^\d{8,}$|^[-=_ ]+$)", re.I)

    gevonden = []
    korting = None
    hangende_tekst = None
    hangend_aantal = None
    for regel in regels[start:eind]:
        # Artikelnummers horen niet in de omschrijving. Action zet er vijf tot
        # acht cijfers voor, andere winkels 'ART. 12345'.
        regel = re.sub(r"^art\.?\s?\d{4,}\s*", "", regel, flags=re.I).strip()
        regel = re.sub(r"^\d{5,8}\s+(?=[A-Za-z])", "", regel).strip()
        if not regel:
            continue
        laag = regel.lower()
        if AL_VERREKEND.search(laag):
            continue
        if "korting" in laag:
            bedragen = _bedragen(regel)
            korting = -abs(bedragen[-1]) if bedragen else None
            continue
        # Karwei rekent met nettoprijzen en zet de uiteindelijke prijs van het
        # vorige artikel op een eigen regel eronder.
        if re.match(r"netto\s?pri", laag):
            bedragen = _bedragen(regel)
            if bedragen and gevonden:
                gevonden[-1]["regelbedrag"] = bedragen[-1]
                gevonden[-1]["stukprijs"] = round(bedragen[-1] / gevonden[-1]["aantal"], 2)
            continue
        m = AANTAL.match(regel)
        if m:
            aantal = int(m.group(1))
            stukprijs = float(m.group(2).replace(",", "."))
            if m.group(3):
                gevonden.append({"omschrijving": hangende_tekst or "", "aantal": aantal,
                                 "stukprijs": stukprijs,
                                 "regelbedrag": float(m.group(3).replace(",", "."))})
                hangende_tekst = None
            elif (gevonden and gevonden[-1]["aantal"] == 1
                  and not gevonden[-1].get("_aantal_gezet")
                  and abs(gevonden[-1]["regelbedrag"] - aantal * stukprijs) < 0.011):
                # De regel hoort bij het artikel erboven en rekent het uit.
                gevonden[-1].update(aantal=aantal, stukprijs=stukprijs, _aantal_gezet=1)
                if hangende_tekst:
                    # Action zet een lange naam op een ingesprongen tweede regel.
                    gevonden[-1]["omschrijving"] += " " + hangende_tekst
                    hangende_tekst = None
            else:
                hangend_aantal = (aantal, stukprijs)
            continue

        m = STUK.match(regel)
        if m:
            stukprijs, bedrag = (float(g.replace(",", ".")) for g in m.groups())
            tekst = hangende_tekst or ""
            aantal = 1
            voorop = re.match(r"^(\d{1,3})\s+(.*)", tekst)
            if voorop:
                aantal, tekst = int(voorop.group(1)), voorop.group(2)
            # Staat het aantal nergens, dan is het bedrag gedeeld door de
            # stukprijs het aantal, zolang dat rond uitkomt.
            if (stukprijs > 0 and abs(aantal * stukprijs - bedrag) > 0.011
                    and abs(round(bedrag / stukprijs) * stukprijs - bedrag) < 0.011):
                aantal = round(bedrag / stukprijs)
            gevonden.append({"omschrijving": tekst, "aantal": aantal,
                             "stukprijs": stukprijs, "regelbedrag": bedrag})
            hangende_tekst = None
            continue

        if OVERSLAAN.search(regel):
            continue

        m = KOLOMMEN.match(regel)
        if m and re.search(r"[A-Za-z]{3}", m.group(1)):
            aantal = int(m.group(2))
            stukprijs = float(m.group(3).replace(",", "."))
            bedrag = float(m.group(4).replace(",", "."))
            if abs(aantal * stukprijs - bedrag) < 0.011:
                gevonden.append({"omschrijving": m.group(1).strip(), "aantal": aantal,
                                 "stukprijs": stukprijs, "regelbedrag": bedrag})
                hangende_tekst = None
                continue

        bedragen = _bedragen(regel)
        heeft_woorden = re.search(r"[A-Za-z]{3}", regel)
        if bedragen and not heeft_woorden and hangende_tekst:
            # Karwei: eerst de omschrijving, dan een regel met alleen prijzen.
            tekst = hangende_tekst
            aantal = 1
            bedrag = bedragen[1] if len(bedragen) >= 2 else bedragen[-1]
            voorop = re.match(r"^(\d{1,3})\s+(.*)", tekst)
            if voorop:
                aantal, tekst = int(voorop.group(1)), voorop.group(2)
            if (len(bedragen) >= 2 and bedragen[0] > 0
                    and abs(round(bedrag / bedragen[0]) * bedragen[0] - bedrag) < 0.011):
                aantal = round(bedrag / bedragen[0])
            gevonden.append({"omschrijving": tekst, "aantal": aantal,
                             "stukprijs": round(bedrag / aantal, 2),
                             "regelbedrag": bedrag})
            hangende_tekst = None
            continue

        if bedragen and heeft_woorden:
            tekst = re.sub(r"\s+(" + BEDRAG + r")\s*[A-Z0-9]?$", "", regel).strip()
            bedrag = bedragen[-1]
            aantal = 1
            voorop = re.match(r"^(\d{1,3})\s+(.*)", tekst)
            if voorop:
                aantal, tekst = int(voorop.group(1)), voorop.group(2)
            if hangend_aantal and abs(hangend_aantal[0] * hangend_aantal[1] - bedrag) < 0.011:
                aantal = hangend_aantal[0]
            hangend_aantal = None
            gevonden.append({"omschrijving": tekst, "aantal": aantal,
                             "stukprijs": round(bedrag / aantal, 2),
                             "regelbedrag": bedrag})
            hangende_tekst = None
        elif not bedragen and heeft_woorden:
            hangende_tekst = regel

    # Een omschrijving die onder het laatste artikel is blijven hangen hoort
    # erbij, tenzij het de aanloop naar het totaal is.
    if (hangende_tekst and gevonden
            and not re.search(r"totaal|eur$", hangende_tekst.lower())):
        gevonden[-1]["omschrijving"] += " " + hangende_tekst
    for regel in gevonden:
        regel.pop("_aantal_gezet", None)
    return gevonden, korting


def _onzeker(uit, winkel_bekend, betaalwijze_geraden, datum_zeker=True):
    """Welke velden Stone moet nakijken.

    Leeg is onzeker, en een veld dat door een rekencontrole valt ook. Dit is
    wat de app markeert; zonder deze lijst zou een stille leesfout er even
    overtuigend uitzien als een goede regel.
    """
    velden = []
    if not uit["winkel"] or not winkel_bekend:
        velden.append("winkel")
    if not uit["datum"] or not datum_zeker:
        velden.append("datum")
    if uit["totaal"] is None:
        velden.append("totaal")
    elif len(uit["controles"].get("totaal_stemmen", [])) > 1:
        stemmen = uit["controles"]["totaal_stemmen"]
        if stemmen.count(uit["totaal"]) < 2:
            velden.append("totaal")
    if not uit["btw"] or uit["controles"].get("btw_klopt") is False:
        velden.append("btw")
    if not uit["betaalwijze"] or betaalwijze_geraden:
        velden.append("betaalwijze")
    if not uit["regels"] or uit["controles"].get("regels_som_klopt") is False:
        velden.append("regels")
    elif any(re.search(LOS_BEDRAG, r["omschrijving"]) for r in uit["regels"]):
        # Staat er nog een los bedrag in een omschrijving, dan is die regel niet
        # goed uit elkaar gehaald. Dat gebeurt bij opmaak die we niet kennen,
        # en de som kan dan toch kloppen, dus de rekencontrole ziet het niet.
        # Los: een maat als '2,13m2' of 'eiken2,13' telt niet mee.
        velden.append("regels")
    return velden


def lees_bon(tekst):
    """Alle velden van een bon, plus de controles en wat onzeker is.

    Een bon die ondersteboven gescand is leest de OCR gewoon uit — die draait
    losse regels zelf om — maar de volgorde staat dan op zijn kop, en de parser
    verwacht de artikelen bóven het totaal. Daarom lezen we hem zo nodig ook
    achterstevoren en houden we de beste van de twee. Dat kost niets: het lezen
    zelf duurt minder dan een honderdste seconde, de OCR is het dure deel.
    """
    regels = [r for r in tekst.splitlines() if r.strip()]
    eerste = _lees_op_volgorde(regels)
    if not eerste["onzeker"]:
        # Niets te winnen; dit is het gewone geval.
        return eerste
    andersom = _lees_op_volgorde(regels[::-1])
    # Alleen omkeren als het echt beter wordt. Bij gelijkspel wint de gewone
    # volgorde, want dat is hoe een bon er normaal uitziet.
    if len(andersom["onzeker"]) < len(eerste["onzeker"]):
        andersom["controles"]["ondersteboven"] = True
        return andersom
    return eerste


def _lees_op_volgorde(ruwe_regels):
    """De velden uit de regels, in de volgorde waarin ze worden aangeleverd."""
    regels = [_normaliseer(r) for r in ruwe_regels if r.strip()]
    uit = {"winkel": None, "datum": None, "regels": [], "korting": None,
           "btw": [], "btw_totaal": None, "totaal": None, "betaalwijze": None,
           "controles": {}, "onzeker": []}

    uit["winkel"], winkel_bekend = _winkel(regels)
    uit["datum"], datum_zeker = _datum(regels)
    uit["totaal"], index_totaal, stemmen = _totaal(regels)
    if stemmen:
        uit["controles"]["totaal_stemmen"] = stemmen

    # De artikelen staan boven het totaal, en boven een subtotaal als dat er is.
    eind = index_totaal if index_totaal is not None else len(regels)
    for i, regel in enumerate(regels[:eind]):
        if _lijkt_op("subtotaal", regel):
            eind = i
            break

    uit["betaalwijze"], geraden = _betaalwijze(regels, index_totaal)

    tarieven = _btw(regels, index_totaal)
    uit["btw"] = [{"tarief": t, "bedrag": b} for t, b in tarieven.items()]
    uit["btw_totaal"] = round(sum(tarieven.values()), 2) if tarieven else None

    # Controle: bij één tarief hoort de btw gelijk te zijn aan het totaal maal
    # 21/121 of 9/109. Klopt dat niet, dan is er één bekende reden waarom: de
    # bon noemt de netto-omzet apart en de btw is van die netto-regel gelezen.
    if uit["totaal"] and len(tarieven) == 1:
        tarief, bedrag = next(iter(tarieven.items()))
        verwacht = round(uit["totaal"] * tarief / (100 + tarief), 2)
        klopt = abs(verwacht - bedrag) <= 0.02
        uit["controles"]["btw_klopt"] = klopt
        if not klopt:
            netto = next((_bedragen(r)[-1] for r in regels
                          if re.search(r"netto-?omzet|^netto\b", r.lower())
                          and _bedragen(r)), None)
            if netto and abs(uit["totaal"] - netto - verwacht) <= 0.02:
                uit["btw"] = [{"tarief": tarief, "bedrag": verwacht}]
                uit["btw_totaal"] = verwacht
                uit["controles"]["btw_gelezen_als"] = bedrag
                uit["controles"]["btw_klopt"] = True

    # De artikelen beginnen onder de kopregel, als die er is.
    start = 0
    for i, regel in enumerate(regels[:eind]):
        if re.search(r"omschrijving|^artikelen|leverdatum", regel.lower()):
            start = i + 1
            break
    uit["regels"], korting = _bonregels(regels, start, eind)
    uit["korting"] = korting
    if uit["korting"] is None:
        # Karwei zet de personeelskorting ná het subtotaal, dus buiten het deel
        # waar de artikelen staan.
        for regel in regels[eind:(index_totaal if index_totaal is not None else len(regels))]:
            if (any(w in regel.lower() for w in AFTREKPOSTEN)) and _bedragen(regel):
                uit["korting"] = -abs(_bedragen(regel)[-1])

    # Controle: de artikelen plus de korting horen het totaal te zijn.
    if uit["regels"] and uit["totaal"]:
        som = round(sum(r["regelbedrag"] for r in uit["regels"]) + (uit["korting"] or 0), 2)
        uit["controles"]["regels_som"] = som
        uit["controles"]["regels_som_klopt"] = abs(som - uit["totaal"]) <= 0.02

    uit["onzeker"] = _onzeker(uit, winkel_bekend, geraden, datum_zeker)
    return uit


if __name__ == "__main__":
    import json
    import sys
    with open(sys.argv[1], encoding="utf-8") as bron:
        print(json.dumps(lees_bon(bron.read()), indent=1, ensure_ascii=False))
