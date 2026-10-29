import csv
import hashlib
import io
import ipaddress
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import socket
import sqlite3
import smtplib
import subprocess
import tempfile
import threading
import time
import unicodedata
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from email.message import EmailMessage
from email.utils import getaddresses
from itertools import zip_longest
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen
from urllib.error import URLError
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from flask import (
    Flask, abort, after_this_request, make_response, request, redirect, url_for,
    render_template, send_file, flash, session,
)
from markupsafe import Markup, escape
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from werkzeug.datastructures import FileStorage
from werkzeug.exceptions import NotFound
from werkzeug.http import parse_options_header
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

# Versie van de app; staat onderaan elke pagina zodat je kunt zien wat er draait.
# Hoort gelijk te lopen met de version in config.yaml. Draait de app in Home
# Assistant, dan wint wat de Supervisor zegt dat hij heeft geïnstalleerd.
VERSIE = os.environ.get("ADDON_VERSION") or "1.43.3"

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
DB_PATH = os.path.join(DATA_DIR, "facturen.db")
PDF_DIR = os.path.join(DATA_DIR, "pdfs")
LOGO_DIR = os.path.join(DATA_DIR, "logo")
BIJLAGE_DIR = os.path.join(DATA_DIR, "bijlagen")
# Scans van de iPhone blijven hier tot je ze aan een bon of notitie koppelt.
# Apart van de bijlagen, zodat een scan die je nog niet hebt ingedeeld niet
# al tussen de bonnen van een klus staat.
SCAN_DIR = os.path.join(DATA_DIR, "scans")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(PDF_DIR, exist_ok=True)
os.makedirs(LOGO_DIR, exist_ok=True)
os.makedirs(BIJLAGE_DIR, exist_ok=True)
os.makedirs(SCAN_DIR, exist_ok=True)


def _activeer_tijdzone():
    """Zet de procesklok op de tijdzone van Home Assistant, of op Nederland.

    `vandaag()` volgt de klok van het proces. In de add-on zet het startscript
    TZ al; hier nog een keer, zodat een losse start (tests, demo) hetzelfde doet
    als er niets is gezet."""
    if not os.environ.get("TZ"):
        os.environ["TZ"] = "Europe/Amsterdam"
    if hasattr(time, "tzset"):
        try:
            time.tzset()
        except OSError:
            pass


_activeer_tijdzone()


def actieve_tijdzone():
    """De tijdzone waarin 'vandaag' wordt gerekend.

    Eerst wat Home Assistant of het startscript in TZ zette. Bestaat die naam
    niet, dan Nederland: liever een uur verschil dan een datum van gisteren."""
    naam = os.environ.get("TZ") or "Europe/Amsterdam"
    try:
        return ZoneInfo(naam)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Europe/Amsterdam")


def nu():
    """Lokale tijd zonder tijdzone-achtervoegsel, zodat opgeslagen tijdstippen
    hetzelfde blijven vergelijken als voorheen."""
    return datetime.now(actieve_tijdzone()).replace(tzinfo=None)


def vandaag():
    return nu().date()


def _secret_key():
    """Genereer eenmalig een sleutel en bewaar die in /data, zodat sessies een
    herstart van de add-on overleven."""
    override = os.environ.get("SECRET_KEY")
    if override:
        return override
    pad = os.path.join(DATA_DIR, "secret_key")
    if os.path.exists(pad):
        with open(pad, "r", encoding="utf-8") as f:
            sleutel = f.read().strip()
        if sleutel:
            return sleutel
    sleutel = secrets.token_hex(32)
    with open(pad, "w", encoding="utf-8") as f:
        f.write(sleutel)
    os.chmod(pad, 0o600)
    return sleutel


class IngressMiddleware:
    """Home Assistant Ingress serveert de app onder een dynamisch pad. De
    X-Ingress-Path header vertelt welk pad dat is, zodat url_for() links
    genereert die binnen het HA-paneel blijven werken."""

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        ingress_path = environ.get("HTTP_X_INGRESS_PATH")
        if ingress_path:
            environ["SCRIPT_NAME"] = ingress_path.rstrip("/")
        return self.wsgi_app(environ, start_response)


app = Flask(__name__)
app.secret_key = _secret_key()
app.wsgi_app = IngressMiddleware(app.wsgi_app)

# Zonder grens kan één upload de opslag van Home Assistant volschrijven. Een logo is
# hooguit een paar honderd kilobyte, maar een foto van een telefoon is zo vijf megabyte
# en je kunt er meerdere tegelijk kiezen; vandaar deze grens voor het geheel.
# Scans vanaf de iPhone mogen samen meer zijn; die ruimere grens geldt alleen
# voor dat ene adres, niet voor de rest van de app.
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024
# Laat de browser de sessiecookie niet meesturen met verzoeken die van een andere
# site komen. Het CSRF-kenmerk vangt dat al af; dit is de tweede grendel.
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# De standaardnaam is "session". Mijn-overzicht draait op dezelfde host, alleen
# een andere poort. Een browser scheidt cookies niet per poort, dus die app
# overschreef deze sessie. Inloggen gaf dan "Deze opdracht kwam niet van de app
# zelf": het CSRF-kenmerk hoorde bij de sessie van de andere app.
app.config["SESSION_COOKIE_NAME"] = "facturen_app_session"

# Wat je als bonnetje of werkfoto bij een klus mag zetten.
BIJLAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif", ".pdf"}

# Wat een browser zelf als plaatje kan laten zien; de rest krijgt een bestandsicoon.
BIJLAGE_PLAATJES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# Zelfde lijst, maar dan het type dat de browser bij het openen verwacht. Zonder
# dit gokt de server het aan de hand van de naam, en een PDF die als plaatje
# binnenkomt opent op een iPhone niet.
BIJLAGE_MIMES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".pdf": "application/pdf",
}

# Eén scan vanaf de iPhone. Meer dan dit weigeren we per bestand: één PDF van
# 80 MB hoort niet in de inbox, ook al past het verzoek nog onder het totaal.
SCAN_PER_BESTAND = 25 * 1024 * 1024
# Een paar scans tegelijk mogen samen meer zijn dan een gewone upload. Alleen
# /api/scans gebruikt deze grens; de rest van de app blijft op 32 MB.
SCAN_VERZOEK_GRENS = 100 * 1024 * 1024
# De opdracht stuurt normaal één bestand per verzoek. Een hogere lading is
# bijna nooit een echte scan en vult de inbox anders in één keer.
SCAN_MAX_AANTAL = 20
# Stukken, zodat een te groot bestand stopt met lezen in plaats van eerst helemaal
# in het geheugen te belanden.
_SCAN_STUK = 64 * 1024
# Tot hier blijft een tijdelijk bestand in het geheugen. Daarboven gaat het naar
# schijf, zodat vier scans van 25 MB niet samen een halve gigabyte vasthouden.
_SCAN_SPOEL = 1024 * 1024
# Eén scan tegelijk uitpakken. Drie naast elkaar liepen naar ruim een gigabyte.
_SCAN_BEZIG = threading.BoundedSemaphore(1)
# Dieper dan dit is geen scan maar een bom. json.loads loopt dan vast en de
# traceback vult het log.
_JSON_DIEPTE = 32
# Zelfde lengte als een echte sha256-hex, zodat compare_digest niet weigert als
# er nog geen code is opgeslagen. Een kortere tekst gooit hij anders meteen weg
# en dan verraadt de fout juist dát er geen code is.
LEEG_SCAN_HASH = hashlib.sha256(b"").hexdigest()
# Merken in een HEIC/HEIF-bestand, op positie 8 achter "ftyp". GIF en WEBP staan
# hier expres niet bij: die mogen als bijlage wel, als scan niet.
_HEIC_MERKEN = {
    b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1", b"heif",
}
_HEIF_MERKEN = {b"heif", b"mif1", b"msf1"}


def veilig_pad(map_, naam):
    """Alleen een kale bestandsnaam binnen deze map.

    De naam in de database is een willekeurig voorvoegsel plus de extensie.
    Staat er toch een map of .. in, dan wijst het pad naar buiten de app en
    mag niemand dat bestand opvragen of meesturen. Een NUL of een regeleinde
    in de naam laat het bestandssysteem of de antwoordkop stuklopen; dat is
    geen bestand, dus ook geen pad."""
    if not naam or any(teken in naam for teken in ("\x00", "\r", "\n")):
        return None
    kale = os.path.basename(naam)
    if not kale or kale != naam or kale in {".", ".."}:
        return None
    wortel = os.path.realpath(map_)
    pad = os.path.realpath(os.path.join(wortel, kale))
    try:
        if os.path.commonpath([wortel, pad]) != wortel:
            return None
    except ValueError:
        return None
    return pad


def formaat_bytes(aantal):
    """Een aantal bytes in gewone taal."""
    if aantal < 1024:
        return "1 byte" if aantal == 1 else f"{aantal} bytes"
    if aantal < 1024 * 1024:
        waarde, eenheid = aantal / 1024, "kB"
    else:
        waarde, eenheid = aantal / (1024 * 1024), "MB"
    tekst = f"{waarde:.0f}" if waarde >= 10 else f"{waarde:.1f}".replace(".", ",")
    return f"{tekst} {eenheid}"


def formaat_grootte(pad):
    """Bestandsgrootte in gewone taal, voor onder de bestandsnaam."""
    try:
        aantal = os.path.getsize(pad)
    except OSError:
        return ""
    return formaat_bytes(aantal)


def soort_bijlage(ext):
    if ext in BIJLAGE_PLAATJES:
        return "Foto"
    if ext == ".pdf":
        return "PDF"
    if ext in {".heic", ".heif"}:
        return "HEIC"
    return "Bestand"


def csrf_token():
    """Kenmerk dat meegaat met elk formulier, zodat de app opdrachten van andere
    websites herkent en weigert. Poort 8099 heeft geen wachtwoord, dus zonder dit
    kan elke site die je bezoekt in de achtergrond iets laten verwijderen."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


app.jinja_env.globals["csrf_token"] = csrf_token
app.jinja_env.globals["versie"] = VERSIE


def terug_naar(standaard=None):
    """Waar een handeling je naartoe terugstuurt.

    De Referer-header komt van de browser en is dus door een ander te bepalen; hem
    blind volgen maakte van elke knop een doorgeefluik naar een willekeurige website.
    Alleen een pad binnen de app zelf, nooit een adres van buiten."""
    referer = request.referrer or ""
    if referer:
        stuk = urlsplit(referer)
        eigen = urlsplit(request.host_url)
        if stuk.netloc == eigen.netloc:
            pad = stuk.path + (f"?{stuk.query}" if stuk.query else "")
            if pad.startswith("/") and not pad.startswith("//"):
                return pad
    return standaard or url_for("index")


def melding(tekst, soort="gelukt", knop=None, link=None, links=None):
    """Een regel die na een handeling bovenaan het scherm blijft hangen.

    Drie soorten: "gelukt" voor een bevestiging, "fout" voor iets dat niet kon, en
    "bezig" voor iets dat nog loopt en dat je kunt tegenhouden. Eerst zag alles er
    hetzelfde uit — geel met een rand — en dan leest "Instellingen opgeslagen" als
    een waarschuwing.

    Met `knop` komt er een knop in de melding, bijvoorbeeld om het verwijderen
    ongedaan te maken of een mail tegen te houden. Staat er `seconden` bij, dan
    loopt er een balkje leeg en verdwijnt de knop daarna vanzelf.

    `link` is een gewone link, bijvoorbeeld naar de rekening waar een dag op
    staat. Meerdere rekeningen gaan in `links`. De tekst zelf blijft platte tekst:
    een adres in de tekst werd anders geëscaped en was niet aan te klikken."""
    if links is None and link:
        links = [link]
    flash(json.dumps(
        {"tekst": tekst, "knop": knop, "link": link, "links": links},
        ensure_ascii=False,
    ), soort)


def _onthoud_mailstand(sleutel, stand):
    """Of een uitgestelde mail nog wacht, gelukt is, of mislukt.

    Het mailscherm vraagt dit na de bedenktijd, zodat het alleen dichtgaat als
    de mail echt weg is. Een lijst die alleen groeit houdt oude sleutels vast."""
    MAIL_STAND[sleutel] = stand
    if len(MAIL_STAND) > 40:
        for oud in list(MAIL_STAND)[:10]:
            MAIL_STAND.pop(oud, None)


def nieuw_mailteken(soort, doc_id):
    """Een eenmalig teken voor het verstuurformulier van dit document.

    Het scherm opnieuw openen mag daarna wél nog een keer mailen. Het oude
    formulier niet: dat teken hoort bij de lichting van vóór een geslaagde mail.
    """
    teken = secrets.token_urlsafe(16)
    with _MAIL_SLOT:
        MAIL_TEKENS[teken] = (soort, doc_id, MAIL_LICHTING.get((soort, doc_id), 0))
        if len(MAIL_TEKENS) > 50:
            for oud in list(MAIL_TEKENS)[:10]:
                MAIL_TEKENS.pop(oud, None)
    return teken


def _claim_versturen(soort, doc_id, teken):
    """Of deze klik de mail mag starten.

    'bezig' als er al een verzending loopt, 'al' als dit formulier al gebruikt
    is of de mail net weg is. Leeg teken (tests, een oud scherm) mag één keer,
    tot er een mail gelukt is.
    """
    with _MAIL_SLOT:
        sleutel = (soort, doc_id)
        if sleutel in MAIL_BEZIG:
            return False, "bezig"
        huidige = MAIL_LICHTING.get(sleutel, 0)
        if teken:
            # Niet weggooien: een mislukte mail mag met hetzelfde formulier
            # opnieuw. Na een geslaagde mail klopt de lichting niet meer, en
            # zolang er een loopt staat het document in MAIL_BEZIG.
            gevonden = MAIL_TEKENS.get(teken)
            if gevonden is None or gevonden[0] != soort or gevonden[1] != doc_id:
                return False, "al"
            if gevonden[2] != huidige:
                return False, "al"
        elif huidige:
            return False, "al"
        MAIL_BEZIG.add(sleutel)
        return True, ""


def _vrijgeven(soort, doc_id, gelukt, eenmalig=None):
    """De verzending is klaar. Een tweede keer voor dezelfde sleutel doet niets,
    anders telt een mail die nét weg is als twee."""
    with _MAIL_SLOT:
        if eenmalig is not None:
            if eenmalig in MAIL_AFGESLOTEN:
                return
            MAIL_AFGESLOTEN.add(eenmalig)
            if len(MAIL_AFGESLOTEN) > 80:
                for oud in list(MAIL_AFGESLOTEN)[:20]:
                    MAIL_AFGESLOTEN.discard(oud)
        MAIL_BEZIG.discard((soort, doc_id))
        if gelukt:
            sleutel = (soort, doc_id)
            MAIL_LICHTING[sleutel] = MAIL_LICHTING.get(sleutel, 0) + 1


def _melding_al_bezig(waarom):
    if waarom == "bezig":
        melding("Deze mail wordt al verstuurd. Nog een keer klikken stuurt hem "
                "niet nog eens.", "fout")
    else:
        melding("Deze mail is al verstuurd. Vernieuw de pagina als je hem nog "
                "een keer wilt sturen.", "fout")


def mail_straks(wat, functie, *argumenten, na=None, document=None):
    """Zet een mail klaar en verstuurt hem pas na de bedenktijd.

    Tot die tijd verandert er niets aan de rekening — een concept krijgt zijn nummer
    ook pas als de mail echt de deur uit gaat. Houd je hem tegen, dan is er dus niets
    terug te draaien. `na` is waar het mailscherm heen gaat als de mail wél weg is.
    `document` is (soort, id): zolang die bezig is, start een tweede klik niets."""
    sleutel = secrets.token_urlsafe(8)

    def versturen():
        UITGESTELDE_MAILS.pop(sleutel, None)
        with _MAIL_SLOT:
            if sleutel in MAIL_AFGESLOTEN:
                return
        _onthoud_mailstand(sleutel, "bezig")
        try:
            with app.app_context():
                gelukt, tekst = functie(*argumenten)
        except Exception as fout:            # de rekening kan intussen weg zijn
            gelukt, tekst = False, f"{wat} kon niet worden verstuurd: {fout}"
        _onthoud_mailstand(sleutel, "gelukt" if gelukt else "fout")
        MAILUITSLAGEN.append((tekst, "gelukt" if gelukt else "fout"))
        if document:
            _vrijgeven(document[0], document[1], gelukt, eenmalig=sleutel)

    # Zonder bedenktijd valt er niets te wachten en niets tegen te houden; dan gaat
    # hij meteen weg en zie je de uitslag op dezelfde pagina. De tests draaien zo.
    if MAIL_BEDENKTIJD <= 0:
        try:
            gelukt, tekst = functie(*argumenten)
        except Exception:
            if document:
                _vrijgeven(document[0], document[1], False, eenmalig=sleutel)
            raise
        if document:
            _vrijgeven(document[0], document[1], gelukt, eenmalig=sleutel)
        melding(tekst, "gelukt" if gelukt else "fout")
        return

    klok = threading.Timer(MAIL_BEDENKTIJD, versturen)
    klok.daemon = True
    UITGESTELDE_MAILS[sleutel] = klok
    if document:
        MAIL_DOCUMENT[sleutel] = document
    _onthoud_mailstand(sleutel, "wacht")
    klok.start()

    knop = {"label": "Toch niet",
            "url": url_for("mail_tegenhouden", sleutel=sleutel),
            "stand": url_for("mail_stand", sleutel=sleutel),
            "seconden": MAIL_BEDENKTIJD}
    # Alleen het mailscherm wil daarna vanzelf dicht. Een andere pagina
    # toont de melding en blijft waar hij is.
    if na:
        knop["na"] = na
    melding(f"{wat} wordt verstuurd.", "bezig", knop=knop)


@app.route("/mail/<sleutel>/tegenhouden", methods=["POST"])
def mail_tegenhouden(sleutel):
    klok = UITGESTELDE_MAILS.pop(sleutel, None)
    if klok is None:
        melding("Deze mail is al de deur uit.", "fout")
    else:
        klok.cancel()
        _onthoud_mailstand(sleutel, "tegengehouden")
        doc = MAIL_DOCUMENT.get(sleutel)
        if doc:
            _vrijgeven(doc[0], doc[1], False, eenmalig=sleutel)
        melding("Tegengehouden; er is niets verstuurd.")
    return redirect(terug_naar(url_for("index")))


@app.route("/mail/<sleutel>/stand")
def mail_stand(sleutel):
    """Of de uitgestelde mail nog wacht, weg is, of mislukt.

    Het mailscherm polst dit als de bedenktijd om is. Tegenhouden en een fout
    laten de pagina staan; alleen 'gelukt' stuurt je terug."""
    if sleutel in UITGESTELDE_MAILS:
        stand = "wacht"
    else:
        stand = MAIL_STAND.get(sleutel, "weg")
    return make_response(
        json.dumps({"stand": stand}), 200,
        {"Content-Type": "application/json; charset=utf-8"},
    )


def toon_mailuitslagen():
    """De uitslag van een mail die tijdens een vorige pagina is weggegaan."""
    while MAILUITSLAGEN:
        tekst, soort = MAILUITSLAGEN.pop(0)
        melding(tekst, soort)


def waarom_mailen_niet_kan(tabel, rij_id):
    """Wat er nu al aan mailen in de weg staat, of None als het kan.

    Alleen wat we meteen kunnen zien. Dat de mailserver het wachtwoord weigert
    merken we pas bij het versturen zelf, maar geen e-mailadres of geen mailserver
    hoort niet pas na de bedenktijd te blijken."""
    conn = get_db()
    rij = conn.execute(f"SELECT klant_email FROM {tabel} WHERE id=?", (rij_id,)).fetchone()
    conn.close()
    if rij is None:
        abort(404)
    if not rij["klant_email"]:
        return ("Deze klant heeft geen e-mailadres. Vul dat in bij de rekening of bij "
                "de klant.")
    if not get_settings().get("smtp_host"):
        return ("Er is nog geen mailserver ingesteld. Vul die in onder "
                "Instellingen → Mailen.")
    return None


def mail_rekening(factuur_id, functie=None, wat=None, eigen_zin="", bon_ids=None, na=None):
    """Zet de rekening klaar om over een paar tellen te mailen."""
    reden = waarom_mailen_niet_kan("facturen", factuur_id)
    if reden:
        melding(reden, "fout")
        return
    ok, waarom = _claim_versturen(
        "facturen", factuur_id, (request.form.get("mail_teken") or "").strip(),
    )
    if not ok:
        _melding_al_bezig(waarom)
        return
    if wat is None:
        conn = get_db()
        factuur = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
        conn.close()
        wat = f"Rekening {factuurnaam(factuur)}"
    mail_straks(
        wat, functie or verstuur_email, factuur_id, eigen_zin, bon_ids,
        na=na, document=("facturen", factuur_id),
    )


def mail_offerte(offerte_id, eigen_zin="", na=None):
    reden = waarom_mailen_niet_kan("offertes", offerte_id)
    if reden:
        melding(reden, "fout")
        return
    ok, waarom = _claim_versturen(
        "offertes", offerte_id, (request.form.get("mail_teken") or "").strip(),
    )
    if not ok:
        _melding_al_bezig(waarom)
        return
    conn = get_db()
    offerte = conn.execute("SELECT nummer FROM offertes WHERE id=?", (offerte_id,)).fetchone()
    conn.close()
    mail_straks(
        f"Offerte {offerte['nummer']}", verstuur_offerte_email,
        offerte_id, eigen_zin, na=na, document=("offertes", offerte_id),
    )


@app.template_filter("uit_json")
def _uit_json(waarde):
    """De melding komt als JSON binnen omdat er een knop bij kan zitten."""
    return json.loads(waarde)


@app.before_request
def toon_wat_er_intussen_gebeurde():
    """Een mail die tijdens een vorige pagina wegging, heeft geen scherm om zijn
    uitslag op te zetten. Die komt hier alsnog terecht."""
    if request.method == "GET":
        toon_mailuitslagen()
    return None


def _tekst_voor_grens(aantal):
    """1024 wordt '1 kB', een veelvoud van een megabyte wordt '32 MB'."""
    if aantal >= 1024 * 1024 and aantal % (1024 * 1024) == 0:
        return f"{aantal // (1024 * 1024)} MB"
    if aantal >= 1024 and aantal % 1024 == 0:
        return f"{aantal // 1024} kB"
    return f"{aantal} bytes"


@app.before_request
def _ruimere_scangrens():
    """De iPhone-opdracht mag meer sturen dan een formulier in de app.

    De grens van 32 MB geldt voor logo's en bonnen. Een paar scans van 25 MB
    komen daar niet doorheen. De hogere grens hangt aan dit verzoek, niet aan
    de app: anders mag een ander formulier intussen ook 100 MB sturen."""
    if request.endpoint == "scans_ontvangen":
        request.max_content_length = SCAN_VERZOEK_GRENS
        # Een tekstveld zonder bestandsnaam valt onder de formuliergrens, niet
        # onder de 100 MB van het hele verzoek. Die staat standaard op 500 kB.
        # Een scan van een paar MB werd daardoor geweigerd vóór we de bytes
        # zagen, met de melding over 100 MB. Eén veld mag tot één scan groot zijn.
        request.max_form_memory_size = SCAN_PER_BESTAND
    return None


@app.before_request
def controleer_csrf():
    if request.method != "POST":
        return None
    # De opdracht op de iPhone heeft geen sessie en dus geen CSRF-kenmerk.
    # Alleen dit adres: de code in de Authorization-kop is daar de controle.
    # Elk ander formulier blijft het kenmerk eisen, ook als iemand die kop
    # er zelf bij zet.
    if request.endpoint == "scans_ontvangen":
        return None
    verwacht = session.get("csrf_token")
    gekregen = request.form.get("csrf_token", "")
    if not verwacht or not secrets.compare_digest(verwacht, gekregen):
        abort(400, "Deze opdracht kwam niet van de app zelf.")
    return None


# Hoe lang je ingelogd blijft als je "onthoud mij" aanzet.
SESSIE_DAGEN = 30
app.permanent_session_lifetime = timedelta(days=SESSIE_DAGEN)

# Het kortste wachtwoord dat de app accepteert. Kort genoeg om te onthouden, lang
# genoeg om niet in een paar seconden te raden.
WACHTWOORD_MINIMUM = 8

# Na zoveel mislukte pogingen op rij gaat de deur even dicht, zodat niemand in je
# netwerk rustig wachtwoorden kan blijven proberen.
MAX_POGINGEN = 5
WACHTTIJD_MINUTEN = 15

# Per afzender: hoeveel keer het misging en wanneer voor het laatst. Staat in het
# geheugen en niet in de database: bij een herstart mag dit gerust weg zijn.
MISLUKTE_POGINGEN = {}

# Waar een Ingress-verzoek vandaan mag komen. Home Assistant zet zijn add-ons in
# 172.30.32.0/23 en de Supervisor praat van daaruit met deze app. Voor het draaien op
# je eigen machine kun je hier iets anders zetten (zie CLAUDE.md); standaard staat er
# alleen het netwerk van Home Assistant, zodat een willekeurige bezoeker op poort 8099
# de inlog niet met een zelfverzonnen header kan overslaan.
INGRESS_NETWERKEN = [
    ipaddress.ip_network(stuk.strip())
    for stuk in os.environ.get("INGRESS_NETWERK", "172.30.32.0/23").split(",")
    if stuk.strip()
]

# Pagina's die je zonder inloggen moet kunnen bereiken, anders kom je er nooit in.
OPEN_PAGINAS = {"inloggen", "account_instellen", "static", "scans_ontvangen"}


def via_ingress():
    """Of dit verzoek écht via de zijbalk van Home Assistant binnenkomt.

    De X-Ingress-Path-header alleen is geen bewijs: die kan iedereen zelf meesturen.
    Wie dat deed, kwam op poort 8099 zonder wachtwoord binnen en kon alles lezen en
    veranderen — precies waar die inlog voor bedoeld is. Daarom telt ook van wélk
    adres het verzoek komt: Ingress loopt altijd via de Supervisor, en die zit in het
    interne netwerk van Home Assistant. Een buitenstaander op het gewone netwerk kan
    de header wel meesturen, maar zijn afzender niet vervalsen zonder al binnen dat
    netwerk te zitten."""
    if not request.environ.get("HTTP_X_INGRESS_PATH"):
        return False
    try:
        afzender = ipaddress.ip_address(request.remote_addr or "")
    except ValueError:
        return False
    return any(afzender in netwerk for netwerk in INGRESS_NETWERKEN)


def zet_bestandskoppen(antwoord):
    """Nosniff en no-store op een foto, een PDF, of de 404 die daarbij hoort.

    nosniff: de browser houdt zich aan het type dat we meesturen en gaat niet
    zelf raden. no-store: het hoort bij een ingelogde sessie en mag niet in
    een gedeelde of blijvende cache. send_file zet anders alleen no-cache,
    en dat mag een cache nog wel bewaren. Een 404 van dezelfde route krijgt
    dezelfde koppen, anders is juist de lege pagina te cachen."""
    antwoord.headers["X-Content-Type-Options"] = "nosniff"
    antwoord.headers["Cache-Control"] = "private, no-store"
    return antwoord


def niet_gevonden():
    """Geen bestand. Ook deze lege pagina niet bewaren of laten raden."""
    return zet_bestandskoppen(make_response("Niet gevonden", 404))


# Stuurtekens uit het bidirectionele algoritme. U+202E (right-to-left override)
# en de rest draaien de getoonde bestandsnaam om, zodat een .txt eruitziet als
# een .pdf. Ze horen niet in de pagina (data-naam, aria-label) en niet in filename*.
_BIDI = frozenset(
    "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
)


def zonder_bidi(naam):
    """Haal bidirectionele stuurtekens uit een bestandsnaam die we tonen."""
    return "".join(teken for teken in (naam or "") if teken not in _BIDI)


def veilige_downloadnaam(naam):
    """Weergavenaam voor Content-Disposition.

    Een regeleinde in die kop laat de server met 500 stuklopen, of smokkelt
    een tweede kop mee. De tekens gaan eruit; het bestand zelf blijft
    gewoon te openen."""
    schoon = "".join(
        teken for teken in zonder_bidi(naam)
        if ord(teken) >= 32 and teken != "\x7f"
    ).strip()
    return schoon or "bijlage"


def toon_bestand(pad, mimetype, download_name):
    """Stuur een foto of PDF om te bekijken, niet om te bewaren in een cache."""
    antwoord = send_file(
        pad,
        mimetype=mimetype,
        as_attachment=als_download(),
        download_name=veilige_downloadnaam(download_name),
        max_age=0,
    )
    return zet_bestandskoppen(antwoord)


def als_download():
    """Of een PDF of foto naar het toestel moet in plaats van in beeld.

    In de zijbalk van Home Assistant kan een bestand niet in een eigen tabblad open:
    zo'n venster heeft de cookie van Ingress niet en Home Assistant antwoordt met
    "401: Unauthorized". In hetzelfde venster tonen is ook niets — de PDF-weergave
    van iOS laat daar alleen het eerste blad zien. Als download klopt het wel: het
    toestel opent hem in zijn eigen viewer, met alle bladen, en je lijst blijft
    gewoon staan. Op poort 8099 is er niets aan de hand en blijft bekijken bekijken."""
    return via_ingress()


def aantal_scans():
    """Hoeveel scans er nog wachten. 0 als de tabel er even niet is."""
    try:
        conn = get_db()
        try:
            rij = conn.execute("SELECT COUNT(*) FROM scans").fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return 0
    return int(rij[0]) if rij else 0


@app.context_processor
def _scans_in_het_menu():
    """Het aantal op het tabblad, op elke pagina hetzelfde."""
    return {"scan_aantal": aantal_scans()}


@app.context_processor
def _nieuw_tabblad():
    """Of een link een nieuw tabblad mag openen. In de zijbalk niet: dat venster valt
    buiten Ingress. Zie als_download() voor het hele verhaal."""
    return {"nieuw_tabblad": Markup("") if via_ingress()
            else Markup('target="_blank" rel="noopener"')}


def heeft_account(conn=None):
    eigen = conn is None
    if eigen:
        conn = get_db()
    aantal = conn.execute("SELECT COUNT(*) FROM gebruikers").fetchone()[0]
    if eigen:
        conn.close()
    return aantal > 0


def _afzender():
    return request.remote_addr or "onbekend"


def wachttijd_over():
    """Hoeveel minuten deze afzender nog moet wachten na te veel mislukte pogingen."""
    pogingen, laatste = MISLUKTE_POGINGEN.get(_afzender(), (0, None))
    if pogingen < MAX_POGINGEN or laatste is None:
        return 0
    verstreken = (nu() - laatste).total_seconds() / 60
    if verstreken >= WACHTTIJD_MINUTEN:
        MISLUKTE_POGINGEN.pop(_afzender(), None)
        return 0
    return int(WACHTTIJD_MINUTEN - verstreken) + 1


@app.before_request
def controleer_inlog():
    """Poort 8099 heeft geen enkele beveiliging van zichzelf; alles wat daar
    binnenkomt moet dus eerst inloggen. Via de zijbalk van Home Assistant is dat niet
    nodig, want daar is al ingelogd bij Home Assistant zelf."""
    if via_ingress() or request.endpoint in OPEN_PAGINAS:
        return None

    if not heeft_account():
        return redirect(url_for("account_instellen"))
    if session.get("gebruiker"):
        return None
    # full_path plakt er een los vraagteken achter als er geen zoekterm in de URL zit.
    terug = request.full_path.rstrip("?") or request.path
    return redirect(url_for("inloggen", verder=terug))


@app.route("/instellen", methods=["GET", "POST"])
def account_instellen():
    """Eenmalig: de gebruikersnaam en het wachtwoord kiezen waarmee je op poort 8099
    binnenkomt."""
    if heeft_account():
        return redirect(url_for("inloggen"))

    if request.method == "POST":
        naam = request.form.get("naam", "").strip()
        wachtwoord = request.form.get("wachtwoord", "")
        nogmaals = request.form.get("nogmaals", "")

        fout = None
        if not naam:
            fout = "Kies een gebruikersnaam."
        elif len(wachtwoord) < WACHTWOORD_MINIMUM:
            fout = f"Kies een wachtwoord van minstens {WACHTWOORD_MINIMUM} tekens."
        elif wachtwoord != nogmaals:
            fout = "De twee wachtwoorden zijn niet hetzelfde."
        if fout:
            melding(fout)
            return redirect(url_for("account_instellen"))

        conn = get_db()
        conn.execute(
            "INSERT INTO gebruikers (naam, wachtwoord, aangemaakt) VALUES (?, ?, ?)",
            (naam, generate_password_hash(wachtwoord), vandaag().isoformat()),
        )
        conn.commit()
        conn.close()

        session["gebruiker"] = naam
        melding("Je account staat klaar. Vanaf nu log je hiermee in op poort 8099.")
        return redirect(url_for("index"))

    return render_template("instellen.html", minimum=WACHTWOORD_MINIMUM)


@app.route("/inloggen", methods=["GET", "POST"])
def inloggen():
    if not heeft_account():
        return redirect(url_for("account_instellen"))
    if session.get("gebruiker"):
        return redirect(url_for("index"))

    if request.method == "POST":
        wacht = wachttijd_over()
        if wacht:
            melding(f"Te veel mislukte pogingen. Probeer het over {wacht} minuten opnieuw.", "fout")
            return redirect(url_for("inloggen"))

        naam = request.form.get("naam", "").strip()
        wachtwoord = request.form.get("wachtwoord", "")

        conn = get_db()
        gebruiker = conn.execute(
            "SELECT * FROM gebruikers WHERE naam=? COLLATE NOCASE", (naam,)
        ).fetchone()
        conn.close()

        if gebruiker and check_password_hash(gebruiker["wachtwoord"], wachtwoord):
            MISLUKTE_POGINGEN.pop(_afzender(), None)
            session["gebruiker"] = gebruiker["naam"]
            # Zonder het vinkje ben je eruit zodra je de browser afsluit.
            session.permanent = request.form.get("onthoud") == "ja"

            verder = request.form.get("verder", "")
            # Alleen terug naar een pagina binnen de app zelf; een adres van buiten
            # zou je na het inloggen zomaar ergens anders naartoe kunnen sturen.
            if verder.startswith("/") and not verder.startswith("//"):
                return redirect(verder)
            return redirect(url_for("index"))

        pogingen, _ = MISLUKTE_POGINGEN.get(_afzender(), (0, None))
        MISLUKTE_POGINGEN[_afzender()] = (pogingen + 1, nu())
        # Niet verklappen wélk van de twee er niet klopte.
        melding("Gebruikersnaam of wachtwoord klopt niet.", "fout")
        return redirect(url_for("inloggen"))

    return render_template("inloggen.html", verder=request.args.get("verder", ""),
                           wacht=wachttijd_over())


@app.route("/uitloggen", methods=["POST"])
def uitloggen():
    session.pop("gebruiker", None)
    melding("Je bent uitgelogd.")
    return redirect(url_for("inloggen"))


@app.route("/wachtwoord", methods=["POST"])
def wachtwoord_wijzigen():
    """Nieuw wachtwoord voor poort 8099.

    Via de zijbalk hoef je het oude niet te kennen: daar ben je al binnen met
    Home Assistant, en dat is de herstelroute als je het wachtwoord kwijt bent
    of als iemand anders het op poort 8099 heeft gezet. Op poort 8099 zelf
    blijft het huidige wachtwoord verplicht."""
    nieuw = request.form.get("nieuw", "")
    nogmaals = request.form.get("nogmaals", "")
    zijbalk = via_ingress()

    conn = get_db()
    gebruiker = conn.execute("SELECT * FROM gebruikers ORDER BY id LIMIT 1").fetchone()
    if gebruiker is None and not zijbalk:
        conn.close()
        melding("Er is nog geen account om een wachtwoord van te wijzigen.", "fout")
        return redirect(url_for("instellingen"))

    if not zijbalk and not check_password_hash(gebruiker["wachtwoord"],
                                                request.form.get("huidig", "")):
        conn.close()
        melding("Je huidige wachtwoord klopt niet.", "fout")
        return redirect(url_for("instellingen"))
    if len(nieuw) < WACHTWOORD_MINIMUM:
        conn.close()
        melding(f"Kies een wachtwoord van minstens {WACHTWOORD_MINIMUM} tekens.", "fout")
        return redirect(url_for("instellingen"))
    if nieuw != nogmaals:
        conn.close()
        melding("De twee nieuwe wachtwoorden zijn niet hetzelfde.", "fout")
        return redirect(url_for("instellingen"))

    if gebruiker is None:
        naam = request.form.get("naam", "").strip()
        if not naam:
            conn.close()
            melding("Kies een gebruikersnaam.", "fout")
            return redirect(url_for("instellingen"))
        conn.execute(
            "INSERT INTO gebruikers (naam, wachtwoord, aangemaakt) VALUES (?, ?, ?)",
            (naam, generate_password_hash(nieuw), vandaag().isoformat()),
        )
    else:
        conn.execute("UPDATE gebruikers SET wachtwoord=? WHERE id=?",
                     (generate_password_hash(nieuw), gebruiker["id"]))
    conn.commit()
    conn.close()
    melding("Je wachtwoord is gewijzigd. Op poort 8099 log je hiermee in.")
    return redirect(url_for("instellingen"))


@app.errorhandler(413)
def te_groot(_fout):
    # De opdracht leest een platte tekst. Een doorverwijzing naar Instellingen
    # zou daar als melding "verhuisd" tonen, en niet waarom de scan niet aankwam.
    # Dit is het totaal van het verzoek, niet één bestand. De zin over 25 MB
    # komt uit de keuring per bestand; die hier zou de opdracht de verkeerde
    # grens laten zien.
    if request.path.rstrip("/").endswith("/api/scans"):
        lengte = request.content_length
        # Groter dan het hele verzoek: de 100 MB. Daaronder is het één veld
        # dat niet in het geheugen van het formulier past, dus de 25 MB.
        if lengte is not None and lengte > SCAN_VERZOEK_GRENS:
            return _scan_tekst(
                "Dat is te veel in één keer. Samen mogen de scans hooguit "
                f"{_tekst_voor_grens(SCAN_VERZOEK_GRENS)} zijn.",
                413,
            )
        return _scan_tekst(
            f"Eén scan mag hooguit {_tekst_voor_grens(SCAN_PER_BESTAND)} zijn. "
            "Dit veld is groter dan dat.",
            413,
        )
    melding("Dat is te veel in één keer. Samen mogen de bestanden hooguit "
            f"{_tekst_voor_grens(app.config['MAX_CONTENT_LENGTH'])} zijn; "
            "kies er wat minder tegelijk.", "fout")
    return redirect(terug_naar(url_for("instellingen")))

MAANDEN = [
    "jan", "feb", "mrt", "apr", "mei", "jun",
    "jul", "aug", "sep", "okt", "nov", "dec",
]
# Maandag eerst, dezelfde volgorde als date.weekday().
WEEKDAGEN = ["ma", "di", "wo", "do", "vr", "za", "zo"]

# Standaardtermijn op een nieuwe rekening als je de vervaldatum aanzet. Net als
# OFFERTE_GELDIG_DAGEN: alleen de default op het formulier, geen globale setting.
BETAALTERMIJN_DAGEN = 14

# Hoe lang een offerte standaard geldig blijft. Zonder einddatum kan een klant er
# volgend jaar nog mee aankomen terwijl de materiaalprijzen allang anders zijn.
OFFERTE_GELDIG_DAGEN = 30

# Wat er onderaan een offerte staat, in plaats van de betaalstrook van een rekening.
OFFERTE_REGEL = (
    "Deze offerte is vrijblijvend. Prijzen zijn vrijgesteld van btw i.v.m. "
    "particuliere levering van diensten."
)

# De fases die een klus doorloopt. Een aanvraag is iemand die gebeld heeft: er is nog
# geen intake geweest en er zijn nog geen uren. Niet elke klus krijgt een offerte, dus
# die stap mag je overslaan — je gaat gewoon door naar lopend.
KLUS_FASES = {
    "aangevraagd": {"label": "Aanvraag", "melding": "Terug naar aanvraag gezet."},
    "open": {"label": "Lopend", "melding": "De klus loopt; je kunt uren bijhouden."},
    "afgerond": {"label": "Afgerond", "melding": "Klus afgerond."},
}


def vraag_weer_als_aanvraag(aantal_dagen, op_rekening=False):
    """De vraag voordat een lopende klus terug een aanvraag wordt.

    Gewerkte dagen worden niet gewist. Op een aanvraag is dat blok verborgen,
    dus zonder deze zin lijkt het alsof ze weg zijn. Bonnen en notities blijven
    gewoon op de pagina staan; die hoeven niet in de vraag.
    """
    if aantal_dagen and op_rekening:
        return (
            "Deze klus weer als aanvraag zetten? De gewerkte dagen blijven bewaard, "
            "ook de dagen die al op een rekening staan. Je ziet ze pas weer als je "
            "de klus opnieuw start."
        )
    if aantal_dagen:
        return (
            "Deze klus weer als aanvraag zetten? De gewerkte dagen blijven bewaard, "
            "maar je ziet ze pas weer als je de klus opnieuw start. Tot die tijd "
            "tellen ze niet mee bij 'nog niet gefactureerd'."
        )
    return "Deze klus weer als aanvraag zetten? Notities en bonnen blijven staan."


app.jinja_env.globals["vraag_weer_als_aanvraag"] = vraag_weer_als_aanvraag

# Wat er bij het opstarten is rechtgezet; wordt één keer aan de gebruiker getoond.
OPSTARTMELDINGEN = []

# ---------- Mailen met bedenktijd ----------
# Een mail gaat niet meteen weg. Een verkeerde klant, een bedrag dat niet klopt: je
# ziet het meestal pas op het moment dat je op mailen drukt. Deze paar tellen zijn
# genoeg om hem nog tegen te houden, en kosten verder niets.
MAIL_BEDENKTIJD = 10

# De mails die nog in hun bedenktijd zitten: sleutel -> de klok die hem straks
# verstuurt. Staat in het geheugen en niet in de database: gaat de add-on binnen die
# tien tellen uit, dan is de mail simpelweg niet verstuurd, en dat is de veilige kant.
UITGESTELDE_MAILS = {}
# Welk document bij een uitgestelde sleutel hoort, zodat tegenhouden de
# verzending weer vrijgeeft en een nieuwe klik daarna wél mag.
MAIL_DOCUMENT = {}

# Eén verzending tegelijk per document. Een tweede klik — of een tweede tabblad
# terwijl de eerste nog wacht — stuurt niet nog een mail. MAIL_TEKENS is het
# formulier (eenmalig), MAIL_BEZIG is "er loopt er al een", MAIL_LICHTING gaat
# omhoog als hij echt weg is zodat een oud formulier hem niet nog eens stuurt.
_MAIL_SLOT = threading.Lock()
MAIL_TEKENS = {}
MAIL_BEZIG = set()
MAIL_LICHTING = {}
MAIL_AFGESLOTEN = set()

# Wat een uitgestelde mail opleverde. De klok loopt buiten een verzoek om, dus er is
# op dat moment geen pagina om iets op te zetten; dit wordt bij het eerstvolgende
# bezoek getoond.
MAILUITSLAGEN = []

# Per sleutel: wacht, bezig, gelukt, fout of tegengehouden. Het mailscherm
# kijkt hiernaar als de balk leeg is, en gaat alleen weg bij gelukt.
MAIL_STAND = {}

# Kleine voetregel onderaan elke rekening.
BTW_REGEL = (
    "Deze rekening is vrijgesteld van btw i.v.m. particuliere levering van diensten."
)

# De soorten regels. 'eenheid' komt achter het aantal op de factuur; bij een vaste
# prijs per klus is er geen aantal, dan telt alleen het bedrag.
SOORTEN = {
    "materiaal": {
        "naam": "Materiaal",
        "eenheid": "st",
        "aantal_label": "Aantal",
        "prijs_label": "Prijs per stuk",
    },
    "arbeid_uur": {
        "naam": "Arbeid per uur",
        "eenheid": "u",
        "aantal_label": "Uren",
        "prijs_label": "Uurtarief",
    },
    "arbeid_dag": {
        "naam": "Arbeid per dag",
        "eenheid": "dg",
        "aantal_label": "Dagen",
        "prijs_label": "Dagtarief",
    },
    "arbeid_klus": {
        "naam": "Arbeid, vaste prijs",
        "eenheid": None,
        "aantal_label": "Aantal",
        "prijs_label": "Bedrag",
    },
    "bon": {
        "naam": "Bon",
        "eenheid": None,
        "aantal_label": "Aantal",
        "prijs_label": "Bedrag",
    },
}


def soort(sleutel):
    """Gegevens van een regelsoort, met materiaal als terugval voor onbekende waarden."""
    return SOORTEN.get(sleutel or "", SOORTEN["materiaal"])

# Kleuren van de factuur.
ORANJE = colors.HexColor("#DD6B0D")
INKT = colors.HexColor("#17212B")
GRIJS_DONKER = colors.HexColor("#5A6B75")
GRIJS = colors.HexColor("#9AA5AD")
LIJN = colors.HexColor("#E6EAEC")
STIPPEL = colors.HexColor("#B9C3C7")
WIT = colors.white


def standaard_vervalt(datum):
    """Factuurdatum plus de standaard betaaltermijn, als ISO-datum.

    Alleen de default op een nieuwe rekening met termijn aan; elke rekening
    bewaart daarna zijn eigen vervalt_op.
    """
    try:
        return (date.fromisoformat(str(datum))
                + timedelta(days=BETAALTERMIJN_DAGEN)).isoformat()
    except ValueError:
        return datum


def nl_bedrag(waarde):
    """1287.5 wordt '1.287,50' — punt als duizendtalscheiding, komma als decimaal."""
    heel, _, decimalen = f"{float(waarde or 0):,.2f}".partition(".")
    return f"{heel.replace(',', '.')},{decimalen}"


app.add_template_filter(nl_bedrag, "bedrag")


@app.template_filter("datum_nl")
def filter_datum_nl(waarde):
    """'2026-08-14' wordt '14 aug 2026'."""
    try:
        jaar, maand, dag = str(waarde).split("-")
        return f"{int(dag)} {MAANDEN[int(maand) - 1]} {jaar}"
    except (ValueError, IndexError):
        return waarde


def eindtijd_ingevuld(tot):
    """Een dag zonder eindtijd loopt nog. Die uren zijn er pas als de tijd er is."""
    return bool((tot or "").strip())


def bezig_sinds_tekst(datum, tijd):
    """'07:30' als de dag vandaag loopt, anders 'di 13 okt 06:45'.

    Op de klussenlijst en bij de klant is de datum geen titel van de dag.
    Alleen de tijd zou een dag van eergisteren laten lijken op vanochtend."""
    tijd = (tijd or "")[:5]
    if not tijd:
        return ""
    if not datum or str(datum) == vandaag().isoformat():
        return tijd
    try:
        dag = date.fromisoformat(str(datum))
    except ValueError:
        return tijd
    return f"{WEEKDAGEN[dag.weekday()]} {dag.day} {MAANDEN[dag.month - 1]} {tijd}"


def lees_tijd(waarde):
    """'7:30' en '07:30:00' worden '07:30'. Leeg blijft leeg, onleesbaar is None.

    Een tijdveld stuurt soms seconden mee. Die horen niet in de database, anders
    wijkt '07:30:00' af van '07:30' op het scherm en bij het vergelijken."""
    tekst = (waarde or "").strip()
    if not tekst:
        return ""
    delen = tekst.split(":")
    if len(delen) < 2 or not delen[0].isdigit() or not delen[1].isdigit():
        return None
    uur, minuut = int(delen[0]), int(delen[1])
    if uur > 23 or minuut > 59:
        return None
    return f"{uur:02d}:{minuut:02d}"


def duur_in_uren(van, tot):
    """Aantal uren tussen twee kloktijden, als kommagetal. Een tijd die vóór de
    starttijd ligt, telt als de volgende ochtend — handig bij een late klus.

    Zonder eindtijd is de dag nog bezig. Dan 0 teruggeven, anders telt een
    lopende dag mee in een totaal, een bedrag of een rekening."""
    if not eindtijd_ingevuld(van) or not eindtijd_ingevuld(tot):
        return 0.0
    try:
        van_u, van_m = (int(deel) for deel in str(van).split(":")[:2])
        tot_u, tot_m = (int(deel) for deel in str(tot).split(":")[:2])
    except ValueError:
        return 0.0
    minuten = (tot_u * 60 + tot_m) - (van_u * 60 + van_m)
    if minuten < 0:
        minuten += 24 * 60
    return round(minuten / 60, 2)


def _pauze(rij):
    """Minuten pauze op een dag. Oude rijen hebben die kolom niet."""
    try:
        return int(rij["pauze_minuten"] or 0)
    except (KeyError, IndexError, TypeError, ValueError):
        return 0


def factureerbare_uren(van, tot, pauze_minuten=0):
    """Uren op de klus en in de CSV: de kloktijd min de pauze, afgerond.

    De pauze blijft op de dag staan, maar die minuten reken je niet. Zonder
    pauze is dit hetzelfde getal als vroeger, anders verschuiven bestaande
    totalen. Het bedrag van een dagregel rekent niet hiermee maar met de
    minuten: 7,33 uur maal het tarief scheelt een cent met 7 uur 20 minuten."""
    try:
        pauze = max(0, int(pauze_minuten or 0)) / 60
    except (TypeError, ValueError):
        pauze = 0
    return round(max(0.0, duur_in_uren(van, tot) - pauze), 2)


def duur_in_minuten(van, tot):
    """Hele minuten tussen twee kloktijden. Zonder eindtijd is de dag nog bezig."""
    if not eindtijd_ingevuld(van) or not eindtijd_ingevuld(tot):
        return 0
    try:
        van_u, van_m = (int(deel) for deel in str(van).split(":")[:2])
        tot_u, tot_m = (int(deel) for deel in str(tot).split(":")[:2])
    except ValueError:
        return 0
    minuten = (tot_u * 60 + tot_m) - (van_u * 60 + van_m)
    if minuten < 0:
        minuten += 24 * 60
    return minuten


def factureerbare_minuten(van, tot, pauze_minuten=0):
    """Minuten die meetellen. Het bedrag is minuten maal tarief gedeeld door 60.

    Afronden naar uren en dan pas vermenigvuldigen rondt twee keer. 07:00 tot
    14:20 is 7,33 uur op de regel, maar 7,33 maal 45,55 is 333,88 terwijl
    440 minuten maal 45,55 gedeeld door 60 op 334,03 uitkomt."""
    try:
        pauze = max(0, int(pauze_minuten or 0))
    except (TypeError, ValueError):
        pauze = 0
    return max(0, duur_in_minuten(van, tot) - pauze)


def uren_uit_minuten(minuten):
    """Het aantal op de regel, leesbaar op twee decimalen. Niet het bedrag."""
    try:
        minuten = int(minuten or 0)
    except (TypeError, ValueError):
        return 0.0
    if minuten <= 0:
        return 0.0
    return rond_centen(Decimal(minuten) / Decimal(60))


def label_van_dag(datum, van, tot):
    """Korte regel in het dagenmenu: datum · begin–eind, zonder duur.

    In het menu hoort dezelfde vorm als een bonregel: een paar stukken met een
    punt ertussen, en de klusnaam er apart achter. De pauze en een notitie
    blijven op de rekeningregel zelf, via omschrijving_van_dag."""
    try:
        dag = date.fromisoformat(str(datum))
        wanneer = (
            f"{WEEKDAGEN[dag.weekday()]} {dag.day} "
            f"{MAANDEN[dag.month - 1]} {dag.year}"
        )
    except (TypeError, ValueError):
        wanneer = str(datum or "")
    return f"{wanneer} · {(van or '')[:5]}–{(tot or '')[:5]}"


def omschrijving_van_dag(datum, van, tot, pauze_minuten, klusnaam, notitie):
    """Eén regel per dag, met de tijden erin. Anders verdwijnt een dag in het
    totaal aantal uren en is bij het teruglezen niet meer te zien wanneer je was."""
    try:
        dag = date.fromisoformat(str(datum))
        wanneer = (
            f"{WEEKDAGEN[dag.weekday()]} {dag.day} "
            f"{MAANDEN[dag.month - 1]} {dag.year}"
        )
    except (TypeError, ValueError):
        wanneer = str(datum or "")
    tekst = f"{wanneer}, {(van or '')[:5]}–{(tot or '')[:5]}"
    try:
        pauze = int(pauze_minuten or 0)
    except (TypeError, ValueError):
        pauze = 0
    if pauze > 0:
        tekst += f" (pauze {pauze} min)"
    extra = [deel.strip() for deel in (klusnaam, notitie) if (deel or "").strip()]
    if extra:
        tekst += " · " + " · ".join(extra)
    return tekst


def als_decimal(waarde):
    """Een bedrag of aantal als Decimal, vanuit de tekst die het formulier stuurde.

    Een float maal honderd zit er soms een fractie naast, en Decimal(0.1) neemt
    die fout mee. Daarom uit de tekst, niet uit een kommagetal."""
    if isinstance(waarde, Decimal):
        return waarde
    if isinstance(waarde, bool):
        return Decimal(int(waarde))
    if isinstance(waarde, int):
        return Decimal(waarde)
    if isinstance(waarde, float):
        return Decimal(str(waarde))
    tekst = str(waarde or "").strip().replace(" ", "")
    if not tekst:
        return Decimal("0")
    if "," in tekst:
        tekst = tekst.replace(".", "").replace(",", ".")
    try:
        return Decimal(tekst)
    except InvalidOperation:
        raise ValueError


def rond_centen(bedrag):
    """Eén regel naar centen, vóór het optellen. Een halve cent gaat omhoog.

    Drie regels van 10,333 worden anders samen 31,00 terwijl je per regel
    10,33 ziet. Het totaal hoort de som van die centen te zijn. round() kiest
    bij precies 5 de even cent, waardoor 1,5 maal 45,55 op 68,32 uitkwam in
    plaats van 68,33."""
    return float(als_decimal(bedrag).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def bedrag_van_minuten(minuten, prijs):
    """Centen voor een dag: minuten maal tarief gedeeld door 60, halve cent omhoog."""
    try:
        minuten = int(minuten or 0)
    except (TypeError, ValueError):
        minuten = 0
    if minuten <= 0:
        return 0.0
    return rond_centen((Decimal(minuten) * als_decimal(prijs)) / Decimal(60))


def som_van(regels):
    """Som van de regelbedragen.

    Een nieuwe regel is al op centen gezet. Een oude regel kan nog een
    derde decimaal hebben, zoals 387,175. Die per regel eerst naar twee
    decimalen dwingen maakt van twee keer 387,175 samen 774,34 in plaats
    van 774,35. Daarom eerst optellen, en dan pas de som naar centen."""
    totaal = Decimal("0")
    for regel in regels:
        totaal += als_decimal(regel[4])
    return rond_centen(totaal)


def tekst_van_getal(waarde):
    """Het opgeslagen getal, zonder het op centen af te ronden.

    '%.2f' maakt van 387,175 het formulierbedrag 387,17. Na opslaan is een
    rekening van 774,35 dan 774,34. De tekst moet het getal terug kunnen
    geven zoals het in de database staat."""
    if waarde is None or waarde == "":
        return ""
    tekst = format(als_decimal(waarde), "f")
    if "." in tekst:
        tekst = tekst.rstrip("0").rstrip(".")
    return tekst or "0"


def _veld(rij, naam, standaard=None):
    """Waarde uit een dict of een sqlite-rij. Een sqlite-rij heeft geen .get."""
    if rij is None:
        return standaard
    try:
        waarde = rij[naam]
    except (KeyError, IndexError, TypeError):
        return standaard
    return standaard if waarde is None else waarde


def aantal_tekst(regel):
    """Wat er in de samenvatting van een regel staat, vóór het script draait."""
    if not regel:
        return "—"
    soort_regel = _veld(regel, "type")
    if soort_regel in ("arbeid_klus", "bon"):
        return "—"
    if not _veld(regel, "aantal"):
        return "—"
    basis = filter_uren(_veld(regel, "aantal"))
    if soort_regel == "arbeid_dag":
        return f"{basis} dg"
    if soort_regel == "arbeid_uur" or _veld(regel, "uur_id"):
        # Alleen het aantal, bijvoorbeeld 7,33 u. De minuten zitten in het
        # bedrag en in de tijden van de omschrijving; een tweede notatie
        # ernaast ("7 u 20 min") maakt de regel druk.
        return f"{basis} u"
    return f"{basis}×"


# De tijdspanne in een dagregel, met een en-dash of een koppelteken.
_TIJDSPAN = re.compile(r"\d{1,2}:\d{2}[–-]\d{1,2}:\d{2}")


def _puntjes_vast(tekst):
    """Een harde spatie vóór ·, zodat een regel niet met het puntje begint.

    De spatie erachter blijft zacht: 'plaatsen' mag wel naar de volgende regel."""
    uit = []
    for index, deel in enumerate(str(tekst).split(" · ")):
        if index:
            uit.append(Markup("\u00a0· "))
        uit.append(escape(deel))
    return Markup("").join(uit)


def kaart_omschrijving(tekst):
    """De omschrijving op de kaart, zonder de opgeslagen tekst te wijzigen.

    De tijden horen bij elkaar. Een puntje hoort bij het woord ervoor, anders
    begint de volgende regel met '· plaatsen'."""
    if not tekst:
        return Markup("Nieuwe regel")
    tekst = str(tekst)
    stukken = []
    laatste = 0
    for gevonden in _TIJDSPAN.finditer(tekst):
        stukken.append(_puntjes_vast(tekst[laatste:gevonden.start()]))
        stukken.append(
            Markup('<span class="regel-tijd">')
            + escape(gevonden.group())
            + Markup("</span>")
        )
        laatste = gevonden.end()
    stukken.append(_puntjes_vast(tekst[laatste:]))
    return Markup("").join(stukken)


def tarief_voor_kaart(regel):
    """Kleine tekst onder het totaal, of leeg als die niets toevoegt.

    Bij één stuk of een vaste prijs is het totaal het tarief zelf. Dan blijft
    alleen het grote bedrag staan."""
    if not regel:
        return ""
    soort = _veld(regel, "type")
    if soort in ("arbeid_klus", "bon"):
        return ""
    aantal = _veld(regel, "aantal")
    if aantal is None:
        return ""
    try:
        if als_decimal(aantal) == 0 or als_decimal(aantal) == 1:
            return ""
    except ValueError:
        return ""
    prijs = _veld(regel, "prijs") or 0
    basis = filter_uren(aantal)
    if soort == "arbeid_dag":
        stuk = f"{basis} dg"
    elif soort == "arbeid_uur" or _veld(regel, "uur_id"):
        stuk = f"{basis} u"
    else:
        stuk = basis
    return f"{stuk} × €\u00a0{nl_bedrag(prijs)}"


app.jinja_env.globals["tekst_van_getal"] = tekst_van_getal
app.jinja_env.globals["aantal_tekst"] = aantal_tekst
app.jinja_env.globals["tarief_voor_kaart"] = tarief_voor_kaart
app.jinja_env.filters["kaart_tekst"] = kaart_omschrijving


@app.template_filter("uren")
def filter_uren(waarde):
    """8.5 wordt '8,5' en 8.0 wordt '8'."""
    return f"{float(waarde or 0):g}".replace(".", ",")


# Alleen een adres dat als link bedoeld is. Een www. midden in een woord
# ("notwww.example.com") of achter een @ hoort er niet bij.
# Aanhalingstekens blijven in het adres zitten; die worden bij het tonen
# geëscaped. Ze hier afkappen brak een adres als https://example.com/foto's.
_LINK_IN_TEKST = re.compile(r"(?<![\w@./])(?:https?://|www\.)[^\s<>]+", re.IGNORECASE)
_LEESTEKEN_ACHTER_LINK = ".,;:!?"


def _url_zonder_leesteken(ruw):
    """Haal de punt of haakje van de zin van het adres af.

    Anders wordt "kijk op https://example.com." een link die op een punt
    eindigt, en die pagina bestaat niet."""
    kern = ruw
    staart = ""
    while kern and kern[-1] in _LEESTEKEN_ACHTER_LINK:
        staart = kern[-1] + staart
        kern = kern[:-1]
    while kern.endswith(")") and kern.count("(") < kern.count(")"):
        staart = ")" + staart
        kern = kern[:-1]
    return kern, staart


@app.template_filter("met_links")
def met_links(tekst):
    """Maak http(s) en www. in een notitie aanklikbaar, alleen bij het tonen.

    De opgeslagen tekst blijft zoals hij is. Alles buiten het adres wordt
    geëscaped, inclusief de regelovergangen, zodat een notitie geen eigen
    code in de pagina kan zetten."""
    if not tekst:
        return Markup("")
    tekst = str(tekst)
    delen = []
    cursor = 0
    for treffer in _LINK_IN_TEKST.finditer(tekst):
        delen.append(escape(tekst[cursor:treffer.start()]))
        kern, staart = _url_zonder_leesteken(treffer.group(0))
        if not kern:
            delen.append(escape(treffer.group(0)))
        else:
            href = kern if kern.lower().startswith(("http://", "https://")) else "https://" + kern
            delen.append(Markup(
                '<a class="externe-link" href="{}" target="_blank" rel="noopener">{}</a>'
            ).format(escape(href), escape(kern)))
            delen.append(escape(staart))
        cursor = treffer.end()
    delen.append(escape(tekst[cursor:]))
    return Markup("").join(delen)


@app.template_global()
def periode_nl(van, tot):
    """'10 – 12 aug 2026' in plaats van '10 aug 2026 t/m 12 aug 2026'; het jaar en
    de maand worden alleen herhaald als ze verschillen."""
    if not van:
        return ""
    if not tot or tot == van:
        return filter_datum_nl(van)
    try:
        v_jaar, v_maand, v_dag = str(van).split("-")
        t_jaar, t_maand, t_dag = str(tot).split("-")
    except ValueError:
        return f"{filter_datum_nl(van)} t/m {filter_datum_nl(tot)}"
    if v_jaar == t_jaar and v_maand == t_maand:
        return f"{int(v_dag)} – {filter_datum_nl(tot)}"
    if v_jaar == t_jaar:
        return f"{int(v_dag)} {MAANDEN[int(v_maand) - 1]} – {filter_datum_nl(tot)}"
    return f"{filter_datum_nl(van)} – {filter_datum_nl(tot)}"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Zonder deze pragma is een FOREIGN KEY alleen documentatie. ON DELETE
    # SET NULL op regels.uur_id doet dan niets, en een dag kan aan twee
    # regels blijven hangen.
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            naam TEXT DEFAULT '',
            adres TEXT DEFAULT '',
            telefoon TEXT DEFAULT '',
            email TEXT DEFAULT '',
            iban TEXT DEFAULT '',
            logo_bestand TEXT DEFAULT '',
            smtp_host TEXT DEFAULT '',
            smtp_port INTEGER DEFAULT 587,
            smtp_user TEXT DEFAULT '',
            smtp_pass TEXT DEFAULT '',
            smtp_van TEXT DEFAULT '',
            betaaltermijn_dagen INTEGER DEFAULT 14,
            bcc_aan INTEGER DEFAULT 1,
            bcc_adres TEXT DEFAULT '',
            meesturen_bijgewerkt INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS gebruikers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            naam TEXT NOT NULL UNIQUE,
            wachtwoord TEXT NOT NULL,
            aangemaakt TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS facturen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nummer TEXT NOT NULL,
            datum TEXT NOT NULL,
            vervalt_op TEXT DEFAULT '',
            klant_id INTEGER,
            klant_naam TEXT NOT NULL,
            klant_adres TEXT DEFAULT '',
            klant_email TEXT DEFAULT '',
            betaalmethode TEXT DEFAULT 'bank',
            status TEXT DEFAULT 'concept',
            totaal REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS regels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            factuur_id INTEGER NOT NULL,
            omschrijving TEXT NOT NULL,
            type TEXT NOT NULL,
            aantal REAL NOT NULL,
            prijs REAL NOT NULL,
            subtotaal REAL NOT NULL,
            klus_id INTEGER,
            -- Welke bon deze regel is. Eén bon, één regel: anders zet een
            -- tweede tabblad dezelfde bon nog een keer op een rekening.
            -- Verdwijnt de bon, dan blijft het bedrag op de regel staan.
            inkoop_id INTEGER UNIQUE,
            -- Welke gewerkte dag deze regel is. Eén dag, één regel: anders zet
            -- een tweede tabblad dezelfde dag nog een keer op een rekening.
            -- Verdwijnt de dag, dan blijft het bedrag op de regel staan.
            uur_id INTEGER UNIQUE,
            FOREIGN KEY (factuur_id) REFERENCES facturen (id),
            FOREIGN KEY (inkoop_id) REFERENCES inkopen (id) ON DELETE SET NULL,
            FOREIGN KEY (uur_id) REFERENCES uren (id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS klanten (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            naam TEXT NOT NULL,
            adres TEXT DEFAULT '',
            email TEXT DEFAULT '',
            telefoon TEXT DEFAULT '',
            notitie TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS klussen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            naam TEXT NOT NULL,
            klant_id INTEGER,
            uurtarief REAL DEFAULT 0,
            notitie TEXT DEFAULT '',
            status TEXT DEFAULT 'open',
            gestart TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS uren (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            klus_id INTEGER NOT NULL,
            datum TEXT NOT NULL,
            van TEXT NOT NULL,
            -- Leeg zolang de dag nog loopt. Een verplichte eindtijd dwong je
            -- 's ochtends een gok te doen die je 's avonds weer moest verbeteren.
            tot TEXT,
            notitie TEXT DEFAULT '',
            factuur_id INTEGER,
            -- Minuten die niet gerekend worden. 0 bij dagen van voor deze kolom,
            -- zodat een oude rekening niet opeens minder uren krijgt.
            pauze_minuten INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (klus_id) REFERENCES klussen (id)
        );

        CREATE TABLE IF NOT EXISTS betalingen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            factuur_id INTEGER NOT NULL,
            datum TEXT NOT NULL,
            bedrag REAL NOT NULL,
            notitie TEXT DEFAULT '',
            -- 1 als de app hem zelf boekte omdat je op "Betaald" drukte; die mag
            -- weer weg als je dat terugdraait, een handmatige boeking niet.
            automatisch INTEGER DEFAULT 0,
            FOREIGN KEY (factuur_id) REFERENCES facturen (id)
        );

        CREATE TABLE IF NOT EXISTS bijlagen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            klus_id INTEGER NOT NULL,
            bestand TEXT NOT NULL,
            naam TEXT NOT NULL,
            toegevoegd TEXT NOT NULL,
            -- Oude vlag per bestand. Een bon die op de rekening staat gaat altijd
            -- mee; het vinkje op het mailscherm beslist, niet deze kolom.
            meesturen INTEGER DEFAULT 0,
            FOREIGN KEY (klus_id) REFERENCES klussen (id)
        );

        CREATE TABLE IF NOT EXISTS notities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            klus_id INTEGER NOT NULL,
            wanneer TEXT NOT NULL,
            tekst TEXT NOT NULL,
            FOREIGN KEY (klus_id) REFERENCES klussen (id)
        );

        CREATE TABLE IF NOT EXISTS inkopen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            klus_id INTEGER NOT NULL,
            omschrijving TEXT NOT NULL,
            bedrag REAL NOT NULL DEFAULT 0,
            toegevoegd TEXT NOT NULL,
            factuur_id INTEGER,
            FOREIGN KEY (klus_id) REFERENCES klussen (id)
        );

        CREATE TABLE IF NOT EXISTS inkoop_materialen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inkoop_id INTEGER NOT NULL,
            omschrijving TEXT NOT NULL,
            aantal REAL DEFAULT 1,
            prijs REAL DEFAULT 0,
            FOREIGN KEY (inkoop_id) REFERENCES inkopen (id)
        );

        CREATE TABLE IF NOT EXISTS offertes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nummer TEXT NOT NULL,
            datum TEXT NOT NULL,
            geldig_tot TEXT DEFAULT '',
            klant_id INTEGER,
            klant_naam TEXT NOT NULL,
            klant_adres TEXT DEFAULT '',
            klant_email TEXT DEFAULT '',
            status TEXT DEFAULT 'concept',
            totaal REAL DEFAULT 0,
            toelichting TEXT DEFAULT '',
            factuur_id INTEGER
        );

        CREATE TABLE IF NOT EXISTS offerte_regels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            offerte_id INTEGER NOT NULL,
            omschrijving TEXT NOT NULL,
            type TEXT NOT NULL,
            aantal REAL NOT NULL,
            prijs REAL NOT NULL,
            subtotaal REAL NOT NULL,
            FOREIGN KEY (offerte_id) REFERENCES offertes (id)
        );

        CREATE TABLE IF NOT EXISTS prullenbak (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            omschrijving TEXT NOT NULL,
            -- Alle weggegooide rijen als JSON, per tabel, met hun oorspronkelijke id
            -- erbij. Zo komt een rekening met zijn regels én betalingen in één keer
            -- terug, en blijven verwijzingen ernaartoe kloppen.
            inhoud TEXT NOT NULL,
            wanneer TEXT NOT NULL
        );

        -- Een dag die bij de migratie niet bij deze rekening hoorde. Het bedrag
        -- blijft, de koppeling niet. Bij het openen van de rekening is die zin
        -- te lezen; een logregel alleen ziet niemand.
        CREATE TABLE IF NOT EXISTS rekening_waarschuwing (
            factuur_id INTEGER PRIMARY KEY,
            tekst TEXT NOT NULL
        );
        """
    )
    conn.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")

    # Kolommen die later zijn toegevoegd, bijzetten in bestaande databases.
    bestaand = {rij["name"] for rij in conn.execute("PRAGMA table_info(settings)")}
    for kolom, definitie in [
        ("tenaamstelling", "TEXT DEFAULT ''"),
        ("betaaltermijn_dagen", "INTEGER DEFAULT 14"),
        # Standaard aan, zonder eigen adres: dan is de afzender de kopie.
        ("bcc_aan", "INTEGER DEFAULT 1"),
        ("bcc_adres", "TEXT DEFAULT ''"),
        # 1 zodra de eenmalige zet van 'meesturen' gedraaid heeft. Zonder vlag
        # zette elke start die kolom weer op 1, ook als je hem daarna uitzette.
        ("meesturen_bijgewerkt", "INTEGER DEFAULT 0"),
        # Alleen de hash van de code voor iPhone-scans. De code zelf staat
        # nergens: die zie je één keer, bij het aanmaken.
        ("scan_token_hash", "TEXT DEFAULT ''"),
    ]:
        if kolom not in bestaand:
            conn.execute(f"ALTER TABLE settings ADD COLUMN {kolom} {definitie}")

    # Tikkie is er in 1.3.0 uitgegaan; de kolom bleef achter in bestaande databases.
    if "tikkie_link" in bestaand:
        try:
            conn.execute("ALTER TABLE settings DROP COLUMN tikkie_link")
        except sqlite3.OperationalError:
            # Te oude SQLite om een kolom te laten vallen. Niet erg: hij wordt
            # nergens meer gelezen of geschreven en staat verder in de weg niet.
            pass

    # Arbeid was er in één smaak en werd per uur gerekend; die regels houden dat.
    conn.execute("UPDATE regels SET type='arbeid_uur' WHERE type='arbeid'")

    # Welke regel bij welke klus hoort, en welke gewerkte dagen al gefactureerd zijn.
    regelkolommen = {rij["name"] for rij in conn.execute("PRAGMA table_info(regels)")}
    if "klus_id" not in regelkolommen:
        conn.execute("ALTER TABLE regels ADD COLUMN klus_id INTEGER")
    urenkolommen = {rij["name"] for rij in conn.execute("PRAGMA table_info(uren)")}
    if "factuur_id" not in urenkolommen:
        conn.execute("ALTER TABLE uren ADD COLUMN factuur_id INTEGER")

    factuurkolommen = {rij["name"] for rij in conn.execute("PRAGMA table_info(facturen)")}
    # Wat de rekening was voordat hij op betaald werd gezet, zodat "toch niet betaald"
    # hem terugzet op concept of verzonden in plaats van te gokken.
    if "status_voor_betaald" not in factuurkolommen:
        conn.execute("ALTER TABLE facturen ADD COLUMN status_voor_betaald TEXT DEFAULT ''")

    if "klant_id" not in factuurkolommen:
        conn.execute("ALTER TABLE facturen ADD COLUMN klant_id INTEGER")

        # Eenmalig, precies op het moment dat het klantenbestand erbij komt: maak van
        # elke bestaande klantnaam één klant en koppel de rekeningen eraan. Dit mag
        # niet bij elke start draaien, anders komen losse of verwijderde klanten terug.
        losse = conn.execute(
            """SELECT klant_naam, MAX(id) AS laatste FROM facturen
               WHERE TRIM(klant_naam) <> '' GROUP BY klant_naam"""
        ).fetchall()
        for rij in losse:
            laatste = conn.execute(
                "SELECT klant_adres, klant_email FROM facturen WHERE id=?", (rij["laatste"],)
            ).fetchone()
            klant_id = conn.execute(
                "INSERT INTO klanten (naam, adres, email) VALUES (?, ?, ?)",
                (rij["klant_naam"], laatste["klant_adres"] or "", laatste["klant_email"] or ""),
            ).lastrowid
            conn.execute(
                "UPDATE facturen SET klant_id=? WHERE klant_naam=?",
                (klant_id, rij["klant_naam"]),
            )

    # Een klus begint als aanvraag en loopt door tot hij af is. De fases ervoor
    # stonden nergens, waardoor iemand die belde alleen in je hoofd zat.
    kluskolommen = {rij["name"] for rij in conn.execute("PRAGMA table_info(klussen)")}
    if "aangevraagd_op" not in kluskolommen:
        conn.execute("ALTER TABLE klussen ADD COLUMN aangevraagd_op TEXT DEFAULT ''")
        # Wat er al staat is allang geen aanvraag meer; de startdatum is het beste
        # wat we hebben voor wanneer het binnenkwam.
        conn.execute("UPDATE klussen SET aangevraagd_op = gestart")
    if "intake_op" not in kluskolommen:
        conn.execute("ALTER TABLE klussen ADD COLUMN intake_op TEXT DEFAULT ''")
    if "offerte_id" not in kluskolommen:
        conn.execute("ALTER TABLE klussen ADD COLUMN offerte_id INTEGER")
    # De echte startdatum, bewaard als je een lopende klus terugzet naar een
    # aanvraag. `gestart` gaat dan terug naar de aanvraagdag; zonder deze kolom
    # is de dag waarop het werk begon weg zodra je opnieuw start.
    if "gestart_was" not in kluskolommen:
        conn.execute("ALTER TABLE klussen ADD COLUMN gestart_was TEXT DEFAULT ''")

    # Een foto kan bij een notitie horen in plaats van bij de bonnen. Zo blijven de
    # foto's van wat je hebt gezien los van de bonnetjes die de klant moet zien.
    bijlagekolommen = {rij["name"] for rij in conn.execute("PRAGMA table_info(bijlagen)")}
    if "notitie_id" not in bijlagekolommen:
        conn.execute("ALTER TABLE bijlagen ADD COLUMN notitie_id INTEGER")

    # Een eigen aantekening om twee rekeningen of offertes voor dezelfde klant uit
    # elkaar te houden. Staat alleen in de app; de klant ziet hem nergens.
    if "kenmerk" not in factuurkolommen:
        conn.execute("ALTER TABLE facturen ADD COLUMN kenmerk TEXT DEFAULT ''")
    # Per-rekening vervaldatum (was eerder een globale betaaltermijn in Instellingen).
    # Lege tekst = geen termijn op de rekening, net als geldig_tot bij offertes.
    if "vervalt_op" not in factuurkolommen:
        conn.execute("ALTER TABLE facturen ADD COLUMN vervalt_op TEXT DEFAULT ''")
        # Bestaande rekeningen hielden hun veertien-dagen-termijn; die niet ineens
        # laten wegvallen doordat de kolom leeg begint.
        conn.execute(
            """UPDATE facturen SET vervalt_op = date(datum, '+14 days')
               WHERE vervalt_op IS NULL OR vervalt_op = ''"""
        )
    # Aanbetalingen en de eindrekening horen bij dezelfde offerte, zodat "Naar
    # rekening" het restant kan voorstellen en niet nog een keer het volle bedrag.
    if "offerte_id" not in {rij["name"] for rij in conn.execute("PRAGMA table_info(facturen)")}:
        conn.execute("ALTER TABLE facturen ADD COLUMN offerte_id INTEGER")
    offertekolommen = {rij["name"] for rij in conn.execute("PRAGMA table_info(offertes)")}
    if "kenmerk" not in offertekolommen:
        conn.execute("ALTER TABLE offertes ADD COLUMN kenmerk TEXT DEFAULT ''")

    # Inkopen (bonnen): aankoop met bedrag, optioneel materiaal eronder, bestanden erbij.
    # Oude losse bijlagen zonder notitie worden één inkoop per bestand, zodat niets
    # verdwijnt bij de overstap.
    bijlagekolommen = {rij["name"] for rij in conn.execute("PRAGMA table_info(bijlagen)")}
    if "inkoop_id" not in bijlagekolommen:
        conn.execute("ALTER TABLE bijlagen ADD COLUMN inkoop_id INTEGER")
        for bijlage in conn.execute(
            "SELECT * FROM bijlagen WHERE notitie_id IS NULL"
        ).fetchall():
            inkoop_id = conn.execute(
                """INSERT INTO inkopen (klus_id, omschrijving, bedrag, toegevoegd)
                   VALUES (?, ?, 0, ?)""",
                (bijlage["klus_id"], bijlage["naam"] or "Bon", bijlage["toegevoegd"]),
            ).lastrowid
            conn.execute(
                "UPDATE bijlagen SET inkoop_id=? WHERE id=?",
                (inkoop_id, bijlage["id"]),
            )

    # Een bon op de rekening gaat altijd mee met de mail. Een oud vinkje
    # 'niet meesturen' haalde zo'n bon stil van de mail. Notitiefoto's blijven
    # uit: die hebben geen inkoop. Eén keer: anders zet elke start de vlag
    # terug en is een latere uitzondering bij de volgende start weer weg.
    vlag = conn.execute(
        "SELECT meesturen_bijgewerkt FROM settings WHERE id=1"
    ).fetchone()
    if not vlag or not vlag["meesturen_bijgewerkt"]:
        conn.execute(
            """UPDATE bijlagen SET meesturen=1
               WHERE inkoop_id IS NOT NULL AND notitie_id IS NULL
                 AND COALESCE(meesturen, 0) = 0"""
        )
        conn.execute("UPDATE settings SET meesturen_bijgewerkt=1 WHERE id=1")

    regelkolommen = {rij["name"] for rij in conn.execute("PRAGMA table_info(regels)")}
    if "inkoop_id" not in regelkolommen:
        conn.execute("ALTER TABLE regels ADD COLUMN inkoop_id INTEGER")
    offerte_regelkolommen = {
        rij["name"] for rij in conn.execute("PRAGMA table_info(offerte_regels)")
    }
    if "inkoop_id" not in offerte_regelkolommen:
        conn.execute("ALTER TABLE offerte_regels ADD COLUMN inkoop_id INTEGER")

    _eindtijd_optioneel(conn)

    # Ná de herbouw van uren. Die kopieert de kolommen die er toen waren; een
    # pauze die je er eerder bij zet, valt bij die herbouw weer weg.
    urenkolommen = {rij["name"] for rij in conn.execute("PRAGMA table_info(uren)")}
    if urenkolommen and "pauze_minuten" not in urenkolommen:
        conn.execute(
            "ALTER TABLE uren ADD COLUMN pauze_minuten INTEGER NOT NULL DEFAULT 0"
        )
    # Bestaande regels houden hun tekst. Alleen nieuwe dagen krijgen een uur_id;
    # een oude samengevoegde regel blijft één regel.
    regelkolommen = {rij["name"] for rij in conn.execute("PRAGMA table_info(regels)")}
    if "uur_id" not in regelkolommen:
        conn.execute("ALTER TABLE regels ADD COLUMN uur_id INTEGER")
    # Ná de kolom. Een tabel van voor deze koppeling heeft uur_id zonder
    # unieke index; die herbouwen we, anders blijft een tweede tabblad
    # dezelfde dag op twee rekeningen zetten. Dubbele bonnen eerst losmaken:
    # de herbouw weigert anders op twee regels met dezelfde bon.
    _ontkoppel_inconsistente_bonnen(conn)
    _uur_koppeling_uniek(conn)
    _bon_koppeling_uniek(conn)

    # Inbox voor scans vanaf de iPhone. Geen verwijzing naar een klus: die
    # koppeling komt pas als je de scan indeelt, en dan verhuist het bestand
    # naar de bijlagen.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bestand TEXT NOT NULL,
            naam TEXT NOT NULL,
            soort TEXT NOT NULL,
            grootte INTEGER NOT NULL DEFAULT 0,
            ontvangen TEXT NOT NULL
        )"""
    )

    # Taken horen bij een notitie, niet in de tekst. Een bestaande notitie
    # krijgt zo een lijst zonder dat de tekst of de foto's worden aangeraakt.
    # IF NOT EXISTS: een database van voor 1.43.0 heeft de tabel nog niet.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS taken (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notitie_id INTEGER NOT NULL,
            tekst TEXT NOT NULL,
            klaar INTEGER NOT NULL DEFAULT 0,
            volgorde INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (notitie_id) REFERENCES notities (id) ON DELETE CASCADE
        )"""
    )
    # Een database van de eerste 1.43.0 heeft de verwijzing zonder CASCADE.
    # Terug naar 1.42.1 wist dan de notitie niet: die versie kent de taken
    # niet, en de vreemde sleutel hield de verwijdering tegen.
    _taken_cascade(conn)

    conn.commit()
    conn.close()


def _uren_teller_meenemen(conn):
    """De volgende dag moet een id krijgen dat er nog niet is.

    Na een hernoeming heet de teller soms nog uren_nieuw. Een lege uren die de
    start net had aangemaakt kan ook een teller hebben achtergelaten; die twee
    namen kunnen niet allebei 'uren' worden."""
    try:
        hoogste = conn.execute("SELECT COALESCE(MAX(id), 0) FROM uren").fetchone()[0]
        conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('uren', 'uren_nieuw')")
        if hoogste:
            conn.execute(
                "INSERT INTO sqlite_sequence (name, seq) VALUES ('uren', ?)",
                (hoogste,),
            )
    except sqlite3.OperationalError:
        pass


def _eindtijd_optioneel(conn):
    """Maak de eindtijd leeg mogen zijn, zonder een bestaande tijd kwijt te raken.

    SQLite kan een NOT NULL niet loslaten. De tabel wordt daarom nagebouwd en de
    rijen gaan eerst naar de kopie; pas daarna gaat de oude tabel weg. Blijft de
    app tussen die twee stappen steken, dan maakt de volgende start het af.
    Staat de data alleen nog in de kopie, dan mag die niet worden weggegooid —
    ook niet als de start intussen een lege uren heeft aangemaakt."""
    namen = {
        rij["name"]
        for rij in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "uren_nieuw" in namen and "uren" not in namen:
        conn.execute("ALTER TABLE uren_nieuw RENAME TO uren")
        _uren_teller_meenemen(conn)
        return
    if "uren_nieuw" in namen and "uren" in namen:
        leeg = conn.execute("SELECT COUNT(*) FROM uren").fetchone()[0] == 0
        heeft_kopie = conn.execute(
            "SELECT COUNT(*) FROM uren_nieuw"
        ).fetchone()[0] > 0
        if leeg and heeft_kopie:
            # De start maakt uren opnieuw aan zodra de tabel weg is. Gebeurt
            # dat ná het weggooien van de oude tabel en vóór de hernoeming,
            # dan staat alles alleen nog in uren_nieuw.
            conn.execute("DROP TABLE uren")
            conn.execute("ALTER TABLE uren_nieuw RENAME TO uren")
            _uren_teller_meenemen(conn)
            return
        conn.execute("DROP TABLE uren_nieuw")
    if "uren" not in namen:
        return
    info = list(conn.execute("PRAGMA table_info(uren)"))
    tot = next((kolom for kolom in info if kolom["name"] == "tot"), None)
    if tot is None or not tot["notnull"]:
        return
    # factuur_id kwam er later bij. Zonder die kolom zou de kopie de koppeling
    # met een rekening laten vallen.
    heeft_factuur = any(kolom["name"] == "factuur_id" for kolom in info)
    if not heeft_factuur:
        conn.execute("ALTER TABLE uren ADD COLUMN factuur_id INTEGER")
    # Binnen een transactie doet deze pragma niets, en dan weigert de kopie
    # een dag waarvan de klus er even niet is.
    if conn.in_transaction:
        conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        """
        CREATE TABLE uren_nieuw (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            klus_id INTEGER NOT NULL,
            datum TEXT NOT NULL,
            van TEXT NOT NULL,
            tot TEXT,
            notitie TEXT DEFAULT '',
            factuur_id INTEGER,
            FOREIGN KEY (klus_id) REFERENCES klussen (id)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO uren_nieuw (id, klus_id, datum, van, tot, notitie, factuur_id)
        SELECT id, klus_id, datum, van, tot, notitie, factuur_id FROM uren
        """
    )
    conn.execute("DROP TABLE uren")
    conn.execute("ALTER TABLE uren_nieuw RENAME TO uren")
    # De hernoeming houdt de teller meestal bij de nieuwe naam. Blijft de oude
    # naam staan, dan kan de volgende dag een id krijgen dat er al was.
    _uren_teller_meenemen(conn)
    if conn.in_transaction:
        conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")


def _zonder_transactie(conn, pragma):
    """Een PRAGMA die SQLite negeert zolang er een transactie openstaat."""
    if conn.in_transaction:
        conn.commit()
    conn.execute(pragma)


def _teller_op_tabel(conn, naam):
    """De volgende rij moet een id krijgen dat er nog niet is.

    Na een hernoeming heet de teller soms nog naar de tijdelijke tabel."""
    try:
        hoogste = conn.execute(f"SELECT COALESCE(MAX(id), 0) FROM {naam}").fetchone()[0]
        conn.execute(
            "DELETE FROM sqlite_sequence WHERE name IN (?, ?)",
            (naam, f"{naam}_nieuw"),
        )
        if hoogste:
            conn.execute(
                "INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)",
                (naam, hoogste),
            )
    except sqlite3.OperationalError:
        pass


def _uur_koppeling_al_uniek(conn):
    """Of regels.uur_id al één dag per regel afdwingt, mét loslaten bij verwijderen."""
    uniek = False
    for idx in conn.execute("PRAGMA index_list(regels)"):
        if not idx["unique"]:
            continue
        kolommen = [
            rij["name"]
            for rij in conn.execute(f"PRAGMA index_info('{idx['name']}')")
        ]
        if kolommen == ["uur_id"]:
            uniek = True
            break
    if not uniek:
        return False
    for fk in conn.execute("PRAGMA foreign_key_list(regels)"):
        if (fk["from"] == "uur_id" and fk["table"] == "uren"
                and (fk["on_delete"] or "").upper() == "SET NULL"):
            return True
    return False


class MigratieAfgebroken(RuntimeError):
    """De herbouw van regels stopt hier, alsof het proces op dat moment wegvalt."""


# "insert", "drop" of "rename". Buiten de tests blijft dit leeg.
_MIGRATIE_STAP = None

_REGELS_NIEUW = """
    CREATE TABLE regels_nieuw (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        factuur_id INTEGER NOT NULL,
        omschrijving TEXT NOT NULL,
        type TEXT NOT NULL,
        aantal REAL NOT NULL,
        prijs REAL NOT NULL,
        subtotaal REAL NOT NULL,
        klus_id INTEGER,
        inkoop_id INTEGER UNIQUE,
        uur_id INTEGER UNIQUE,
        FOREIGN KEY (factuur_id) REFERENCES facturen (id),
        FOREIGN KEY (inkoop_id) REFERENCES inkopen (id) ON DELETE SET NULL,
        FOREIGN KEY (uur_id) REFERENCES uren (id) ON DELETE SET NULL
    )
"""


def _tabelnamen(conn):
    return {
        rij["name"]
        for rij in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def _crash_op(stap):
    if _MIGRATIE_STAP == stap:
        raise MigratieAfgebroken(stap)


def zin_van_rekening(rij):
    """'rekening 2026-014' of 'een conceptrekening (Testklant A, 16 okt 2026)'.

    'rekening CONCEPT' leest alsof CONCEPT het nummer is. Een concept heeft
    nog geen nummer; de klant en de datum zijn wel te herkennen."""
    if rij is None:
        return "een rekening"
    nummer = (rij["nummer"] or "").strip()
    if nummer:
        return f"rekening {nummer}"
    delen = []
    klant = (rij["klant_naam"] or "").strip()
    if klant:
        delen.append(klant)
    if rij["datum"]:
        delen.append(filter_datum_nl(rij["datum"]))
    if delen:
        return f"een conceptrekening ({', '.join(delen)})"
    return "een conceptrekening"


def _rekening_rij(conn, factuur_id):
    if not factuur_id:
        return None
    return conn.execute(
        "SELECT id, nummer, klant_naam, datum FROM facturen WHERE id=?",
        (factuur_id,),
    ).fetchone()


def _conflict_zin(conn, dag, regel):
    """Wat er mis was, in gewone taal. Het bedrag noemen we erbij: dat blijft."""
    bedrag = nl_bedrag(regel["subtotaal"])
    if dag is None:
        return (
            f"Een regel van €\u00a0{bedrag} verwees naar een dag die er niet meer is. "
            "Het bedrag is blijven staan en de koppeling is weg."
        )
    wanneer = filter_datum_nl(dag["datum"])
    if not dag["factuur_id"]:
        return (
            f"De dag van {wanneer} staat op geen rekening. "
            f"Het bedrag van €\u00a0{bedrag} is blijven staan, "
            "maar de dag hangt niet aan deze rekening."
        )
    titel = zin_van_rekening(_rekening_rij(conn, dag["factuur_id"]))
    return (
        f"De dag van {wanneer} hoort bij {titel}. "
        f"Het bedrag van €\u00a0{bedrag} op deze rekening is blijven staan, "
        "maar de dag is hier losgekoppeld. "
        "Controleer of je die uren niet twee keer rekent."
    )


def _ontkoppel_inconsistente_dagen(conn):
    """uren.factuur_id is leidend. Alleen de regel op díé rekening houdt de dag.

    Een regel die naar een vrije dag wijst, of naar een dag van een andere
    rekening, verliest uur_id. Het bedrag blijft: weggooien zou een verstuurde
    rekening stil kleiner maken. Het conflict gaat naar de log en is te zien
    als je die rekening opent. De dag zelf wordt niet verplaatst."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS rekening_waarschuwing (
               factuur_id INTEGER PRIMARY KEY,
               tekst TEXT NOT NULL
           )"""
    )
    rijen = conn.execute(
        """SELECT id, factuur_id, uur_id, subtotaal FROM regels
           WHERE uur_id IS NOT NULL ORDER BY id"""
    ).fetchall()
    per_dag = {}
    for rij in rijen:
        per_dag.setdefault(rij["uur_id"], []).append(rij)

    per_rekening = {}
    for uur_id, groep in per_dag.items():
        dag = conn.execute(
            "SELECT id, factuur_id, datum FROM uren WHERE id=?", (uur_id,)
        ).fetchone()
        houden = None
        if dag is not None and dag["factuur_id"]:
            for kandidaat in groep:
                if kandidaat["factuur_id"] == dag["factuur_id"]:
                    houden = kandidaat["id"]
                    break
        for kandidaat in groep:
            if kandidaat["id"] == houden:
                continue
            conn.execute(
                "UPDATE regels SET uur_id=NULL WHERE id=?", (kandidaat["id"],)
            )
            zin = _conflict_zin(conn, dag, kandidaat)
            app.logger.warning("Dagkoppeling losgelaten: %s", zin)
            per_rekening.setdefault(kandidaat["factuur_id"], []).append(zin)

    for factuur_id, zinnen in per_rekening.items():
        # Erbíj, niet eroverheen: een bonwaarschuwing van dezelfde start
        # mag niet verdwijnen omdat deze dag er ook niet bij hoorde.
        _waarschuwing_erbij(conn, factuur_id, " ".join(zinnen))


def _herstel_regels_nieuw(conn):
    """Maak een afgebroken herbouw af, of ruim een achtergebleven klad op.

    CREATE TABLE wordt vastgelegd zodra er nog geen transactie openstaat. Valt
    het proces daarna weg, dan bestaat regels_nieuw al en weigert de volgende
    start met 'table regels_nieuw already exists'. De kopie is pas de data als
    de oude tabel weg is; een lege regels die de start net aanmaakte telt niet."""
    namen = _tabelnamen(conn)
    if "regels_nieuw" not in namen:
        return
    if "regels" not in namen:
        conn.execute("ALTER TABLE regels_nieuw RENAME TO regels")
        _teller_op_tabel(conn, "regels")
        return
    oud = conn.execute("SELECT COUNT(*) FROM regels").fetchone()[0]
    nieuw = conn.execute("SELECT COUNT(*) FROM regels_nieuw").fetchone()[0]
    if oud == 0 and nieuw > 0:
        conn.execute("DROP TABLE regels")
        conn.execute("ALTER TABLE regels_nieuw RENAME TO regels")
        _teller_op_tabel(conn, "regels")
        return
    conn.execute("DROP TABLE regels_nieuw")


def _bon_conflict_zin(conn, bon, regel):
    """Wat er mis was bij een bonkoppeling. Het bedrag noemen we erbij: dat blijft."""
    bedrag = nl_bedrag(regel["subtotaal"])
    if bon is None:
        return (
            f"Een regel van €\u00a0{bedrag} verwees naar een bon die er niet meer is. "
            "Het bedrag is blijven staan en de koppeling is weg."
        )
    naam = (bon["omschrijving"] or "een bon").strip()
    if not bon["factuur_id"]:
        return (
            f"De bon '{naam}' staat op meer dan één regel. "
            f"Het bedrag van €\u00a0{bedrag} is blijven staan, "
            "maar de bon hangt niet aan deze rekening."
        )
    titel = zin_van_rekening(_rekening_rij(conn, bon["factuur_id"]))
    return (
        f"De bon '{naam}' hoort bij {titel}. "
        f"Het bedrag van €\u00a0{bedrag} op deze rekening is blijven staan, "
        "maar de bon is hier losgekoppeld. "
        "Controleer of je die bon niet twee keer rekent."
    )


def _waarschuwing_erbij(conn, factuur_id, zin):
    """Zet de zin bij de rekening. Een eerdere zin over een dag blijft staan."""
    bestaat = conn.execute(
        "SELECT tekst FROM rekening_waarschuwing WHERE factuur_id=?",
        (factuur_id,),
    ).fetchone()
    tekst = zin if bestaat is None else f"{bestaat['tekst']} {zin}"
    conn.execute(
        """INSERT INTO rekening_waarschuwing (factuur_id, tekst)
           VALUES (?, ?)
           ON CONFLICT(factuur_id) DO UPDATE SET tekst=excluded.tekst""",
        (factuur_id, tekst),
    )


def _ontkoppel_inconsistente_bonnen(conn):
    """inkopen.factuur_id is leidend. Eén bon, één regel.

    Een regel die naar een bon van een andere rekening wijst, of een tweede
    regel naar dezelfde bon, verliest inkoop_id. Het bedrag blijft: weggooien
    zou een verstuurde rekening stil kleiner maken. Staat de bon nog nergens,
    dan houdt de oudste regel de koppeling."""
    if "regels" not in _tabelnamen(conn) or "inkopen" not in _tabelnamen(conn):
        return
    kolommen = {rij["name"] for rij in conn.execute("PRAGMA table_info(regels)")}
    if "inkoop_id" not in kolommen:
        return
    conn.execute(
        """CREATE TABLE IF NOT EXISTS rekening_waarschuwing (
               factuur_id INTEGER PRIMARY KEY,
               tekst TEXT NOT NULL
           )"""
    )
    rijen = conn.execute(
        """SELECT id, factuur_id, inkoop_id, subtotaal FROM regels
           WHERE inkoop_id IS NOT NULL ORDER BY id"""
    ).fetchall()
    per_bon = {}
    for rij in rijen:
        per_bon.setdefault(rij["inkoop_id"], []).append(rij)

    for inkoop_id, groep in per_bon.items():
        bon = conn.execute(
            "SELECT id, factuur_id, omschrijving FROM inkopen WHERE id=?",
            (inkoop_id,),
        ).fetchone()
        houden = None
        if bon is not None and bon["factuur_id"]:
            for kandidaat in groep:
                if kandidaat["factuur_id"] == bon["factuur_id"]:
                    houden = kandidaat["id"]
                    break
        elif bon is not None:
            houden = groep[0]["id"]
        for kandidaat in groep:
            if kandidaat["id"] == houden:
                continue
            conn.execute(
                "UPDATE regels SET inkoop_id=NULL WHERE id=?", (kandidaat["id"],)
            )
            zin = _bon_conflict_zin(conn, bon, kandidaat)
            app.logger.warning("Bonkoppeling losgelaten: %s", zin)
            _waarschuwing_erbij(conn, kandidaat["factuur_id"], zin)


def _uur_koppeling_uniek(conn):
    """Eén gewerkte dag per regel, en de koppeling valt weg als de dag weggaat.

    SQLite kan een UNIQUE of een foreign key niet achteraf op een kolom zetten.
    De tabel wordt daarom nagebouwd, in één transactie: een crash halverwege
    draait CREATE, INSERT, DROP en RENAME allemaal terug. Blijft er toch een
    regels_nieuw staan — van een eerdere start die de tabel al had vastgelegd —
    dan ruimen we die eerst op.

    De koppeling wordt ook nagekeken als de tabel al uniek was. Een eerdere
    migratie liet soms een regel naar een vrije dag wijzen, of haalde uur_id
    stil weg terwijl het bedrag dubbel bleef staan."""
    _herstel_regels_nieuw(conn)
    if "regels" not in _tabelnamen(conn):
        return
    _ontkoppel_inconsistente_dagen(conn)
    if _uur_koppeling_al_uniek(conn):
        return
    _herbouw_regels(conn)


def _bon_koppeling_al_uniek(conn):
    """Of regels.inkoop_id al één bon per regel afdwingt, mét loslaten bij verwijderen."""
    uniek = False
    for idx in conn.execute("PRAGMA index_list(regels)"):
        if not idx["unique"]:
            continue
        kolommen = [
            rij["name"]
            for rij in conn.execute(f"PRAGMA index_info('{idx['name']}')")
        ]
        if kolommen == ["inkoop_id"]:
            uniek = True
            break
    if not uniek:
        return False
    for fk in conn.execute("PRAGMA foreign_key_list(regels)"):
        if (fk["from"] == "inkoop_id" and fk["table"] == "inkopen"
                and (fk["on_delete"] or "").upper() == "SET NULL"):
            return True
    return False


def _bon_koppeling_uniek(conn):
    """Eén bon per regel, en de koppeling valt weg als de bon weggaat.

    Zelfde herbouw als bij de dagen. Een database van 1.36 had de dag al
    uniek, maar de bon nog niet: twee schermen konden dezelfde bon op twee
    rekeningen zetten. De bedragen blijven staan."""
    _herstel_regels_nieuw(conn)
    if "regels" not in _tabelnamen(conn):
        return
    _ontkoppel_inconsistente_bonnen(conn)
    if _bon_koppeling_al_uniek(conn):
        return
    _herbouw_regels(conn)


def _herbouw_regels(conn):
    """regels nabouwen met de unieke koppelingen, in één transactie.

    Buiten een transactie eerst de foreign keys uit, anders negeert SQLite
    die pragma en weigert de kopie een regel waarvan de rekening er even niet
    is. Valt het proces daarna weg, dan draait CREATE, INSERT, DROP en RENAME
    allemaal terug."""
    _zonder_transactie(conn, "PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")
        conn.execute(_REGELS_NIEUW)
        _crash_op("insert")
        conn.execute(
            """
            INSERT INTO regels_nieuw
                (id, factuur_id, omschrijving, type, aantal, prijs, subtotaal,
                 klus_id, inkoop_id, uur_id)
            SELECT id, factuur_id, omschrijving, type, aantal, prijs, subtotaal,
                   klus_id, inkoop_id, uur_id FROM regels
            """
        )
        _crash_op("drop")
        conn.execute("DROP TABLE regels")
        _crash_op("rename")
        conn.execute("ALTER TABLE regels_nieuw RENAME TO regels")
        _teller_op_tabel(conn, "regels")
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        _zonder_transactie(conn, "PRAGMA foreign_keys=ON")


def tenaamstelling(s):
    """Naam van de rekeninghouder voor op de factuur. Vaak dezelfde als je eigen
    naam, maar bij een en/of-rekening of een rekening op naam van je partner niet."""
    return (s.get("tenaamstelling") or "").strip() or s.get("naam", "")


def bruikbaar_logo(pad):
    """Of reportlab deze afbeelding op de rekening kan tekenen."""
    try:
        ImageReader(pad).getSize()
        return True
    except Exception:
        return False


def get_settings():
    conn = get_db()
    row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    conn.close()
    return dict(row) if row else {}


# ---------- Prullenbak ----------
# Verwijderen is niet meer definitief: wat weggaat wordt eerst hier bewaard, zodat de
# melding erna een knop kan hebben om het terug te halen. Zonder dat is één misgetikte
# knop genoeg om een rekening met al zijn regels en betalingen kwijt te zijn.

# Hoe lang een weggegooid ding blijft staan. Lang genoeg om een vergissing de volgende
# dag nog te herstellen, kort genoeg om de database niet vol te laten lopen.
PRULLENBAK_DAGEN = 30

# Waar een teruggehaalde rij in mag. De tabel- en kolomnamen komen uit JSON en gaan
# rechtstreeks in de query — die kun je niet als parameter meegeven. Vandaag schrijft
# alleen de app zelf die JSON, maar één lek waarlangs iemand hem kan aanpassen is
# genoeg voor een vrije SQL-opdracht. Deze lijst maakt dat onmogelijk.
TERUG_TE_ZETTEN = {
    "facturen", "regels", "betalingen", "offertes", "offerte_regels",
    "klanten", "klussen", "uren", "bijlagen", "notities", "taken",
    "inkopen", "inkoop_materialen",
}


def naar_prullenbak(conn, omschrijving, rijen, bestanden=None, koppelingen=None):
    """Bewaart weggegooide rijen en geeft het id terug om ze mee terug te halen.

    `rijen` is {"tabelnaam": [rij, ...]} in de volgorde waarin ze terug moeten:
    eerst waar naar verwezen wordt, dan wat verwijst. De rijen gaan er met hun
    oorspronkelijke id in, zodat verwijzingen naar de rekening blijven kloppen.

    `bestanden` zijn foto's en bonnetjes in BIJLAGE_DIR. Die blijven gewoon staan
    zolang ze in de prullenbak zitten — een foto is niet opnieuw te maken, een PDF
    wel — en gaan pas weg als de prullenbak wordt opgeruimd.

    `koppelingen` zijn uren en bonnen die aan een rekening hingen. Die rijen
    zelf blijven bestaan (ze komen weer vrij), maar bij ongedaan maken moeten
    ze terug aan díé rekening, anders kun je ze een tweede keer factureren."""
    inhoud = {"rijen": {tabel: [dict(r) for r in lijst] for tabel, lijst in rijen.items()},
              "bestanden": list(bestanden or []),
              "koppelingen": koppelingen or {}}
    prullenbak_id = conn.execute(
        "INSERT INTO prullenbak (omschrijving, inhoud, wanneer) VALUES (?, ?, ?)",
        (omschrijving, json.dumps(inhoud, ensure_ascii=False), nu().isoformat()),
    ).lastrowid
    ruim_prullenbak_op(conn)
    return prullenbak_id


def ruim_prullenbak_op(conn):
    """Wat te lang in de prullenbak staat gaat er echt uit, bestanden en al."""
    grens = (nu() - timedelta(days=PRULLENBAK_DAGEN)).isoformat()
    oud = conn.execute("SELECT id, inhoud FROM prullenbak WHERE wanneer < ?", (grens,)).fetchall()
    for rij in oud:
        for bestand in json.loads(rij["inhoud"]).get("bestanden", []):
            pad = os.path.join(BIJLAGE_DIR, bestand)
            if os.path.exists(pad):
                os.remove(pad)
    conn.execute("DELETE FROM prullenbak WHERE wanneer < ?", (grens,))


def _taak_toch_terug(conn, regel):
    """Zet een taak terug als zijn oude nummer intussen ergens anders staat.

    INSERT OR IGNORE doet dan niets. Is het dezelfde taak, dan staat hij er
    al. Is het een ander nummer dat opnieuw is uitgegeven, dan komt de tekst
    terug onder een vrij nummer — anders is de lijst na ongedaan maken leeg.
    """
    bestaat = conn.execute(
        "SELECT notitie_id, tekst FROM taken WHERE id=?", (regel["id"],)
    ).fetchone()
    if bestaat is None:
        raise sqlite3.IntegrityError("taak niet teruggezet")
    if (bestaat["notitie_id"] == regel.get("notitie_id")
            and bestaat["tekst"] == regel.get("tekst")):
        return
    zonder_id = {k: v for k, v in regel.items() if k != "id"}
    if not zonder_id:
        return
    kolommen = ", ".join(zonder_id.keys())
    vraagtekens = ", ".join("?" for _ in zonder_id)
    conn.execute(
        f"INSERT INTO taken ({kolommen}) VALUES ({vraagtekens})",
        list(zonder_id.values()),
    )


def terugknop(prullenbak_id):
    """De knop die bij de melding na een verwijdering hoort."""
    return {"label": "Ongedaan maken",
            "url": url_for("prullenbak_terug", prullenbak_id=prullenbak_id)}


@app.route("/prullenbak/<int:prullenbak_id>/terug", methods=["POST"])
def prullenbak_terug(prullenbak_id):
    conn = get_db()
    rij = conn.execute("SELECT * FROM prullenbak WHERE id=?", (prullenbak_id,)).fetchone()
    if rij is None:
        conn.close()
        melding("Dit is niet meer terug te halen.", "fout")
        return redirect(terug_naar(url_for("index")))

    inhoud = json.loads(rij["inhoud"])
    botst = _terugzetten_botst(conn, inhoud)
    if botst:
        conn.close()
        melding(botst, "fout")
        return redirect(terug_naar(url_for("index")))

    begin_direct(conn)
    try:
        for tabel, regels in inhoud["rijen"].items():
            if tabel not in TERUG_TE_ZETTEN:
                continue
            for regel in regels:
                kolommen_in_tabel = {k["name"] for k in
                                     conn.execute(f"PRAGMA table_info({tabel})")}
                regel = {k: v for k, v in regel.items() if k in kolommen_in_tabel}
                if not regel:
                    continue
                kolommen = ", ".join(regel.keys())
                vraagtekens = ", ".join("?" for _ in regel)
                # OR IGNORE: haal je twee keer hetzelfde terug, dan hoeft dat niet te
                # klappen op een id dat er al staat. Een regel die stilletjes
                # wegvalt omdat de dag al op een andere rekening staat, mag niet:
                # dan komt de rekening terug met het oude totaal en nul regels.
                cur = conn.execute(
                    f"INSERT OR IGNORE INTO {tabel} ({kolommen}) VALUES ({vraagtekens})",
                    list(regel.values()),
                )
                if tabel == "regels" and cur.rowcount == 0 and "id" in regel:
                    bestaat = conn.execute(
                        "SELECT id FROM regels WHERE id=?", (regel["id"],)
                    ).fetchone()
                    if bestaat is None:
                        raise sqlite3.IntegrityError("regel niet teruggezet")
                # Een herbouwde tabel kan een taaknummer opnieuw uitgeven.
                # OR IGNORE slaat de oude taak dan stil over; die hoort terug,
                # desnoods onder een nieuw nummer.
                if tabel == "taken" and cur.rowcount == 0 and "id" in regel:
                    _taak_toch_terug(conn, regel)
        gemist = _herstel_koppelingen(conn, inhoud.get("koppelingen") or {})
        _herstel_uur_regels(conn, inhoud.get("koppelingen") or {})
        _herstel_inkoop_regels(conn, inhoud.get("koppelingen") or {})
        conn.execute("DELETE FROM prullenbak WHERE id=?", (prullenbak_id,))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        conn.close()
        melding(
            "Deze rekening kan niet terug. Een dag erop staat al op een andere "
            "rekening, en de regels zouden anders stil verdwijnen.",
            "fout",
        )
        return redirect(terug_naar(url_for("index")))
    conn.close()
    melding(f"{rij['omschrijving']} staat weer terug.")
    if gemist:
        melding("Een deel van de uren of bonnen stond al op een andere rekening "
                "en is daar gebleven.", "fout")
    return redirect(terug_naar(url_for("index")))


def _herstel_koppelingen(conn, koppelingen):
    """Hangt uren en bonnen weer aan de teruggezette rekening.

    Alleen als ze nog vrij zijn. Staan ze intussen op een andere rekening, dan
    laten we die met rust: anders haal je ze daar stilletjes af."""
    factuur_id = koppelingen.get("factuur_id")
    if not factuur_id:
        return 0
    gemist = 0
    for tabel, sleutel in (("uren", "uren"), ("inkopen", "inkopen")):
        for rij_id in koppelingen.get(sleutel) or []:
            cur = conn.execute(
                f"UPDATE {tabel} SET factuur_id=? WHERE id=? AND factuur_id IS NULL",
                (factuur_id, rij_id),
            )
            if cur.rowcount:
                continue
            bestaat = conn.execute(
                f"SELECT factuur_id FROM {tabel} WHERE id=?", (rij_id,)
            ).fetchone()
            if bestaat and bestaat["factuur_id"] not in (None, factuur_id):
                gemist += 1
    return gemist


def _herstel_uur_regels(conn, koppelingen):
    """Zet uur_id terug op de regel, nadat een klus uit de prullenbak komt.

    Verwijderen van de dag zet die kolom op NULL (ON DELETE SET NULL). Zonder
    deze stap staat de regel erna los van de dag, terwijl de dag weer naar de
    rekening wijst."""
    for paar in koppelingen.get("uur_regels") or []:
        uur_id = paar.get("uur_id")
        regel_id = paar.get("regel_id")
        if not uur_id or not regel_id:
            continue
        bezet = conn.execute(
            "SELECT id FROM regels WHERE uur_id=? AND id<>?",
            (uur_id, regel_id),
        ).fetchone()
        if bezet:
            continue
        conn.execute(
            "UPDATE regels SET uur_id=? WHERE id=? AND (uur_id IS NULL OR uur_id=?)",
            (uur_id, regel_id, uur_id),
        )


def _herstel_inkoop_regels(conn, koppelingen):
    """Zet inkoop_id terug op de regel, nadat een bon uit de prullenbak komt.

    Verwijderen van de bon zet die kolom op NULL (ON DELETE SET NULL). Zonder
    deze stap staat de regel erna los van de bon, terwijl de bon er weer is."""
    for paar in koppelingen.get("inkoop_regels") or []:
        inkoop_id = paar.get("inkoop_id")
        regel_id = paar.get("regel_id")
        if not inkoop_id or not regel_id:
            continue
        bezet = conn.execute(
            "SELECT id FROM regels WHERE inkoop_id=? AND id<>?",
            (inkoop_id, regel_id),
        ).fetchone()
        if bezet:
            continue
        conn.execute(
            "UPDATE regels SET inkoop_id=? WHERE id=? AND (inkoop_id IS NULL OR inkoop_id=?)",
            (inkoop_id, regel_id, inkoop_id),
        )


def _dag_op_andere_rekening(conn, regel):
    """De rekening waar deze dag nu op staat, als dat niet de terug te zetten is."""
    uur_id = regel.get("uur_id")
    if not uur_id:
        return None
    factuur_id = regel.get("factuur_id")
    bezet = conn.execute(
        """SELECT f.id, f.nummer, f.klant_naam, f.datum
           FROM regels r JOIN facturen f ON f.id = r.factuur_id
           WHERE r.uur_id=? AND r.factuur_id<>?""",
        (uur_id, factuur_id),
    ).fetchone()
    if bezet:
        return uur_id, bezet
    dag = conn.execute(
        """SELECT f.id, f.nummer, f.klant_naam, f.datum
           FROM uren u JOIN facturen f ON f.id = u.factuur_id
           WHERE u.id=? AND u.factuur_id IS NOT NULL AND u.factuur_id<>?""",
        (uur_id, factuur_id),
    ).fetchone()
    if dag:
        return uur_id, dag
    return None


def _bon_op_andere_rekening(conn, regel):
    """De rekening waar deze bon nu op staat, als dat niet de terug te zetten is."""
    inkoop_id = regel.get("inkoop_id")
    if not inkoop_id:
        return None
    factuur_id = regel.get("factuur_id")
    bezet = conn.execute(
        """SELECT f.id, f.nummer, f.klant_naam, f.datum, i.omschrijving
           FROM regels r
           JOIN facturen f ON f.id = r.factuur_id
           JOIN inkopen i ON i.id = r.inkoop_id
           WHERE r.inkoop_id=? AND r.factuur_id<>?""",
        (inkoop_id, factuur_id),
    ).fetchone()
    if bezet:
        return bezet
    return conn.execute(
        """SELECT f.id, f.nummer, f.klant_naam, f.datum, i.omschrijving
           FROM inkopen i JOIN facturen f ON f.id = i.factuur_id
           WHERE i.id=? AND i.factuur_id IS NOT NULL AND i.factuur_id<>?""",
        (inkoop_id, factuur_id),
    ).fetchone()


def _terugzetten_botst(conn, inhoud):
    """None, of een melding als terughalen regels stil zou laten vallen.

    De dag of de bon staat dan al op een andere rekening. INSERT OR IGNORE
    slaat de regel over vanwege de unieke koppeling, en de rekening komt
    terug met het oude totaal en nul regels. Het jaartotaal telt dat bedrag
    dan dubbel."""
    per_rekening = {}
    bon_per_rekening = {}
    for regel in (inhoud.get("rijen") or {}).get("regels") or []:
        gevonden = _dag_op_andere_rekening(conn, regel)
        if gevonden:
            uur_id, andere = gevonden
            dag = conn.execute(
                "SELECT datum FROM uren WHERE id=?", (uur_id,)
            ).fetchone()
            wanneer = filter_datum_nl(dag["datum"]) if dag else "een dag"
            per_rekening.setdefault(andere["id"], {
                "titel": zin_van_rekening(andere),
                "dagen": [],
            })["dagen"].append(wanneer)
        bon = _bon_op_andere_rekening(conn, regel)
        if bon:
            bon_per_rekening.setdefault(bon["id"], {
                "titel": zin_van_rekening(bon),
                "bonnen": [],
            })["bonnen"].append((bon["omschrijving"] or "een bon").strip())
    if not per_rekening and not bon_per_rekening:
        return None
    zinnen = []
    for info in per_rekening.values():
        dagen = info["dagen"]
        if len(dagen) == 1:
            zinnen.append(f"De dag van {dagen[0]} staat nu op {info['titel']}.")
        else:
            zinnen.append(
                f"De dagen van {' en '.join(dagen)} staan nu op {info['titel']}."
            )
    for info in bon_per_rekening.values():
        namen = info["bonnen"]
        if len(namen) == 1:
            zinnen.append(f"De bon '{namen[0]}' staat nu op {info['titel']}.")
        else:
            zinnen.append(
                "De bonnen "
                + " en ".join(f"'{naam}'" for naam in namen)
                + f" staan nu op {info['titel']}."
            )
    if per_rekening and bon_per_rekening:
        slot = "Haal die dagen en bonnen daar eerst af als je deze rekening toch terug wilt."
    elif bon_per_rekening:
        slot = "Haal die bon daar eerst af als je deze rekening toch terug wilt."
    else:
        slot = "Haal die dagen daar eerst af als je deze rekening toch terug wilt."
    return "Deze rekening kan niet terug. " + " ".join(zinnen) + " " + slot


def factuurnaam(factuur):
    """Hoe de rekening heet in de app en in een mail. Een concept heeft nog geen
    nummer: dat wordt pas vergeven als hij definitief wordt, zodat een weggegooid
    concept geen gat in de reeks achterlaat."""
    return factuur["nummer"] or f"concept {factuur['id']}"


def pdf_bestandsnaam(factuur):
    """De naam van het PDF-bestand. Concepten staan onder hun eigen naam in de map,
    zodat ze niet botsen met een echte rekening en makkelijk te herkennen zijn."""
    if factuur["nummer"]:
        return f"{factuur['nummer']}.pdf"
    return f"concept-{factuur['id']}.pdf"


def maak_definitief(conn, factuur_id):
    """Geeft een concept zijn nummer en ruimt de concept-PDF op. Geeft de rekening
    terug zoals hij daarna is. Een rekening die al een nummer heeft, blijft zoals hij is."""
    factuur = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    if factuur is None or factuur["nummer"]:
        return factuur

    oude_pdf = os.path.join(PDF_DIR, pdf_bestandsnaam(factuur))
    nummer = volgend_nummer(conn)
    conn.execute("UPDATE facturen SET nummer=? WHERE id=?", (nummer, factuur_id))
    conn.commit()

    if os.path.exists(oude_pdf):
        os.remove(oude_pdf)
    maak_pdf(factuur_id)
    return conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()


def _volgnummer(nummer):
    """Het cijferdeel achter het jaartal, of 0 als het nummer er anders uitziet."""
    staart = str(nummer or "").split("-", 1)[-1]
    return int(staart) if staart.isdigit() else 0


def volgend_nummer(conn=None):
    """Het eerstvolgende vrije nummer van dit jaar.

    Doortellen op het hóógste bestaande nummer, niet op het aantal rekeningen:
    anders levert een verwijderde rekening een nummer op dat al bestaat, en
    overschrijft de nieuwe PDF die van de oude rekening."""
    eigen = conn is None
    if eigen:
        conn = get_db()
    jaar = vandaag().year
    nummers = conn.execute(
        "SELECT nummer FROM facturen WHERE nummer LIKE ?", (f"{jaar}-%",)
    ).fetchall()
    if eigen:
        conn.close()
    hoogste = max((_volgnummer(rij["nummer"]) for rij in nummers), default=0)
    return f"{jaar}-{hoogste + 1:03d}"


def volgend_offertenummer(conn=None):
    """Het eerstvolgende vrije offertenummer van dit jaar, in de vorm OFF-2026-001.

    Offertes tellen apart van rekeningen: het voorvoegsel houdt de twee reeksen uit
    elkaar, ook in de map met PDF's."""
    eigen = conn is None
    if eigen:
        conn = get_db()
    jaar = vandaag().year
    nummers = conn.execute(
        "SELECT nummer FROM offertes WHERE nummer LIKE ?", (f"OFF-{jaar}-%",)
    ).fetchall()
    if eigen:
        conn.close()
    hoogste = max((_volgnummer(rij["nummer"].rsplit("-", 1)[-1]) for rij in nummers),
                  default=0)
    return f"OFF-{jaar}-{hoogste + 1:03d}"


def geldig_tot(datum):
    """Offertedatum plus de geldigheidstermijn, als ISO-datum."""
    try:
        return (date.fromisoformat(str(datum))
                + timedelta(days=OFFERTE_GELDIG_DAGEN)).isoformat()
    except ValueError:
        return datum


def net_bestaande_gegevens():
    """Zet adressen en telefoonnummers die er al staan ook in de vaste vorm.

    Eenmalig bij het opstarten, anders blijft je lijst er door elkaar uitzien: alleen
    wat je ná deze versie opslaat zou netjes worden. Wat de opmaakregels niet als
    Nederlands adres of nummer herkennen blijft staan zoals het is, dus een
    buitenlandse klant verandert niet."""
    conn = get_db()
    aangepast = 0
    for tabel, velden in [("klanten", ("adres", "telefoon")),
                          ("facturen", ("klant_adres",)),
                          ("offertes", ("klant_adres",)),
                          ("settings", ("adres", "telefoon"))]:
        kolommen = ", ".join(velden)
        for rij in conn.execute(f"SELECT id, {kolommen} FROM {tabel}").fetchall():
            nieuw = {}
            for veld in velden:
                oud = rij[veld] or ""
                schoon = net_telefoon(oud) if veld == "telefoon" else net_adres(oud)
                if schoon != oud:
                    nieuw[veld] = schoon
            if nieuw:
                zetten = ", ".join(f"{veld}=?" for veld in nieuw)
                conn.execute(f"UPDATE {tabel} SET {zetten} WHERE id=?",
                             list(nieuw.values()) + [rij["id"]])
                aangepast += 1
    conn.commit()
    conn.close()
    return aangepast


def herstel_dubbele_nummers():
    """Repareert rekeningen die door de oude telling hetzelfde nummer kregen.

    De oudste houdt zijn nummer, de nieuwere krijgen een vrij nummer. Daarna
    worden beide PDF's opnieuw getekend, want die van de oudste is destijds
    overschreven door de nieuwere."""
    conn = get_db()
    dubbel = conn.execute(
        """SELECT nummer FROM facturen WHERE nummer <> ''
           GROUP BY nummer HAVING COUNT(*) > 1 ORDER BY nummer"""
    ).fetchall()
    if not dubbel:
        conn.close()
        return []

    hersteld = []
    for rij in dubbel:
        facturen = conn.execute(
            "SELECT id, nummer FROM facturen WHERE nummer=? ORDER BY id", (rij["nummer"],)
        ).fetchall()
        for factuur in facturen[1:]:
            jaar = factuur["nummer"].split("-", 1)[0]
            gebruikt = {
                _volgnummer(r["nummer"])
                for r in conn.execute(
                    "SELECT nummer FROM facturen WHERE nummer LIKE ?", (f"{jaar}-%",)
                )
            }
            vrij = max(gebruikt) + 1
            nieuw_nummer = f"{jaar}-{vrij:03d}"
            conn.execute(
                "UPDATE facturen SET nummer=? WHERE id=?", (nieuw_nummer, factuur["id"])
            )
            hersteld.append((factuur["nummer"], nieuw_nummer))
        conn.commit()
    ids = [r["id"] for r in conn.execute("SELECT id FROM facturen ORDER BY id")]
    conn.close()

    # Alle PDF's opnieuw tekenen: de overschreven exemplaren kloppen weer.
    for factuur_id in ids:
        maak_pdf(factuur_id)
    return hersteld


def zoek_in(rijen, term, velden):
    """Rijen die de zoekterm bevatten in een van de opgegeven velden.

    Elk woord moet ergens voorkomen, maar niet per se in hetzelfde veld: zo vindt
    "jansen 2026" de rekening van Jansen uit dit jaar."""
    woorden = term.lower().split()
    if not woorden:
        return rijen

    gevonden = []
    for rij in rijen:
        hooiberg = " ".join(str(rij[veld] or "") for veld in velden).lower()
        if all(woord in hooiberg for woord in woorden):
            gevonden.append(rij)
    return gevonden


def betalingen_van(conn, factuur_id):
    """De losse betalingen op één rekening, oudste eerst."""
    return conn.execute(
        "SELECT * FROM betalingen WHERE factuur_id=? ORDER BY datum, id", (factuur_id,)
    ).fetchall()


def betaald_op(conn, factuur_id):
    """Wat er tot nu toe op deze rekening is binnengekomen."""
    som = conn.execute(
        "SELECT COALESCE(SUM(bedrag), 0) FROM betalingen WHERE factuur_id=?", (factuur_id,)
    ).fetchone()[0]
    return round(som, 2)


def herzie_betaalstatus(conn, factuur_id):
    """Zet de rekening op betaald zodra het hele bedrag binnen is, en weer open als
    dat door een teruggedraaide boeking niet meer zo is. Een cent speling, want
    optellen van kommagetallen komt niet altijd precies uit."""
    factuur = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    if factuur is None:
        return
    betaald = betaald_op(conn, factuur_id)

    if betaald + 0.005 >= factuur["totaal"] and factuur["status"] != "betaald":
        conn.execute(
            "UPDATE facturen SET status='betaald', status_voor_betaald=? WHERE id=?",
            (factuur["status"], factuur_id),
        )
    elif betaald + 0.005 < factuur["totaal"] and factuur["status"] == "betaald":
        terug = factuur["status_voor_betaald"] or "verzonden"
        conn.execute(
            "UPDATE facturen SET status=?, status_voor_betaald='' WHERE id=?",
            (terug, factuur_id),
        )


def factuurlijst(conn, klant_id=None):
    """Alle rekeningen, met de vervaldatum, wat er al betaald is en wat er nog
    openstaat. Met een klant_id alleen die van één klant."""
    vandaag = nu().date().isoformat()
    per_factuur = {
        rij["factuur_id"]: rij["som"]
        for rij in conn.execute(
            "SELECT factuur_id, SUM(bedrag) AS som FROM betalingen GROUP BY factuur_id"
        )
    }

    if klant_id is None:
        rijen = conn.execute("SELECT * FROM facturen ORDER BY datum DESC, id DESC")
    else:
        rijen = conn.execute(
            "SELECT * FROM facturen WHERE klant_id=? ORDER BY datum DESC, id DESC",
            (klant_id,)
        )

    lijst = []
    for rij in rijen:
        factuur = dict(rij)
        # Hoe hij heet in de lijst: zijn nummer, of "concept 6" zolang hij er geen heeft.
        factuur["naam"] = factuurnaam(rij)
        # Leeg = geen termijn; dan ook geen "Te laat" en geen "vóór ..." in de lijst.
        factuur["vervalt"] = rij["vervalt_op"] or ""
        factuur["verlopen"] = (
            bool(factuur["vervalt"])
            and rij["status"] != "betaald"
            and factuur["vervalt"] < vandaag
        )
        factuur["betaald"] = round(per_factuur.get(rij["id"], 0.0), 2)
        factuur["openstaand"] = round(rij["totaal"] - factuur["betaald"], 2)
        # Er is iets binnen, maar nog niet alles: dat verdient een eigen chip, anders
        # ziet een rekening waarop de helft is betaald eruit als een die nog dicht is.
        factuur["deels_betaald"] = (
            rij["status"] != "betaald" and factuur["betaald"] > 0
        )
        lijst.append(factuur)
    return lijst


@app.route("/")
def index():
    # Wat er bij het opstarten is rechtgezet, hoort de gebruiker één keer te zien.
    while OPSTARTMELDINGEN:
        melding(OPSTARTMELDINGEN.pop(0))

    conn = get_db()
    facturen = factuurlijst(conn)
    conn.close()

    jaar = str(vandaag().year)
    openstaand = [f for f in facturen if f["status"] != "betaald"]
    verlopen = [f for f in openstaand if f["verlopen"]]
    overzicht = {
        "openstaand": sum(f["openstaand"] for f in openstaand),
        "openstaand_aantal": len(openstaand),
        "verlopen_aantal": len(verlopen),
        "verlopen_bedrag": sum(f["openstaand"] for f in verlopen),
        "jaar": jaar,
        "jaar_totaal": sum(f["totaal"] for f in facturen if str(f["datum"]).startswith(jaar)),
        "aantal": len(facturen),
    }

    keuze = request.args.get("status", "alles")
    if keuze == "openstaand":
        zichtbaar = openstaand
    elif keuze == "verlopen":
        zichtbaar = verlopen
    elif keuze == "betaald":
        zichtbaar = [f for f in facturen if f["status"] == "betaald"]
    else:
        keuze, zichtbaar = "alles", facturen

    # Wie op zijn geld wacht, wil de langst openstaande bovenaan zien; bij de rest
    # staat de nieuwste bovenaan. "Nieuwste" is de datum van de rekening en niet de
    # volgorde waarin je ze intypte: voer je er een van vorige maand alsnog in, dan
    # hoort hij niet bovenaan te springen.
    if keuze in ("openstaand", "verlopen"):
        zichtbaar = sorted(zichtbaar, key=lambda f: f["datum"])

    zoek = request.args.get("q", "").strip()
    if zoek:
        zichtbaar = zoek_in(zichtbaar, zoek, ["nummer", "klant_naam", "klant_email", "kenmerk"])

    return render_template("index.html", facturen=zichtbaar, overzicht=overzicht,
                           keuze=keuze, zoek=zoek, totaal_aantal=len(facturen),
                           actief="index")


@app.route("/instellingen", methods=["GET", "POST"])
def instellingen():
    if request.method == "POST":
        conn = get_db()
        huidig = get_settings()
        logo_bestand = request.form.get("bestaand_logo", "")
        logo_file = request.files.get("logo")
        if logo_file and logo_file.filename:
            # secure_filename kan leeg uitkomen bij een naam van alleen vreemde tekens;
            # opslaan onder de map zelf gaf dan een foutpagina.
            filename = secure_filename(logo_file.filename) or "logo.png"
            doel = os.path.join(LOGO_DIR, filename)
            logo_file.save(doel)
            if bruikbaar_logo(doel):
                logo_bestand = filename
            else:
                # Anders staat er straks een factuur zonder logo zonder dat je weet waarom.
                os.remove(doel)
                melding(f"{filename} kan niet op de rekening worden getekend. Gebruik een "
                      "PNG of JPG; een HEIC-foto van een iPhone of een SVG werkt niet.", "fout")
        # Leeg betekent "de afzender van dit moment". Het veld in het scherm
        # is daarom leeg, met de afzender als voorbeeld. Wat je intikt en dat
        # hetzelfde adres is, bewaren we ook niet: anders blijft een oude kopie
        # staan als je de afzender later wijzigt.
        afzender = _adres_specificatie(
            request.form.get("smtp_van", "")
        ) or _adres_specificatie(request.form.get("smtp_user", ""))
        # Leeg is expres leeg. Tekst die geen adres is, is dat niet: die
        # weggooien zou een kapot veld stil tot "volg de afzender" maken.
        bcc_ingevoerd = request.form.get("bcc_adres", "").strip()
        specs = _adres_specificaties(bcc_ingevoerd) if bcc_ingevoerd else []
        if not bcc_ingevoerd:
            bcc_spec = ""
        elif len(specs) == 1 and geldig_mailadres(specs[0]):
            bcc_spec = specs[0]
            if afzender and bcc_spec.casefold() == afzender.casefold():
                bcc_ingevoerd = ""
                bcc_spec = ""
            else:
                bcc_ingevoerd = bcc_spec
        else:
            bcc_spec = ""
        bcc_aan = 1 if request.form.get("bcc_aan") == "ja" else 0
        # Uitvinken moet uit blijven, ook als het adresveld kapot is. Alleen
        # een aangevinkte kopie met een onbruikbaar adres houden we tegen.
        bcc_fout = bool(bcc_ingevoerd) and not geldig_mailadres(bcc_spec)
        if bcc_fout:
            bcc_ingevoerd = (huidig.get("bcc_adres") or "").strip()
            if not bcc_aan:
                bcc_fout = False
        conn.execute(
            """UPDATE settings SET naam=?, adres=?, telefoon=?, email=?, iban=?,
               tenaamstelling=?, logo_bestand=?, smtp_host=?,
               smtp_port=?, smtp_user=?, smtp_pass=?, smtp_van=?,
               bcc_aan=?, bcc_adres=? WHERE id=1""",
            (
                request.form.get("naam", ""),
                net_adres(request.form.get("adres", "")),
                net_telefoon(request.form.get("telefoon", "")),
                request.form.get("email", ""),
                request.form.get("iban", ""),
                request.form.get("tenaamstelling", ""),
                logo_bestand,
                request.form.get("smtp_host", ""),
                int(request.form.get("smtp_port") or 587),
                request.form.get("smtp_user", ""),
                # Leeg betekent "niet gewijzigd": het veld komt bewust leeg binnen,
                # want het opgeslagen wachtwoord wordt niet naar de browser gestuurd.
                request.form.get("smtp_pass", "") or huidig.get("smtp_pass", ""),
                request.form.get("smtp_van", ""),
                bcc_aan,
                bcc_ingevoerd,
            ),
        )
        conn.commit()
        conn.close()
        if bcc_fout:
            melding("Het adres voor de kopie klopt niet. Gebruik één e-mailadres, "
                    "zonder extra tekst. De rest is opgeslagen.", "fout")
        else:
            melding("Instellingen opgeslagen.")
        return redirect(url_for("instellingen"))

    # De code blijft in de sessie tot de pagina eruit is. Weghalen vóór het
    # tekenen betekent dat een mislukte pagina hem kwijt is zonder dat je hem
    # zag. Na een gelukte pagina is een verversing te laat: hij staat nergens
    # meer, ook niet in de database, alleen de hash.
    scan_code = session.get("scan_token_eenmalig") or ""
    if scan_code:
        @after_this_request
        def _wis_getoonde_scancode(antwoord):
            session.pop("scan_token_eenmalig", None)
            return antwoord
    return render_template("instellingen.html", s=get_settings(),
                           gebruiker=session.get("gebruiker"),
                           via_zijbalk=via_ingress(),
                           heeft_account=heeft_account(),
                           wachtwoord_minimum=WACHTWOORD_MINIMUM,
                           scan_code=scan_code or "",
                           scan_heeft_code=bool((get_settings().get("scan_token_hash") or "").strip()),
                           scan_url=scan_upload_adres(),
                           actief="instellingen")


def klantenlijst():
    """Alle klanten met hoeveel er per klant is gefactureerd en wat er nog openstaat."""
    conn = get_db()
    klanten = conn.execute(
        """SELECT k.*,
                  COUNT(f.id) AS aantal_facturen,
                  COALESCE(SUM(f.totaal), 0) AS omzet,
                  COALESCE(SUM(CASE WHEN f.status <> 'betaald' THEN
                      f.totaal - COALESCE((SELECT SUM(b.bedrag) FROM betalingen b
                                           WHERE b.factuur_id = f.id), 0)
                      ELSE 0 END), 0) AS openstaand
           FROM klanten k
           LEFT JOIN facturen f ON f.klant_id = k.id
           GROUP BY k.id
           ORDER BY k.naam COLLATE NOCASE"""
    ).fetchall()
    conn.close()
    return klanten


def klant_uit_form(form):
    return (
        form.get("naam", "").strip(),
        net_adres(form.get("adres", "")),
        form.get("email", "").strip(),
        net_telefoon(form.get("telefoon", "")),
        form.get("notitie", "").strip(),
    )


# Hoe kolommen in een CSV mogen heten. Excel, Google Contacts en boekhoudpakketten
# noemen dezelfde kolom allemaal net anders; hier vertalen we ze naar één naam.
CSV_KOLOMMEN = {
    "naam": ["naam", "klant", "klantnaam", "bedrijf", "bedrijfsnaam", "name",
             "company", "display name", "volledige naam"],
    "adres": ["adres", "straat", "address", "street", "adresregel", "postadres"],
    "email": ["email", "e-mail", "emailadres", "e-mailadres", "mail",
              "e-mail address", "email address", "e-mail 1 - value"],
    "telefoon": ["telefoon", "tel", "telefoonnummer", "mobiel", "gsm", "phone",
                 "phone 1 - value", "telephone"],
    "notitie": ["notitie", "notities", "opmerking", "opmerkingen", "notes", "memo"],
}

# Hoe een postcode-en-plaatskolom bij het adres wordt gezet, als die apart staat.
CSV_ADRES_EXTRA = ["postcode", "zip", "postal code", "plaats", "woonplaats", "city"]


def _csv_lezer(inhoud):
    """Geeft een DictReader die overweg kan met puntkomma's (Excel in Nederland),
    komma's en tabs, en met een byte-order-mark aan het begin van het bestand."""
    tekst = inhoud.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
    eerste = tekst.split("\n", 1)[0]
    scheiding = max([";", ",", "\t"], key=eerste.count)
    return csv.DictReader(io.StringIO(tekst), delimiter=scheiding)


def _kolomnamen(veldnamen):
    """Koppelt de koppen uit het bestand aan onze eigen namen."""
    gevonden = {}
    extra_adres = []
    for kop in veldnamen or []:
        schoon = (kop or "").strip().lower()
        for eigen, namen in CSV_KOLOMMEN.items():
            if schoon in namen and eigen not in gevonden:
                gevonden[eigen] = kop
                break
        else:
            if schoon in CSV_ADRES_EXTRA:
                extra_adres.append(kop)
    return gevonden, extra_adres


@app.route("/klanten")
def klanten():
    lijst = klantenlijst()
    zoek = request.args.get("q", "").strip()
    zichtbaar = zoek_in(lijst, zoek, ["naam", "email", "telefoon", "adres"]) if zoek else lijst
    return render_template("klanten.html", klanten=zichtbaar, zoek=zoek,
                           totaal_aantal=len(lijst), actief="klanten")


@app.route("/klanten/import", methods=["GET", "POST"])
def klanten_import():
    """Klanten uit een CSV-bestand inlezen, bijvoorbeeld een export uit je oude
    boekhouding of uit je telefoonboek."""
    if request.method != "POST":
        return render_template("klanten_import.html", actief="klanten")

    bestand = request.files.get("bestand")
    if not bestand or not bestand.filename:
        melding("Kies eerst een CSV-bestand.", "fout")
        return redirect(url_for("klanten_import"))

    lezer = _csv_lezer(bestand.read())
    kolommen, extra_adres = _kolomnamen(lezer.fieldnames)
    if "naam" not in kolommen:
        melding("In dit bestand staat geen kolom met een naam. Zorg dat de eerste regel "
              "de kopjes bevat, met in elk geval een kolom 'naam'.", "fout")
        return redirect(url_for("klanten_import"))

    bijwerken = request.form.get("bijwerken") == "ja"
    conn = get_db()
    nieuw = bijgewerkt = overgeslagen = 0
    for rij in lezer:
        def waarde(eigen, rij=rij):
            return (rij.get(kolommen.get(eigen, ""), "") or "").strip()

        naam = waarde("naam")
        if not naam:
            overgeslagen += 1
            continue

        adres_delen = [waarde("adres")] + [
            (rij.get(kop, "") or "").strip() for kop in extra_adres
        ]
        # Ook wat uit een CSV komt in dezelfde vorm zetten; die bestanden zijn juist
        # een bron van "1234ab voorbeeldstad" en "06-12345678".
        adres = net_adres("\n".join(deel for deel in adres_delen if deel))
        email, notitie = waarde("email"), waarde("notitie")
        telefoon = net_telefoon(waarde("telefoon"))

        bestaand = conn.execute(
            "SELECT id FROM klanten WHERE naam=? COLLATE NOCASE", (naam,)
        ).fetchone()
        if bestaand and not bijwerken:
            overgeslagen += 1
            continue
        if bestaand:
            # Alleen overschrijven wat er in het bestand staat; een lege kolom mag
            # niet zomaar wissen wat je zelf al had ingevuld.
            conn.execute(
                """UPDATE klanten SET
                   adres = CASE WHEN ? <> '' THEN ? ELSE adres END,
                   email = CASE WHEN ? <> '' THEN ? ELSE email END,
                   telefoon = CASE WHEN ? <> '' THEN ? ELSE telefoon END,
                   notitie = CASE WHEN ? <> '' THEN ? ELSE notitie END
                   WHERE id=?""",
                (adres, adres, email, email, telefoon, telefoon, notitie, notitie,
                 bestaand["id"]),
            )
            bijgewerkt += 1
        else:
            conn.execute(
                """INSERT INTO klanten (naam, adres, email, telefoon, notitie)
                   VALUES (?, ?, ?, ?, ?)""",
                (naam, adres, email, telefoon, notitie),
            )
            nieuw += 1
    conn.commit()
    conn.close()

    tekst = f"{nieuw} klant{'en' if nieuw != 1 else ''} toegevoegd"
    if bijgewerkt:
        tekst += f", {bijgewerkt} bijgewerkt"
    if overgeslagen:
        tekst += f", {overgeslagen} overgeslagen (naam leeg of stond er al)"
    melding(tekst + ".")
    return redirect(url_for("klanten"))


@app.route("/regels/voorbeeld.csv")
def regels_voorbeeld():
    """Voorbeeld voor de regels van een rekening of offerte."""
    uitvoer = io.StringIO()
    schrijver = csv.writer(uitvoer, delimiter=";")
    schrijver.writerow(["omschrijving", "aantal", "prijs", "soort"])
    schrijver.writerow(["Buis 32 mm", "4", "12,50", "materiaal"])
    schrijver.writerow(["Kraan vervangen", "3", "45,00", "arbeid_uur"])
    schrijver.writerow(["Voorrijkosten", "1", "25,00", "arbeid_klus"])
    return send_file(
        io.BytesIO(uitvoer.getvalue().encode("utf-8-sig")),
        mimetype="text/csv", as_attachment=True, download_name="regels-voorbeeld.csv",
    )


@app.route("/klanten/voorbeeld.csv")
def klanten_voorbeeld():
    """Een leeg bestand met de juiste kopjes, om zelf in te vullen."""
    uitvoer = io.StringIO()
    schrijver = csv.writer(uitvoer, delimiter=";")
    schrijver.writerow(["naam", "adres", "email", "telefoon", "notitie"])
    schrijver.writerow(["Voorbeeld Bedrijf BV", "Voorbeeldstraat 1\n1234 AB Voorbeeldstad",
                        "info@example.org", "0123456789", "Vaste klant"])
    return send_file(
        io.BytesIO(uitvoer.getvalue().encode("utf-8-sig")),
        mimetype="text/csv", as_attachment=True, download_name="klanten-voorbeeld.csv",
    )


@app.route("/klanten/nieuw", methods=["GET", "POST"])
def klant_nieuw():
    if request.method == "POST":
        naam, adres, email, telefoon, notitie = klant_uit_form(request.form)
        if not naam:
            melding("Vul een naam in om de klant op te slaan.", "fout")
            return redirect(url_for("klant_nieuw"))
        conn = get_db()
        cur = conn.execute(
            """INSERT INTO klanten (naam, adres, email, telefoon, notitie)
               VALUES (?, ?, ?, ?, ?)""",
            (naam, adres, email, telefoon, notitie),
        )
        conn.commit()
        klant_id = cur.lastrowid
        conn.close()
        melding(f"Klant {naam} opgeslagen.")
        return redirect(url_for("klant", klant_id=klant_id))

    return render_template("klant_form.html", klant=None, actief="klanten")


@app.route("/klant/<int:klant_id>")
def klant(klant_id):
    conn = get_db()
    gegevens = conn.execute("SELECT * FROM klanten WHERE id=?", (klant_id,)).fetchone()
    if gegevens is None:
        conn.close()
        abort(404)
    facturen = factuurlijst(conn, klant_id)
    offertes_van_klant = conn.execute(
        "SELECT * FROM offertes WHERE klant_id=? ORDER BY datum DESC, id DESC", (klant_id,)
    ).fetchall()
    conn.close()

    vandaag = nu().date().isoformat()
    onbetaald = [f for f in facturen if f["status"] != "betaald"]
    klussen_van_klant = [k for k in klussenlijst() if k["klant_id"] == klant_id]
    # Dagen van een aanvraag staan niet op de klus. Ze horen hier ook niet bij
    # het totaal, anders factureer je uren die je nergens ziet.
    te_factureren = [k for k in klussen_van_klant if k["status"] != "aangevraagd"]
    open_offertes = [o for o in offertes_van_klant
                     if o["status"] in ("concept", "verzonden") and not o["factuur_id"]]

    cijfers = {
        "aantal": len(facturen),
        "omzet": sum(f["totaal"] for f in facturen),
        # Wat er nog moet komen is het totaal min wat er al is binnengekomen; anders
        # klopt het niet zodra een klant in termijnen betaalt.
        "openstaand": sum(f["openstaand"] for f in onbetaald),
        "te_laat": sum(f["openstaand"] for f in facturen if f["verlopen"]),
        "te_laat_aantal": sum(1 for f in facturen if f["verlopen"]),
        "offertes_uit": sum(o["totaal"] for o in open_offertes),
        "offertes_aantal": len(open_offertes),
        "uren_open": round(sum(k["uren_open"] for k in te_factureren), 2),
        "uren_bedrag": round(sum(k["bedrag_open"] for k in te_factureren), 2),
        "laatste": max((f["datum"] for f in facturen), default=""),
    }
    return render_template("klant.html", klant=gegevens, facturen=facturen,
                           offertes=offertes_van_klant, klussen=klussen_van_klant,
                           cijfers=cijfers, vandaag=vandaag, actief="klanten")


@app.route("/klant/<int:klant_id>/bewerk", methods=["GET", "POST"])
def klant_bewerk(klant_id):
    conn = get_db()
    gegevens = conn.execute("SELECT * FROM klanten WHERE id=?", (klant_id,)).fetchone()
    if gegevens is None:
        conn.close()
        abort(404)

    if request.method == "POST":
        naam, adres, email, telefoon, notitie = klant_uit_form(request.form)
        if not naam:
            conn.close()
            melding("Vul een naam in om de klant op te slaan.", "fout")
            return redirect(url_for("klant_bewerk", klant_id=klant_id))
        conn.execute(
            """UPDATE klanten SET naam=?, adres=?, email=?, telefoon=?, notitie=?
               WHERE id=?""",
            (naam, adres, email, telefoon, notitie, klant_id),
        )
        conn.commit()
        conn.close()
        melding("Klantgegevens bijgewerkt.")
        return redirect(url_for("klant", klant_id=klant_id))

    conn.close()
    return render_template("klant_form.html", klant=gegevens, actief="klanten")


@app.route("/klant/<int:klant_id>/verwijder", methods=["POST"])
def klant_verwijder(klant_id):
    conn = get_db()
    gegevens = conn.execute("SELECT * FROM klanten WHERE id=?", (klant_id,)).fetchone()
    if gegevens is None:
        conn.close()
        abort(404)
    prullenbak_id = naar_prullenbak(conn, f"Klant {gegevens['naam']}", {"klanten": [gegevens]})
    # De rekeningen zelf blijven staan: daar hoort de klantnaam bij zoals hij
    # op de rekening is gedrukt. Alleen de koppeling gaat weg — ook die van klussen,
    # anders houden die een klantnummer dat nergens meer heen wijst. Haal je de klant
    # terug, dan krijgt hij zijn oude id en hangen ze er weer aan.
    conn.execute("UPDATE facturen SET klant_id=NULL WHERE klant_id=?", (klant_id,))
    conn.execute("UPDATE klussen SET klant_id=NULL WHERE klant_id=?", (klant_id,))
    conn.execute("DELETE FROM klanten WHERE id=?", (klant_id,))
    conn.commit()
    conn.close()
    melding(f"Klant {gegevens['naam']} verwijderd. De rekeningen zijn blijven staan.",
            knop=terugknop(prullenbak_id))
    return redirect(url_for("klanten"))


def klus_met_uren(conn, klus_id):
    """De klus, zijn dagen en de opgetelde uren. Geeft None als de klus niet bestaat."""
    klus = conn.execute(
        """SELECT kl.*, k.naam AS klant_naam FROM klussen kl
           LEFT JOIN klanten k ON k.id = kl.klant_id WHERE kl.id=?""",
        (klus_id,),
    ).fetchone()
    if klus is None:
        return None, [], 0.0

    dagen = []
    totaal = 0.0
    for rij in conn.execute(
        """SELECT u.*, f.nummer AS factuur_nummer FROM uren u
           LEFT JOIN facturen f ON f.id = u.factuur_id
           WHERE u.klus_id=? ORDER BY u.datum, u.van""",
        (klus_id,),
    ):
        dag = dict(rij)
        dag["lopend"] = not eindtijd_ingevuld(rij["tot"])
        # Zonder eindtijd zijn de uren er nog niet. Optellen zou een bedrag
        # maken van een dag die nog loopt.
        dag["uren"] = (
            0.0 if dag["lopend"]
            else factureerbare_uren(rij["van"], rij["tot"], _pauze(rij))
        )
        if not dag["lopend"]:
            totaal += dag["uren"]
        dagen.append(dag)
    # Een lopende dag hoort bovenaan. Op de telefoon sluit je hem anders pas
    # af nadat je langs alle oudere dagen bent gescrold.
    dagen.sort(key=lambda dag: (0 if dag["lopend"] else 1, dag["datum"], dag["van"] or ""))
    return klus, dagen, round(totaal, 2)


def klussenlijst():
    """Alle klussen met hun opgetelde uren en de periode waarin is gewerkt.

    Binnen één status blijft de volgorde staan: bij een aanvraag de langst
    wachtende bovenaan, bij lopend en afgerond de nieuwste. De pagina zet de
    drie statussen daarna zelf in groepen.
    """
    conn = get_db()
    klussen = conn.execute(
        """SELECT kl.*, k.naam AS klant_naam FROM klussen kl
           LEFT JOIN klanten k ON k.id = kl.klant_id
           ORDER BY (kl.status <> 'aangevraagd'), (kl.status = 'afgerond'),
                    -- Bij aanvragen staat de langst wachtende bovenaan; bij de rest
                    -- juist de nieuwste, want daar ben je meestal mee bezig.
                    CASE WHEN kl.status = 'aangevraagd' THEN kl.aangevraagd_op END,
                    CASE WHEN kl.status = 'aangevraagd' THEN kl.id END,
                    kl.id DESC"""
    ).fetchall()
    uren = conn.execute("SELECT * FROM uren ORDER BY datum, van").fetchall()
    conn.close()

    leeg = {"uren": 0.0, "open": 0.0, "dagen": 0, "van": None, "tot": None,
            "lopend": 0, "bezig_sinds": ""}
    per_klus = {}
    for rij in uren:
        gegevens = per_klus.setdefault(rij["klus_id"], dict(leeg))
        if eindtijd_ingevuld(rij["tot"]):
            duur = factureerbare_uren(rij["van"], rij["tot"], _pauze(rij))
            gegevens["uren"] += duur
            if rij["factuur_id"] is None:
                gegevens["open"] += duur
        else:
            # De laatste in deze volgorde is de recentste dag die nog loopt.
            gegevens["lopend"] += 1
            gegevens["bezig_sinds"] = bezig_sinds_tekst(rij["datum"], rij["van"])
        gegevens["dagen"] += 1
        if gegevens["van"] is None:
            gegevens["van"] = rij["datum"]
        gegevens["tot"] = rij["datum"]

    lijst = []
    for kl in klussen:
        gegevens = per_klus.get(kl["id"], leeg)
        rij = dict(kl)
        rij["uren"] = round(gegevens["uren"], 2)
        rij["uren_open"] = round(gegevens["open"], 2)
        rij["uren_gefactureerd"] = round(gegevens["uren"] - gegevens["open"], 2)
        rij["dagen"] = gegevens["dagen"]
        rij["eerste_datum"] = gegevens["van"]
        rij["laatste_datum"] = gegevens["tot"]
        rij["bedrag"] = round(rij["uren"] * (kl["uurtarief"] or 0), 2)
        rij["bedrag_open"] = round(rij["uren_open"] * (kl["uurtarief"] or 0), 2)
        rij["lopende_dagen"] = gegevens["lopend"]
        rij["bezig_sinds"] = gegevens["bezig_sinds"]
        # De dagen zelf blijven meetellen in rij["dagen"], voor de vraag bij
        # terugzetten. Als open uren zouden ze overal waar die som gebruikt
        # wordt tóch te factureren zijn.
        if rij["status"] == "aangevraagd":
            rij["uren_open"] = 0.0
            rij["bedrag_open"] = 0.0
        lijst.append(rij)
    return lijst


def csv_gewerkte_dagen(conn):
    """CSV van de dagen die meetellen, met puntkomma zoals de andere exports.

    Een lopende dag heeft nog geen uren. Die hoort niet in een export of in een
    jaaroverzicht, anders reken je tijd die er nog niet is."""
    uitvoer = io.StringIO()
    schrijver = csv.writer(uitvoer, delimiter=";")
    schrijver.writerow(["datum", "van", "tot", "uren", "klus", "notitie"])
    rijen = conn.execute(
        """SELECT u.datum, u.van, u.tot, u.notitie, k.naam AS klus
           FROM uren u JOIN klussen k ON k.id = u.klus_id
           WHERE u.tot IS NOT NULL AND TRIM(u.tot) <> ''
           ORDER BY u.datum, u.van, u.id"""
    )
    for rij in rijen:
        schrijver.writerow([
            rij["datum"], rij["van"], rij["tot"],
            filter_uren(factureerbare_uren(rij["van"], rij["tot"], _pauze(rij))),
            rij["klus"], rij["notitie"] or "",
        ])
    return uitvoer.getvalue()


@app.route("/klussen/gewerkte-dagen.csv")
def gewerkte_dagen_csv():
    """Download van de afgesloten dagen, naast de andere CSV-knoppen.

    Een lopende dag hoort hier niet in: die uren zijn er nog niet, en een
    boekhouder zou ze anders meetellen."""
    conn = get_db()
    tekst = csv_gewerkte_dagen(conn)
    conn.close()
    return send_file(
        io.BytesIO(tekst.encode("utf-8-sig")),
        mimetype="text/csv",
        as_attachment=True,
        download_name="gewerkte-dagen.csv",
    )


def klus_uit_form(form):
    klant = (form.get("klant_id") or "").strip()
    try:
        tarief = float((form.get("uurtarief") or "0").replace(",", "."))
    except ValueError:
        tarief = 0.0
    return (
        form.get("naam", "").strip(),
        int(klant) if klant.isdigit() else None,
        tarief,
        form.get("notitie", "").strip(),
    )


@app.route("/klussen")
def klussen():
    lijst = klussenlijst()
    # Drie groepen, in deze volgorde: wat nu loopt, wat nog moet beginnen, en
    # wat af is. Een klus die je terugzette is weer status aangevraagd en hoort
    # dus bij de aanvragen. De volgorde binnen een groep komt uit klussenlijst().
    lopend = [k for k in lijst if k["status"] == "open"]
    aanvragen = [k for k in lijst if k["status"] == "aangevraagd"]
    afgerond = [k for k in lijst if k["status"] == "afgerond"]
    # Uren van een aanvraag tellen nergens mee. Die van een afgeronde klus nog
    # wel, zolang ze niet op een rekening staan.
    meetellend = lopend + afgerond
    overzicht = {
        "open_uren": round(sum(k["uren_open"] for k in meetellend), 2),
        "open_bedrag": round(sum(k["bedrag_open"] for k in meetellend), 2),
        "lopend": len(lopend),
        "aangevraagd": len(aanvragen),
    }
    return render_template(
        "klussen.html", lopend=lopend, aanvragen=aanvragen, afgerond=afgerond,
        overzicht=overzicht, fases=KLUS_FASES, actief="klussen",
    )


@app.route("/klussen/nieuw", methods=["GET", "POST"])
def klus_nieuw():
    if request.method == "POST":
        naam, klant_id, uurtarief, notitie = klus_uit_form(request.form)
        if not naam:
            melding("Geef de klus een naam om hem op te slaan.", "fout")
            return redirect(url_for("klus_nieuw"))
        vandaag = nu().date().isoformat()
        # Een klus die nog moet beginnen is een aanvraag: iemand heeft gebeld, er is
        # nog geen intake en er zijn nog geen uren.
        aanvraag = request.form.get("aanvraag") == "ja"
        conn = get_db()
        cur = conn.execute(
            """INSERT INTO klussen (naam, klant_id, uurtarief, notitie, status,
               gestart, aangevraagd_op) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (naam, klant_id, uurtarief, notitie,
             "aangevraagd" if aanvraag else "open", vandaag, vandaag),
        )
        conn.commit()
        klus_id = cur.lastrowid
        conn.close()
        if aanvraag:
            melding(f"Aanvraag {naam} staat erbij.")
        else:
            melding(f"Klus {naam} aangemaakt. Zet hieronder je eerste dag erbij.")
        return redirect(url_for("klus", klus_id=klus_id))

    # Vanuit een klantpagina kun je meteen een klus voor die klant beginnen.
    gekozen = None
    vooraf = (request.args.get("klant") or "").strip()
    if vooraf.isdigit():
        conn = get_db()
        rij = conn.execute("SELECT id FROM klanten WHERE id=?", (int(vooraf),)).fetchone()
        conn.close()
        gekozen = rij["id"] if rij else None

    return render_template("klus_form.html", klus=None, klanten=klantenlijst(),
                           actief="klussen", gekozen_klant_id=gekozen)


@app.route("/klus/<int:klus_id>")
def klus(klus_id):
    conn = get_db()
    gegevens, dagen, totaal = klus_met_uren(conn, klus_id)
    if gegevens is None:
        conn.close()
        abort(404)
    aankopen = inkopen_van(conn, klus_id)
    aantekeningen = notities_van(conn, klus_id)
    taken_gedaan = sum(n["taken_gedaan"] for n in aantekeningen)
    taken_totaal = sum(n["taken_totaal"] for n in aantekeningen)
    offerte = None
    if gegevens["offerte_id"]:
        offerte = conn.execute("SELECT * FROM offertes WHERE id=?",
                               (gegevens["offerte_id"],)).fetchone()
    conn.close()

    open_uren = round(sum(
        d["uren"] for d in dagen if d["factuur_id"] is None and not d["lopend"]
    ), 2)
    return render_template(
        "klus.html", klus=gegevens, dagen=dagen, totaal=totaal, open_uren=open_uren,
        nu_tijd=nu().strftime("%H:%M"),
        inkopen=aankopen, notities=aantekeningen, offerte=offerte, fases=KLUS_FASES,
        taken_voortgang=voortgang_zin(taken_gedaan, taken_totaal),
        taken_gedaan=taken_gedaan, taken_totaal=taken_totaal,
        taak_max_tekens=TAAK_MAX_TEKENS,
        offerte_status=OFFERTE_STATUS,
        bedrag=round(totaal * (gegevens["uurtarief"] or 0), 2),
        bonnen_bedrag=bonnen_totaal(aankopen),
        # Voor de vraag bij "weer als aanvraag": dagen op een rekening blijven
        # daaraan hangen, maar het urenblok verdwijnt zolang het een aanvraag is.
        dagen_op_rekening=any(d["factuur_id"] for d in dagen),
        vandaag=vandaag().isoformat(), actief="klussen",
    )


@app.route("/klus/<int:klus_id>/bewerk", methods=["GET", "POST"])
def klus_bewerk(klus_id):
    conn = get_db()
    gegevens = conn.execute("SELECT * FROM klussen WHERE id=?", (klus_id,)).fetchone()
    if gegevens is None:
        conn.close()
        abort(404)

    if request.method == "POST":
        naam, klant_id, uurtarief, notitie = klus_uit_form(request.form)
        if not naam:
            conn.close()
            melding("Geef de klus een naam om hem op te slaan.", "fout")
            return redirect(url_for("klus_bewerk", klus_id=klus_id))
        conn.execute(
            "UPDATE klussen SET naam=?, klant_id=?, uurtarief=?, notitie=? WHERE id=?",
            (naam, klant_id, uurtarief, notitie, klus_id),
        )
        conn.commit()
        conn.close()
        melding("Klus bijgewerkt.")
        return redirect(url_for("klus", klus_id=klus_id))

    conn.close()
    return render_template("klus_form.html", klus=gegevens, klanten=klantenlijst(),
                           actief="klussen")


@app.route("/klus/<int:klus_id>/status", methods=["POST"])
def klus_status(klus_id):
    """Zet de klus in een andere fase: aangevraagd, lopend of afgerond."""
    conn = get_db()
    gegevens = conn.execute("SELECT * FROM klussen WHERE id=?", (klus_id,)).fetchone()
    if gegevens is None:
        conn.close()
        abort(404)

    gevraagd = request.form.get("naar", "")
    if gevraagd in KLUS_FASES:
        nieuw_status = gevraagd
    else:
        # Zonder opgave: de knop wisselt tussen lopend en afgerond, zoals eerst.
        nieuw_status = "open" if gegevens["status"] == "afgerond" else "afgerond"

    # Afgerond eerst heropenen. Anders sla je die stap over met een los verzoek
    # en verdwijnen de dagen uit beeld terwijl de klus nog af leek.
    if nieuw_status == "aangevraagd" and gegevens["status"] == "afgerond":
        conn.close()
        melding("Heropen de klus eerst. Daarna kun je hem weer een aanvraag maken.",
                "fout")
        return redirect(terug_naar(url_for("klus", klus_id=klus_id)))

    velden, waarden = ["status=?"], [nieuw_status]
    # Bij het echt beginnen telt vanaf vandaag, tenzij deze klus al eens liep:
    # dan hoort de startdatum bij de dagen die er al staan, niet bij vandaag.
    # De aanvraagdatum blijft staan zodat je ziet hoe lang het heeft geduurd.
    if nieuw_status == "open" and gegevens["status"] == "aangevraagd":
        bewaard = (gegevens["gestart_was"] or "").strip()
        velden.append("gestart=?")
        waarden.append(bewaard or vandaag().isoformat())
        if bewaard:
            velden.append("gestart_was=?")
            waarden.append("")
    # Terug naar een aanvraag vanaf lopend. De startdatum zelf gaat terug naar
    # de aanvraagdag; de dag waarop het werk begon blijft in gestart_was, zodat
    # opnieuw starten die niet vervangt door vandaag.
    if nieuw_status == "aangevraagd" and gegevens["status"] == "open":
        if gegevens["gestart"]:
            velden.append("gestart_was=?")
            waarden.append(gegevens["gestart"])
        if gegevens["aangevraagd_op"]:
            velden.append("gestart=?")
            waarden.append(gegevens["aangevraagd_op"])
    # Niets weggooien. Dagen, notities en bonnen blijven in de database; het
    # urenblok is op een aanvraag alleen even niet in beeld.
    aantal_dagen = 0
    if nieuw_status == "aangevraagd" and gegevens["status"] != "aangevraagd":
        aantal_dagen = conn.execute(
            "SELECT COUNT(*) FROM uren WHERE klus_id=?", (klus_id,)
        ).fetchone()[0]
    conn.execute(f"UPDATE klussen SET {', '.join(velden)} WHERE id=?",
                 waarden + [klus_id])
    conn.commit()
    conn.close()
    tekst = KLUS_FASES[nieuw_status]["melding"]
    if aantal_dagen:
        tekst += " De gewerkte dagen blijven bewaard."
    melding(tekst)
    return redirect(terug_naar(url_for("klussen")))


@app.route("/klus/<int:klus_id>/intake", methods=["POST"])
def klus_intake(klus_id):
    """Vinkt de intake af of haalt hem weer weg."""
    conn = get_db()
    gegevens = conn.execute("SELECT intake_op FROM klussen WHERE id=?", (klus_id,)).fetchone()
    if gegevens is None:
        conn.close()
        abort(404)
    weg = bool(gegevens["intake_op"])
    conn.execute("UPDATE klussen SET intake_op=? WHERE id=?",
                 ("" if weg else vandaag().isoformat(), klus_id))
    conn.commit()
    conn.close()
    melding("Intake weer opengezet." if weg else "Intake afgevinkt.")
    return redirect(terug_naar(url_for("klus", klus_id=klus_id)))


@app.route("/klus/<int:klus_id>/verwijder", methods=["POST"])
def klus_verwijder(klus_id):
    conn = get_db()
    gegevens = conn.execute("SELECT * FROM klussen WHERE id=?", (klus_id,)).fetchone()
    if gegevens is None:
        conn.close()
        abort(404)
    rekeningen = conn.execute(
        """SELECT DISTINCT f.id, f.nummer, f.klant_naam, f.datum
           FROM uren u JOIN facturen f ON f.id = u.factuur_id
           WHERE u.klus_id=? AND u.factuur_id IS NOT NULL
           ORDER BY f.id""",
        (klus_id,),
    ).fetchall()
    if rekeningen:
        titels = [zin_van_rekening(rij) for rij in rekeningen]
        if len(titels) == 1:
            waar = titels[0]
        elif len(titels) == 2:
            waar = f"{titels[0]} en {titels[1]}"
        else:
            waar = ", ".join(titels[:-1]) + " en " + titels[-1]
        links = [
            {
                "url": url_for("bewerk", factuur_id=rij["id"]),
                "label": "Naar de rekening" if len(rekeningen) == 1
                else f"Naar {zin_van_rekening(rij)}",
            }
            for rij in rekeningen
        ]
        conn.close()
        melding(
            f"Deze klus kan niet weg. Er staan dagen op {waar}. "
            "Haal die eerst van de rekening, of verwijder de rekening.",
            "fout",
            links=links,
        )
        return redirect(url_for("klus", klus_id=klus_id))

    bijlagen = conn.execute("SELECT * FROM bijlagen WHERE klus_id=?", (klus_id,)).fetchall()
    inkopen = conn.execute("SELECT * FROM inkopen WHERE klus_id=?", (klus_id,)).fetchall()
    materialen = []
    for inkoop in inkopen:
        materialen.extend(conn.execute(
            "SELECT * FROM inkoop_materialen WHERE inkoop_id=?", (inkoop["id"],)
        ).fetchall())
    uren = conn.execute("SELECT * FROM uren WHERE klus_id=?", (klus_id,)).fetchall()
    # Welke regel naar welke dag wees. Verwijderen zet uur_id op NULL; bij
    # terughalen moet die koppeling terug, anders lopen ze uit elkaar.
    uur_regels = [
        {"regel_id": rij["id"], "uur_id": rij["uur_id"]}
        for rij in conn.execute(
            """SELECT r.id, r.uur_id FROM regels r
               JOIN uren u ON u.id = r.uur_id
               WHERE u.klus_id=?""",
            (klus_id,),
        )
    ]
    # Zelfde reden als bij de dagen: verwijderen zet inkoop_id op NULL.
    inkoop_regels = [
        {"regel_id": rij["id"], "inkoop_id": rij["inkoop_id"]}
        for rij in conn.execute(
            """SELECT r.id, r.inkoop_id FROM regels r
               JOIN inkopen i ON i.id = r.inkoop_id
               WHERE i.klus_id=?""",
            (klus_id,),
        )
    ]
    notitie_rijen = conn.execute(
        "SELECT * FROM notities WHERE klus_id=?", (klus_id,)
    ).fetchall()
    taak_rijen = conn.execute(
        """SELECT t.* FROM taken t
           JOIN notities n ON n.id = t.notitie_id
           WHERE n.klus_id=?""",
        (klus_id,),
    ).fetchall()
    prullenbak_id = naar_prullenbak(
        conn, f"Klus {gegevens['naam']}",
        {"klussen": [gegevens],
         "uren": uren,
         "notities": notitie_rijen,
         "taken": taak_rijen,
         "inkopen": inkopen,
         "inkoop_materialen": materialen,
         "bijlagen": bijlagen},
        bestanden=[b["bestand"] for b in bijlagen],
        koppelingen={"uur_regels": uur_regels, "inkoop_regels": inkoop_regels},
    )
    for inkoop in inkopen:
        conn.execute("DELETE FROM inkoop_materialen WHERE inkoop_id=?", (inkoop["id"],))
    conn.execute(
        """DELETE FROM taken WHERE notitie_id IN
           (SELECT id FROM notities WHERE klus_id=?)""",
        (klus_id,),
    )
    conn.execute("DELETE FROM notities WHERE klus_id=?", (klus_id,))
    conn.execute("DELETE FROM bijlagen WHERE klus_id=?", (klus_id,))
    conn.execute("DELETE FROM inkopen WHERE klus_id=?", (klus_id,))
    conn.execute("DELETE FROM uren WHERE klus_id=?", (klus_id,))
    conn.execute("DELETE FROM klussen WHERE id=?", (klus_id,))
    conn.commit()
    conn.close()

    melding(f"Klus {gegevens['naam']} en de bijbehorende uren zijn verwijderd.",
            knop=terugknop(prullenbak_id))
    return redirect(url_for("klussen"))


def bijlagen_van(conn, klus_id, notitie_id=None, inkoop_id=None):
    """Bestanden bij een klus, gefilterd op notitie of inkoop.

    Zonder filter krijg je niets terug: bonnen hangen aan een inkoop, notitiefoto's
    aan een notitie. Zo blijven die twee werelden uit elkaar."""
    lijst = []
    for rij in conn.execute(
        "SELECT * FROM bijlagen WHERE klus_id=? ORDER BY id", (klus_id,)
    ):
        bijlage = dict(rij)
        if notitie_id is not None:
            if (bijlage.get("notitie_id") or None) != notitie_id:
                continue
        elif inkoop_id is not None:
            if (bijlage.get("inkoop_id") or None) != inkoop_id:
                continue
        else:
            continue
        ext = os.path.splitext(rij["bestand"] or "")[1].lower()
        bijlage["plaatje"] = ext in BIJLAGE_PLAATJES
        # HEIC is ook een foto: op een iPhone opent hij in de lichtbak. Een
        # browser op de computer tekent hem niet als miniatuur, dat blijft een merk.
        if bijlage["plaatje"] or ext in {".heic", ".heif"}:
            bijlage["soort"] = "foto"
        elif ext == ".pdf":
            bijlage["soort"] = "pdf"
        else:
            bijlage["soort"] = "bestand"
        # De opgeslagen naam kan een bidi-teken bevatten. Op de pagina (de
        # titel, data-naam en het aria-label van de lichtbak) draait dat de
        # naam om. Alleen de weergave wordt schoon; het bestand blijft staan.
        bijlage["naam"] = zonder_bidi(bijlage.get("naam") or "") or "bijlage"
        lijst.append(bijlage)
    return lijst


def inkopen_van(conn, klus_id):
    """De inkopen (bonnen) bij een klus, met bestanden en optionele materialen."""
    lijst = []
    for rij in conn.execute(
        "SELECT * FROM inkopen WHERE klus_id=? ORDER BY toegevoegd DESC, id DESC",
        (klus_id,),
    ):
        inkoop = dict(rij)
        inkoop["bestanden"] = bijlagen_van(conn, klus_id, inkoop_id=rij["id"])
        inkoop["materialen"] = [
            dict(m) for m in conn.execute(
                """SELECT * FROM inkoop_materialen WHERE inkoop_id=?
                   ORDER BY id""",
                (rij["id"],),
            )
        ]
        lijst.append(inkoop)
    return lijst


def bonnen_totaal(inkopen):
    """Som van de aankoopbedragen bij een klus.

    Dat is wat je zelf betaalt en via de rekening terugkrijgt. Alleen
    inkopen.bedrag telt: de materiaalregels eronder zijn een notitie, geen
    tweede bedrag, en een bon die al op een rekening staat hoort er nog bij.
    """
    return round(sum(float(rij["bedrag"] or 0) for rij in inkopen), 2)


def voortgang_zin(gedaan, totaal):
    """'3 van 7 klaar', of niets als er geen taken zijn."""
    if not totaal:
        return ""
    return f"{int(gedaan)} van {int(totaal)} klaar"


def taken_van(conn, notitie_id):
    return [
        dict(rij) for rij in conn.execute(
            "SELECT * FROM taken WHERE notitie_id=? ORDER BY volgorde, id",
            (notitie_id,),
        )
    ]


def _hang_taken_aan(notitie, conn):
    taken = taken_van(conn, notitie["id"])
    gedaan = sum(1 for taak in taken if taak["klaar"])
    notitie["taken"] = taken
    notitie["taken_gedaan"] = gedaan
    notitie["taken_totaal"] = len(taken)
    notitie["voortgang"] = voortgang_zin(gedaan, len(taken))
    return notitie


def notities_van(conn, klus_id):
    """De notities bij een klus, de nieuwste bovenaan, elk met zijn eigen foto's."""
    lijst = []
    for rij in conn.execute(
        "SELECT * FROM notities WHERE klus_id=? ORDER BY wanneer DESC, id DESC", (klus_id,)
    ):
        notitie = dict(rij)
        notitie["fotos"] = bijlagen_van(conn, klus_id, notitie_id=rij["id"])
        _hang_taken_aan(notitie, conn)
        lijst.append(notitie)
    return lijst


def _voortgang_notitie(conn, notitie_id):
    rij = conn.execute(
        """SELECT COUNT(*) AS totaal, COALESCE(SUM(klaar), 0) AS gedaan
           FROM taken WHERE notitie_id=?""",
        (notitie_id,),
    ).fetchone()
    return int(rij["gedaan"] or 0), int(rij["totaal"] or 0)


def _voortgang_klus(conn, klus_id):
    rij = conn.execute(
        """SELECT COUNT(*) AS totaal, COALESCE(SUM(t.klaar), 0) AS gedaan
           FROM taken t
           JOIN notities n ON n.id = t.notitie_id
           WHERE n.klus_id=?""",
        (klus_id,),
    ).fetchone()
    return int(rij["gedaan"] or 0), int(rij["totaal"] or 0)


def _sqlite_teller(conn, naam):
    try:
        rij = conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name=?", (naam,)
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(rij[0]) if rij else 0


def _hoogste_id_in_prullenbak(conn, tabel):
    """Het hoogste id van deze tabel dat nog in de prullenbak staat."""
    hoogste = 0
    try:
        rijen = conn.execute("SELECT inhoud FROM prullenbak").fetchall()
    except sqlite3.OperationalError:
        return 0
    for (inhoud,) in rijen:
        try:
            data = json.loads(inhoud)
        except (TypeError, json.JSONDecodeError):
            continue
        for regel in (data.get("rijen") or {}).get(tabel) or []:
            try:
                hoogste = max(hoogste, int(regel.get("id") or 0))
            except (TypeError, ValueError, AttributeError):
                continue
    return hoogste


def _zet_teller_minstens(conn, naam, minstens):
    """De volgende rij krijgt geen id dat al eens bestaan heeft.

    Een nieuwe tabel na een herbouw telt anders vanaf de hoogste rij die
    meegekopieerd is. Een taak die intussen in de prullenbak zit kan dat
    nummer weer krijgen, en terugzetten slaat hem dan stil over.
    """
    if minstens <= 0:
        return
    try:
        if _sqlite_teller(conn, naam) >= minstens:
            return
        conn.execute("DELETE FROM sqlite_sequence WHERE name=?", (naam,))
        conn.execute(
            "INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)",
            (naam, minstens),
        )
    except sqlite3.OperationalError:
        pass


def _taken_cascade(conn):
    """Zorg dat een notitie zijn taken meeneemt, ook zonder onze DELETE."""
    bestaat = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='taken'"
    ).fetchone()
    if not bestaat:
        return
    # Vóór de herbouw. Daarna staat de teller op de gekopieerde rijen,
    # niet meer op een id dat alleen nog in de prullenbak zit.
    ondergrens = max(
        _sqlite_teller(conn, "taken"),
        int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM taken").fetchone()[0] or 0),
        _hoogste_id_in_prullenbak(conn, "taken"),
    )
    wijzen = conn.execute("PRAGMA foreign_key_list(taken)").fetchall()
    if not (wijzen and all((rij["on_delete"] or "").upper() == "CASCADE" for rij in wijzen)):
        conn.execute("ALTER TABLE taken RENAME TO taken_oud")
        conn.execute(
            """CREATE TABLE taken (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                notitie_id INTEGER NOT NULL,
                tekst TEXT NOT NULL,
                klaar INTEGER NOT NULL DEFAULT 0,
                volgorde INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (notitie_id) REFERENCES notities (id) ON DELETE CASCADE
            )"""
        )
        conn.execute(
            """INSERT INTO taken (id, notitie_id, tekst, klaar, volgorde)
               SELECT id, notitie_id, tekst, klaar, volgorde FROM taken_oud"""
        )
        conn.execute("DROP TABLE taken_oud")
    _zet_teller_minstens(conn, "taken", ondergrens)


# Een taak is een regel, geen opstel. Meer dan dit is een vergissing of een
# plakactie die de pagina oprekt.
TAAK_MAX_TEKENS = 500
TAAK_MAX_AANTAL = 200


def _taak_tekst_schoon(ruw):
    """Een taak is één regel. Een geplakt regeleinde wordt een spatie."""
    return " ".join((ruw or "").replace("\r", "\n").replace("\n", " ").split())


def _json_taak(gegevens, status=200):
    antwoord = make_response(json.dumps(gegevens), status)
    antwoord.mimetype = "application/json"
    antwoord.headers["Cache-Control"] = "no-store"
    antwoord.headers["X-Content-Type-Options"] = "nosniff"
    return antwoord


def _taak_weigering(tekst):
    if "application/json" in (request.headers.get("Accept") or ""):
        return _json_taak({"fout": tekst}, 400)
    return None


def _taken_antwoord(conn, notitie, extra=None):
    """JSON voor een tik, of een gewone terugkeer als de pagina geen script heeft."""
    if "application/json" not in (request.headers.get("Accept") or ""):
        return None
    gedaan, totaal = _voortgang_notitie(conn, notitie["id"])
    klus_gedaan, klus_totaal = _voortgang_klus(conn, notitie["klus_id"])
    gegevens = {
        "gedaan": gedaan,
        "totaal": totaal,
        "voortgang": voortgang_zin(gedaan, totaal),
        "klus_gedaan": klus_gedaan,
        "klus_totaal": klus_totaal,
        "klus_voortgang": voortgang_zin(klus_gedaan, klus_totaal),
    }
    if extra:
        gegevens.update(extra)
    return _json_taak(gegevens)


def _terug_naar_taken(notitie):
    return redirect(
        url_for("klus", klus_id=notitie["klus_id"], taak=notitie["id"])
        + f"#taken-{notitie['id']}"
    )


def _notitie_of_404(conn, notitie_id):
    rij = conn.execute("SELECT * FROM notities WHERE id=?", (notitie_id,)).fetchone()
    if rij is None:
        conn.close()
        abort(404)
    return dict(rij)


def _taak_of_404(conn, notitie_id, taak_id):
    notitie = _notitie_of_404(conn, notitie_id)
    taak = conn.execute(
        "SELECT * FROM taken WHERE id=? AND notitie_id=?", (taak_id, notitie_id)
    ).fetchone()
    if taak is None:
        conn.close()
        abort(404)
    return notitie, dict(taak)


def bewaar_bestanden(conn, klus_id, bestanden, notitie_id=None, inkoop_id=None):
    """Zet gekozen foto's en PDF's op schijf en in de database.

    Geeft terug hoeveel er bij kwamen en welke er niet door de keuring kwamen."""
    erbij, geweigerd = 0, []
    for bestand in bestanden:
        if not bestand or not bestand.filename:
            continue
        veilig = secure_filename(bestand.filename) or "bijlage"
        extensie = os.path.splitext(veilig)[1].lower()
        if extensie not in BIJLAGE_TYPES:
            geweigerd.append(bestand.filename)
            continue

        # Een willekeurig voorvoegsel, zodat twee keer "IMG_0001.jpg" elkaar niet
        # overschrijft en niemand een pad kan raden.
        opslagnaam = f"{secrets.token_hex(8)}{extensie}"
        bestand.save(os.path.join(BIJLAGE_DIR, opslagnaam))
        conn.execute(
            """INSERT INTO bijlagen (klus_id, bestand, naam, toegevoegd, meesturen,
               notitie_id, inkoop_id) VALUES (?, ?, ?, ?, 0, ?, ?)""",
            (klus_id, opslagnaam, veilig, vandaag().isoformat(),
             notitie_id, inkoop_id),
        )
        erbij += 1
        if extensie in {".heic", ".heif"}:
            # De browser en reportlab tekenen HEIC niet. Het bestand blijft staan
            # zodat je het niet kwijt bent, maar zonder deze zin lijkt de preview stuk.
            melding("Dit is een HEIC-foto van een iPhone. Hij is opgeslagen, maar een "
                    "voorbeeld of het logo lukt alleen met JPG of PNG. Op de iPhone: "
                    "Instellingen → Camera → Formaten → Meest compatibel.", "fout")
    return erbij, geweigerd


@app.route("/klus/<int:klus_id>/notitie", methods=["POST"])
def notitie_erbij(klus_id):
    """Een aantekening bij een klus, met eventueel foto's erbij.

    Foto's bij een notitie horen bij het verhaal — hoe het eruitzag, wat er is
    afgesproken. De bonnetjes verderop op de pagina zijn iets anders: die kunnen
    met de rekening mee naar de klant."""
    conn = get_db()
    if conn.execute("SELECT id FROM klussen WHERE id=?", (klus_id,)).fetchone() is None:
        conn.close()
        abort(404)

    tekst = (request.form.get("tekst") or "").strip()
    fotos = [b for b in request.files.getlist("foto") if b and b.filename]
    if not tekst and not fotos:
        conn.close()
        melding("Schrijf iets op of kies een foto.", "fout")
        return redirect(url_for("klus", klus_id=klus_id))

    wanneer = geldige_datum(request.form.get("wanneer"), vandaag().isoformat())
    notitie_id = conn.execute(
        "INSERT INTO notities (klus_id, wanneer, tekst) VALUES (?, ?, ?)",
        (klus_id, wanneer, tekst),
    ).lastrowid
    erbij, geweigerd = bewaar_bestanden(conn, klus_id, fotos, notitie_id=notitie_id)
    conn.commit()
    conn.close()

    hoeveel = "1 foto" if erbij == 1 else f"{erbij} foto's"
    melding("Notitie bewaard." if not erbij else f"Notitie bewaard, met {hoeveel}.")
    if geweigerd:
        melding(f"Niet toegevoegd: {', '.join(geweigerd)}. Kies een foto (JPG, PNG, HEIC) "
                "of een PDF.", "fout")
    return redirect(url_for("klus", klus_id=klus_id))


@app.route("/notitie/<int:notitie_id>/foto", methods=["POST"])
def notitie_foto(notitie_id):
    """Er later nog een foto bij zetten."""
    conn = get_db()
    rij = conn.execute("SELECT * FROM notities WHERE id=?", (notitie_id,)).fetchone()
    if rij is None:
        conn.close()
        abort(404)
    erbij, geweigerd = bewaar_bestanden(conn, rij["klus_id"],
                                        request.files.getlist("foto"), notitie_id=notitie_id)
    conn.commit()
    conn.close()

    if erbij:
        hoeveel = "1 foto" if erbij == 1 else f"{erbij} foto's"
        melding(f"{hoeveel} bij de notitie gezet.")
    if geweigerd:
        melding(f"Niet toegevoegd: {', '.join(geweigerd)}. Kies een foto (JPG, PNG, HEIC) "
                "of een PDF.", "fout")
    if not erbij and not geweigerd:
        melding("Er was geen bestand gekozen.", "fout")
    return redirect(url_for("klus", klus_id=rij["klus_id"]))


@app.route("/notitie/<int:notitie_id>/bewerk", methods=["POST"])
def notitie_bewerk(notitie_id):
    """De tekst van een notitie aanpassen.

    Je schrijft ter plekke iets op en scherpt het later aan; dat hoort niet te
    betekenen dat je hem moet weggooien en de foto's opnieuw moet kiezen."""
    conn = get_db()
    rij = conn.execute("SELECT * FROM notities WHERE id=?", (notitie_id,)).fetchone()
    if rij is None:
        conn.close()
        abort(404)

    tekst = (request.form.get("tekst") or "").strip()
    heeft_fotos = conn.execute(
        "SELECT COUNT(*) FROM bijlagen WHERE notitie_id=?", (notitie_id,)).fetchone()[0]
    heeft_taken = conn.execute(
        "SELECT COUNT(*) FROM taken WHERE notitie_id=?", (notitie_id,)).fetchone()[0]
    if not tekst and not heeft_fotos and not heeft_taken:
        # Anders houd je een notitie over waar niets meer in staat.
        conn.close()
        melding("Laat de tekst niet leeg; verwijder de notitie als hij weg mag.", "fout")
        return redirect(url_for("klus", klus_id=rij["klus_id"]))

    conn.execute("UPDATE notities SET tekst=? WHERE id=?", (tekst, notitie_id))
    conn.commit()
    conn.close()
    melding("Notitie aangepast.")
    return redirect(url_for("klus", klus_id=rij["klus_id"]))


@app.route("/notitie/<int:notitie_id>/verwijder", methods=["POST"])
def notitie_verwijder(notitie_id):
    """De notitie en de foto's die eraan hangen gaan samen naar de prullenbak."""
    conn = get_db()
    rij = conn.execute("SELECT * FROM notities WHERE id=?", (notitie_id,)).fetchone()
    if rij is None:
        conn.close()
        abort(404)
    fotos = conn.execute("SELECT * FROM bijlagen WHERE notitie_id=?", (notitie_id,)).fetchall()
    taken = conn.execute("SELECT * FROM taken WHERE notitie_id=?", (notitie_id,)).fetchall()
    # Eerst de notitie, dan de taken: terugzetten volgt die volgorde, en een
    # taak verwijst naar de notitie.
    prullenbak_id = naar_prullenbak(
        conn, "Notitie bij de klus",
        {"notities": [rij], "taken": taken, "bijlagen": fotos},
        bestanden=[f["bestand"] for f in fotos],
    )
    conn.execute("DELETE FROM taken WHERE notitie_id=?", (notitie_id,))
    conn.execute("DELETE FROM bijlagen WHERE notitie_id=?", (notitie_id,))
    conn.execute("DELETE FROM notities WHERE id=?", (notitie_id,))
    conn.commit()
    conn.close()
    melding("Notitie verwijderd.", knop=terugknop(prullenbak_id))
    return redirect(url_for("klus", klus_id=rij["klus_id"]))


def _zet_volgorde(conn, notitie_id, ids):
    for plek, taak_id in enumerate(ids):
        conn.execute(
            "UPDATE taken SET volgorde=? WHERE id=? AND notitie_id=?",
            (plek, taak_id, notitie_id),
        )


@app.route("/notitie/<int:notitie_id>/taak", methods=["POST"])
def taak_erbij(notitie_id):
    """Een regel onderaan de lijst. Enter voegt hem toe en laat het veld leeg."""
    conn = get_db()
    notitie = _notitie_of_404(conn, notitie_id)
    tekst = _taak_tekst_schoon(request.form.get("tekst"))
    if not tekst:
        conn.close()
        geweigerd = _taak_weigering("Schrijf eerst wat er gedaan moet worden.")
        if geweigerd is not None:
            return geweigerd
        melding("Schrijf eerst wat er gedaan moet worden.", "fout")
        return _terug_naar_taken(notitie)
    if len(tekst) > TAAK_MAX_TEKENS:
        conn.close()
        zin = f"Een taak mag hooguit {TAAK_MAX_TEKENS} tekens zijn."
        geweigerd = _taak_weigering(zin)
        if geweigerd is not None:
            return geweigerd
        melding(zin, "fout")
        return _terug_naar_taken(notitie)
    aantal = conn.execute(
        "SELECT COUNT(*) FROM taken WHERE notitie_id=?", (notitie_id,)
    ).fetchone()[0]
    if aantal >= TAAK_MAX_AANTAL:
        conn.close()
        zin = f"Een notitie mag hooguit {TAAK_MAX_AANTAL} taken hebben."
        geweigerd = _taak_weigering(zin)
        if geweigerd is not None:
            return geweigerd
        melding(zin, "fout")
        return _terug_naar_taken(notitie)
    hoogste = conn.execute(
        "SELECT COALESCE(MAX(volgorde), -1) FROM taken WHERE notitie_id=?",
        (notitie_id,),
    ).fetchone()[0]
    taak_id = conn.execute(
        "INSERT INTO taken (notitie_id, tekst, klaar, volgorde) VALUES (?, ?, 0, ?)",
        (notitie_id, tekst, int(hoogste) + 1),
    ).lastrowid
    conn.commit()
    json_antwoord = _taken_antwoord(conn, notitie, {
        "id": taak_id, "tekst": tekst, "klaar": 0,
    })
    conn.close()
    if json_antwoord is not None:
        return json_antwoord
    return _terug_naar_taken(notitie)


@app.route("/notitie/<int:notitie_id>/taak/<int:taak_id>/vink", methods=["POST"])
def taak_vink(notitie_id, taak_id):
    """Eén tik zet de taak op klaar of weer open. De regel blijft staan."""
    conn = get_db()
    notitie, taak = _taak_of_404(conn, notitie_id, taak_id)
    klaar = 0 if taak["klaar"] else 1
    conn.execute("UPDATE taken SET klaar=? WHERE id=?", (klaar, taak_id))
    conn.commit()
    json_antwoord = _taken_antwoord(conn, notitie, {"id": taak_id, "klaar": klaar})
    conn.close()
    if json_antwoord is not None:
        return json_antwoord
    return _terug_naar_taken(notitie)


@app.route("/notitie/<int:notitie_id>/taak/<int:taak_id>/tekst", methods=["POST"])
def taak_tekst(notitie_id, taak_id):
    conn = get_db()
    notitie, taak = _taak_of_404(conn, notitie_id, taak_id)
    tekst = _taak_tekst_schoon(request.form.get("tekst"))
    if not tekst:
        conn.close()
        geweigerd = _taak_weigering(
            "Een taak heeft tekst nodig. Verwijder hem als hij weg mag."
        )
        if geweigerd is not None:
            return geweigerd
        melding("Een taak heeft tekst nodig. Verwijder hem als hij weg mag.", "fout")
        return _terug_naar_taken(notitie)
    if len(tekst) > TAAK_MAX_TEKENS:
        conn.close()
        zin = f"Een taak mag hooguit {TAAK_MAX_TEKENS} tekens zijn."
        geweigerd = _taak_weigering(zin)
        if geweigerd is not None:
            return geweigerd
        melding(zin, "fout")
        return _terug_naar_taken(notitie)
    conn.execute("UPDATE taken SET tekst=? WHERE id=?", (tekst, taak_id))
    conn.commit()
    json_antwoord = _taken_antwoord(conn, notitie, {"id": taak_id, "tekst": tekst})
    conn.close()
    if json_antwoord is not None:
        return json_antwoord
    return _terug_naar_taken(notitie)


@app.route("/notitie/<int:notitie_id>/taak/<int:taak_id>/verwijder", methods=["POST"])
def taak_verwijder(notitie_id, taak_id):
    conn = get_db()
    notitie, _taak = _taak_of_404(conn, notitie_id, taak_id)
    conn.execute("DELETE FROM taken WHERE id=? AND notitie_id=?", (taak_id, notitie_id))
    conn.commit()
    json_antwoord = _taken_antwoord(conn, notitie, {"id": taak_id, "weg": True})
    conn.close()
    if json_antwoord is not None:
        return json_antwoord
    return _terug_naar_taken(notitie)


@app.route("/notitie/<int:notitie_id>/taak/<int:taak_id>/verplaats", methods=["POST"])
def taak_verplaats(notitie_id, taak_id):
    """Omhoog of omlaag. Geen slepen: twee knoppen zijn op een telefoon raakbaar."""
    conn = get_db()
    notitie, _taak = _taak_of_404(conn, notitie_id, taak_id)
    richting = (request.form.get("richting") or "").strip()
    ids = [
        rij["id"] for rij in conn.execute(
            "SELECT id FROM taken WHERE notitie_id=? ORDER BY volgorde, id",
            (notitie_id,),
        )
    ]
    plek = ids.index(taak_id)
    doel = plek - 1 if richting == "omhoog" else plek + 1 if richting == "omlaag" else plek
    if 0 <= doel < len(ids) and doel != plek:
        ids[plek], ids[doel] = ids[doel], ids[plek]
        _zet_volgorde(conn, notitie_id, ids)
        conn.commit()
    json_antwoord = _taken_antwoord(conn, notitie, {"ids": ids})
    conn.close()
    if json_antwoord is not None:
        return json_antwoord
    return _terug_naar_taken(notitie)


def _lees_bedrag(waarde):
    """Bedrag uit een formulier; lege of ongeldige waarde wordt 0."""
    try:
        return round(_getal(waarde), 2)
    except (TypeError, ValueError):
        return 0.0


@app.route("/klus/<int:klus_id>/inkoop", methods=["POST"])
def inkoop_erbij(klus_id):
    """Een aankoop (bon) bij de klus: omschrijving, totaalbedrag, optioneel bestanden
    en materiaalregels van wat erin zat."""
    conn = get_db()
    if conn.execute("SELECT id FROM klussen WHERE id=?", (klus_id,)).fetchone() is None:
        conn.close()
        abort(404)

    omschrijving = (request.form.get("omschrijving") or "").strip()
    bestanden = [b for b in request.files.getlist("bijlage") if b and b.filename]
    if not omschrijving and not bestanden:
        conn.close()
        melding("Geef de bon een omschrijving of kies een bestand.", "fout")
        return redirect(url_for("klus", klus_id=klus_id))
    if not omschrijving:
        # Zonder tekst is de bestandsnaam het enige houvast.
        omschrijving = secure_filename(bestanden[0].filename) or "Bon"

    bedrag = _lees_bedrag(request.form.get("bedrag"))
    inkoop_id = conn.execute(
        """INSERT INTO inkopen (klus_id, omschrijving, bedrag, toegevoegd)
           VALUES (?, ?, ?, ?)""",
        (klus_id, omschrijving, bedrag, vandaag().isoformat()),
    ).lastrowid

    # Optionele materialen: parallelle lijsten, net als regels op een rekening.
    for mat_oms, mat_aantal, mat_prijs in zip_longest(
        request.form.getlist("materiaal_omschrijving"),
        request.form.getlist("materiaal_aantal"),
        request.form.getlist("materiaal_prijs"),
        fillvalue="",
    ):
        mat_oms = (mat_oms or "").strip()
        if not mat_oms:
            continue
        conn.execute(
            """INSERT INTO inkoop_materialen (inkoop_id, omschrijving, aantal, prijs)
               VALUES (?, ?, ?, ?)""",
            (inkoop_id, mat_oms, _lees_bedrag(mat_aantal) or 1.0,
             _lees_bedrag(mat_prijs)),
        )

    erbij, geweigerd = bewaar_bestanden(
        conn, klus_id, bestanden, inkoop_id=inkoop_id)
    conn.commit()
    conn.close()

    melding(f"Bon '{omschrijving}' erbij.")
    if geweigerd:
        melding(f"Niet toegevoegd: {', '.join(geweigerd)}. Kies een foto (JPG, PNG, HEIC) "
                "of een PDF.", "fout")
    return redirect(url_for("klus", klus_id=klus_id))


@app.route("/inkoop/<int:inkoop_id>", methods=["POST"])
def inkoop_bewerk(inkoop_id):
    """Omschrijving of bedrag van een bestaande bon aanpassen; eventueel bestanden
    erbij zetten."""
    conn = get_db()
    rij = conn.execute("SELECT * FROM inkopen WHERE id=?", (inkoop_id,)).fetchone()
    if rij is None:
        conn.close()
        abort(404)

    omschrijving = (request.form.get("omschrijving") or "").strip()
    if not omschrijving:
        conn.close()
        melding("Geef de bon een omschrijving.", "fout")
        return redirect(url_for("klus", klus_id=rij["klus_id"]))

    conn.execute(
        "UPDATE inkopen SET omschrijving=?, bedrag=? WHERE id=?",
        (omschrijving, _lees_bedrag(request.form.get("bedrag")), inkoop_id),
    )
    erbij, geweigerd = bewaar_bestanden(
        conn, rij["klus_id"], request.files.getlist("bijlage"), inkoop_id=inkoop_id)
    conn.commit()
    conn.close()

    melding("Bon bijgewerkt.")
    if geweigerd:
        melding(f"Niet toegevoegd: {', '.join(geweigerd)}. Kies een foto (JPG, PNG, HEIC) "
                "of een PDF.", "fout")
    return redirect(url_for("klus", klus_id=rij["klus_id"]))


@app.route("/inkoop/<int:inkoop_id>/verwijder", methods=["POST"])
def inkoop_verwijder(inkoop_id):
    """Bon, materialen en bestanden samen naar de prullenbak."""
    conn = get_db()
    rij = conn.execute("SELECT * FROM inkopen WHERE id=?", (inkoop_id,)).fetchone()
    if rij is None:
        conn.close()
        abort(404)
    materialen = conn.execute(
        "SELECT * FROM inkoop_materialen WHERE inkoop_id=?", (inkoop_id,)).fetchall()
    bestanden = conn.execute(
        "SELECT * FROM bijlagen WHERE inkoop_id=?", (inkoop_id,)).fetchall()
    # De rekeningregel verliest de bon bij verwijderen. Bij terughalen hoort
    # die koppeling terug, zolang geen andere regel de bon intussen heeft.
    inkoop_regels = [
        {"regel_id": regel["id"], "inkoop_id": inkoop_id}
        for regel in conn.execute(
            "SELECT id FROM regels WHERE inkoop_id=?", (inkoop_id,)
        )
    ]
    prullenbak_id = naar_prullenbak(
        conn, f"Bon {rij['omschrijving']}",
        {"inkopen": [rij], "inkoop_materialen": materialen, "bijlagen": bestanden},
        bestanden=[b["bestand"] for b in bestanden],
        koppelingen={"inkoop_regels": inkoop_regels},
    )
    conn.execute("DELETE FROM inkoop_materialen WHERE inkoop_id=?", (inkoop_id,))
    conn.execute("DELETE FROM bijlagen WHERE inkoop_id=?", (inkoop_id,))
    conn.execute("DELETE FROM inkopen WHERE id=?", (inkoop_id,))
    conn.commit()
    conn.close()
    melding(f"Bon '{rij['omschrijving']}' verwijderd.", knop=terugknop(prullenbak_id))
    return redirect(url_for("klus", klus_id=rij["klus_id"]))


@app.route("/inkoop/<int:inkoop_id>/materiaal", methods=["POST"])
def inkoop_materiaal_erbij(inkoop_id):
    """Optioneel: wat er in de tas zat, als notitie onder de bon — geen aparte
    factuurregel."""
    conn = get_db()
    rij = conn.execute("SELECT * FROM inkopen WHERE id=?", (inkoop_id,)).fetchone()
    if rij is None:
        conn.close()
        abort(404)
    omschrijving = (request.form.get("omschrijving") or "").strip()
    if not omschrijving:
        conn.close()
        melding("Geef het materiaal een omschrijving.", "fout")
        return redirect(url_for("klus", klus_id=rij["klus_id"]))
    conn.execute(
        """INSERT INTO inkoop_materialen (inkoop_id, omschrijving, aantal, prijs)
           VALUES (?, ?, ?, ?)""",
        (inkoop_id, omschrijving,
         _lees_bedrag(request.form.get("aantal")) or 1.0,
         _lees_bedrag(request.form.get("prijs"))),
    )
    conn.commit()
    conn.close()
    melding("Materiaal bij de bon gezet.")
    return redirect(url_for("klus", klus_id=rij["klus_id"]))


@app.route("/inkoop_materiaal/<int:materiaal_id>/verwijder", methods=["POST"])
def inkoop_materiaal_verwijder(materiaal_id):
    conn = get_db()
    rij = conn.execute(
        "SELECT * FROM inkoop_materialen WHERE id=?", (materiaal_id,)).fetchone()
    if rij is None:
        conn.close()
        abort(404)
    inkoop = conn.execute(
        "SELECT klus_id FROM inkopen WHERE id=?", (rij["inkoop_id"],)).fetchone()
    prullenbak_id = naar_prullenbak(
        conn, f"Materiaal {rij['omschrijving']}", {"inkoop_materialen": [rij]})
    conn.execute("DELETE FROM inkoop_materialen WHERE id=?", (materiaal_id,))
    conn.commit()
    conn.close()
    melding(f"{rij['omschrijving']} verwijderd.", knop=terugknop(prullenbak_id))
    if inkoop is None:
        return redirect(url_for("klussen"))
    return redirect(url_for("klus", klus_id=inkoop["klus_id"]))


@app.route("/klus/<int:klus_id>/bijlage", methods=["POST"])
def bijlage_erbij(klus_id):
    """Bestanden bij een bestaande bon, of — zonder inkoop_id — een nieuwe bon
    aanmaken met alleen het bestand (compatibel met oudere flows)."""
    conn = get_db()
    if conn.execute("SELECT id FROM klussen WHERE id=?", (klus_id,)).fetchone() is None:
        conn.close()
        abort(404)

    bestanden = [b for b in request.files.getlist("bijlage") if b and b.filename]
    if not bestanden:
        conn.close()
        melding("Er was geen bestand gekozen.", "fout")
        return redirect(url_for("klus", klus_id=klus_id))

    inkoop_raw = (request.form.get("inkoop_id") or "").strip()
    if inkoop_raw.isdigit():
        inkoop = conn.execute(
            "SELECT * FROM inkopen WHERE id=? AND klus_id=?",
            (int(inkoop_raw), klus_id),
        ).fetchone()
        if inkoop is None:
            conn.close()
            abort(404)
        inkoop_id = inkoop["id"]
    else:
        # Eén bon voor deze upload: bestandsnaam als omschrijving, bedrag later
        # invullen via bewerken.
        naam = secure_filename(bestanden[0].filename) or "Bon"
        inkoop_id = conn.execute(
            """INSERT INTO inkopen (klus_id, omschrijving, bedrag, toegevoegd)
               VALUES (?, ?, 0, ?)""",
            (klus_id, naam, vandaag().isoformat()),
        ).lastrowid

    erbij, geweigerd = bewaar_bestanden(
        conn, klus_id, bestanden, inkoop_id=inkoop_id)
    conn.commit()
    conn.close()

    if erbij:
        melding(f"{erbij} bestand{'en' if erbij != 1 else ''} bij de bon gezet.")
    if geweigerd:
        melding(f"Niet toegevoegd: {', '.join(geweigerd)}. Kies een foto (JPG, PNG, HEIC) "
              "of een PDF.", "fout")
    return redirect(url_for("klus", klus_id=klus_id))


@app.route("/bijlage/<int:bijlage_id>")
def bijlage(bijlage_id):
    """Toont de foto of het bonnetje in de browser.

    Zelfde route als op de kluspagina: ingelogd zijn is verplicht, en het bestand
    moet in de bijlagenmap staan. Inline, zodat een iPhone hem in het tabblad
    laat zien in plaats van hem alleen te downloaden. In de zijbalk van Home
    Assistant blijft het een download, zie als_download()."""
    conn = get_db()
    rij = conn.execute("SELECT * FROM bijlagen WHERE id=?", (bijlage_id,)).fetchone()
    conn.close()
    if rij is None:
        return niet_gevonden()
    pad = veilig_pad(BIJLAGE_DIR, rij["bestand"])
    if pad is None or not os.path.isfile(pad):
        return niet_gevonden()
    mime = BIJLAGE_MIMES.get(os.path.splitext(pad)[1].lower())
    if mime is None:
        return niet_gevonden()
    return toon_bestand(pad, mime, rij["naam"])


def _scan_tekst(tekst, status):
    """Plat antwoord, zodat de melding van de opdracht de zin zelf laat zien.

    HTML of JSON komt daar als een lap code binnen. Een regeleinde aan het
    eind houdt de laatste regel leesbaar in een melding."""
    antwoord = make_response(tekst + "\n", status)
    antwoord.mimetype = "text/plain"
    antwoord.headers["X-Content-Type-Options"] = "nosniff"
    antwoord.headers["Cache-Control"] = "no-store"
    return antwoord


def _echo_bestandsnaam(ruw):
    """Naam zoals die in een antwoord mag, zonder regeleinde of stuurtekens."""
    naam = veilige_downloadnaam(os.path.basename(ruw or ""))
    if not naam or naam == "bijlage":
        return "het bestand"
    return naam[:80]


def schone_scannaam(ruw, ext):
    """Weergavenaam met de extensie die bij de inhoud hoort, niet bij de titel.

    De titel komt van de iPhone en kan een andere extensie claimen dan het
    bestand is. De opgeslagen naam volgt de inhoud, anders opent een PDF
    straks als foto."""
    basis = _echo_bestandsnaam(ruw)
    if basis == "het bestand":
        stam = ""
    else:
        # ".pdf" heeft voor splitext geen stam: de punt hoort bij een
        # verborgen naam, en de uitkomst is ".pdf". Punten weghalen laat
        # "pdf" over, en daar de echte extensie achter plakken werd
        # alsnog "pdf.pdf". Een naam die alleen de extensie is, is geen naam.
        stam = os.path.splitext(basis)[0].strip().strip(".")
        if stam.casefold() == ext.lstrip(".").casefold():
            stam = ""
    if not stam:
        stam = "scan"
    return f"{stam[:80]}{ext}"


def soort_van_scan(kop):
    """(soort, extensie) uit de eerste bytes, of (None, None).

    De extensie van de bestandsnaam telt niet: die zet de afzender zelf.
    GIF en WEBP vallen hierdoor af, ook al mogen ze als bijlage wel."""
    if kop.startswith(b"%PDF-"):
        return "pdf", ".pdf"
    if kop.startswith(b"\xff\xd8\xff"):
        return "jpeg", ".jpg"
    if kop.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", ".png"
    if len(kop) >= 12 and kop[4:8] == b"ftyp" and kop[8:12] in _HEIC_MERKEN:
        if kop[8:12] in _HEIF_MERKEN:
            return "heif", ".heif"
        return "heic", ".heic"
    return None, None


def _scan_code_geldig(gegeven):
    """Of de meegestuurde code de code uit Instellingen is.

    De vergelijking duurt even lang als de code klopt, ontbreekt of is
    ingetrokken. Anders verraadt de wachttijd welke van de drie het is.
    """
    opgeslagen = (get_settings().get("scan_token_hash") or "").strip()
    verwacht = opgeslagen if len(opgeslagen) == 64 else LEEG_SCAN_HASH
    gekregen = hashlib.sha256((gegeven or "").encode("utf-8")).hexdigest()
    gelijk = secrets.compare_digest(gekregen, verwacht)
    return len(opgeslagen) == 64 and gelijk


def _bearer():
    kop = request.headers.get("Authorization", "")
    delen = kop.split(None, 1)
    if len(delen) != 2 or delen[0].lower() != "bearer":
        return ""
    return delen[1].strip()


def _ontvang_scanbestand(bestand):
    """Schrijf één upload naar een tijdelijk bestand en keur hem.

    Te groot stopt met lezen: anders schrijft een enorme upload de schijf
    alsnog vol, voorbij de grens die we de gebruiker beloven. De lengte die
    de afzender zelf meestuurt telt niet, die is vrij te verzinnen.
    """
    label = _echo_bestandsnaam(getattr(bestand, "filename", "") or "")
    # Niet in /tmp: die kan een andere schijf zijn, en verplaatsen naar de
    # bijlagen wordt dan een kopie.
    fd, pad = tempfile.mkstemp(prefix="in-", dir=SCAN_DIR)
    bewaren = False
    try:
        totaal = 0
        te_groot = False
        with os.fdopen(fd, "wb") as uit:
            while True:
                blok = bestand.stream.read(_SCAN_STUK)
                if not blok:
                    break
                if totaal + len(blok) > SCAN_PER_BESTAND:
                    te_groot = True
                    break
                totaal += len(blok)
                uit.write(blok)
        if te_groot:
            if label == "het bestand":
                return None, "Dat bestand is te groot. Eén scan mag hooguit 25 MB zijn.", 413
            return None, f"{label} is te groot. Eén scan mag hooguit 25 MB zijn.", 413
        if totaal == 0:
            if label == "het bestand":
                return None, "Er zat geen bestand bij de scan.", 400
            return None, f"{label}: er zat geen bestand bij de scan.", 400
        with open(pad, "rb") as bron:
            kop = bron.read(32)
        soort, ext = soort_van_scan(kop)
        if not soort:
            if label == "het bestand":
                return None, (
                    "Dit bestand is geen PDF of foto. Stuur een PDF, JPG, PNG of HEIC."
                ), 415
            return None, (
                f"{label} is geen PDF of foto. Stuur een PDF, JPG, PNG of HEIC."
            ), 415
        bewaren = True
        return {
            "pad": pad,
            "naam": schone_scannaam(getattr(bestand, "filename", "") or "", ext),
            "soort": soort,
            "ext": ext,
            "grootte": totaal,
        }, None, None
    finally:
        if not bewaren and os.path.isfile(pad):
            os.remove(pad)


def _ruim_scanpaden(paden):
    for pad in paden:
        if pad and os.path.isfile(pad):
            try:
                os.remove(pad)
            except OSError:
                pass


def _scans_zin(namen):
    if len(namen) == 1:
        return f"Scan ontvangen: {namen[0]}"
    if len(namen) == 2:
        return f"Scans ontvangen: {namen[0]} en {namen[1]}"
    return "Scans ontvangen: " + ", ".join(namen[:-1]) + " en " + namen[-1]


def _logregel(tekst, lengte=80):
    """Eén regel voor het log en de melding, zonder stuurtekens of de inhoud."""
    schoon = "".join(
        teken if teken.isprintable() and teken not in "\r\n\t" else " "
        for teken in str(tekst or "")
    )
    return " ".join(schoon.split())[:lengte]


def _sleutelzin(multi):
    namen = []
    for sleutel in multi.keys():
        stuk = _logregel(sleutel, 40)
        if stuk and stuk not in namen:
            namen.append(stuk)
        if len(namen) >= 8:
            break
    return ", ".join(namen)


def _is_http_link(data):
    """Of dit precies een http(s)-adres is, en dus geen bestand.

    De app haalt zo'n adres niet op. Een factuur uit een andere app komt
    soms alleen als link binnen, en die ophalen vanaf de server zou een
    verzoek naar een willekeurig adres zijn."""
    if not data or len(data) > 2048 or b"\x00" in data:
        return False
    try:
        tekst = data.strip().decode("utf-8")
    except UnicodeDecodeError:
        return False
    if not tekst or any(teken.isspace() for teken in tekst):
        return False
    lager = tekst.lower()
    return lager.startswith("http://") or lager.startswith("https://")


def _stuk_uit_bytes(data, naam):
    return FileStorage(stream=io.BytesIO(data), filename=naam or "")


def _bytes_van_veld(waarde):
    if isinstance(waarde, bytes):
        return waarde
    if isinstance(waarde, str):
        return waarde.encode("utf-8", "surrogateescape")
    return b""


def _bestandsnaam_uit_disposition(waarde):
    if not waarde:
        return ""
    _soort, extra = parse_options_header(waarde)
    naam = extra.get("filename") or extra.get("filename*") or ""
    if isinstance(naam, tuple):
        naam = naam[-1] or ""
    return str(naam)


def _naam_van_verzoek():
    """Naam uit de koppen van de aanvraag, als de scan zelf de inhoud is."""
    naam = _bestandsnaam_uit_disposition(request.headers.get("Content-Disposition", ""))
    if naam:
        return naam
    return request.headers.get("X-Filename", "") or ""


def _multipart_ruwe_delen(inhoud, grens):
    """Ruwe delen van een multipart-aanvraag.

    Zonder bestandsnaam maakt de formulier-parser er tekst van en vervangt
    hij bytes die geen UTF-8 zijn. Een JPEG raakt dan zijn begin kwijt. Hier
    blijven de bytes zoals ze binnenkwamen."""
    if not inhoud or not grens:
        return
    try:
        marker = grens.encode("ascii")
    except UnicodeEncodeError:
        return
    scheiding = b"--" + marker
    for deel in inhoud.split(scheiding):
        if not deel or deel.startswith(b"--"):
            continue
        if deel.startswith(b"\r\n"):
            deel = deel[2:]
        elif deel.startswith(b"\n"):
            deel = deel[1:]
        if deel.endswith(b"\r\n"):
            deel = deel[:-2]
        elif deel.endswith(b"\n"):
            deel = deel[:-1]
        kop, sep, data = deel.partition(b"\r\n\r\n")
        if not sep:
            kop, sep, data = deel.partition(b"\n\n")
        if not sep:
            continue
        yield kop, data


def _naam_uit_deelkop(blob):
    tekst = blob.decode("utf-8", "replace")
    for regel in tekst.splitlines():
        if ":" not in regel:
            continue
        naam, _, waarde = regel.partition(":")
        if naam.strip().lower() == "content-disposition":
            return _bestandsnaam_uit_disposition(waarde.strip())
    return ""


def _stroom_is_link(opslag):
    """Of een meegestuurd 'bestand' in werkelijkheid alleen een http-adres is."""
    stroom = getattr(opslag, "stream", None)
    if stroom is None:
        return False
    plek = stroom.tell() if hasattr(stroom, "tell") else None
    try:
        data = stroom.read(2049)
    except OSError:
        return False
    if plek is not None:
        try:
            stroom.seek(plek)
        except OSError:
            pass
    if not data or len(data) > 2048:
        return False
    return _is_http_link(data)


class _JsonTeDiep(Exception):
    """De JSON is zo diep genest dat uitpakken het log vol zou schrijven."""


def _json_is_link(data):
    """JSON met een http-adres erin. De app haalt dat adres niet op.

    Te diep genest gooit _JsonTeDiep: json.loads zelf recurses, en die
    fout werd een 500 met duizenden logregels."""
    if not data or len(data) > 8192:
        return False
    try:
        tekst = bytes(data).strip().decode("utf-8")
    except UnicodeDecodeError:
        return False
    if not tekst or tekst[0] not in '{["':
        return False
    try:
        waarde = json.loads(tekst)
    except RecursionError:
        raise _JsonTeDiep()
    except json.JSONDecodeError:
        return False
    uit = _waarde_is_link(waarde)
    if uit == "te_diep":
        raise _JsonTeDiep()
    return uit is True


def _waarde_is_link(waarde):
    """Of er ergens een http-adres in zit, zonder zelf te recursen.

    Geeft 'te_diep' terug als de boom dieper is dan _JSON_DIEPTE."""
    stapel = [(waarde, 1)]
    while stapel:
        stuk, diepte = stapel.pop()
        if diepte > _JSON_DIEPTE:
            return "te_diep"
        if isinstance(stuk, str):
            if _is_http_link(stuk.encode("utf-8")):
                return True
        elif isinstance(stuk, dict):
            for onder in stuk.values():
                stapel.append((onder, diepte + 1))
        elif isinstance(stuk, list):
            for onder in stuk:
                stapel.append((onder, diepte + 1))
    return False


def _uit_ruwe_delen(inhoud, grens, alleen_zonder_naam=False):
    gevonden = []
    link = False
    for kop, data in _multipart_ruwe_delen(inhoud, grens):
        naam = _naam_uit_deelkop(kop)
        # Een deel mét bestandsnaam zit al bij de bestanden. Alleen de
        # tekstvelden ernaast mogen hier nog bij, anders bewaren we ze dubbel.
        if alleen_zonder_naam and naam:
            continue
        if soort_van_scan(data[:32])[0]:
            gevonden.append(_stuk_uit_bytes(data, naam))
        elif _is_http_link(data):
            link = True
    if gevonden:
        return gevonden, False
    return [], link


def _uit_formulier():
    gevonden = []
    link = False
    for sleutel, waarde in request.form.items(multi=True):
        data = _bytes_van_veld(waarde)
        if soort_van_scan(data[:32])[0]:
            gevonden.append(_stuk_uit_bytes(data, str(sleutel)))
        elif _is_http_link(data):
            link = True
    if gevonden:
        return gevonden, False
    return [], link


class _VeldTeGroot(Exception):
    """Eén deel is groter dan één scan. Niet het totaal van 100 MB."""


def _nieuwe_spoel():
    return tempfile.SpooledTemporaryFile(max_size=_SCAN_SPOEL, mode="w+b")


def _spoel_van_stroom(stroom):
    """Het lichaam naar schijf, in stukken. Niet als één blok in het geheugen."""
    spoel = _nieuwe_spoel()
    totaal = 0
    while True:
        blok = stroom.read(_SCAN_STUK)
        if not blok:
            break
        totaal += len(blok)
        if totaal > SCAN_VERZOEK_GRENS:
            spoel.close()
            from werkzeug.exceptions import RequestEntityTooLarge
            raise RequestEntityTooLarge()
        spoel.write(blok)
    spoel.seek(0)
    return spoel, totaal


def _zoek_in_bestand(bestand, naald, vanaf, tot=None):
    bestand.seek(vanaf)
    cursor = vanaf
    venster = b""
    while tot is None or cursor < tot:
        blok = bestand.read(_SCAN_STUK)
        if not blok:
            return -1
        if tot is not None and cursor + len(venster) + len(blok) > tot:
            blok = blok[:max(0, tot - (cursor + len(venster)))]
            if not blok:
                return -1
        venster += blok
        plek = venster.find(naald)
        if plek >= 0:
            return cursor + plek
        behoud = max(0, len(naald) - 1)
        if len(venster) > behoud:
            cursor += len(venster) - behoud
            venster = venster[-behoud:] if behoud else b""
    return -1


def _lees_bereik(bestand, start, eind):
    bestand.seek(start)
    return bestand.read(max(0, eind - start))


def _kopieer_bereik(bestand, start, eind):
    if eind - start > SCAN_PER_BESTAND:
        raise _VeldTeGroot()
    spoel = _nieuwe_spoel()
    bestand.seek(start)
    over = eind - start
    while over > 0:
        blok = bestand.read(min(_SCAN_STUK, over))
        if not blok:
            break
        spoel.write(blok)
        over -= len(blok)
    spoel.seek(0)
    return spoel


def _deelkop(blob):
    """(bestandsnaam, of de parameter filename er überhaupt staat).

    filename="" is wél een bestandsdeel. Alleen kijken of de naam leeg is
    telde dat deel nog een keer als tekstveld, en de scan werd twee keer
    opgeslagen."""
    tekst = bytes(blob).decode("utf-8", "replace")
    disp = ""
    for regel in tekst.splitlines():
        if ":" not in regel:
            continue
        naam, _, waarde = regel.partition(":")
        if naam.strip().lower() == "content-disposition":
            disp = waarde.strip()
    if not disp:
        return "", False
    _soort, extra = parse_options_header(disp)
    heeft = "filename" in extra or "filename*" in extra
    naam = extra.get("filename") or extra.get("filename*") or ""
    if isinstance(naam, tuple):
        naam = naam[-1] or ""
    return str(naam), heeft


def _spoel_is_link(spoel):
    spoel.seek(0, os.SEEK_END)
    if spoel.tell() > 2048:
        spoel.seek(0)
        return False
    spoel.seek(0)
    data = spoel.read()
    spoel.seek(0)
    return _is_http_link(data)


def _multipart_van_spoel(bron, grens):
    scheiding = b"--" + grens.encode("ascii")
    bron.seek(0, os.SEEK_END)
    grootte = bron.tell()
    pos = _zoek_in_bestand(bron, scheiding, 0, grootte)
    if pos < 0:
        return
    pos += len(scheiding)
    while pos <= grootte:
        bron.seek(pos)
        twee = bron.read(2)
        if twee.startswith(b"--"):
            return
        if twee == b"\r\n":
            pos += 2
        elif twee[:1] == b"\n":
            pos += 1
        volgend = _zoek_in_bestand(bron, scheiding, pos, grootte)
        if volgend < 0:
            return
        einde = volgend
        if einde >= pos + 2:
            staart = _lees_bereik(bron, einde - 2, einde)
            if staart == b"\r\n":
                einde -= 2
            elif staart.endswith(b"\n"):
                einde -= 1
        kop_eind = _zoek_in_bestand(bron, b"\r\n\r\n", pos, einde)
        sprong = 4
        if kop_eind < 0:
            kop_eind = _zoek_in_bestand(bron, b"\n\n", pos, einde)
            sprong = 2
        if kop_eind < 0 or kop_eind > einde:
            pos = volgend + len(scheiding)
            continue
        kop = _lees_bereik(bron, pos, min(kop_eind, pos + 8192))
        yield kop, kop_eind + sprong, einde
        pos = volgend + len(scheiding)


def _uit_multipart(bron, grens):
    gevonden = []
    link = False
    velden = []
    bestandsvelden = []
    try:
        for kop, start, einde in _multipart_van_spoel(bron, grens):
            naam, heeft_naam = _deelkop(kop)
            if heeft_naam:
                bestandsvelden.append(naam or "(leeg)")
            else:
                velden.append(_logregel(naam, 40) or "veld")
            if einde < start:
                continue
            stuk = _kopieer_bereik(bron, start, einde)
            if _spoel_is_link(stuk):
                # Een link naast een echte scan blijft een link: die hoort
                # in het antwoord, niet stil weg.
                link = True
                stuk.close()
                continue
            stuk.seek(0)
            # Een bestandsdeel dat geen scan is moet de 415 geven, ook als er
            # een goede scan naast staat. Een tekstveld dat geen scan is valt weg.
            if soort_van_scan(stuk.read(32))[0] or heeft_naam:
                stuk.seek(0)
                gevonden.append(FileStorage(stream=stuk, filename=naam or ""))
            else:
                stuk.close()
    except _VeldTeGroot:
        for stuk in gevonden:
            stuk.close()
        raise
    return gevonden, link, ", ".join(velden), ", ".join(bestandsvelden)


def _uit_urlencoded(bron):
    """Formuliervelden zonder het lichaam een tweede keer in het geheugen te houden.

    Alleen kleine waarden (een link) of een scan. Een groot veld gaat naar
    een tijdelijk bestand, niet naar een BytesIO-kopie ernaast."""
    bron.seek(0)
    ruw = bron.read()
    from urllib.parse import parse_qsl
    try:
        items = parse_qsl(ruw.decode("utf-8", "surrogateescape"), keep_blank_values=True)
    except UnicodeError:
        items = []
    del ruw
    gevonden = []
    link = False
    velden = []
    for sleutel, waarde in items:
        velden.append(_logregel(sleutel, 40) or "veld")
        data = waarde.encode("utf-8", "surrogateescape")
        if _is_http_link(data):
            link = True
            continue
        if not soort_van_scan(data[:32])[0]:
            continue
        if len(data) > SCAN_PER_BESTAND:
            raise _VeldTeGroot()
        spoel = _nieuwe_spoel()
        spoel.write(data)
        spoel.seek(0)
        gevonden.append(FileStorage(stream=spoel, filename=str(sleutel)))
    return gevonden, link, ", ".join(velden), ""


def _enkel_stuk(bron, totaal, soort):
    if totaal == 0:
        bron.close()
        return [], False, "", ""
    if totaal <= 8192:
        bron.seek(0)
        data = bron.read()
        bron.seek(0)
        if _is_http_link(data) or _json_is_link(data):
            bron.close()
            return [], True, "", ""
    bron.seek(0)
    kop = bron.read(32)
    bron.seek(0)
    if soort == "application/x-www-form-urlencoded" and not soort_van_scan(kop)[0]:
        try:
            return _uit_urlencoded(bron)
        finally:
            bron.close()
    return [FileStorage(stream=bron, filename=_naam_van_verzoek() or "")], False, "", ""


def _scanbijlagen():
    """De scans in dit verzoek, en of er een link binnenkwam.

    Het lichaam gaat naar een tijdelijk bestand. request.get_data hield het
    én nog een kopie per tekstveld in het geheugen; vier scans van 25 MB
    werden daar een halve gigabyte van.
    """
    soort = (request.mimetype or "").lower()
    bron, totaal = _spoel_van_stroom(request.stream)
    if soort == "multipart/form-data":
        grens = (request.mimetype_params or {}).get("boundary")
        try:
            if not grens:
                return [], False, "", ""
            return _uit_multipart(bron, grens)
        finally:
            bron.close()
    return _enkel_stuk(bron, totaal, soort)


def _melding_geen_scan(link, velden="", bestanden=""):
    """Wat er aankwam, zonder de inhoud en zonder de code.

    request.form hier aanraken zou het lichaam nóg een keer inlezen."""
    mime = _logregel(request.mimetype or "geen type", 80) or "geen type"
    lengte = request.content_length
    lengte_tekst = (
        f"{lengte} bytes" if isinstance(lengte, int) and lengte >= 0
        else "lengte onbekend"
    )
    velden = velden or "geen"
    bestanden = bestanden or "geen"
    if link:
        app.logger.warning(
            "Scan zonder bestand: er kwam een link binnen. type=%s lengte=%s "
            "velden=%s bestanden=%s",
            mime, lengte_tekst, velden, bestanden,
        )
        return (
            "Er kwam een link binnen, geen bestand. De app haalt links niet op. "
            "Zet in de opdracht «Maak PDF» op «Herhaal onderdeel», vóór het "
            "versturen, zodat de iPhone er zelf een pdf van maakt."
        )
    app.logger.warning(
        "Scan zonder bestand: type=%s lengte=%s velden=%s bestanden=%s",
        mime, lengte_tekst, velden, bestanden,
    )
    return (
        "Er zat geen bestand bij de scan. "
        f"Aangekomen: type {mime}, {lengte_tekst}, "
        f"formuliervelden: {velden}, bestandsvelden: {bestanden}."
    )


@app.route("/api/scans", methods=["POST"])
def scans_ontvangen():
    """Scans vanaf de opdracht op de iPhone. Geen sessie, wel de code."""
    if not _scan_code_geldig(_bearer()):
        return _scan_tekst(
            "Deze scan is niet geaccepteerd. Maak in Instellingen een nieuwe code aan.",
            401,
        )
    # Eén uitpakking tegelijk. Een tweede verzoek wacht niet in het geheugen.
    if not _SCAN_BEZIG.acquire(blocking=False):
        return _scan_tekst(
            "De app is een andere scan aan het ontvangen. Probeer het zo nog eens.",
            503,
        )
    binnen = []
    link_erbij = False
    try:
        try:
            binnen, link, velden, bestandsvelden = _scanbijlagen()
        except _VeldTeGroot:
            return _scan_tekst(
                f"Eén scan mag hooguit {_tekst_voor_grens(SCAN_PER_BESTAND)} zijn. "
                "Dit veld is groter dan dat.",
                413,
            )
        except _JsonTeDiep:
            return _scan_tekst(
                "Dit verzoek is te ingewikkeld. Stuur het bestand zelf.",
                400,
            )
        if not binnen:
            return _scan_tekst(_melding_geen_scan(link, velden, bestandsvelden), 400)
        # De echte scans blijven. De link wordt niet stil weggegooid
        # en ook niet opgehaald.
        link_erbij = bool(link)
        if len(binnen) > SCAN_MAX_AANTAL:
            return _scan_tekst(
                f"Te veel bestanden. Eén verzoek mag hooguit {SCAN_MAX_AANTAL} scans bevatten.",
                400,
            )

        # Eerst alles keuren. Eén bestand dat niet mag, en er wordt niets bewaard:
        # anders staat een halve lading in de inbox en denkt de melding dat het
        # mislukte.
        goedgekeurd = []
        for bestand in binnen:
            goed, fout, status = _ontvang_scanbestand(bestand)
            if fout:
                _ruim_scanpaden(stuk.get("pad") for stuk in goedgekeurd)
                return _scan_tekst(fout, status)
            goedgekeurd.append(goed)

        conn = get_db()
        definitief = []
        try:
            ontvangen = nu().strftime("%Y-%m-%dT%H:%M:%S")
            for goed in goedgekeurd:
                opslag = secrets.token_hex(8) + goed["ext"]
                doel = os.path.join(SCAN_DIR, opslag)
                os.replace(goed["pad"], doel)
                goed["pad"] = None
                definitief.append(opslag)
                conn.execute(
                    """INSERT INTO scans (bestand, naam, soort, grootte, ontvangen)
                       VALUES (?, ?, ?, ?, ?)""",
                    (opslag, goed["naam"], goed["soort"], goed["grootte"], ontvangen),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            _ruim_scanpaden(os.path.join(SCAN_DIR, naam) for naam in definitief)
            raise
        finally:
            conn.close()
        _ruim_scanpaden(stuk.get("pad") for stuk in goedgekeurd)
    finally:
        for bestand in binnen:
            try:
                bestand.close()
            except Exception:
                pass
        _SCAN_BEZIG.release()

    zin = _scans_zin([goed["naam"] for goed in goedgekeurd])
    if link_erbij:
        zin += " Er kwam ook een link binnen. Die haalt de app niet op."
    return _scan_tekst(zin, 200)


def scan_upload_adres():
    """Het adres dat de opdracht moet aanroepen, als we het kunnen zien.

    Via de zijbalk kennen we alleen het pad van Ingress, niet het adres van
    Home Assistant op het netwerk. Daar is een voorbeeld dus eerlijker dan
    een adres dat vanaf de bank niet werkt."""
    if via_ingress():
        return ""
    return request.host_url.rstrip("/") + "/api/scans"


def scan_tijdstip(waarde):
    """'2026-10-18T20:49:00' wordt '18 okt 2026, 20:49'."""
    try:
        dag = datetime.fromisoformat(str(waarde).replace(" ", "T")[:19])
    except ValueError:
        return str(waarde or "")
    return f"{dag.day} {MAANDEN[dag.month - 1]} {dag.year}, {dag.strftime('%H:%M')}"


def _scan_rij(conn, scan_id):
    rij = conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
    return dict(rij) if rij else None


def _miniatuur_pad(rij):
    """Klein plaatje bij een scan, of None.

    JPEG en PNG zijn zelf het voorbeeld. Van een PDF bewaren we de eerste
    pagina, eenmalig: pdftoppm tekent die zonder dat de app een zware
    tekenbibliotheek meeneemt. HEIC tekent een browser op de computer niet;
    daar volstaat het merk, net als bij de foto's op een klus."""
    pad = veilig_pad(SCAN_DIR, rij["bestand"])
    if pad is None or not os.path.isfile(pad):
        return None
    if rij["soort"] in {"jpeg", "png"}:
        return pad
    if rij["soort"] != "pdf":
        return None
    doelnaam = rij["bestand"] + ".min.png"
    doel = veilig_pad(SCAN_DIR, doelnaam)
    if doel and os.path.isfile(doel):
        return doel
    # Ontbreekt het programma, dan niet onthouden: na een update van de add-on
    # kan het er opeens wel zijn. Een PDF die het programma zelf niet aankan
    # blijft dat, en die hoort niet bij elk bezoek opnieuw geprobeerd te worden.
    mislukt = veilig_pad(SCAN_DIR, rij["bestand"] + ".min.mislukt")
    if mislukt and os.path.isfile(mislukt):
        return None
    if not doel or shutil.which("pdftoppm") is None:
        return None
    voorvoegsel = pad + ".min"
    try:
        subprocess.run(
            ["pdftoppm", "-png", "-f", "1", "-l", "1", "-scale-to", "480",
             pad, voorvoegsel],
            check=True, timeout=20, capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        _onthoud_mislukte_miniatuur(mislukt)
        return None
    gemaakt = voorvoegsel + "-1.png"
    if not os.path.isfile(gemaakt):
        _onthoud_mislukte_miniatuur(mislukt)
        return None
    os.replace(gemaakt, doel)
    return doel


def _onthoud_mislukte_miniatuur(pad):
    if not pad:
        return
    try:
        open(pad, "ab").close()
    except OSError:
        pass


def _wis_scanminiatuur(rij):
    """Voorbeeld en een mislukte poging horen bij de scan, niet los in de map."""
    for extra in (".min.png", ".min.mislukt"):
        pad = veilig_pad(SCAN_DIR, rij["bestand"] + extra)
        if pad and os.path.isfile(pad):
            os.remove(pad)


def _verwijder_scanbestanden(rij):
    pad = veilig_pad(SCAN_DIR, rij["bestand"])
    if pad and os.path.isfile(pad):
        os.remove(pad)
    _wis_scanminiatuur(rij)


def klussen_gegroepeerd(conn):
    """Klussen per klant, voor de keuzelijst bij het indelen van een scan.

    Een klus zonder klant staat onderaan, onder 'Zonder klant'. Anders
    verdwijnt hij tussen de klanten of valt hij helemaal uit de lijst."""
    rijen = conn.execute(
        """SELECT k.id, k.naam AS naam, c.naam AS klant
           FROM klussen k
           LEFT JOIN klanten c ON c.id = k.klant_id
           ORDER BY k.naam COLLATE NOCASE"""
    ).fetchall()
    per = {}
    for rij in rijen:
        per.setdefault((rij["klant"] or "").strip(), []).append(rij)
    uit = [(naam, per[naam]) for naam in sorted((n for n in per if n), key=str.casefold)]
    if "" in per:
        uit.append(("Zonder klant", per[""]))
    return uit


@app.route("/scans")
def scans():
    conn = get_db()
    rijen = []
    for rij in conn.execute(
        "SELECT * FROM scans ORDER BY ontvangen DESC, id DESC"
    ):
        scan = dict(rij)
        scan["grootte_tekst"] = formaat_bytes(scan["grootte"] or 0)
        scan["wanneer"] = scan_tijdstip(scan["ontvangen"])
        scan["miniatuur"] = _miniatuur_pad(scan) is not None
        scan["foto"] = scan["soort"] in {"jpeg", "png", "heic", "heif"}
        rijen.append(scan)
    conn.close()
    return render_template("scans.html", scans=rijen, actief="scans")


@app.route("/scans/<int:scan_id>/bestand")
def scan_bestand(scan_id):
    conn = get_db()
    rij = _scan_rij(conn, scan_id)
    conn.close()
    if rij is None:
        return niet_gevonden()
    pad = veilig_pad(SCAN_DIR, rij["bestand"])
    if pad is None or not os.path.isfile(pad):
        return niet_gevonden()
    ext = os.path.splitext(pad)[1].lower()
    mime = BIJLAGE_MIMES.get(ext)
    if mime is None:
        return niet_gevonden()
    return toon_bestand(pad, mime, rij["naam"])


@app.route("/scans/<int:scan_id>/miniatuur")
def scan_miniatuur(scan_id):
    conn = get_db()
    rij = _scan_rij(conn, scan_id)
    conn.close()
    if rij is None:
        return niet_gevonden()
    pad = _miniatuur_pad(rij)
    if pad is None:
        return niet_gevonden()
    mime = "image/png" if rij["soort"] == "pdf" else BIJLAGE_MIMES.get(
        os.path.splitext(pad)[1].lower(), "application/octet-stream")
    return toon_bestand(pad, mime, rij["naam"])


def _scan_of_404(scan_id):
    conn = get_db()
    rij = _scan_rij(conn, scan_id)
    if rij is None:
        conn.close()
        abort(404)
    return conn, rij


@app.route("/scans/<int:scan_id>/bon", methods=["GET", "POST"])
def scan_als_bon(scan_id):
    """De scan wordt een bon bij een klus. Het bestand gaat mee; een
    bestandsveld kun je niet van tevoren invullen."""
    conn, rij = _scan_of_404(scan_id)
    if request.method == "POST":
        return _koppel_als_bon(conn, rij)
    groepen = klussen_gegroepeerd(conn)
    conn.close()
    return render_template(
        "scan_koppel.html", scan=_scan_voor_formulier(rij), groepen=groepen,
        doel="bon", titel="Scan als bon", knop="Bon maken",
        omschrijving=os.path.splitext(rij["naam"])[0], actief="scans",
    )


@app.route("/scans/<int:scan_id>/notitie", methods=["GET", "POST"])
def scan_als_notitie(scan_id):
    conn, rij = _scan_of_404(scan_id)
    if request.method == "POST":
        return _koppel_als_notitie(conn, rij)
    groepen = klussen_gegroepeerd(conn)
    conn.close()
    return render_template(
        "scan_koppel.html", scan=_scan_voor_formulier(rij), groepen=groepen,
        doel="notitie", titel="Scan bij een notitie", knop="Notitie maken",
        vandaag_iso=vandaag().isoformat(), actief="scans",
    )


def _scan_voor_formulier(rij):
    scan = dict(rij)
    scan["grootte_tekst"] = formaat_bytes(scan["grootte"] or 0)
    return scan


def _gekozen_klus(conn):
    ruw = (request.form.get("klus_id") or "").strip()
    if not ruw.isdigit():
        return None
    return conn.execute("SELECT * FROM klussen WHERE id=?", (int(ruw),)).fetchone()


def _koppel_als_bon(conn, rij):
    klus = _gekozen_klus(conn)
    if klus is None:
        conn.close()
        melding("Kies een klus.", "fout")
        return redirect(url_for("scan_als_bon", scan_id=rij["id"]))
    omschrijving = (request.form.get("omschrijving") or "").strip()
    if not omschrijving:
        omschrijving = os.path.splitext(rij["naam"])[0] or "Bon"
    bedrag = _lees_bedrag(request.form.get("bedrag"))
    inkoop_id = conn.execute(
        """INSERT INTO inkopen (klus_id, omschrijving, bedrag, toegevoegd)
           VALUES (?, ?, ?, ?)""",
        (klus["id"], omschrijving, bedrag, vandaag().isoformat()),
    ).lastrowid
    if not _scan_naar_bijlage(conn, rij, klus["id"], inkoop_id=inkoop_id):
        conn.rollback()
        conn.close()
        melding("Het bestand van deze scan is weg. Verwijder de scan en stuur hem opnieuw.",
                "fout")
        return redirect(url_for("scans"))
    conn.commit()
    conn.close()
    melding(f"Scan bij de bon '{omschrijving}' gezet.")
    return redirect(url_for("klus", klus_id=klus["id"]))


def _koppel_als_notitie(conn, rij):
    klus = _gekozen_klus(conn)
    if klus is None:
        conn.close()
        melding("Kies een klus.", "fout")
        return redirect(url_for("scan_als_notitie", scan_id=rij["id"]))
    tekst = (request.form.get("tekst") or "").strip()
    wanneer = geldige_datum(request.form.get("wanneer"), vandaag().isoformat())
    notitie_id = conn.execute(
        "INSERT INTO notities (klus_id, wanneer, tekst) VALUES (?, ?, ?)",
        (klus["id"], wanneer, tekst),
    ).lastrowid
    if not _scan_naar_bijlage(conn, rij, klus["id"], notitie_id=notitie_id):
        conn.rollback()
        conn.close()
        melding("Het bestand van deze scan is weg. Verwijder de scan en stuur hem opnieuw.",
                "fout")
        return redirect(url_for("scans"))
    conn.commit()
    conn.close()
    melding("Scan bij de notitie gezet.")
    return redirect(url_for("klus", klus_id=klus["id"]))


def _scan_naar_bijlage(conn, rij, klus_id, notitie_id=None, inkoop_id=None):
    """Koppel de scan en verplaats het bestand pas als de rijen klaarstaan.

    Verplaatsen vóór de insert liet een bestand in de bijlagenmap achter
    zonder rij, zodra de database daarna nog weigerde."""
    bron = veilig_pad(SCAN_DIR, rij["bestand"])
    if bron is None or not os.path.isfile(bron):
        return False
    ext = os.path.splitext(rij["bestand"])[1].lower()
    if ext not in BIJLAGE_TYPES:
        return False
    opslag = f"{secrets.token_hex(8)}{ext}"
    conn.execute(
        """INSERT INTO bijlagen (klus_id, bestand, naam, toegevoegd, meesturen,
           notitie_id, inkoop_id) VALUES (?, ?, ?, ?, 0, ?, ?)""",
        (klus_id, opslag, rij["naam"], vandaag().isoformat(), notitie_id, inkoop_id),
    )
    conn.execute("DELETE FROM scans WHERE id=?", (rij["id"],))
    try:
        shutil.move(bron, os.path.join(BIJLAGE_DIR, opslag))
    except OSError:
        return False
    _wis_scanminiatuur(rij)
    return True


@app.route("/scans/<int:scan_id>/verwijder", methods=["POST"])
def scan_verwijder(scan_id):
    conn, rij = _scan_of_404(scan_id)
    _verwijder_scanbestanden(rij)
    conn.execute("DELETE FROM scans WHERE id=?", (scan_id,))
    conn.commit()
    conn.close()
    melding("Scan verwijderd.")
    return redirect(url_for("scans"))


@app.route("/instellingen/scans/token", methods=["POST"])
def scan_token():
    """Nieuwe code, of de oude intrekken. De code zelf gaat niet de database in."""
    actie = (request.form.get("actie") or "").strip()
    conn = get_db()
    if actie == "intrekken":
        conn.execute("UPDATE settings SET scan_token_hash='' WHERE id=1")
        conn.commit()
        conn.close()
        session.pop("scan_token_eenmalig", None)
        melding("De code voor iPhone-scans is ingetrokken. De opdracht op je "
                "iPhone doet het niet meer tot je een nieuwe code maakt.")
        return redirect(url_for("instellingen") + "#iphone-scans")
    if actie != "nieuw":
        conn.close()
        abort(400)
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn.execute("UPDATE settings SET scan_token_hash=? WHERE id=1", (digest,))
    conn.commit()
    conn.close()
    session["scan_token_eenmalig"] = token
    melding("Nieuwe code gemaakt. Kopieer hem nu; daarna is hij niet meer te zien.")
    return redirect(url_for("instellingen") + "#iphone-scans")


@app.route("/bijlage/<int:bijlage_id>/meesturen", methods=["POST"])
def bijlage_meesturen(bijlage_id):
    """Oude knop. Een bon op de rekening gaat altijd mee; dit vinkje doet niets meer.

    De route blijft, zodat een nog open kluspagina geen fout geeft. Het
    mailscherm is de enige plek waar je een bon alsnog weg kunt laten."""
    conn = get_db()
    rij = conn.execute("SELECT * FROM bijlagen WHERE id=?", (bijlage_id,)).fetchone()
    if rij is None:
        conn.close()
        abort(404)
    klus_id = rij["klus_id"]
    conn.close()
    return redirect(url_for("klus", klus_id=klus_id))


@app.route("/bijlage/<int:bijlage_id>/verwijder", methods=["POST"])
def bijlage_verwijder(bijlage_id):
    conn = get_db()
    rij = conn.execute("SELECT * FROM bijlagen WHERE id=?", (bijlage_id,)).fetchone()
    if rij is None:
        conn.close()
        abort(404)
    prullenbak_id = naar_prullenbak(conn, rij["naam"], {"bijlagen": [rij]},
                                    bestanden=[rij["bestand"]])
    conn.execute("DELETE FROM bijlagen WHERE id=?", (bijlage_id,))
    conn.commit()
    conn.close()

    melding(f"{rij['naam']} verwijderd.", knop=terugknop(prullenbak_id))
    return redirect(url_for("klus", klus_id=rij["klus_id"]))


@app.route("/klus/<int:klus_id>/dag", methods=["POST"])
def dag_erbij(klus_id):
    """Eén gewerkte dag bij de klus zetten: datum, van-tot en eventueel een notitie."""
    conn = get_db()
    bestaat = conn.execute("SELECT id FROM klussen WHERE id=?", (klus_id,)).fetchone()
    if bestaat is None:
        conn.close()
        abort(404)

    tijden, fout = tijden_uit_form(request.form)
    if fout:
        conn.close()
        melding(fout, "fout")
        return redirect(url_for("klus", klus_id=klus_id))
    van, tot = tijden
    datum = geldige_datum(request.form.get("datum"))
    # Twee open dagen op dezelfde datum zijn niet uit elkaar te houden: allebei
    # 'bezig sinds', en bij het afsluiten weet je niet welke je dicht doet.
    if tot is None:
        bezig = lopende_dag_op(conn, klus_id, datum)
        if bezig is not None:
            conn.close()
            melding(melding_tweede_open_dag(bezig, datum), "fout")
            return redirect(url_for("klus", klus_id=klus_id))

    pauze, pauze_fout = lees_pauze(request.form, van, tot)
    if pauze_fout:
        conn.close()
        melding(pauze_fout, "fout")
        return redirect(url_for("klus", klus_id=klus_id))

    conn.execute(
        """INSERT INTO uren (klus_id, datum, van, tot, notitie, pauze_minuten)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            klus_id,
            datum,
            van,
            tot,
            request.form.get("notitie", "").strip(),
            pauze,
        ),
    )
    conn.commit()
    conn.close()
    if tot is None:
        melding(f"De dag is opgeslagen, bezig sinds {van}.")
    return redirect(url_for("klus", klus_id=klus_id))


def tijden_uit_form(form):
    """(van, tot) of een foutmelding. tot is None zolang de dag nog loopt.

    Een eindtijd vóór de begintijd is meestal een tikfout: 07:30 tot 07:00
    werd anders stil 23,5 uur. Alleen met het vinkje telt die tijd als de
    volgende ochtend. Dezelfde tijd is geen gewerkte dag."""
    van = lees_tijd(form.get("van"))
    tot = lees_tijd(form.get("tot"))
    if van is None:
        return None, "De begintijd klopt niet. Gebruik een tijd zoals 07:30."
    if not van:
        return None, "Vul een begintijd in om de dag op te slaan."
    if tot is None:
        return None, ("De eindtijd klopt niet. Gebruik een tijd zoals 16:30, "
                      "of laat hem leeg als je nog bezig bent.")
    if form.get("afsluiten") == "ja" and not tot:
        return None, "Vul een eindtijd in om de dag af te sluiten."
    if tot and tot < van and form.get("over_middernacht") != "ja":
        return None, "Eindtijd ligt vóór de begintijd."
    if tot and duur_in_uren(van, tot) <= 0:
        return None, "De eindtijd moet na de begintijd liggen."
    return (van, tot or None), None


def lees_pauze(form, van, tot):
    """Minuten pauze, of een foutmelding. Leeg is geen pauze.

    Een pauze die langer is dan de gewerkte tijd zou de uren negatief maken.
    Die dag slaan we niet op, anders staat er 0 uur op een rekening terwijl
    de kloktijden iets anders zeggen."""
    tekst = (form.get("pauze") or "").strip()
    if not tekst:
        return 0, None
    if not tekst.isdigit():
        return None, ("De pauze klopt niet. Vul het aantal minuten in, "
                      "bijvoorbeeld 30.")
    minuten = int(tekst)
    if minuten > 24 * 60:
        return None, ("De pauze klopt niet. Vul het aantal minuten in, "
                      "bijvoorbeeld 30.")
    if tot and minuten > round(duur_in_uren(van, tot) * 60):
        return None, "De pauze is langer dan de gewerkte tijd."
    return minuten, None


def lopende_dag_op(conn, klus_id, datum, behalve_id=None):
    """De open dag op deze klus en datum, als die er al is."""
    sql = f"""SELECT id, van FROM uren
              WHERE klus_id=? AND datum=? AND NOT ({_GESLOTEN_DAG})"""
    params = [klus_id, datum]
    if behalve_id is not None:
        sql += " AND id<>?"
        params.append(behalve_id)
    return conn.execute(sql + " ORDER BY id LIMIT 1", params).fetchone()


def melding_tweede_open_dag(rij, datum):
    return (
        f"Op {filter_datum_nl(datum)} loopt al een dag, sinds {(rij['van'] or '')[:5]}. "
        "Sluit die eerst af."
    )


@app.route("/uur/<int:uur_id>/bewerk", methods=["POST"])
def dag_bewerk(uur_id):
    conn = get_db()
    gegevens = conn.execute("SELECT * FROM uren WHERE id=?", (uur_id,)).fetchone()
    if gegevens is None:
        conn.close()
        abort(404)

    tijden, fout = tijden_uit_form(request.form)
    if fout:
        conn.close()
        melding(fout, "fout")
        return redirect(url_for("klus", klus_id=gegevens["klus_id"]))
    van, tot = tijden
    datum = geldige_datum(request.form.get("datum"))
    pauze, pauze_fout = lees_pauze(request.form, van, tot)
    if pauze_fout:
        conn.close()
        melding(pauze_fout, "fout")
        return redirect(url_for("klus", klus_id=gegevens["klus_id"]))

    # De tijden staan in de omschrijving op de rekening. Ze hier wijzigen
    # laat die tekst en het bedrag achter, of maakt de dag leeg terwijl de
    # regel blijft staan. Eerst van de rekening halen, dan pas aanpassen.
    # Een notitie mag wel: die staat niet in het bedrag.
    if gegevens["factuur_id"] and _dag_wijkt_af(gegevens, datum, van, tot, pauze):
        tekst, link = melding_dag_op_rekening(conn, gegevens["factuur_id"])
        if tot is None and eindtijd_ingevuld(gegevens["tot"]):
            tekst += ". De eindtijd kun je niet leegmaken."
        else:
            tekst += (". De tijden pas je op die rekening aan, of haal de dag "
                      "eerst van de rekening.")
        conn.close()
        melding(tekst, "fout", link=link)
        return redirect(url_for("klus", klus_id=gegevens["klus_id"]))

    if tot is None:
        bezig = lopende_dag_op(conn, gegevens["klus_id"], datum, behalve_id=uur_id)
        if bezig is not None:
            conn.close()
            melding(melding_tweede_open_dag(bezig, datum), "fout")
            return redirect(url_for("klus", klus_id=gegevens["klus_id"]))

    was_lopend = not eindtijd_ingevuld(gegevens["tot"])
    conn.execute(
        """UPDATE uren SET datum=?, van=?, tot=?, notitie=?, pauze_minuten=?
           WHERE id=?""",
        (
            datum,
            van,
            tot,
            request.form.get("notitie", "").strip(),
            pauze,
            uur_id,
        ),
    )
    conn.commit()
    conn.close()
    if was_lopend and tot:
        melding(f"Dag afgesloten, van {van} tot {tot}.")
    return redirect(url_for("klus", klus_id=gegevens["klus_id"]))


def _tijd_gelijk(a, b):
    return (a or "")[:5] == (b or "")[:5]


def _dag_wijkt_af(gegevens, datum, van, tot, pauze):
    """Of datum, tijden of pauze anders zijn dan wat er nu staat."""
    return (
        str(datum) != str(gegevens["datum"])
        or not _tijd_gelijk(van, gegevens["van"])
        or not _tijd_gelijk(tot, gegevens["tot"])
        or int(pauze or 0) != _pauze(gegevens)
    )


def melding_dag_op_rekening(conn, factuur_id):
    """Tekst en link naar de rekening waar deze dag op staat.

    Zonder link is de melding alleen tekst, en de rekening is niet aan te
    tikken: de tekst wordt geëscaped."""
    rij = _rekening_rij(conn, factuur_id)
    if rij is None:
        return "Deze dag staat op een conceptrekening", None
    return (
        f"Deze dag staat op {zin_van_rekening(rij)}",
        {"url": url_for("bewerk", factuur_id=rij["id"]), "label": "Naar de rekening"},
    )


@app.route("/uur/<int:uur_id>/verwijder", methods=["POST"])
def dag_verwijder(uur_id):
    conn = get_db()
    gegevens = conn.execute("SELECT * FROM uren WHERE id=?", (uur_id,)).fetchone()
    if gegevens is None:
        conn.close()
        abort(404)
    if gegevens["factuur_id"]:
        tekst, link = melding_dag_op_rekening(conn, gegevens["factuur_id"])
        conn.close()
        melding(f"{tekst}.", "fout", link=link)
        return redirect(url_for("klus", klus_id=gegevens["klus_id"]))
    prullenbak_id = naar_prullenbak(
        conn, f"De dag van {filter_datum_nl(gegevens['datum'])}", {"uren": [gegevens]})
    conn.execute("DELETE FROM uren WHERE id=?", (uur_id,))
    conn.commit()
    conn.close()
    melding(f"De dag van {filter_datum_nl(gegevens['datum'])} is weg.",
            knop=terugknop(prullenbak_id))
    return redirect(url_for("klus", klus_id=gegevens["klus_id"]))


def _getal(waarde):
    """'12,50' en '12.50' leveren allebei 12.5 op. Een browser stuurt een getalveld
    met een punt, maar wie het formulier anders invult (of vult vanuit een CSV) typt
    hier gewoon een komma; dan hoort de regel niet stilletjes te verdwijnen."""
    tekst = str(waarde or "").strip().replace(" ", "")
    if not tekst:
        return 0.0
    if "," in tekst:
        tekst = tekst.replace(".", "").replace(",", ".")
    return float(tekst)


def lees_regels(form):
    """Haalt de ingevulde regels uit het formulier en telt het totaal op.
    Regels zonder omschrijving worden overgeslagen.

    Het bedrag komt uit aantal maal prijs, halve cent omhoog. Het subtotaal
    in het formulier telt niet mee: zonder script blijft dat het oude bedrag,
    ook als je de prijs of het aantal hebt veranderd. Een ongewijzigde oude
    regel krijgt zijn bewaarde bedrag later terug, uit de database en niet
    uit dit formulier. Een dagregel wordt daarna nog langs de minuten gelegd."""
    regels = []
    for o, t, a_ruw, p_ruw, klus, inkoop, uur, _sub_ruw, regel_id in zip_longest(
        form.getlist("omschrijving"),
        form.getlist("type"),
        form.getlist("aantal"),
        form.getlist("prijs"),
        form.getlist("regel_klus"),
        form.getlist("regel_inkoop"),
        form.getlist("regel_uur"),
        form.getlist("regel_subtotaal"),
        form.getlist("regel_id"),
        fillvalue="",
    ):
        if not str(o or "").strip():
            continue
        try:
            aantal = als_decimal(a_ruw)
            prijs = als_decimal(p_ruw)
        except ValueError:
            continue
        if t in ("arbeid_klus", "bon"):
            # Vaste prijs: het bedrag is de prijs, geen aantal keer tarief.
            aantal = Decimal("1")
        regels.append((
            str(o), t, float(aantal), float(prijs), rond_centen(aantal * prijs),
            int(klus) if str(klus).isdigit() else None,
            int(inkoop) if str(inkoop).isdigit() else None,
            int(uur) if str(uur).isdigit() else None,
            int(regel_id) if str(regel_id).isdigit() else None,
        ))
    return regels, som_van(regels)


def zonder_dubbele_dagen(regels):
    """Dezelfde gewerkte dag of bon mag niet twee regels vullen.

    Het bedrag zou anders twee keer op de rekening staan terwijl de dag of
    de bon maar één keer gekoppeld kan worden."""
    dagen = set()
    bonnen = set()
    uit = []
    dag_dubbel = False
    bon_dubbel = False
    for regel in regels:
        uur_id = regel[7] if len(regel) > 7 else None
        inkoop_id = regel[6] if len(regel) > 6 else None
        if uur_id and uur_id in dagen:
            dag_dubbel = True
            continue
        if inkoop_id and inkoop_id in bonnen:
            bon_dubbel = True
            continue
        if uur_id:
            dagen.add(uur_id)
        if inkoop_id:
            bonnen.add(inkoop_id)
        uit.append(regel)
    return uit, dag_dubbel, bon_dubbel


DUBBELE_DAG = (
    "Een gewerkte dag stond twee keer op de rekening. "
    "Hij is maar één keer meegerekend."
)

DUBBELE_BON = (
    "Een bon stond twee keer op de rekening. "
    "Hij is maar één keer meegerekend."
)


def regels_uit_form(form):
    """Regels en totaal. Een dag of bon die twee keer voorkomt telt één keer."""
    regels, totaal = lees_regels(form)
    regels, dag_dubbel, bon_dubbel = zonder_dubbele_dagen(regels)
    if dag_dubbel or bon_dubbel:
        totaal = som_van(regels)
    return regels, totaal, dag_dubbel, bon_dubbel


# ---------- Adressen en telefoonnummers netjes maken ----------
# Wat je intikt komt overal terug: op de rekening, in de mail, op de klantpagina. Dat
# hoort er hetzelfde uit te zien of je nu "1234ab voorbeeldstad" of "1234 AB Voorbeeldstad" typt.
# Alles gaat door deze twee functies op het moment van opslaan, zodat het ook klopt
# voor wat er via een CSV binnenkomt.

# Een Nederlandse postcode: vier cijfers, dan twee letters. De letters mogen tegen de
# cijfers aan staan of er los van.
POSTCODE = re.compile(r"\b(\d{4})\s*([A-Za-z]{2})\b(?![A-Za-z0-9])")

# Tussenvoegsels in plaatsnamen blijven klein: "Dorp aan den Berg", niet "Aan Den".
KLEINE_WOORDEN = {"aan", "de", "den", "der", "des", "het", "in", "op", "over", "te",
                  "ten", "ter", "'t", "'s", "van", "bij"}


def _hoofdletters(tekst):
    """Elk woord met een hoofdletter, maar tussenvoegsels niet."""
    woorden = tekst.split()
    uit = []
    for i, woord in enumerate(woorden):
        if i and woord.lower() in KLEINE_WOORDEN:
            uit.append(woord.lower())
        else:
            # Ook na een koppelteken hoort een hoofdletter: Voorbeeld-Stad.
            uit.append("-".join(deel[:1].upper() + deel[1:] for deel in woord.split("-")))
    return " ".join(uit)


def net_adres(tekst):
    """Straat en huisnummer op de eerste regel, postcode en plaats op de tweede.

    Vindt de functie geen Nederlandse postcode, dan blijft het adres staan zoals het
    is getypt — een buitenlands adres of een half ingevuld adres hoort niet door een
    opmaakregel te worden verbouwd."""
    tekst = (tekst or "").strip()
    if not tekst:
        return ""

    gevonden = POSTCODE.search(tekst)
    if not gevonden:
        regels = [" ".join(regel.split()) for regel in tekst.splitlines() if regel.strip()]
        # Zonder postcode maar met een komma op één regel: die komma scheidt de straat
        # van de plaats. Komt zo uit de adressenlijst van het Kadaster ("Voorbeeldstraat
        # 14, Voorbeeldstad") en zo typen mensen het ook.
        if len(regels) == 1 and "," in regels[0]:
            straat, _, plaats = regels[0].rpartition(",")
            regels = [straat.strip(), plaats.strip()]
        return "\n".join(regel for regel in regels if regel)

    straat = tekst[:gevonden.start()]
    plaats = tekst[gevonden.end():]
    # Komma's en regelovergangen tussen de delen zijn scheidingstekens, geen inhoud.
    straat = " ".join(straat.replace(",", " ").split())
    plaats = " ".join(plaats.replace(",", " ").split())

    postcode = f"{gevonden.group(1)} {gevonden.group(2).upper()}"
    # Alleen bijwerken wat helemaal klein is getypt; wie zelf hoofdletters zet,
    # bedoelt dat waarschijnlijk zo (McVoorbeeld, IJvoorbeeld, 's-Voorbeeld).
    if plaats and plaats == plaats.lower():
        plaats = _hoofdletters(plaats)
    if straat and straat == straat.lower():
        straat = _hoofdletters(straat)

    tweede = f"{postcode} {plaats}".strip()
    return f"{straat}\n{tweede}".strip() if straat else tweede


def navigatie_voor(adres):
    """Routeplanners voor een adres, of niets als er geen adres is.

    Het adres staat opgeslagen met de straat op de eerste regel en de postcode
    en plaats op de tweede. Een routeplanner wil één bestemming, dus die regels
    komen achter elkaar. Ontbreekt een deel, dan gaat alleen wat er is mee."""
    regels = [regel.strip() for regel in str(adres or "").splitlines() if regel.strip()]
    if not regels:
        return []
    bestemming = quote(", ".join(regels), safe="")
    return [
        ("Google Maps",
         f"https://www.google.com/maps/dir/?api=1&destination={bestemming}"),
        ("Apple Kaarten", f"https://maps.apple.com/?daddr={bestemming}"),
        ("Waze", f"https://waze.com/ul?q={bestemming}&navigate=yes"),
    ]


app.jinja_env.globals["navigatie_voor"] = navigatie_voor


# Netnummers van drie cijfers; al het andere vaste nummer heeft er vier. Zonder deze
# lijst valt niet te zien waar het netnummer ophoudt en het abonneenummer begint.
KORTE_NETNUMMERS = {
    "10", "13", "15", "20", "23", "24", "26", "30", "33", "35", "36", "38", "40",
    "43", "45", "46", "50", "53", "55", "58", "70", "71", "72", "73", "74", "75",
    "76", "77", "78", "79",
}


def net_telefoon(tekst):
    """06 12345678 voor een mobiel nummer, 010 1234567 voor een vast nummer.

    Een nummer met landcode wordt +31 6 12345678. Wat er niet als Nederlands nummer
    uitziet, wordt alleen opgeschoond en verder met rust gelaten: een buitenlands
    nummer verkeerd opdelen is erger dan het laten staan."""
    tekst = (tekst or "").strip()
    if not tekst:
        return ""

    cijfers = re.sub(r"[^\d+]", "", tekst)
    landcode = ""
    if cijfers.startswith("+31"):
        landcode, cijfers = "+31", cijfers[3:]
    elif cijfers.startswith("0031"):
        landcode, cijfers = "+31", cijfers[4:]
    if landcode:
        # "+31 (0)6 ..." is een gangbare schrijfwijze; die nul hoort bij de binnen-
        # landse vorm en staat dubbel zodra de landcode er al is.
        cijfers = cijfers.lstrip("0")
    elif cijfers.startswith("+"):
        return tekst  # een ander land; daar gaan we niet over
    elif cijfers.startswith("0"):
        cijfers = cijfers[1:]

    if not cijfers.isdigit() or len(cijfers) != 9:
        return " ".join(tekst.split())

    if cijfers.startswith("6"):
        kop, rest = "6", cijfers[1:]
    elif cijfers[:2] in KORTE_NETNUMMERS:
        kop, rest = cijfers[:2], cijfers[2:]
    else:
        kop, rest = cijfers[:3], cijfers[3:]

    if landcode:
        return f"{landcode} {kop} {rest}"
    return f"0{kop} {rest}"


# ---------- Adressen opzoeken ----------
# De Locatieserver van PDOK: alle adressen van Nederland als open data van het
# Kadaster. Geen account, geen sleutel, geen tarief. Wat je in het adresveld typt
# gaat er wel naartoe, dus alleen bij het zoeken zelf en nooit iets anders.
PDOK = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/suggest"

# Kort wachten en dan opgeven: een adresveld hoort niet te blijven hangen omdat een
# server aan de andere kant traag is. Je kunt intussen gewoon doortypen.
PDOK_WACHTTIJD = 4

# Wat er al eens is opgezocht. Tijdens het typen komt dezelfde beginletterreeks steeds
# terug; dit scheelt verkeer naar buiten en maakt de suggesties merkbaar sneller.
_ADRESCACHE = {}
ADRESCACHE_MAX = 200


def zoek_adressen(term, aantal=5):
    """Adressen die bij deze zoekterm passen, als lijst van (adres, toelichting).

    Lukt het opzoeken niet — geen internet, PDOK plat, wat dan ook — dan komt er een
    lege lijst terug. Suggesties zijn een gemak; zonder moet je gewoon kunnen typen."""
    term = " ".join((term or "").split())
    if len(term) < 3:
        return []
    if term in _ADRESCACHE:
        return _ADRESCACHE[term]

    adres = f"{PDOK}?{urlencode({'q': term, 'rows': aantal, 'fq': 'type:adres'})}"
    try:
        verzoek = Request(adres, headers={"User-Agent": f"Facturen App/{VERSIE}"})
        with urlopen(verzoek, timeout=PDOK_WACHTTIJD) as antwoord:
            gegevens = json.loads(antwoord.read().decode("utf-8"))
        gevonden = [
            net_adres(doc["weergavenaam"])
            for doc in gegevens.get("response", {}).get("docs", [])
            if doc.get("weergavenaam")
        ]
    except (URLError, OSError, ValueError, KeyError) as fout:
        app.logger.info("Adressen opzoeken lukte niet: %s", fout)
        return []

    if len(_ADRESCACHE) >= ADRESCACHE_MAX:
        _ADRESCACHE.clear()
    _ADRESCACHE[term] = gevonden
    return gevonden


@app.route("/adressen")
def adressuggesties():
    """Wat het adresveld tijdens het typen ophaalt: eerst je eigen klanten, dan de
    officiële adressen. Een adres dat je al eens hebt ingevoerd staat bovenaan, want
    dat is meestal degene die je bedoelt."""
    term = request.args.get("q", "").strip()
    if len(term) < 3:
        return {"suggesties": []}

    conn = get_db()
    eigen = conn.execute(
        """SELECT naam, adres FROM klanten
           WHERE adres <> '' AND (adres LIKE ? OR naam LIKE ?)
           ORDER BY naam LIMIT 4""",
        (f"%{term}%", f"%{term}%"),
    ).fetchall()
    conn.close()

    # Twee klanten op hetzelfde adres — of een klant die er per ongeluk twee keer in
    # staat — leveren anders twee regels op die er precies hetzelfde uitzien.
    suggesties, al_gezien = [], set()
    for rij in eigen:
        if rij["adres"] in al_gezien:
            continue
        al_gezien.add(rij["adres"])
        suggesties.append({"adres": rij["adres"], "bron": rij["naam"]})
    for adres in zoek_adressen(term):
        if adres not in al_gezien:
            al_gezien.add(adres)
            suggesties.append({"adres": adres, "bron": ""})
    return {"suggesties": suggesties[:8]}


def wachtduur(datum):
    """Hoe lang een aanvraag al ligt, in gewone woorden.

    Bij een aanvraag is dat het cijfer dat telt: niet hoeveel uur erin zit, maar hoe
    lang die persoon al op je wacht."""
    try:
        dagen = (vandaag() - date.fromisoformat(str(datum))).days
    except (TypeError, ValueError):
        return ""
    if dagen <= 0:
        return "vandaag"
    if dagen == 1:
        return "gisteren"
    if dagen < 14:
        return f"{dagen} dagen"
    if dagen < 60:
        return f"{dagen // 7} weken"
    return f"{dagen // 30} maanden"


app.jinja_env.globals["wachtduur"] = wachtduur


def geldige_datum(waarde, terugval=None):
    """Een datum die niet als jaar-maand-dag te lezen is, breekt later de sortering
    en de weergave. Zo'n waarde vervangen we door de terugval."""
    try:
        return date.fromisoformat(str(waarde)).isoformat()
    except (TypeError, ValueError):
        return terugval or vandaag().isoformat()


def geboekte_klussen(factuur_id=None, klant_id=None):
    """De klussen met uren die nog niet op een rekening staan, om als één regel toe
    te voegen. Bij het bewerken van een rekening tellen de uren die er al op staan
    gewoon mee, anders zou de klus daar verdwijnen.

    Een aanvraag telt niet mee: die dagen zijn op de klus uit beeld. Een regel
    die al op een rekening staat blijft daar staan; die komt uit de regels zelf,
    niet uit deze lijst.

    Elke klus krijgt `past`: of hij bij de klant van deze rekening hoort, dezelfde
    regel als bij de bonnen. De rest blijft in de lijst, zodat wisselen van klant
    de lijst kan bijwerken zonder de pagina opnieuw te laden."""
    lijst = []
    for klus in klussenlijst():
        if klus["status"] == "aangevraagd":
            continue
        if factuur_id is not None:
            conn = get_db()
            eigen = conn.execute(
                """SELECT COUNT(*) FROM uren WHERE klus_id=? AND factuur_id=?""",
                (klus["id"], factuur_id),
            ).fetchone()[0]
            conn.close()
            if eigen:
                klus = dict(klus, uren_open=klus["uren"], bedrag_open=klus["bedrag"])
        if klus["uren_open"] > 0:
            # Zelfde vergelijking als de bonnen: zelfde klant, of allebei geen.
            klus["past"] = inkoop_past_bij_klant(klus["klant_id"], klant_id)
            # Vrije dagen, plus de dagen van déze rekening. Die laatste staan
            # al als regel op het formulier en beginnen verborgen; haal je de
            # regel weg vóór het opslaan, dan kun je de dag weer kiezen.
            klus["dagen"] = menu_dagen(klus, factuur_id)
            lijst.append(klus)
    return lijst


def geboekte_dagen(factuur_id=None, klant_id=None):
    """Gesloten dagen voor het menu, één knop per dag.

    Zelfde lijst als de bonnen: alles van deze klant, met de klusnaam op de
    regel. Je hoeft niet eerst een klus te kiezen. Een dag die al op déze
    rekening staat blijft erin, zodat hij terugkomt als je de regel weghaalt
    vóór het opslaan. De koppeling zelf blijft het uur van die dag."""
    uit = []
    for klus in geboekte_klussen(factuur_id, klant_id):
        for dag in klus["dagen"]:
            uit.append({
                **dag,
                "klus_id": klus["id"],
                "klus_naam": klus["naam"],
                "klant_id": klus["klant_id"],
                "past": klus["past"],
                "uurtarief": klus["uurtarief"] or 0,
            })
    # Op datum, en op één dag de vroegste begintijd eerst. Het id houdt twee
    # gelijke tijden stabiel. Bonnen houden hun eigen volgorde.
    uit.sort(key=lambda dag: (dag.get("datum") or "", dag.get("van") or "", dag["id"]))
    return uit


def menu_dagen(klus, factuur_id=None):
    """Gesloten dagen die in de keuzelijst mogen.

    Een dag op een andere rekening niet, en een dag zonder eindtijd niet.
    Een dag op déze rekening wel, zodat hij terugkomt als de regel weggaat."""
    if klus.get("status") == "aangevraagd":
        return []
    conn = get_db()
    try:
        return dagen_voor_keuze(conn, klus, factuur_id)
    finally:
        conn.close()


def dagen_voor_keuze(conn, klus, factuur_id=None):
    """Eén dict per dag die je vanuit het menu op de rekening kunt zetten."""
    if klus["status"] == "aangevraagd":
        return []
    # Een dag die al op deze rekening staat mag alleen terug in de lijst als
    # er een regel met dat uur_id is. Een oude rekening zette alle uren op één
    # regel zonder uur_id; die dagen nog eens aanbieden rekent ze dubbel.
    gekoppeld = []
    if factuur_id:
        gekoppeld = [
            rij["uur_id"] for rij in conn.execute(
                """SELECT uur_id FROM regels
                   WHERE factuur_id=? AND uur_id IS NOT NULL""",
                (factuur_id,),
            )
        ]
    if gekoppeld:
        plekken = ",".join("?" * len(gekoppeld))
        waar = f"AND (factuur_id IS NULL OR id IN ({plekken}))"
        params = (klus["id"], *gekoppeld)
    else:
        waar = "AND factuur_id IS NULL"
        params = (klus["id"],)
    rijen = conn.execute(
        f"""SELECT id, datum, van, tot, notitie, pauze_minuten, factuur_id
            FROM uren
            WHERE klus_id=? {waar} AND {_GESLOTEN_DAG}
            ORDER BY datum, van, id""",
        params,
    ).fetchall()
    prijs = float(klus["uurtarief"] or 0)
    uit = []
    for rij in rijen:
        minuten = factureerbare_minuten(rij["van"], rij["tot"], _pauze(rij))
        uren = uren_uit_minuten(minuten)
        if uren <= 0:
            continue
        uit.append({
            "id": rij["id"],
            "datum": rij["datum"],
            "van": (rij["van"] or "")[:5],
            "label": label_van_dag(rij["datum"], rij["van"], rij["tot"]),
            "omschrijving": omschrijving_van_dag(
                rij["datum"], rij["van"], rij["tot"], _pauze(rij),
                klus["naam"], rij["notitie"],
            ),
            "uren": uren,
            "minuten": minuten,
            "prijs": prijs,
        })
    return uit


def regels_van_open_dagen(conn, klus):
    """Eén dict per gesloten dag die nog niet op een rekening staat.

    Een lopende dag hoort hier niet in: die uren zijn er nog niet. Een dag
    zonder factureerbare uren (pauze zo lang als de kloktijd) ook niet."""
    if klus["status"] == "aangevraagd":
        return []
    rijen = conn.execute(
        f"""SELECT id, datum, van, tot, notitie, pauze_minuten FROM uren
            WHERE klus_id=? AND factuur_id IS NULL AND {_GESLOTEN_DAG}
            ORDER BY datum, id""",
        (klus["id"],),
    ).fetchall()
    uit = []
    prijs = float(klus["uurtarief"] or 0)
    for rij in rijen:
        minuten = factureerbare_minuten(rij["van"], rij["tot"], _pauze(rij))
        # Het aantal is om te lezen. Het bedrag komt uit de minuten, anders
        # wordt 7 uur 20 minuten 7,33 uur en scheelt het een cent.
        uren = uren_uit_minuten(minuten)
        if uren <= 0:
            continue
        uit.append({
            "omschrijving": omschrijving_van_dag(
                rij["datum"], rij["van"], rij["tot"], _pauze(rij),
                klus["naam"], rij["notitie"],
            ),
            "type": "arbeid_uur",
            "aantal": uren,
            "prijs": prijs,
            "subtotaal": bedrag_van_minuten(minuten, prijs),
            "klus_id": klus["id"],
            "inkoop_id": None,
            "uur_id": rij["id"],
            "uren": uren,
            "minuten": minuten,
            "id": rij["id"],
        })
    return uit


def met_minuten(conn, regels):
    """Bestaande regels, met de minuten van de dag erbij voor het scherm.

    `bewaard` zorgt dat het script het opgeslagen bedrag toont en niet
    opnieuw afrondt. Een nieuwe dagregel heeft die vlag niet."""
    uit = []
    for rij in regels:
        regel = dict(rij)
        regel["bewaard"] = True
        uur_id = regel.get("uur_id")
        if uur_id:
            dag = conn.execute(
                "SELECT van, tot, pauze_minuten FROM uren WHERE id=?",
                (uur_id,),
            ).fetchone()
            if dag is not None and eindtijd_ingevuld(dag["tot"]):
                regel["minuten"] = factureerbare_minuten(
                    dag["van"], dag["tot"], _pauze(dag))
        uit.append(regel)
    return uit


def klant_sleutel(klant_id):
    """Een klant-id als getal, of None als er geen klant is.

    Uit het formulier komt een tekst, uit de database een getal of NULL. Voor de
    vergelijking 'zelfde klant' moet dat hetzelfde betekenen."""
    if klant_id is None or klant_id == "":
        return None
    try:
        return int(klant_id)
    except (TypeError, ValueError):
        return None


def inkoop_past_bij_klant(klus_klant_id, rekening_klant_id):
    """Of een bon of een klus in de lijst bij deze rekening hoort.

    Alleen van dezelfde klant. Heeft de rekening nog geen klant, dan alleen van
    klussen die ook geen klant hebben — anders staan andermans uren en aankopen
    tussen de regels die je zo aanmaakt."""
    rekening = klant_sleutel(rekening_klant_id)
    klus = klant_sleutel(klus_klant_id)
    if rekening is None:
        return klus is None
    return klus == rekening


def geboekte_inkopen(factuur_id=None, klant_id=None):
    """Open bonnen (nog niet op een rekening, of al op déze rekening bij bewerken).

    Elke bon krijgt `past`: of hij bij de klant van deze rekening hoort. De rest
    blijft in de lijst staan, zodat wisselen van klant op het formulier de lijst
    kan bijwerken zonder de pagina opnieuw te laden."""
    conn = get_db()
    if factuur_id is None:
        rijen = conn.execute(
            """SELECT i.*, k.naam AS klus_naam, k.klant_id
               FROM inkopen i JOIN klussen k ON k.id = i.klus_id
               WHERE i.factuur_id IS NULL
               ORDER BY i.toegevoegd DESC, i.id DESC"""
        ).fetchall()
    else:
        rijen = conn.execute(
            """SELECT i.*, k.naam AS klus_naam, k.klant_id
               FROM inkopen i JOIN klussen k ON k.id = i.klus_id
               WHERE i.factuur_id IS NULL OR i.factuur_id=?
               ORDER BY i.toegevoegd DESC, i.id DESC""",
            (factuur_id,),
        ).fetchall()
    conn.close()
    lijst = []
    for rij in rijen:
        bon = dict(rij)
        bon["past"] = inkoop_past_bij_klant(bon["klant_id"], klant_id)
        lijst.append(bon)
    return lijst


def bepaal_klant(conn, form):
    """Geeft het klant-id terug dat bij deze rekening hoort. Is er geen klant gekozen
    maar wel gevraagd om op te slaan, dan wordt de klant hier aangemaakt."""
    gekozen = (form.get("klant_id") or "").strip()
    if gekozen.isdigit():
        bestaat = conn.execute("SELECT id FROM klanten WHERE id=?", (gekozen,)).fetchone()
        if bestaat:
            return int(gekozen)

    naam = form.get("klant_naam", "").strip()
    if form.get("klant_opslaan") == "ja" and naam:
        bestaand = conn.execute(
            "SELECT id FROM klanten WHERE naam=? COLLATE NOCASE", (naam,)
        ).fetchone()
        if bestaand:
            return bestaand["id"]
        return conn.execute(
            "INSERT INTO klanten (naam, adres, email) VALUES (?, ?, ?)",
            (naam, net_adres(form.get("klant_adres", "")), form.get("klant_email", "").strip()),
        ).lastrowid
    return None


def verborgen_uren_melding(conn, regels, factuur_id=None):
    """None als geen enkele nieuwe regel uren van een aanvraag pakt.

    Die dagen zie je op de klus niet. Een regel die al op déze rekening stond
    mag blijven: terugzetten naar een aanvraag maakt een rekening niet ongedaan.
    """
    al_eraan = set()
    if factuur_id is not None:
        al_eraan = {
            rij["klus_id"]
            for rij in conn.execute(
                """SELECT DISTINCT klus_id FROM regels
                   WHERE factuur_id=? AND klus_id IS NOT NULL""",
                (factuur_id,),
            )
        }
    for regel in regels:
        _o, _t, _a, _p, _sub, klus_id, _inkoop, _uur = regel[:8]
        if not klus_id or klus_id in al_eraan:
            continue
        klus = conn.execute(
            "SELECT status FROM klussen WHERE id=?", (klus_id,)
        ).fetchone()
        if klus is not None and klus["status"] == "aangevraagd":
            return ("Deze klus is nog een aanvraag. Start hem eerst, dan kun je "
                    "de dagen op de rekening zetten.")
    return None


def regels_passen_bij_klant(conn, regels, klant_id):
    """None als elke gekoppelde klus en bon bij deze klant hoort, anders een melding.

    Het menu verbergt andermans uren al. Een regel die er al stond, of een POST
    die het menu omzeilt, mag de uren of de bon niet alsnog op de verkeerde
    rekening zetten."""
    for regel in regels:
        _o, _t, _a, _p, _sub, klus_id, inkoop_id, _uur = regel[:8]
        if klus_id:
            klus = conn.execute(
                "SELECT klant_id FROM klussen WHERE id=?", (klus_id,)
            ).fetchone()
            if klus is None or not inkoop_past_bij_klant(klus["klant_id"], klant_id):
                return ("De uren op een regel horen bij een andere klant. Haal die "
                        "regel weg of kies de klant waarbij de klus hoort.")
        if inkoop_id:
            bon = conn.execute(
                """SELECT k.klant_id FROM inkopen i
                   JOIN klussen k ON k.id = i.klus_id WHERE i.id=?""",
                (inkoop_id,),
            ).fetchone()
            if bon is None or not inkoop_past_bij_klant(bon["klant_id"], klant_id):
                return ("Een bon op een regel hoort bij een andere klant. Haal die "
                        "regel weg of kies de klant waarbij de bon hoort.")
    return None


def begin_direct(conn):
    """Een transactie die meteen de schrijflock pakt.

    De module opent anders pas een transactie bij de eerste wijziging, en die
    is niet exclusief. Twee tabbladen lezen de dag dan allebei als vrij en
    zetten hem allebei op een rekening. Een mislukte tweede poging hoort de
    hele rekening terug te draaien, niet een factuur zonder regels te laten
    staan."""
    if conn.in_transaction:
        conn.commit()
    conn.isolation_level = None
    conn.execute("BEGIN IMMEDIATE")


def dagen_horen_bij_klant_en_klus(conn, regels, klant_id):
    """None als elke dagregel bij deze klant en deze klus hoort.

    Het menu zet dat zelf goed. Een POST die een uur_id van een andere klus
    meestuurt, mag die dag niet op deze rekening zetten."""
    for regel in regels:
        _o, _t, _a, _p, _sub, klus_id, _inkoop, uur_id = regel[:8]
        if not uur_id:
            continue
        dag = conn.execute(
            """SELECT u.klus_id, k.klant_id
               FROM uren u JOIN klussen k ON k.id = u.klus_id
               WHERE u.id=?""",
            (uur_id,),
        ).fetchone()
        if dag is None:
            return "Deze dag bestaat niet meer."
        if not inkoop_past_bij_klant(dag["klant_id"], klant_id):
            return "Deze dag hoort bij een andere klant."
        if not klus_id or dag["klus_id"] != klus_id:
            return "Deze dag hoort bij een andere klus."
    return None


def _nummer_van_rekening(conn, factuur_id):
    return zin_van_rekening(_rekening_rij(conn, factuur_id))


def zelfde_getal(a, b):
    """Of twee formulier- of databasegetallen hetzelfde zijn, zonder afronden."""
    try:
        return als_decimal(a) == als_decimal(b)
    except (ValueError, InvalidOperation):
        return False


def herstel_ongewijzigde_bedragen(conn, regels, tabel, parent_id):
    """Zet het opgeslagen subtotaal terug als soort, aantal en prijs gelijk zijn.

    Het formulier rondt 387,175 anders af naar 387,17 en na opslaan is 774,35
    ineens 774,34. Alleen een regel die niet is aangepast houdt dat bedrag,
    en dan het getal uit de database, niet de afgeronde tekst uit het formulier.
    Een ander aantal, een andere prijs of een andere soort rekent opnieuw."""
    if not parent_id or tabel not in ("regels", "offerte_regels"):
        return [regel[:8] for regel in regels]
    kolom = "factuur_id" if tabel == "regels" else "offerte_id"
    bestaand = conn.execute(
        f"SELECT * FROM {tabel} WHERE {kolom}=? ORDER BY id", (parent_id,)
    ).fetchall()
    per_id = {rij["id"]: rij for rij in bestaand}
    # Een formulier van voor dit veld heeft geen regel-id. Dan telt de
    # volgorde. Zodra er wél id's meegaan, mag een nieuwe regel niet het
    # bedrag van de regel op die plek overnemen.
    heeft_ids = any(len(regel) > 8 and regel[8] for regel in regels)
    gebruikt = set()
    uit = []
    for index, regel in enumerate(regels):
        o, t, aantal, prijs, subtotaal, klus_id, inkoop_id, uur_id = regel[:8]
        regel_id = regel[8] if len(regel) > 8 else None
        oud = None
        if regel_id and regel_id in per_id and regel_id not in gebruikt:
            oud = per_id[regel_id]
        elif not heeft_ids and index < len(bestaand) and bestaand[index]["id"] not in gebruikt:
            oud = bestaand[index]
        if (oud is not None and (oud["type"] or "") == (t or "")
                and zelfde_getal(oud["aantal"], aantal)
                and zelfde_getal(oud["prijs"], prijs)):
            subtotaal = oud["subtotaal"]
            gebruikt.add(oud["id"])
        uit.append((o, t, aantal, prijs, subtotaal, klus_id, inkoop_id, uur_id))
    return uit


def zonder_bezette_dagen(conn, regels, eigen_factuur_id):
    """Regels waarvan de dag al op een andere rekening staat, eruit.

    Het bedrag mag niet op deze rekening blijven staan als de dag er niet aan
    hangt: het jaaroverzicht telt de totalen op en rekent die dag dan dubbel."""
    houden = []
    meldingen = []
    gezien = set()
    for regel in regels:
        uur_id = regel[7]
        if not uur_id:
            houden.append(regel)
            continue
        dag = conn.execute(
            "SELECT factuur_id, datum FROM uren WHERE id=?", (uur_id,)
        ).fetchone()
        bezet = (
            dag is not None
            and dag["factuur_id"]
            and dag["factuur_id"] != eigen_factuur_id
        )
        if not bezet:
            houden.append(regel)
            continue
        titel = _nummer_van_rekening(conn, dag["factuur_id"])
        tekst = _tekst_dag_bezet(dag["datum"], titel)
        if tekst not in gezien:
            gezien.add(tekst)
            meldingen.append(tekst)
    return houden, meldingen


def zonder_bezette_bonnen(conn, regels, eigen_factuur_id):
    """Regels waarvan de bon al op een andere rekening staat, eruit.

    Zelfde reden als bij een dag: het totaal van het jaar telt beide
    rekeningen op, en dan reken je de bon twee keer."""
    houden = []
    meldingen = []
    gezien = set()
    for regel in regels:
        inkoop_id = regel[6]
        if not inkoop_id:
            houden.append(regel)
            continue
        bon = conn.execute(
            "SELECT factuur_id, omschrijving FROM inkopen WHERE id=?",
            (inkoop_id,),
        ).fetchone()
        bezet = (
            bon is not None
            and bon["factuur_id"]
            and bon["factuur_id"] != eigen_factuur_id
        )
        if not bezet:
            houden.append(regel)
            continue
        titel = _nummer_van_rekening(conn, bon["factuur_id"])
        naam = (bon["omschrijving"] or "Deze bon").strip()
        tekst = (
            f"De bon '{naam}' staat al op {titel}. "
            "Hij is niet nog een keer op deze rekening gezet."
        )
        if tekst not in gezien:
            gezien.add(tekst)
            meldingen.append(tekst)
    return houden, meldingen


AANTAL_VOLGT_DE_DAG = (
    "Het aantal uren volgt de dag en is niet aangepast. "
    "Een korting zet je op een aparte regel."
)


def _zelfde_centen(a, b):
    return rond_centen(a) == rond_centen(b)


def bedragen_uit_de_dag(conn, regels, eigen_factuur_id):
    """Nieuwe dagregels rekenen met de minuten. Een bestaande houdt zijn bedrag.

    Het aantal op de regel volgt de dag en is niet los te zetten: anders
    verdwijnt een uur van de rekening terwijl de dag wel gekoppeld blijft.
    Een ander tarief rekent wel opnieuw. Het oude bedrag laten staan als alleen
    het scherm opnieuw opent, anders schuift een rekening van 774,35 naar
    iets anders zodra je hem opslaat."""
    uit = []
    waarschuwing = False
    for o, t, aantal, prijs, subtotaal, klus_id, inkoop_id, uur_id in regels:
        if not uur_id:
            uit.append((o, t, aantal, prijs, subtotaal, klus_id, inkoop_id, uur_id))
            continue
        dag = conn.execute(
            """SELECT u.van, u.tot, u.pauze_minuten, u.factuur_id, u.datum,
                      u.notitie, k.naam AS klusnaam
               FROM uren u JOIN klussen k ON k.id = u.klus_id
               WHERE u.id=?""",
            (uur_id,),
        ).fetchone()
        if dag is None or not eindtijd_ingevuld(dag["tot"]):
            uit.append((o, t, aantal, prijs, subtotaal, klus_id, inkoop_id, uur_id))
            continue
        vers = omschrijving_van_dag(
            dag["datum"], dag["van"], dag["tot"], _pauze(dag),
            dag["klusnaam"], dag["notitie"],
        )
        # Een formulier dat openstond terwijl de dag nog anders was, houdt
        # anders de oude tijden in de tekst terwijl het bedrag de nieuwe is.
        # Een zelf toegevoegde zin blijft staan zolang de tijden kloppen.
        if not _tekst_hoort_bij_dag(o, dag):
            o = vers
        minuten = factureerbare_minuten(dag["van"], dag["tot"], _pauze(dag))
        aantal_dag = uren_uit_minuten(minuten)
        if abs(float(aantal) - float(aantal_dag)) > 0.001:
            waarschuwing = True
        bestaand = None
        if eigen_factuur_id and dag["factuur_id"] == eigen_factuur_id:
            bestaand = conn.execute(
                """SELECT prijs, subtotaal FROM regels
                   WHERE factuur_id=? AND uur_id=?
                   ORDER BY id LIMIT 1""",
                (eigen_factuur_id, uur_id),
            ).fetchone()
        if bestaand is not None and zelfde_getal(bestaand["prijs"], prijs):
            # Het opgeslagen getal, niet opnieuw via float naar centen.
            subtotaal_af = bestaand["subtotaal"]
        else:
            subtotaal_af = bedrag_van_minuten(minuten, prijs)
        uit.append((
            o, t, aantal_dag, float(prijs), subtotaal_af,
            klus_id, inkoop_id, uur_id,
        ))
    return uit, waarschuwing


def _tekst_hoort_bij_dag(tekst, dag):
    """Of de omschrijving nog de tijden en de pauze van deze dag noemt."""
    van = (dag["van"] or "")[:5]
    tot = (dag["tot"] or "")[:5]
    if not van or not tot or f"{van}–{tot}" not in (tekst or ""):
        return False
    pauze = _pauze(dag)
    if pauze:
        return f"(pauze {pauze} min)" in (tekst or "")
    return "(pauze " not in (tekst or "")


def regels_voor_opslaan(conn, form, klant_id, eigen_factuur_id=None):
    """Regels die op de rekening mogen, het totaal, meldingen, of een fout.

    Een fout weigert de hele opdracht. Staat een dag of bon al op een andere
    rekening, dan gaat de hele opslag niet door: anders blijft een
    materiaalregel uit hetzelfde formulier als losse rekening achter."""
    regels, _totaal, dag_dubbel, bon_dubbel = regels_uit_form(form)
    if not regels:
        return None, None, [], (
            "Vul minstens één regel in met een omschrijving; anders is er niets "
            "te factureren."
        )
    fout = regels_passen_bij_klant(conn, regels, klant_id)
    if fout is None:
        fout = verborgen_uren_melding(conn, regels, eigen_factuur_id)
    if fout is None:
        fout = dagen_horen_bij_klant_en_klus(conn, regels, klant_id)
    if fout:
        return None, None, [], fout
    regels, dag_bezet = zonder_bezette_dagen(conn, regels, eigen_factuur_id)
    regels, bon_bezet = zonder_bezette_bonnen(conn, regels, eigen_factuur_id)
    bezet = list(dag_bezet) + list(bon_bezet)
    if bezet:
        tekst = " ".join(bezet)
        if eigen_factuur_id:
            tekst += " Er is niets gewijzigd."
        else:
            tekst += " De rekening is niet aangemaakt."
        return None, None, bezet, tekst
    if not regels:
        return None, None, [], (
            "Vul minstens één regel in met een omschrijving; anders is er niets "
            "te factureren."
        )
    regels = herstel_ongewijzigde_bedragen(conn, regels, "regels", eigen_factuur_id)
    regels, aantal_waarschuwing = bedragen_uit_de_dag(conn, regels, eigen_factuur_id)
    meldingen = []
    if dag_dubbel:
        meldingen.append(DUBBELE_DAG)
    if bon_dubbel:
        meldingen.append(DUBBELE_BON)
    if aantal_waarschuwing:
        meldingen.append(AANTAL_VOLGT_DE_DAG)
    return regels, som_van(regels), meldingen, None


def _tekst_dag_bezet(datum, titel=None):
    """De weigering noemt de dag. 'Deze dag' zegt niets als er meerdere regels zijn."""
    wanneer = filter_datum_nl(datum) if datum else ""
    waar = titel or "een andere rekening"
    if wanneer:
        return (
            f"De dag van {wanneer} staat al op {waar}. "
            "Hij is niet nog een keer op deze rekening gezet."
        )
    return (
        f"Deze dag staat al op {waar}. "
        "Hij is niet nog een keer op deze rekening gezet."
    )


DAG_AL_ELDERS = _tekst_dag_bezet(None)

BON_AL_ELDERS = (
    "Deze bon staat al op een andere rekening. "
    "Hij is niet nog een keer op deze rekening gezet."
)


def _bezette_dag_uit_form(conn, form, eigen_factuur_id):
    """De eerste dag uit het formulier die al op een andere rekening staat."""
    if conn is None or form is None:
        return None
    for ruw in form.getlist("regel_uur"):
        if not str(ruw).isdigit():
            continue
        dag = conn.execute(
            "SELECT datum, factuur_id FROM uren WHERE id=?", (int(ruw),)
        ).fetchone()
        if dag and dag["factuur_id"] and dag["factuur_id"] != eigen_factuur_id:
            return dag
    return None


def _melding_bij_conflict(exc, conn=None, form=None, bestaand=False,
                          eigen_factuur_id=None):
    """Tekst als het opslaan botst op een dag of bon die net bezet raakte.

    De controle ervoor ziet het meestal al. Dit is de rest: twee schermen
    die op hetzelfde moment opslaan. De datum erbij, anders weet je niet
    welke dag uit het formulier het was."""
    if "inkoop_id" in str(exc):
        kern = BON_AL_ELDERS
    else:
        dag = _bezette_dag_uit_form(conn, form, eigen_factuur_id)
        if dag is not None:
            kern = _tekst_dag_bezet(
                dag["datum"], _nummer_van_rekening(conn, dag["factuur_id"]))
        else:
            kern = DAG_AL_ELDERS
    if bestaand:
        return kern + " Er is niets gewijzigd."
    return kern + " De rekening is niet aangemaakt."


def bewaar_regels(conn, factuur_id, regels):
    conn.execute("DELETE FROM regels WHERE factuur_id=?", (factuur_id,))
    for o, t, a, p, subtotaal, klus_id, inkoop_id, uur_id in regels:
        conn.execute(
            """INSERT INTO regels (factuur_id, omschrijving, type, aantal, prijs, subtotaal,
               klus_id, inkoop_id, uur_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (factuur_id, o, t, a, p, subtotaal, klus_id, inkoop_id, uur_id),
        )
    meldingen = boek_uren(conn, factuur_id, regels)
    boek_inkopen(conn, factuur_id, [r[6] for r in regels if r[6]])
    return meldingen


def _uren_tekst(waarde):
    return f"{filter_uren(waarde)} uur"


# Een lopende dag heeft geen uren. Zonder deze eis past een dag van 0 uur in
# elk aantal en belandt hij alsnog op de rekening.
_GESLOTEN_DAG = "tot IS NOT NULL AND TRIM(tot) <> ''"


def melding_lopende_dagen(conn, klus_id, naam):
    """Tekst als er nog een dag zonder eindtijd bij deze klus hoort, anders None."""
    lopend = conn.execute(
        f"""SELECT van FROM uren
            WHERE klus_id=? AND factuur_id IS NULL AND NOT ({_GESLOTEN_DAG})
            ORDER BY datum, van, id""",
        (klus_id,),
    ).fetchall()
    if not lopend:
        return None
    if len(lopend) == 1:
        return (
            f"Bij {naam} is nog een dag bezig, sinds {lopend[0]['van']}. "
            "Die staat niet op de rekening. Vul de eindtijd in als hij mee moet."
        )
    return (
        f"Bij {naam} zijn nog {len(lopend)} dagen bezig. "
        "Die staan niet op de rekening. Vul de eindtijd in als ze mee moeten."
    )


def _duur_dag(dag):
    return factureerbare_uren(dag["van"], dag["tot"], _pauze(dag))


def _koppel_expliciete_dag(conn, factuur_id, uur_id, subtotaal, gezien):
    """Koppel precies deze dag, of een melding als dat niet mag.

    Een regel mét uur_id wijst één dag aan. Opnieuw opslaan mag die niet
    verruilen voor een andere dag die toevallig evenveel uren heeft."""
    if uur_id in gezien:
        return ("Een gewerkte dag stond twee keer op de rekening. "
                "Hij is maar één keer gekoppeld.")
    gezien.add(uur_id)
    if float(subtotaal) <= 0.004:
        return ("Een dag is niet als gefactureerd gemarkeerd: het bedrag op "
                "de regel is nul. De dag blijft open.")
    dag = conn.execute(
        f"""SELECT id FROM uren
            WHERE id=? AND factuur_id IS NULL AND {_GESLOTEN_DAG}""",
        (uur_id,),
    ).fetchone()
    if dag is None:
        andere = conn.execute(
            "SELECT factuur_id FROM uren WHERE id=?", (uur_id,)
        ).fetchone()
        if andere is not None and andere["factuur_id"]:
            return ("Een dag staat al op een andere rekening en is hier "
                    "niet nog een keer gekoppeld.")
        return None
    conn.execute(
        "UPDATE uren SET factuur_id=? WHERE id=?", (factuur_id, uur_id)
    )
    return None


def boek_uren(conn, factuur_id, regels):
    """Markeert de dagen die bij de regels horen.

    Een regel met een uur_id koppelt die ene dag. Zonder uur_id — een oude
    rekening waar alle uren op één regel stonden — blijven hele dagen passen
    bij het aantal uren. Wat al op deze rekening stond blijft daar, ook als
    je tussendoor een oudere dag hebt afgesloten; anders worden al gefactureerde
    dagen weer open en kun je ze een tweede keer rekenen. Nieuwe dagen vullen
    daarna aan, oudste eerst. Een dag die niet meer in het resterende aantal
    past blijft open. Bij een bedrag van nul wordt niets gemarkeerd.

    Een dag zelf splitsen (4 uur van een dag van 8) kan hier niet. Een dag
    zonder eindtijd blijft altijd open."""
    # Dagen die al op deze rekening stonden. Bij een aanvraag mogen er geen
    # nieuwe bij; anders markeer je dagen die op de klus uit beeld zijn.
    eerder = {
        rij["id"]
        for rij in conn.execute("SELECT id FROM uren WHERE factuur_id=?", (factuur_id,))
    }
    conn.execute("UPDATE uren SET factuur_id=NULL WHERE factuur_id=?", (factuur_id,))
    per_klus = {}
    klus_ids = []
    gezien = set()
    meldingen = []
    dubbel_gezegd = False
    for _o, _t, aantal, _p, subtotaal, klus_id, _inkoop, uur_id in regels:
        if klus_id and klus_id not in klus_ids:
            klus_ids.append(klus_id)
        if uur_id:
            tekst = _koppel_expliciete_dag(
                conn, factuur_id, uur_id, subtotaal, gezien)
            if tekst and not (dubbel_gezegd and "twee keer" in tekst):
                if "twee keer" in tekst:
                    dubbel_gezegd = True
                meldingen.append(tekst)
            continue
        if not klus_id:
            continue
        emmer = per_klus.setdefault(klus_id, {"uren": 0.0, "bedrag": 0.0})
        emmer["uren"] += float(aantal)
        emmer["bedrag"] += float(subtotaal)

    for klus_id in klus_ids:
        klus = conn.execute(
            "SELECT naam, status FROM klussen WHERE id=?", (klus_id,)
        ).fetchone()
        # Op een aanvraag zie je de dagen niet. Daar hoort deze melding niet bij.
        if klus is None or klus["status"] == "aangevraagd":
            continue
        tekst = melding_lopende_dagen(conn, klus_id, klus["naam"])
        if tekst:
            meldingen.append(tekst)

    for klus_id, emmer in per_klus.items():
        klus = conn.execute(
            "SELECT naam, status FROM klussen WHERE id=?", (klus_id,)
        ).fetchone()
        naam = klus["naam"] if klus else "deze klus"
        if emmer["bedrag"] <= 0.004 or emmer["uren"] <= 0:
            meldingen.append(
                f"De uren van {naam} zijn niet als gefactureerd gemarkeerd: het "
                "bedrag op de regel is nul. De dagen blijven open."
            )
            continue
        if klus is not None and klus["status"] == "aangevraagd":
            if not eerder:
                continue
            plekken = ",".join("?" * len(eerder))
            dagen = conn.execute(
                f"""SELECT id, van, tot, pauze_minuten FROM uren
                    WHERE klus_id=? AND factuur_id IS NULL AND id IN ({plekken})
                      AND {_GESLOTEN_DAG}
                    ORDER BY datum, id""",
                (klus_id, *eerder),
            ).fetchall()
        else:
            # Eerst de dagen die hier al op stonden. Een net afgesloten oudere
            # dag zou anders hun plek innemen en de latere dagen loskoppelen.
            # Dagen die via een uur_id al gekoppeld zijn, zijn niet meer vrij.
            if eerder:
                plekken = ",".join("?" * len(eerder))
                dagen = conn.execute(
                    f"""SELECT id, van, tot, pauze_minuten FROM uren
                        WHERE klus_id=? AND factuur_id IS NULL AND {_GESLOTEN_DAG}
                        ORDER BY CASE WHEN id IN ({plekken}) THEN 0 ELSE 1 END,
                                 datum, id""",
                    (klus_id, *eerder),
                ).fetchall()
            else:
                dagen = conn.execute(
                    f"""SELECT id, van, tot, pauze_minuten FROM uren
                        WHERE klus_id=? AND factuur_id IS NULL AND {_GESLOTEN_DAG}
                        ORDER BY datum, id""",
                    (klus_id,),
                ).fetchall()
        dagen = [dag for dag in dagen if _duur_dag(dag) > 0]
        if not dagen:
            continue
        open_uren = sum(_duur_dag(dag) for dag in dagen)
        if emmer["uren"] + 0.01 >= open_uren:
            gekozen = list(dagen)
        else:
            gekozen = []
            geboekt = 0.0
            for dag in dagen:
                duur = _duur_dag(dag)
                if geboekt + duur <= emmer["uren"] + 0.01:
                    gekozen.append(dag)
                    geboekt += duur
        if not gekozen:
            meldingen.append(
                f"De uren van {naam} zijn niet als gefactureerd gemarkeerd: de "
                f"open dagen ({_uren_tekst(open_uren)}) passen niet in de "
                f"{_uren_tekst(emmer['uren'])} op de regel. Ze blijven open."
            )
            continue
        for dag in gekozen:
            conn.execute(
                "UPDATE uren SET factuur_id=? WHERE id=?", (factuur_id, dag["id"])
            )
        geboekt = sum(_duur_dag(dag) for dag in gekozen)
        if geboekt + 0.01 < open_uren:
            meldingen.append(
                f"Van {naam} staan {_uren_tekst(geboekt)} op deze rekening. "
                f"Er blijft {_uren_tekst(round(open_uren - geboekt, 2))} open."
            )
    return meldingen


def boek_inkopen(conn, factuur_id, inkoop_ids):
    """Legt vast welke bonnen op deze rekening staan, zodat dezelfde inkoop niet
    twee keer wordt gefactureerd."""
    conn.execute("UPDATE inkopen SET factuur_id=NULL WHERE factuur_id=?", (factuur_id,))
    for inkoop_id in inkoop_ids:
        conn.execute(
            """UPDATE inkopen SET factuur_id=?
               WHERE id=? AND (factuur_id IS NULL OR factuur_id=?)""",
            (factuur_id, inkoop_id, factuur_id),
        )


def _regels_zoals_ingevuld(form):
    """De regels uit het formulier, nog niet herberekend.

    Bij een geweigerde opslag moet het scherm terugkomen zoals het was
    ingevuld. Het subtotaal uit het formulier telt bij een geslaagde opslag
    niet mee; hier bewaren we alleen wat de gebruiker zag."""
    uit = []
    for o, t, a, p, klus, inkoop, uur, sub, minuten in zip_longest(
        form.getlist("omschrijving"),
        form.getlist("type"),
        form.getlist("aantal"),
        form.getlist("prijs"),
        form.getlist("regel_klus"),
        form.getlist("regel_inkoop"),
        form.getlist("regel_uur"),
        form.getlist("regel_subtotaal"),
        form.getlist("regel_minuten"),
        fillvalue="",
    ):
        if not str(o or "").strip():
            continue
        uit.append({
            "omschrijving": str(o),
            "type": t or "materiaal",
            "aantal": a if str(a).strip() else "1",
            "prijs": p,
            "subtotaal": sub,
            "klus_id": int(klus) if str(klus).isdigit() else None,
            "inkoop_id": int(inkoop) if str(inkoop).isdigit() else None,
            "uur_id": int(uur) if str(uur).isdigit() else None,
            "minuten": int(minuten) if str(minuten).isdigit() else None,
            # Open, zodat de velden ook zonder script te zien zijn. Met script
            # klappen ze weer in en blijft de samenvatting staan.
            "open": True,
        })
    return uit


def _dag_staat_elders(conn, uur_id, eigen_factuur_id):
    dag = conn.execute(
        "SELECT factuur_id FROM uren WHERE id=?", (uur_id,)
    ).fetchone()
    return bool(
        dag and dag["factuur_id"] and dag["factuur_id"] != eigen_factuur_id
    )


def _bon_staat_elders(conn, inkoop_id, eigen_factuur_id):
    bon = conn.execute(
        "SELECT factuur_id FROM inkopen WHERE id=?", (inkoop_id,)
    ).fetchone()
    return bool(
        bon and bon["factuur_id"] and bon["factuur_id"] != eigen_factuur_id
    )


def formulier_van_post(conn, form, eigen_factuur_id=None):
    """Wat er op het scherm terug moet na een geweigerde opslag.

    De geweigerde dag- en bonregels gaan eraf: die staan al op een andere
    rekening. Klant, datums en de overige regels blijven. Er wordt niets
    opgeslagen."""
    regels = []
    for regel in _regels_zoals_ingevuld(form):
        uur_id = regel.get("uur_id")
        inkoop_id = regel.get("inkoop_id")
        if uur_id and _dag_staat_elders(conn, uur_id, eigen_factuur_id):
            continue
        if inkoop_id and _bon_staat_elders(conn, inkoop_id, eigen_factuur_id):
            continue
        if uur_id and not regel.get("minuten"):
            dag = conn.execute(
                "SELECT van, tot, pauze_minuten FROM uren WHERE id=?", (uur_id,)
            ).fetchone()
            if dag is not None and eindtijd_ingevuld(dag["tot"]):
                regel["minuten"] = factureerbare_minuten(
                    dag["van"], dag["tot"], _pauze(dag))
        regels.append(regel)
    klant_id = (form.get("klant_id") or "").strip()
    return {
        "klant": {
            "id": int(klant_id) if klant_id.isdigit() else None,
            "naam": form.get("klant_naam", ""),
            "adres": form.get("klant_adres", ""),
            "email": form.get("klant_email", ""),
        },
        "datum": geldige_datum(form.get("datum")),
        "vervalt": (form.get("vervalt_op") or "") if form.get("betaaltermijn") == "ja" else "",
        "termijn_aan": form.get("betaaltermijn") == "ja",
        "kenmerk": form.get("kenmerk", ""),
        "betaalmethode": form.get("betaalmethode") or "bank",
        "regels": regels,
    }


def _render_nieuw_ingevuld(ingevuld):
    """Nieuwe rekening, met de gegevens die net geweigerd zijn nog ingevuld."""
    vandaag = ingevuld["datum"]
    gekozen = ingevuld["klant"]
    klant_id = gekozen.get("id")
    return render_template(
        "nieuw.html",
        vandaag=vandaag,
        actief="nieuw",
        factuur=None,
        regels=ingevuld["regels"],
        klanten=klantenlijst(),
        gekozen_klant=gekozen,
        open_dagen=geboekte_dagen(klant_id=klant_id),
        open_inkopen=geboekte_inkopen(klant_id=klant_id),
        vooraf_klus="",
        vervalt=ingevuld["vervalt"],
        standaard_vervalt=standaard_vervalt(vandaag),
        kenmerk=ingevuld["kenmerk"],
        betaalmethode=ingevuld["betaalmethode"],
        termijn_aan=ingevuld["termijn_aan"],
    )


def _factuur_zoals_ingevuld(factuur, ingevuld):
    """De opgeslagen rekening, met de velden die net geweigerd zijn eroverheen.

    Nummer en status blijven van de rekening. De rest is wat er in het
    formulier stond, anders verdwijnt een aangepaste naam bij de melding."""
    kopie = dict(factuur)
    klant = ingevuld["klant"]
    kopie["klant_id"] = klant.get("id")
    kopie["klant_naam"] = klant.get("naam") or ""
    kopie["klant_adres"] = klant.get("adres") or ""
    kopie["klant_email"] = klant.get("email") or ""
    kopie["datum"] = ingevuld["datum"]
    kopie["vervalt_op"] = ingevuld["vervalt"]
    kopie["kenmerk"] = ingevuld["kenmerk"]
    kopie["betaalmethode"] = ingevuld["betaalmethode"]
    return kopie


def _render_bewerk_ingevuld(factuur, ingevuld):
    """Bestaande rekening, met de geweigerde invoer nog op het scherm."""
    tonen = _factuur_zoals_ingevuld(factuur, ingevuld)
    return render_template(
        "nieuw.html",
        vandaag=ingevuld["datum"],
        actief="nieuw",
        factuur=tonen,
        regels=ingevuld["regels"],
        klanten=klantenlijst(),
        gekozen_klant=None,
        open_dagen=geboekte_dagen(factuur["id"], tonen["klant_id"]),
        open_inkopen=geboekte_inkopen(factuur["id"], tonen["klant_id"]),
        vooraf_klus="",
        vervalt=ingevuld["vervalt"],
        standaard_vervalt=standaard_vervalt(ingevuld["datum"]),
        kenmerk=ingevuld["kenmerk"],
        betaalmethode=ingevuld["betaalmethode"],
        termijn_aan=ingevuld["termijn_aan"],
    )


@app.route("/nieuw", methods=["GET", "POST"])
def nieuw():
    if request.method == "POST":
        conn = get_db()
        try:
            begin_direct(conn)
            klant_id = bepaal_klant(conn, request.form)
            regels, totaal, meldingen, fout = regels_voor_opslaan(
                conn, request.form, klant_id)
            if fout:
                ingevuld = formulier_van_post(conn, request.form)
                conn.rollback()
                conn.close()
                melding(fout, "fout")
                return _render_nieuw_ingevuld(ingevuld)
            datum = geldige_datum(request.form.get("datum"))

            # Nog geen nummer: dat komt pas als de rekening definitief wordt. Zo laat een
            # concept dat je weggooit geen gat achter in de reeks.
            cur = conn.execute(
                """INSERT INTO facturen (nummer, datum, vervalt_op, klant_id, klant_naam,
                   klant_adres, klant_email, betaalmethode, status, totaal, kenmerk)
                   VALUES ('', ?, ?, ?, ?, ?, ?, ?, 'concept', ?, ?)""",
                (
                    datum,
                    lees_vervaldatum(request.form, datum),
                    klant_id,
                    request.form.get("klant_naam", ""),
                    net_adres(request.form.get("klant_adres", "")),
                    request.form.get("klant_email", ""),
                    request.form.get("betaalmethode", "bank"),
                    totaal,
                    request.form.get("kenmerk", "").strip(),
                ),
            )
            factuur_id = cur.lastrowid
            uren_meldingen = bewaar_regels(conn, factuur_id, regels)
            conn.commit()
        except sqlite3.IntegrityError as exc:
            # Eerst terugdraaien: na een mislukte insert weigert SQLite elke
            # volgende leesopdracht tot de transactie weg is.
            conn.rollback()
            ingevuld = formulier_van_post(conn, request.form)
            tekst = _melding_bij_conflict(exc, conn, request.form)
            conn.close()
            melding(tekst, "fout")
            return _render_nieuw_ingevuld(ingevuld)
        conn.close()

        maak_pdf(factuur_id)

        # Ook als het formulier nog `verstuur=ja` meestuurt: opslaan mailt niet.
        # De mail gaat pas weg vanaf het controlescherm, met Mail versturen.
        melding("Rekening opgeslagen als concept. Hij krijgt zijn nummer zodra je "
              "hem verstuurt of definitief maakt.")
        for tekst in list(uren_meldingen) + list(meldingen):
            melding(tekst, "fout")

        return redirect(url_for("index"))

    # Vanuit een klantpagina kun je meteen een rekening voor die klant beginnen.
    vooraf = request.args.get("klant", "")
    vooraf_klus = request.args.get("klus", "")
    gekozen = None
    regels = []
    conn = get_db()
    if vooraf.isdigit():
        gekozen = conn.execute("SELECT * FROM klanten WHERE id=?", (vooraf,)).fetchone()
    if vooraf_klus.isdigit():
        klus = conn.execute(
            "SELECT * FROM klussen WHERE id=?", (int(vooraf_klus),)
        ).fetchone()
        if klus is not None:
            # De dagen staan al in het formulier. Het script mag ze niet nog
            # een keer toevoegen, anders staat elke dag er dubbel.
            regels = regels_van_open_dagen(conn, klus)
            if regels:
                vooraf_klus = ""
            if gekozen is None and klus["klant_id"]:
                gekozen = conn.execute(
                    "SELECT * FROM klanten WHERE id=?", (klus["klant_id"],)
                ).fetchone()
    conn.close()

    vandaag = nu().date().isoformat()
    return render_template(
        "nieuw.html", vandaag=vandaag, actief="nieuw",
        factuur=None, regels=regels, klanten=klantenlijst(), gekozen_klant=gekozen,
        open_dagen=geboekte_dagen(klant_id=gekozen["id"] if gekozen else None),
        open_inkopen=geboekte_inkopen(klant_id=gekozen["id"] if gekozen else None),
        vooraf_klus=vooraf_klus,
        vervalt=standaard_vervalt(vandaag),
        standaard_vervalt=standaard_vervalt(vandaag),
    )


@app.route("/factuur/<int:factuur_id>/bewerk", methods=["GET", "POST"])
def bewerk(factuur_id):
    conn = get_db()
    factuur = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    if factuur is None:
        conn.close()
        abort(404)

    if request.method == "POST":
        try:
            begin_direct(conn)
            klant_id = bepaal_klant(conn, request.form)
            regels, totaal, meldingen, fout = regels_voor_opslaan(
                conn, request.form, klant_id, factuur_id)
            if fout:
                ingevuld = formulier_van_post(conn, request.form, factuur_id)
                conn.rollback()
                conn.close()
                melding(fout, "fout")
                return _render_bewerk_ingevuld(factuur, ingevuld)
            datum = geldige_datum(request.form.get("datum"), factuur["datum"])
            conn.execute(
                """UPDATE facturen SET datum=?, vervalt_op=?, klant_id=?, klant_naam=?,
                   klant_adres=?, klant_email=?, betaalmethode=?, totaal=?, kenmerk=?
                   WHERE id=?""",
                (
                    datum,
                    lees_vervaldatum(request.form, datum),
                    klant_id,
                    request.form.get("klant_naam", ""),
                    net_adres(request.form.get("klant_adres", "")),
                    request.form.get("klant_email", ""),
                    request.form.get("betaalmethode", "bank"),
                    totaal,
                    request.form.get("kenmerk", "").strip(),
                    factuur_id,
                ),
            )
            uren_meldingen = bewaar_regels(conn, factuur_id, regels)
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            ingevuld = formulier_van_post(conn, request.form, factuur_id)
            tekst = _melding_bij_conflict(
                exc, conn, request.form, bestaand=True,
                eigen_factuur_id=factuur_id)
            conn.close()
            melding(tekst, "fout")
            return _render_bewerk_ingevuld(factuur, ingevuld)
        conn.close()

        # De PDF hoort bij de oude gegevens, dus opnieuw tekenen.
        maak_pdf(factuur_id)
        # Bewerken mailt niet mee, ook niet als `verstuur=ja` in het formulier staat.
        # Mailen is een aparte stap, na de controle van wat er de deur uitgaat.
        melding(f"Rekening {factuurnaam(factuur)} bijgewerkt.")
        for tekst in list(uren_meldingen) + list(meldingen):
            melding(tekst, "fout")

        return redirect(url_for("index"))

    regels = met_minuten(conn, conn.execute(
        "SELECT * FROM regels WHERE factuur_id=? ORDER BY id", (factuur_id,)
    ).fetchall())
    waarschuwing = conn.execute(
        "SELECT tekst FROM rekening_waarschuwing WHERE factuur_id=?",
        (factuur_id,),
    ).fetchone()
    conn.close()
    return render_template(
        "nieuw.html", vandaag=factuur["datum"], actief="nieuw",
        factuur=factuur, regels=regels, klanten=klantenlijst(),
        dagwaarschuwing=waarschuwing["tekst"] if waarschuwing else "",
        gekozen_klant=None, open_dagen=geboekte_dagen(factuur_id, factuur["klant_id"]),
        open_inkopen=geboekte_inkopen(factuur_id, factuur["klant_id"]),
        vooraf_klus="",
        vervalt=factuur["vervalt_op"] or "",
        standaard_vervalt=standaard_vervalt(factuur["datum"]),
    )


OFFERTE_STATUS = {
    "concept": "Concept",
    "verzonden": "Verzonden",
    "geaccepteerd": "Geaccepteerd",
    "afgewezen": "Afgewezen",
}

app.jinja_env.globals["offerte_status"] = OFFERTE_STATUS


def offerte_of_404(conn, offerte_id):
    rij = conn.execute("SELECT * FROM offertes WHERE id=?", (offerte_id,)).fetchone()
    if rij is None:
        conn.close()
        abort(404)
    return rij


def lees_geldigheid(form, datum):
    """Tot wanneer de offerte geldt, of een lege tekst als je er geen einddatum bij
    wilt. Het vinkje bepaalt dat; staat het aan zonder datum, dan pakken we de
    standaardtermijn."""
    if form.get("geldigheid") != "ja":
        return ""
    return geldige_datum(form.get("geldig_tot"), geldig_tot(datum))


def lees_vervaldatum(form, datum):
    """Tot wanneer de rekening betaald moet zijn, of leeg als je er geen termijn bij
    wilt. Het vinkje bepaalt dat; staat het aan zonder datum, dan pakken we de
    standaardtermijn."""
    if form.get("betaaltermijn") != "ja":
        return ""
    return geldige_datum(form.get("vervalt_op"), standaard_vervalt(datum))


def bewaar_offerte_regels(conn, offerte_id, regels):
    conn.execute("DELETE FROM offerte_regels WHERE offerte_id=?", (offerte_id,))
    for o, t, a, p, subtotaal, _klus_id, inkoop_id, _uur_id in (
        regel[:8] for regel in regels
    ):
        conn.execute(
            """INSERT INTO offerte_regels (offerte_id, omschrijving, type, aantal, prijs,
               subtotaal, inkoop_id) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (offerte_id, o, t, a, p, subtotaal, inkoop_id),
        )


@app.route("/offertes")
def offertes():
    conn = get_db()
    lijst = conn.execute("SELECT * FROM offertes ORDER BY datum DESC, id DESC").fetchall()
    conn.close()

    vandaag = nu().date().isoformat()
    open_offertes = [o for o in lijst
                     if o["status"] in ("concept", "verzonden") and not o["factuur_id"]]
    overzicht = {
        "open_bedrag": sum(o["totaal"] for o in open_offertes),
        "open_aantal": len(open_offertes),
        "geaccepteerd": sum(o["totaal"] for o in lijst if o["status"] == "geaccepteerd"),
        "verlopen": sum(1 for o in open_offertes
                        if o["geldig_tot"] and o["geldig_tot"] < vandaag),
    }

    keuze = request.args.get("status", "alles")
    if keuze in OFFERTE_STATUS:
        zichtbaar = [o for o in lijst if o["status"] == keuze]
    elif keuze == "verlopen":
        zichtbaar = [o for o in open_offertes
                     if o["geldig_tot"] and o["geldig_tot"] < vandaag]
    else:
        keuze, zichtbaar = "alles", lijst

    zoek = request.args.get("q", "").strip()
    if zoek:
        zichtbaar = zoek_in(zichtbaar, zoek, ["nummer", "klant_naam", "klant_email", "kenmerk"])

    return render_template("offertes.html", offertes=zichtbaar, overzicht=overzicht,
                           keuze=keuze, zoek=zoek, totaal_aantal=len(lijst),
                           vandaag=vandaag, actief="offertes")


@app.route("/offertes/nieuw", methods=["GET", "POST"])
def offerte_nieuw():
    if request.method == "POST":
        regels, totaal = lees_regels(request.form)
        if not regels:
            melding("Vul minstens één regel in met een omschrijving; anders staat er "
                  "niets in de offerte.", "fout")
            return redirect(url_for("offerte_nieuw"))

        conn = get_db()
        nummer = volgend_offertenummer(conn)
        klant_id = bepaal_klant(conn, request.form)
        past_niet = regels_passen_bij_klant(conn, regels, klant_id)
        if past_niet:
            conn.close()
            melding(past_niet, "fout")
            return redirect(url_for("offerte_nieuw"))
        datum = geldige_datum(request.form.get("datum"))

        cur = conn.execute(
            """INSERT INTO offertes (nummer, datum, geldig_tot, klant_id, klant_naam,
               klant_adres, klant_email, status, totaal, toelichting, kenmerk)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'concept', ?, ?, ?)""",
            (
                nummer,
                datum,
                lees_geldigheid(request.form, datum),
                klant_id,
                request.form.get("klant_naam", ""),
                net_adres(request.form.get("klant_adres", "")),
                request.form.get("klant_email", ""),
                totaal,
                request.form.get("toelichting", "").strip(),
                request.form.get("kenmerk", "").strip(),
            ),
        )
        offerte_id = cur.lastrowid
        bewaar_offerte_regels(conn, offerte_id, regels)
        # Kwam je hier vanaf een aanvraag, dan hoort de offerte daaraan vast: op de
        # klus zie je dan dat die stap is gedaan, en met welke offerte.
        vanaf_klus = (request.form.get("voor_klus") or "").strip()
        if vanaf_klus.isdigit():
            conn.execute("UPDATE klussen SET offerte_id=? WHERE id=?",
                         (offerte_id, int(vanaf_klus)))
        conn.commit()
        conn.close()

        maak_offerte_pdf(offerte_id)
        melding(f"Offerte {nummer} aangemaakt.")
        return redirect(url_for("offertes"))

    vooraf = request.args.get("klant", "")
    gekozen = None
    if vooraf.isdigit():
        conn = get_db()
        gekozen = conn.execute("SELECT * FROM klanten WHERE id=?", (vooraf,)).fetchone()
        conn.close()

    vandaag = nu().date().isoformat()
    return render_template(
        "nieuw.html", mode="offerte", vandaag=vandaag, actief="offertes",
        factuur=None, regels=[], klanten=klantenlijst(), gekozen_klant=gekozen,
        open_dagen=[], open_inkopen=[], vooraf_klus="", geldig=geldig_tot(vandaag),
        standaard_geldig=geldig_tot(vandaag),
        # Kwam je hier vanaf een aanvraag, dan gaat de offerte daar straks aan vast.
        voor_klus=request.args.get("voor_klus", ""),
    )


@app.route("/offerte/<int:offerte_id>/bewerk", methods=["GET", "POST"])
def offerte_bewerk(offerte_id):
    conn = get_db()
    offerte = offerte_of_404(conn, offerte_id)

    if request.method == "POST":
        regels, totaal = lees_regels(request.form)
        if not regels:
            conn.close()
            melding("Vul minstens één regel in met een omschrijving; anders staat er "
                  "niets in de offerte.", "fout")
            return redirect(url_for("offerte_bewerk", offerte_id=offerte_id))
        regels = herstel_ongewijzigde_bedragen(
            conn, regels, "offerte_regels", offerte_id)
        totaal = som_van(regels)

        klant_id = bepaal_klant(conn, request.form)
        past_niet = regels_passen_bij_klant(conn, regels, klant_id)
        if past_niet:
            conn.close()
            melding(past_niet, "fout")
            return redirect(url_for("offerte_bewerk", offerte_id=offerte_id))
        datum = geldige_datum(request.form.get("datum"), offerte["datum"])
        conn.execute(
            """UPDATE offertes SET datum=?, geldig_tot=?, klant_id=?, klant_naam=?,
               klant_adres=?, klant_email=?, totaal=?, toelichting=?, kenmerk=?
               WHERE id=?""",
            (
                datum,
                lees_geldigheid(request.form, datum),
                klant_id,
                request.form.get("klant_naam", ""),
                net_adres(request.form.get("klant_adres", "")),
                request.form.get("klant_email", ""),
                totaal,
                request.form.get("toelichting", "").strip(),
                request.form.get("kenmerk", "").strip(),
                offerte_id,
            ),
        )
        bewaar_offerte_regels(conn, offerte_id, regels)
        conn.commit()
        conn.close()

        maak_offerte_pdf(offerte_id)
        melding(f"Offerte {offerte['nummer']} bijgewerkt.")
        return redirect(url_for("offertes"))

    regels = conn.execute(
        "SELECT * FROM offerte_regels WHERE offerte_id=? ORDER BY id", (offerte_id,)
    ).fetchall()
    conn.close()
    return render_template("nieuw.html", mode="offerte", vandaag=offerte["datum"],
                           actief="offertes", factuur=offerte, regels=regels,
                           klanten=klantenlijst(), gekozen_klant=None, open_dagen=[],
                           open_inkopen=[], vooraf_klus="",
                           geldig=offerte["geldig_tot"],
                           standaard_geldig=geldig_tot(offerte["datum"]))


def _offerte_pdf_pad(offerte_id):
    conn = get_db()
    offerte = offerte_of_404(conn, offerte_id)
    conn.close()
    pad = os.path.join(PDF_DIR, f"{offerte['nummer']}.pdf")
    if not os.path.exists(pad):
        maak_offerte_pdf(offerte_id)
    return pad, offerte["nummer"]


@app.route("/offerte/<int:offerte_id>/bekijk")
def bekijk_offerte(offerte_id):
    try:
        pad, nummer = _offerte_pdf_pad(offerte_id)
    except NotFound:
        return niet_gevonden()
    return toon_bestand(pad, "application/pdf", f"{nummer}.pdf")


@app.route("/offerte/<int:offerte_id>/pdf")
def download_offerte(offerte_id):
    try:
        pad, nummer = _offerte_pdf_pad(offerte_id)
    except NotFound:
        return niet_gevonden()
    return zet_bestandskoppen(
        send_file(
            pad, as_attachment=True,
            download_name=veilige_downloadnaam(f"{nummer}.pdf"),
        )
    )


@app.route("/offerte/<int:offerte_id>/vernieuw", methods=["POST"])
def vernieuw_offerte(offerte_id):
    conn = get_db()
    offerte = offerte_of_404(conn, offerte_id)
    conn.close()
    maak_offerte_pdf(offerte_id)
    melding(f"Offerte {offerte['nummer']} is opnieuw gemaakt met je huidige instellingen.")
    return redirect(terug_naar(url_for("offertes")))


@app.route("/offerte/<int:offerte_id>/mail")
def offerte_mail(offerte_id):
    """Hetzelfde controlescherm als bij een rekening, vóór de offerte de deur uitgaat."""
    conn = get_db()
    offerte = offerte_of_404(conn, offerte_id)
    conn.close()
    pdf_pad = maak_offerte_pdf(offerte_id)
    return render_template(
        "mailen.html",
        wie=offerte["klant_naam"],
        inhoud=mailinhoud_offerte(offerte),
        pdf_url=url_for("bekijk_offerte", offerte_id=offerte_id),
        document_grootte=formaat_grootte(pdf_pad),
        concept=False,
        voorlopig_nummer=False,
        al_verzonden=offerte["status"] != "concept",
        reden=waarom_mailen_niet_kan("offertes", offerte_id),
        form_action=url_for("verstuur_offerte", offerte_id=offerte_id),
        terug_url=url_for("offertes"),
        terug_label="Terug naar offertes",
        document_label="PDF van de offerte",
        bonnen=[],
        mail_teken=nieuw_mailteken("offertes", offerte_id),
        actief="offertes",
    )


@app.route("/offerte/<int:offerte_id>/verstuur", methods=["POST"])
def verstuur_offerte(offerte_id):
    mail_offerte(offerte_id, request.form.get("eigen_zin", ""), na=url_for("offertes"))
    return redirect(terug_naar(url_for("offertes")))


@app.route("/offerte/<int:offerte_id>/status", methods=["POST"])
def offerte_status(offerte_id):
    """Vastleggen wat de klant ervan vond: geaccepteerd, afgewezen, of toch weer open."""
    nieuw_status = request.form.get("status", "")
    if nieuw_status not in OFFERTE_STATUS:
        abort(400, "Onbekende status voor een offerte.")
    conn = get_db()
    offerte = offerte_of_404(conn, offerte_id)
    conn.execute("UPDATE offertes SET status=? WHERE id=?", (nieuw_status, offerte_id))
    conn.commit()
    conn.close()
    melding(f"Offerte {offerte['nummer']}: {OFFERTE_STATUS[nieuw_status].lower()}.")
    return redirect(terug_naar(url_for("offertes")))


def al_gefactureerd_van_offerte(conn, offerte_id):
    """Wat er al op rekeningen staat die aan deze offerte hangen."""
    som = conn.execute(
        "SELECT COALESCE(SUM(totaal), 0) FROM facturen WHERE offerte_id=?",
        (offerte_id,),
    ).fetchone()[0]
    return round(som, 2)


@app.route("/offerte/<int:offerte_id>/naar-rekening", methods=["POST"])
def offerte_naar_rekening(offerte_id):
    """Maakt van een offerte een concept-rekening voor wat er nog openstaat.

    Zonder eerdere aanbetaling zijn dat de regels van de offerte, inclusief de
    bonnen: die worden dan pas als gefactureerd gemarkeerd. Is er al een deel
    gerekend, dan komt er een regel 'reeds aanbetaald' bij zodat het totaal het
    restant is en niet nog een keer het volle bedrag."""
    conn = get_db()
    offerte = offerte_of_404(conn, offerte_id)
    if offerte["factuur_id"]:
        bestaat = conn.execute(
            "SELECT id FROM facturen WHERE id=?", (offerte["factuur_id"],)
        ).fetchone()
        if bestaat:
            conn.close()
            melding(f"Offerte {offerte['nummer']} is al omgezet naar een rekening.", "fout")
            return redirect(url_for("bewerk", factuur_id=offerte["factuur_id"]))

    bron = conn.execute(
        "SELECT * FROM offerte_regels WHERE offerte_id=? ORDER BY id", (offerte_id,)
    ).fetchall()
    reeds = al_gefactureerd_van_offerte(conn, offerte_id)
    lijn_som = round(sum(r["subtotaal"] for r in bron), 2)
    rest = round(lijn_som - reeds, 2)
    if rest <= 0.005:
        conn.close()
        melding(f"Van offerte {offerte['nummer']} staat al het hele bedrag op een "
                "rekening. Er is geen restant meer.", "fout")
        return redirect(url_for("offertes"))

    nieuwe_datum = vandaag().isoformat()
    cur = conn.execute(
        """INSERT INTO facturen (nummer, datum, vervalt_op, klant_id, klant_naam,
           klant_adres, klant_email, betaalmethode, status, totaal, kenmerk, offerte_id)
           VALUES ('', ?, ?, ?, ?, ?, ?, 'bank', 'concept', ?, ?, ?)""",
        (
            nieuwe_datum,
            standaard_vervalt(nieuwe_datum),
            offerte["klant_id"],
            offerte["klant_naam"],
            offerte["klant_adres"],
            offerte["klant_email"],
            rest,
            offerte["kenmerk"],
            offerte_id,
        ),
    )
    factuur_id = cur.lastrowid
    regels = []
    for r in bron:
        inkoop_id = r["inkoop_id"] if "inkoop_id" in r.keys() else None
        regels.append((
            r["omschrijving"], r["type"], r["aantal"], r["prijs"], r["subtotaal"],
            None, inkoop_id, None,
        ))
    if reeds > 0.004:
        regels.append((
            f"Reeds aanbetaald op offerte {offerte['nummer']}",
            "arbeid_klus", 1, -reeds, -reeds, None, None, None,
        ))
    bewaar_regels(conn, factuur_id, regels)
    conn.execute(
        "UPDATE offertes SET factuur_id=?, status='geaccepteerd' WHERE id=?",
        (factuur_id, offerte_id),
    )
    conn.commit()
    conn.close()

    maak_pdf(factuur_id)
    if reeds > 0.004:
        melding(f"Offerte {offerte['nummer']} staat als concept klaar voor het restant "
                f"van € {nl_bedrag(rest)}. De eerdere aanbetaling is eraf gehaald. "
                "Controleer hem voordat je hem verstuurt.")
    else:
        melding(f"Offerte {offerte['nummer']} staat nu als concept-rekening klaar. "
                "Controleer hem en verstuur hem als hij klopt; dan krijgt hij zijn nummer.")
    return redirect(url_for("bewerk", factuur_id=factuur_id))


@app.route("/offerte/<int:offerte_id>/aanbetaling", methods=["GET", "POST"])
def offerte_aanbetaling(offerte_id):
    """Maakt een rekening voor een deel van de offerte, bijvoorbeeld dertig procent
    vooraf. De offerte blijft gewoon staan, zodat je de rest later kunt factureren."""
    conn = get_db()
    offerte = offerte_of_404(conn, offerte_id)

    reeds = al_gefactureerd_van_offerte(conn, offerte_id)
    openstaand = round(offerte["totaal"] - reeds, 2)
    if request.method != "POST":
        conn.close()
        return render_template(
            "aanbetaling.html", offerte=offerte, actief="offertes",
            reeds=reeds, openstaand=max(openstaand, 0),
        )

    try:
        deel = _getal(request.form.get("percentage"))
    except ValueError:
        deel = 0.0
    if not 0 < deel <= 100:
        conn.close()
        melding("Vul een percentage in tussen 1 en 100.", "fout")
        return redirect(url_for("offerte_aanbetaling", offerte_id=offerte_id))

    bedrag = round(offerte["totaal"] * deel / 100, 2)
    if bedrag > openstaand + 0.005:
        conn.close()
        melding(f"Samen met eerdere rekeningen is dat meer dan de offerte. "
                f"Er staat nog € {nl_bedrag(max(openstaand, 0))} open.", "fout")
        return redirect(url_for("offerte_aanbetaling", offerte_id=offerte_id))

    omschrijving = (request.form.get("omschrijving", "").strip()
                    or f"Aanbetaling {deel:g}% van offerte {offerte['nummer']}")

    nieuwe_datum = vandaag().isoformat()
    cur = conn.execute(
        """INSERT INTO facturen (nummer, datum, vervalt_op, klant_id, klant_naam,
           klant_adres, klant_email, betaalmethode, status, totaal, kenmerk, offerte_id)
           VALUES ('', ?, ?, ?, ?, ?, ?, 'bank', 'concept', ?, ?, ?)""",
        (
            nieuwe_datum,
            standaard_vervalt(nieuwe_datum),
            offerte["klant_id"],
            offerte["klant_naam"],
            offerte["klant_adres"],
            offerte["klant_email"],
            bedrag,
            offerte["kenmerk"],
            offerte_id,
        ),
    )
    factuur_id = cur.lastrowid
    conn.execute(
        """INSERT INTO regels (factuur_id, omschrijving, type, aantal, prijs, subtotaal)
           VALUES (?, ?, 'arbeid_klus', 1, ?, ?)""",
        (factuur_id, omschrijving, bedrag, bedrag),
    )
    conn.commit()
    conn.close()

    maak_pdf(factuur_id)
    melding(f"Aanbetaling van € {nl_bedrag(bedrag)} staat als concept-rekening klaar. "
          "De offerte blijft staan, zodat je de rest later kunt factureren.")
    return redirect(url_for("bewerk", factuur_id=factuur_id))


@app.route("/offerte/<int:offerte_id>/verwijder", methods=["POST"])
def offerte_verwijder(offerte_id):
    conn = get_db()
    offerte = offerte_of_404(conn, offerte_id)
    prullenbak_id = naar_prullenbak(conn, f"Offerte {offerte['nummer']}", {
        "offertes": [offerte],
        "offerte_regels": conn.execute(
            "SELECT * FROM offerte_regels WHERE offerte_id=?", (offerte_id,)).fetchall(),
    })
    conn.execute("DELETE FROM offerte_regels WHERE offerte_id=?", (offerte_id,))
    conn.execute("DELETE FROM offertes WHERE id=?", (offerte_id,))
    conn.commit()
    conn.close()

    # De PDF mag weg: die wordt bij terughalen vanzelf opnieuw getekend.
    pdf = os.path.join(PDF_DIR, f"{offerte['nummer']}.pdf")
    if os.path.exists(pdf):
        os.remove(pdf)

    melding(f"Offerte {offerte['nummer']} verwijderd.", knop=terugknop(prullenbak_id))
    return redirect(url_for("offertes"))


def _regels_afbreken(c, tekst, font, grootte, maxbreedte):
    """Knipt een lap tekst op woordgrenzen in regels die binnen de breedte passen."""
    if not tekst:
        return [""]
    regels = []
    huidig = ""
    for woord in tekst.split():
        proef = f"{huidig} {woord}".strip()
        if c.stringWidth(proef, font, grootte) <= maxbreedte or not huidig:
            huidig = proef
        else:
            regels.append(huidig)
            huidig = woord
    regels.append(huidig)
    return regels


def maak_pdf(factuur_id, weergavenummer=None, pad=None):
    """Tekent de rekening en geeft het pad naar de PDF terug.

    `weergavenummer` zet een nummer op het vel zonder het op de rekening op te
    slaan. Het mailvoorbeeld van een concept gebruikt dat, zodat je dezelfde PDF
    ziet als de klant straks krijgt. `pad` is waar het bestand komt; zonder pad
    de gewone naam in de PDF-map."""
    conn = get_db()
    factuur = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    regels = conn.execute("SELECT * FROM regels WHERE factuur_id=?", (factuur_id,)).fetchall()
    # Is er al iets binnen, dan hoort de strook te zeggen wat er nog moet komen. Anders
    # maakt een klant die de helft heeft betaald na een herinnering het hele bedrag over.
    betaald = betaald_op(conn, factuur_id)
    s = get_settings()
    conn.close()

    doc = dict(factuur)
    if weergavenummer is not None:
        doc["nummer"] = weergavenummer
    doc["soort"] = "factuur"
    doc["weergavenummer"] = doc["nummer"] or "CONCEPT"
    doc["betaald"] = betaald
    doel = pad or os.path.join(PDF_DIR, pdf_bestandsnaam(doc))
    return _teken_document(doel, doc, regels, s)


def maak_offerte_pdf(offerte_id):
    """Tekent de offerte. Zelfde vel als een rekening, maar zonder de betaalstrook
    onderaan: er valt nog niets te betalen."""
    conn = get_db()
    offerte = conn.execute("SELECT * FROM offertes WHERE id=?", (offerte_id,)).fetchone()
    regels = conn.execute(
        "SELECT * FROM offerte_regels WHERE offerte_id=? ORDER BY id", (offerte_id,)
    ).fetchall()
    s = get_settings()
    conn.close()

    doc = dict(offerte)
    doc["soort"] = "offerte"
    return _teken_document(os.path.join(PDF_DIR, f"{offerte['nummer']}.pdf"), doc, regels, s)


class GenummerdCanvas(canvas.Canvas):
    """Zet 'Pagina 1 van 5' erop als het aantal pagina's bekend is.

    Dat is pas bij het opslaan. De pagina's worden daarom bewaard en daarna
    pas echt geschreven. De lijst moet vóór de lus gekopieerd: het terugzetten
    van een pagina zet ook die lijst terug, en anders raak je de rest kwijt."""

    def __init__(self, *args, **kwargs):
        canvas.Canvas.__init__(self, *args, **kwargs)
        self._paginas = []

    def showPage(self):
        self._paginas.append(dict(self.__dict__))
        self._startPage()

    def _teken_paginanummer(self, nummer, totaal):
        self.setFillColor(GRIJS)
        self.setFont("Helvetica", 8)
        self.drawRightString(
            self._pagesize[0] - 18 * mm, 9 * mm,
            f"Pagina {nummer} van {totaal}",
        )

    def save(self):
        self._paginas.append(dict(self.__dict__))
        paginas = list(self._paginas)
        totaal = len(paginas)
        for nummer, staat in enumerate(paginas, start=1):
            self.__dict__.update(staat)
            self._teken_paginanummer(nummer, totaal)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)


class Vel:
    """Eén vel papier met de opmaak van een rekening of offerte.

    Kent de marges, de kolommen en de kleine hulpjes die overal terugkomen:
    de oranje bies, kleine grijze kapitalen als kopje, en tekst die netjes wordt
    afgekapt in plaats van over de volgende kolom heen te lopen. De losse
    onderdelen van het vel staan hieronder als aparte teken-functies."""

    def __init__(self, pad, titel, nummer):
        self.pad = pad
        self.titel = titel
        self.nummer = nummer
        self.c = GenummerdCanvas(pad, pagesize=A4)
        self.c.setTitle(f"{titel} {nummer}")
        self.breedte, self.hoogte = A4
        self.links = 26 * mm
        self.rechts = self.breedte - 18 * mm
        self.kolom_aantal = self.rechts - 62 * mm
        self.kolom_prijs = self.rechts - 32 * mm
        self.y = self.hoogte - 22 * mm
        self._bies()

    def _bies(self):
        """De oranje bies langs de linkerkant, op elke pagina."""
        self.c.setFillColor(ORANJE)
        self.c.rect(0, 0, 6 * mm, self.hoogte, stroke=0, fill=1)

    def kort(self, tekst, font, grootte, maxbreedte):
        """Kapt tekst af met een beletselteken zodra hij niet meer past."""
        tekst = tekst or ""
        if self.c.stringWidth(tekst, font, grootte) <= maxbreedte:
            return tekst
        while tekst and self.c.stringWidth(tekst + "…", font, grootte) > maxbreedte:
            tekst = tekst[:-1]
        return tekst + "…"

    def label(self, tekst, x, y, rechts_uit=False):
        """Kleine grijze kapitalen met ruime letterafstand."""
        self.c.setFont("Helvetica-Bold", 6.5)
        self.c.setFillColor(GRIJS)
        letters = " ".join(tekst.upper())
        (self.c.drawRightString if rechts_uit else self.c.drawString)(x, y, letters)

    def nieuwe_pagina(self, vervolg=False):
        """Begint een nieuw vel. Met vervolg=True komt er bovenaan te staan waar je
        naar kijkt, want het briefhoofd staat alleen op de eerste pagina."""
        self.c.showPage()
        self._bies()
        if vervolg:
            self.c.setFillColor(GRIJS)
            self.c.setFont("Helvetica", 8.5)
            self.c.drawString(self.links, self.hoogte - 18 * mm,
                              f"{self.titel} {self.nummer} · vervolg")
            self.y = self.hoogte - 28 * mm
        else:
            self.y = self.hoogte - 22 * mm

    def bewaar(self):
        self.c.save()
        return self.pad


def _teken_merkregel(vel, s):
    """Logo, bedrijfsnaam en de contactregel bovenaan het vel."""
    c = vel.c
    tekst_x = vel.links
    logo_bestand = s.get("logo_bestand") or ""
    logo_pad = os.path.join(LOGO_DIR, logo_bestand) if logo_bestand else None
    if logo_pad and os.path.exists(logo_pad):
        try:
            c.drawImage(logo_pad, vel.links, vel.y - 12 * mm, width=16 * mm, height=14 * mm,
                        preserveAspectRatio=True, anchor="sw", mask="auto")
            tekst_x = vel.links + 20 * mm
        except Exception:
            # Een kapot of onleesbaar plaatje mag de rekening niet tegenhouden.
            tekst_x = vel.links

    c.setFillColor(INKT)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(tekst_x, vel.y - 3 * mm, s.get("naam", ""))
    c.setFont("Helvetica", 8.5)
    c.setFillColor(GRIJS_DONKER)
    contact = " · ".join(
        deel for deel in [
            ((s.get("adres") or "").split("\n") or [""])[0].strip(),
            (s.get("telefoon") or "").strip(),
            (s.get("email") or "").strip(),
        ] if deel
    )
    c.drawString(tekst_x, vel.y - 8 * mm,
                 vel.kort(contact, "Helvetica", 8.5, vel.rechts - tekst_x))


def _teken_titel(vel, kopregel):
    """'Rekening' of 'Offerte' in het groot, met het nummer en de datum eronder."""
    # Krapper dan 30 mm: op de eerste pagina bleef er een leeg vlak tussen
    # de merkregel en de titel, terwijl een lange rekening juist ruimte nodig
    # heeft voor de dagen. 20 mm houdt de titel nog onder het logo.
    vel.y -= 20 * mm
    vel.c.setFillColor(ORANJE)
    vel.c.setFont("Helvetica", 30)
    vel.c.drawString(vel.links, vel.y, vel.titel)

    vel.y -= 7 * mm
    vel.c.setFillColor(GRIJS_DONKER)
    vel.c.setFont("Helvetica", 9)
    vel.c.drawString(vel.links, vel.y, kopregel)


def _teken_partijen(vel, s, doc):
    """Van wie de rekening komt en voor wie hij is, naast elkaar."""
    c = vel.c
    tweede = vel.links + 68 * mm

    vel.y -= 9 * mm
    vel.label("Van", vel.links, vel.y)
    vel.label("Voor", tweede, vel.y)
    vel.y -= 5.5 * mm

    c.setFillColor(INKT)
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(vel.links, vel.y, s.get("naam", ""))
    c.drawString(tweede, vel.y, doc["klant_naam"] or "")

    c.setFont("Helvetica", 9)
    c.setFillColor(GRIJS_DONKER)
    links_y = rechts_y = vel.y - 4.8 * mm
    for regel in (s.get("adres") or "").split("\n"):
        if regel.strip():
            c.drawString(vel.links, links_y, vel.kort(regel.strip(), "Helvetica", 9, 62 * mm))
            links_y -= 4.4 * mm
    for regel in (doc["klant_adres"] or "").split("\n"):
        if regel.strip():
            c.drawString(tweede, rechts_y, vel.kort(regel.strip(), "Helvetica", 9, 62 * mm))
            rechts_y -= 4.4 * mm
    if doc["klant_email"]:
        c.drawString(tweede, rechts_y, vel.kort(doc["klant_email"], "Helvetica", 9, 62 * mm))
        rechts_y -= 4.4 * mm

    vel.y = min(links_y, rechts_y) - 5 * mm


def _teken_kolomkoppen(vel):
    """De koppen boven de regels, met de streep eronder."""
    vel.label("Omschrijving", vel.links, vel.y)
    vel.label("Aantal", vel.kolom_aantal, vel.y, rechts_uit=True)
    vel.label("Prijs", vel.kolom_prijs, vel.y, rechts_uit=True)
    vel.label("Bedrag", vel.rechts, vel.y, rechts_uit=True)
    vel.y -= 3 * mm
    vel.c.setStrokeColor(INKT)
    vel.c.setLineWidth(0.8)
    vel.c.line(vel.links, vel.y, vel.rechts, vel.y)
    vel.y -= 4.5 * mm


# Een rekeningregel mag niet in de betaalstrook zakken. Lager dan dit gaat hij
# naar de volgende pagina; de pagina ervoor blijft zo vol als die regel toelaat.
REGEL_BODEM = 72 * mm
# Een offerte heeft geen strook. Dezelfde bodem liet onder de laatste regel een
# leeg vlak van zo'n 86 mm, terwijl de regel er nog paste. De streep onder een
# regel ligt ongeveer 4 mm boven deze bodem; 20 mm houdt die streep los van het
# paginanummer op 9 mm en van de voetregel op 14 mm.
OFFERTE_REGEL_BODEM = 20 * mm
# De betaalstrook staat vast op 62 mm. Het totaal hoort erboven, op hetzelfde
# vel. Onder 80 mm zakt het bedrag in de strook; dan gaat dat blok samen naar
# de volgende pagina. De regels blijven liggen: die meenemen liet erboven een
# leeg vlak, terwijl de regel zelf nog paste.
STROOK_GRENS = 80 * mm
# Een offerte heeft geen strook. Het totaal mag daarom lager, tot boven de
# voettekst op 14 mm. 28 mm houdt het bedrag van 17 pt daar los van.
TOTAAL_GRENS = 28 * mm
# Alleen een rekening gebruikt deze grens nog. De toelichting staat daar vóór
# de strook, en die volgorde is al uitgeleverd. Een offerte meet het slot
# zelf: een vaste 40 mm keek niet naar de lengte, waardoor het kopje alleen
# onderaan bleef of de tekst halverwege scheurde.
TOELICHTING_GRENS = 40 * mm
# Opbouw van het slot, dezelfde stappen als bij het tekenen. Het totaal staat
# op de cursor. 14 mm lager het kopje, 5,5 mm daaronder de eerste tekstregel,
# en elke volgende regel 4,6 mm lager. Onder 24 mm loopt de tekst door de
# voetregel op 14 mm.
TOELICHTING_VOOR_KOP = 14 * mm
TOELICHTING_NA_KOP = 5.5 * mm
TOELICHTING_REGELHOOGTE = 4.6 * mm
TOELICHTING_TEKST_BODEM = 24 * mm
# Het kopje blijft niet alleen staan. Minstens twee tekstregels gaan mee,
# tenzij de toelichting zelf korter is.
TOELICHTING_MIN_REGELS = 2
# Koppen boven de regels: 3 mm tot de streep en 4,5 mm erna.
KOLOMKOP = 7.5 * mm


def _daling_regel(aantal_tekstregels):
    """Hoeveel y zakt na het tekenen van één rekeningregel."""
    return aantal_tekstregels * 4.4 * mm + 11 * mm


def _plaats_regels(items, y, bodem):
    """Regels over pagina's, met `y` al onder de kolomkoppen.

    Geeft de pagina's en de y na de laatste regel. Een nieuwe pagina begint
    op de vervolgzijde; de aanroeper tekent de koppen daar zelf."""
    paginas = []
    huidige = []
    eind = y
    for item in items:
        n = len(item["tekstregels"])
        nodig = n * 4.4 * mm + 8 * mm
        if huidige and y - nodig < bodem:
            paginas.append(huidige)
            huidige = []
            y = _vervolg_y_zonder_vel()
        huidige.append(item)
        y -= _daling_regel(n)
        eind = y
    if huidige:
        paginas.append(huidige)
    return paginas, eind


def _vervolg_y_zonder_vel():
    """Waar de eerste regel op een vervolgpagina begint, onder de koppen.

    A4 staat vast, dus het vel zelf is niet nodig. De aanroeper tekent de
    kolomkoppen daarna zelf, en alleen als er op dat blad nog regels staan."""
    return A4[1] - 28 * mm - KOLOMKOP


def _plan_regelpaginas(vel, voorbereid, bodem):
    """Pagina's met regels, tot aan `bodem`.

    Het totaal hoort hier niet bij. Past dat blok niet meer onder de laatste
    regel, dan schuift de aanroeper alleen het blok op. Een regel meenemen
    liet op de vorige pagina een leeg vlak achter, op een rekening én op een
    offerte, ook als het document al drie pagina's telde. Een offerte krijgt
    een lagere bodem mee: daar is geen betaalstrook die de ruimte opeist."""
    y0 = vel.y - KOLOMKOP
    paginas, _eind = _plaats_regels(voorbereid, y0, bodem)
    return paginas


def _teken_regels(vel, regels, bodem):
    """De regels zelf. Past er niets meer op, dan gaat het door op een vervolgvel.

    Geen typenaam onder de omschrijving (rekening én offerte): die staat al in
    de tekst van de regel, en dubbele labeling verwart — zelfde reden als geen
    badge in de UI. Eenheid (st/u/dg) blijft wel staan.

    Een lange materiaalomschrijving loopt over meerdere regels in plaats van
    afgekapt te worden: de klant moet de hele naam kunnen lezen.

    De pagina loopt vol tot `bodem`. Het totaal — en bij een rekening de
    betaalstrook, bij een offerte de toelichting — wordt daarna getekend en
    schuift alleen zelf op als het er niet meer onder past."""
    c = vel.c
    # Het aantal staat rechts uitgelijnd en steekt dus naar links. 6 mm was
    # krapper dan "10,33 u", waardoor een afgebroken omschrijving in de
    # cijfers liep.
    omschrijfbreedte = vel.kolom_aantal - vel.links - 18 * mm
    voorbereid = []
    for r in regels:
        tekst = (r["omschrijving"] or "").strip()
        voorbereid.append({
            "regel": r,
            "tekstregels": _regels_afbreken(
                c, tekst, "Helvetica", 9.5, omschrijfbreedte),
        })
    paginas = _plan_regelpaginas(vel, voorbereid, bodem)
    if not paginas:
        _teken_kolomkoppen(vel)
        return

    for index, pagina in enumerate(paginas):
        if index:
            vel.nieuwe_pagina(vervolg=True)
        _teken_kolomkoppen(vel)
        for item in pagina:
            r = item["regel"]
            soort_info = soort(r["type"])
            for i, tekst in enumerate(item["tekstregels"]):
                c.setFillColor(INKT)
                c.setFont("Helvetica", 9.5)
                c.drawString(vel.links, vel.y, tekst)
                if i == 0:
                    # Bij een vaste prijs per klus zeggen aantal en tarief niets.
                    if soort_info["eenheid"]:
                        aantal = f"{r['aantal']:g}".replace(".", ",")
                        c.drawRightString(vel.kolom_aantal, vel.y,
                                          f"{aantal} {soort_info['eenheid']}")
                        c.drawRightString(vel.kolom_prijs, vel.y, nl_bedrag(r["prijs"]))
                    c.drawRightString(vel.rechts, vel.y, nl_bedrag(r["subtotaal"]))
                vel.y -= 4.4 * mm
            vel.y -= 4 * mm
            c.setStrokeColor(LIJN)
            c.setLineWidth(0.5)
            c.line(vel.links, vel.y, vel.rechts, vel.y)
            vel.y -= 7 * mm


def _teken_totaal(vel, totaal):
    """Het bedrag onder de streep, rechts uitgelijnd."""
    vel.label("Totaal", vel.rechts - 42 * mm, vel.y + 1 * mm, rechts_uit=True)
    vel.c.setFillColor(ORANJE)
    vel.c.setFont("Helvetica-Bold", 17)
    vel.c.drawRightString(vel.rechts, vel.y - 1 * mm, f"€ {nl_bedrag(totaal)}")


def _toelichting_tekstregels(c, toelichting, breedte):
    """De tekstregels van een toelichting, inclusief een lege alinea.

    Een lege alinea wordt toch getekend en kost dus hoogte. Dezelfde knip als
    bij het tekenen, anders meet het slot een andere lengte dan er op papier
    komt."""
    regels = []
    for alinea in toelichting.split("\n"):
        regels.extend(_regels_afbreken(c, alinea.strip(), "Helvetica", 9, breedte))
    return regels


def _slot_past(y, aantal):
    """Of totaal, kopje, alle tekstregels en de voetregel nog op dit vel passen.

    `y` is de cursor na de laatste rekeningregel, waar het totaal komt. De
    stappen zijn dezelfde als in `_teken_toelichting`. Past het niet, dan
    schuift het hele slot op. Alleen als het ook op een leeg vel niet past,
    mag de tekst daar verdergaan."""
    y -= TOELICHTING_VOOR_KOP
    y -= TOELICHTING_NA_KOP
    for _ in range(aantal):
        if y < TOELICHTING_TEKST_BODEM:
            return False
        y -= TOELICHTING_REGELHOOGTE
    return True


def _teken_toelichting(vel, toelichting, zelf_breken=True):
    """Wat er wel en niet bij de prijs zit; staat vooral op een offerte.

    Een lange toelichting gaat door op een volgend vel in plaats van door de
    voettekst heen te lopen. `zelf_breken` is de oude vaste grens, alleen nog
    voor een rekening: die PDF is al uitgeleverd. Een offerte beslist zelf of
    het slot nog past, en laat het kopje niet alleen achter."""
    if zelf_breken and vel.y < TOELICHTING_GRENS:
        vel.nieuwe_pagina(vervolg=True)
    vel.y -= TOELICHTING_VOOR_KOP
    vel.label("Toelichting", vel.links, vel.y)
    vel.y -= TOELICHTING_NA_KOP
    vel.c.setFont("Helvetica", 9)
    vel.c.setFillColor(GRIJS_DONKER)
    geplaatst = 0
    for regel in _toelichting_tekstregels(vel.c, toelichting, vel.rechts - vel.links):
        # Op een rekening mag de tekst al vóór de tweede regel doorlopen.
        # Op een offerte blijven minstens twee regels bij het kopje.
        if vel.y < TOELICHTING_TEKST_BODEM and (
            zelf_breken or geplaatst >= TOELICHTING_MIN_REGELS
        ):
            vel.nieuwe_pagina(vervolg=True)
            vel.c.setFont("Helvetica", 9)
            vel.c.setFillColor(GRIJS_DONKER)
        vel.c.drawString(vel.links, vel.y, regel)
        vel.y -= TOELICHTING_REGELHOOGTE
        geplaatst += 1


def _teken_betaalstrook(vel, doc, s, vervalt):
    """De afscheurbare strook onderaan een rekening, met alles wat de klant nodig
    heeft om te betalen. Bij contant afrekenen staat er 'Voldaan' en geen IBAN.

    De strook gaat niet zelf naar een nieuwe pagina. Het totaal hoort erboven
    op hetzelfde vel; de aanroeper schuift ze samen op als ze niet meer passen."""
    c = vel.c

    strook_y = 62 * mm
    c.setStrokeColor(STIPPEL)
    c.setLineWidth(0.8)
    c.setDash(2, 3)
    c.line(vel.links, strook_y, vel.rechts, strook_y)
    c.setDash()

    contant = doc.get("betaalmethode") == "cash"
    gespreid = " ".join("VOLDAAN" if contant else "BETAALSTROOK")
    tekstbreedte = c.stringWidth(gespreid, "Helvetica-Bold", 6.5)
    midden = vel.links + (vel.rechts - vel.links) / 2
    # Wit vlak zodat de stippellijn niet door de tekst heen loopt.
    c.setFillColor(WIT)
    c.rect(midden - tekstbreedte / 2 - 3 * mm, strook_y - 1.6 * mm,
           tekstbreedte + 6 * mm, 3.4 * mm, stroke=0, fill=1)
    c.setFillColor(GRIJS)
    c.setFont("Helvetica-Bold", 6.5)
    c.drawCentredString(midden, strook_y - 1 * mm, gespreid)

    if contant:
        c.setFillColor(INKT)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(vel.links, strook_y - 16 * mm, "Contant afgehandeld.")
        c.setFont("Helvetica", 9)
        c.setFillColor(GRIJS_DONKER)
        c.drawString(vel.links, strook_y - 22 * mm, "Deze rekening is ter plekke voldaan.")
        return

    betaald = round(doc.get("betaald") or 0, 2)
    openstaand = round(doc["totaal"] - betaald, 2)

    vel.label("Overmaken naar", vel.links, strook_y - 8 * mm)
    regel_y = strook_y - 14 * mm
    regels = [
        ("IBAN", s.get("iban", "")),
        ("T.n.v.", tenaamstelling(s)),
    ]
    # Geen termijn op de rekening: dan ook geen "Vóór" op de strook.
    if vervalt:
        regels.append(("Vóór", filter_datum_nl(vervalt)))
    if betaald > 0:
        regels.append(("Al betaald", f"€ {nl_bedrag(betaald)}"))
    for naam, waarde in regels:
        c.setFont("Helvetica", 9)
        c.setFillColor(GRIJS)
        c.drawString(vel.links, regel_y, naam)
        c.setFillColor(INKT)
        c.drawString(vel.links + 18 * mm, regel_y, waarde or "")
        regel_y -= 5.2 * mm

    vak_b, vak_h = 58 * mm, 20 * mm
    vak_x, vak_y = vel.rechts - vak_b, strook_y - 26 * mm
    c.setStrokeColor(ORANJE)
    c.setLineWidth(1.4)
    c.roundRect(vak_x, vak_y, vak_b, vak_h, 2 * mm, stroke=1, fill=0)
    vel.label("Nog te betalen" if betaald > 0 else "Te betalen",
              vak_x + vak_b - 5 * mm, vak_y + vak_h - 6 * mm, rechts_uit=True)
    c.setFillColor(ORANJE)
    c.setFont("Helvetica-Bold", 16)
    c.drawRightString(vak_x + vak_b - 5 * mm, vak_y + 5 * mm,
                      f"€ {nl_bedrag(max(openstaand, 0))}")


def _teken_voetregel(vel, offerte):
    vel.c.setFont("Helvetica", 7)
    vel.c.setFillColor(GRIJS)
    vel.c.drawString(vel.links, 14 * mm, OFFERTE_REGEL if offerte else BTW_REGEL)


def _teken_document(pad, doc, regels, s):
    """Tekent een rekening of een offerte: ruime opzet met een oranje rand en
    merkregel bovenaan. Een rekening krijgt onderaan een afscheurbare betaalstrook;
    een offerte niet, want daar valt nog niets te betalen."""
    offerte = doc.get("soort") == "offerte"
    naam = doc.get("weergavenummer") or doc["nummer"]
    vel = Vel(pad, "Offerte" if offerte else "Rekening", naam)

    # Offerte: geldig_tot; rekening: vervalt_op. Beide mogen leeg — dan geen einddatum
    # op het vel (en bij een rekening ook geen "Vóór" op de strook).
    vervalt = doc.get("geldig_tot") if offerte else (doc.get("vervalt_op") or "")
    kopregel = f"{naam}   ·   {filter_datum_nl(doc['datum'])}"
    if offerte and vervalt:
        kopregel += f"   ·   geldig tot {filter_datum_nl(vervalt)}"

    _teken_merkregel(vel, s)
    _teken_titel(vel, kopregel)
    _teken_partijen(vel, s, doc)
    # De rekeningbodem is de strook. Een offerte mag de regels lager zetten;
    # dezelfde 72 mm liet de negende regel naar de volgende pagina schuiven
    # terwijl er op pagina 1 nog een leeg vlak overbleef.
    _teken_regels(vel, regels, OFFERTE_REGEL_BODEM if offerte else REGEL_BODEM)

    toelichting = (doc.get("toelichting") or "").strip()
    if offerte:
        # Alleen het slot schuift op, niet de laatste regel. Het slot is het
        # totaal, het kopje, de hele toelichting en de voetregel. Past dat
        # niet meer onder de regels, dan gaat het in zijn geheel naar het
        # volgende vel. Splitsen mag pas als het ook op een leeg vel niet
        # past; het kopje blijft dan bij de eerste regels. De kolomkoppen
        # horen bij regels; een blad met alleen het slot heeft ze niet nodig.
        if toelichting:
            tekstregels = _toelichting_tekstregels(
                vel.c, toelichting, vel.rechts - vel.links)
            if not _slot_past(vel.y, len(tekstregels)):
                vel.nieuwe_pagina(vervolg=True)
        elif vel.y < TOTAAL_GRENS:
            vel.nieuwe_pagina(vervolg=True)
        _teken_totaal(vel, doc["totaal"])
        if toelichting:
            _teken_toelichting(vel, toelichting, zelf_breken=False)
    else:
        # De toelichting mag de pagina vullen. Het totaal en de strook blijven
        # daarna bij elkaar: past dat blok niet meer, dan gaan ze samen naar
        # het volgende vel, onder de vervolgkop. De regels blijven staan.
        if toelichting:
            _teken_toelichting(vel, toelichting)
        if vel.y < STROOK_GRENS:
            vel.nieuwe_pagina(vervolg=True)
        _teken_totaal(vel, doc["totaal"])
        _teken_betaalstrook(vel, doc, s, vervalt)

    _teken_voetregel(vel, offerte)
    return vel.bewaar()


# Eén adres, niets eromheen. Een regeleinde of een komma is een tweede ontvanger
# of een extra kop — dat mag een veld dat de gebruiker invult niet kunnen.
_MAILADRES = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def geldig_mailadres(adres):
    adres = (adres or "").strip()
    if not adres or len(adres) > 254:
        return False
    return _MAILADRES.fullmatch(adres) is not None


def afzender_adres(s):
    """De afzender zoals hij in de kop Van staat, inclusief een naam ervoor."""
    return (s.get("smtp_van") or s.get("smtp_user") or "").strip()


def _adres_specificaties(tekst):
    """De losse adressen in een veld, zonder de naam ervoor.

    'klant@example.org, tweede@example.org' en 'Naam <klant@example.org>' zijn
    allebei geldige invoer. Op de komma knippen laat de naam aan het adres
    plakken, en dat hele stuk is dan één ontvanger die de mailserver weigert.
    Lege stukken vallen weg. Hetzelfde adres, alleen anders geschreven, één keer.
    """
    uit = []
    gezien = set()
    for _naam, adres in getaddresses([tekst or ""]):
        adres = (adres or "").strip()
        if not adres:
            continue
        sleutel = adres.casefold()
        if sleutel in gezien:
            continue
        gezien.add(sleutel)
        uit.append(adres)
    return uit


def _adres_specificatie(tekst):
    """Het eerste adres in een veld, of leeg. Voor de afzender en de BCC."""
    gevonden = _adres_specificaties(tekst)
    return gevonden[0] if gevonden else ""


def kopie_adres(ontvanger, s=None):
    """Het adres voor de BCC-kopie, of leeg als die niet mee mag.

    Standaard aan, en een leeg veld betekent de afzender. Een naam voor dat
    adres ('Naam <afzender@…>') telt niet als een ander adres: anders valt de
    kopie stil weg, of er gaat een tweede exemplaar naar hetzelfde adres.
    Staat het al bij Aan, dan niet nog een keer. Het adres komt niet in de
    zichtbare koppen; alleen de envelop van de mailserver kent het.
    """
    s = get_settings() if s is None else s
    if not s.get("bcc_aan"):
        return ""
    eigen = (s.get("bcc_adres") or "").strip()
    spec = _adres_specificatie(eigen) if eigen else _adres_specificatie(afzender_adres(s))
    if not geldig_mailadres(spec):
        return ""
    if spec.casefold() in {adres.casefold() for adres in _adres_specificaties(ontvanger)}:
        return ""
    return spec


def _envelop_adressen(msg, kopie):
    """RCPT-adressen: elke Aan en Cc apart, de kopie erbij als die er niet al in zit.

    Geeft (verplicht, envelop) terug. Verplicht zijn de adressen van de klant;
    weigert de server er daar een van, dan is de mail mislukt ook als de kopie
    wél aankwam. Anders staat de rekening op verstuurd en heeft de klant niets.
    """
    verplicht = []
    for kop in ("To", "Cc"):
        for tekst in msg.get_all(kop, []):
            verplicht.extend(_adres_specificaties(str(tekst)))
    verplicht = _adres_specificaties(", ".join(verplicht))
    envelop = list(verplicht)
    gezien = {adres.casefold() for adres in envelop}
    if kopie and kopie.casefold() not in gezien:
        envelop.append(kopie)
    return verplicht, envelop


def _tekst_geweigerd(geweigerd, verplicht):
    """Gewone taal voor een adres dat de mailserver niet aannam."""
    verplicht_klein = {adres.casefold() for adres in verplicht}
    namen = []
    for adres in geweigerd or {}:
        adres = str(adres)
        if adres.casefold() in verplicht_klein and adres not in namen:
            namen.append(adres)
    if not namen:
        namen = [str(adres) for adres in (geweigerd or {})] or ["het adres"]
    if len(namen) == 1:
        return f"De mailserver weigert het adres {namen[0]}."
    return "De mailserver weigert deze adressen: " + ", ".join(namen) + "."


app.jinja_env.globals["kopie_adres"] = kopie_adres


def _html_van_tekst(tekst):
    """De brief als HTML, met dezelfde woorden als de platte tekst.

    Apple Mail behandelt een mail van alleen platte tekst plus een PDF alsof de
    PDF bij de brief hoort en zet hem er op ware grootte onder. Een HTML-deel
    maakt de brief de body."""
    alinea = []
    stukken = []
    for regel in (tekst or "").split("\n"):
        if not regel.strip():
            if alinea:
                stukken.append("<p>" + "<br>\n".join(alinea) + "</p>")
                alinea = []
            continue
        alinea.append(str(escape(regel)))
    if alinea:
        stukken.append("<p>" + "<br>\n".join(alinea) + "</p>")
    lichaam = "\n".join(stukken) or "<p></p>"
    return "<!DOCTYPE html>\n<html><body>\n" + lichaam + "\n</body></html>\n"


def _veilige_bestandsnaam(naam):
    """Een naam die in een kop mag.

    Een regeleinde daarin breekt de kop open. De bidirectionele stuurtekens
    (U+202A–U+202E en U+2066–U+2069, plus de rest van `_BIDI`) draaien de
    getoonde naam om, zodat een .txt eruitziet als een .pdf."""
    schoon = "".join(
        c for c in (naam or "")
        if c not in _BIDI and c not in "\r\n\x00\"\\"
    )
    return schoon.strip() or "bijlage"


def _ascii_bestandsnaam(naam):
    """Een naam die oude mailprogramma's ook kunnen lezen, naast filename*.

    Zonder die gewone filename= laten ze de bijlage vallen of tonen ze een
    kapotte naam. De echte naam blijft in filename* staan."""
    plat = unicodedata.normalize("NFKD", naam).encode("ascii", "ignore").decode("ascii")
    plat = "".join(c if 32 <= ord(c) < 127 else "_" for c in plat).strip(" ._")
    if not plat:
        plat = "bijlage"
    if len(plat) > 40:
        stam, punt, ext = plat.rpartition(".")
        plat = ((stam or "bijlage")[:30]) + (punt + ext if punt else "")
    return plat or "bijlage"


def _rfc2231_stukken(sleutel, waarde, ruimte=60):
    """filename* (of name*), in stukken als de naam anders de regel uit duwt.

    Een te lange regel vouwt de bibliotheek opnieuw, en dan blijft alleen de
    ASCII-naam over. De stukken blijven zelf kort en bestaan uit gewone tekens.
    """
    encoded = quote(waarde, safe="", encoding="utf-8")
    enkel = f"{sleutel}*=utf-8''{encoded}"
    if len(enkel) <= ruimte:
        return [enkel]
    stukken = []
    rest = encoded
    eerste = True
    while rest:
        label = f"{sleutel}*{len(stukken)}*="
        voor = "utf-8''" if eerste else ""
        ruimte_brok = max(8, ruimte - len(label) - len(voor))
        brok = rest[:ruimte_brok]
        # De stukken worden eerst aan elkaar geplakt en dan gedecodeerd.
        # Midden in %XX knippen maakt van het procentteken een ander teken.
        if len(rest) > len(brok):
            procent = brok.rfind("%")
            if procent > 0 and len(brok) - procent < 3:
                brok = brok[:procent]
        if not brok:
            brok = rest[:3]
        rest = rest[len(brok):]
        stukken.append(label + voor + brok)
        eerste = False
    return stukken


def _vouw_kop(kopnaam, stukken):
    """Zet de parameters op regels die de mailbibliotheek niet opnieuw vouwt.

    Doet ze dat wel, dan verdwijnt filename* en blijft alleen de ASCII-naam.
    De eerste regel telt de kopnaam mee; die moet onder de 78 tekens blijven.
    """
    eerste_max = 78 - len(kopnaam) - 2
    regels = []
    huidig = ""
    maxnu = eerste_max
    for stuk in stukken:
        extra = stuk if not huidig else "; " + stuk
        if huidig and len(huidig) + len(extra) + 1 <= maxnu:
            huidig += extra
        elif not huidig and len(stuk) + 1 <= maxnu:
            huidig = stuk
        else:
            if huidig:
                regels.append(huidig + ";")
            huidig = (" " + stuk) if regels else stuk
            maxnu = 78
    if huidig:
        regels.append(huidig)
    return "\n".join(regels)


def _kop_met_naam(kopnaam, basis, sleutel, naam):
    """ASCII-naam én, als de naam vreemde tekens heeft, filename* ervoor.

    filename* eerst: een programma dat hem begrijpt gebruikt die, de rest de
    gewone filename=. Alleen de ster-vorm laat oude programma's de bijlage
    vallen; alleen de ASCII-vorm verminkt de naam.
    """
    ascii_naam = _ascii_bestandsnaam(naam)
    stukken = [basis]
    if naam != ascii_naam:
        stukken.extend(_rfc2231_stukken(sleutel, naam))
    stukken.append(f'{sleutel}="{ascii_naam}"')
    return _vouw_kop(kopnaam, stukken)


def _zet_ruwe_kop(deel, naam, waarde):
    """De kop als platte tekst. Via de normale weg vouwt de bibliotheek een
    naam met een vreemd teken terug tot alleen filename*."""
    deel._headers = [(kop, inhoud) for kop, inhoud in deel._headers if kop.lower() != naam.lower()]
    deel._headers.append((naam, waarde))


def _voeg_bijlage_toe(msg, data, hoofd, onder, naam):
    """Een bestand naast de brief, niet erin.

    `name` op het type en `filename` op de dispositie horen bij elkaar; zonder
    die twee opent Mail de PDF als onderdeel van de tekst. Een Content-ID doet
    hetzelfde, dan hoort het bestand bij de HTML zoals een logo in de brief.
    """
    naam = _veilige_bestandsnaam(naam)
    hoofd = hoofd or "application"
    onder = onder or "octet-stream"
    msg.add_attachment(data, maintype=hoofd, subtype=onder)
    deel = list(msg.iter_attachments())[-1]
    _zet_ruwe_kop(deel, "Content-Type", _kop_met_naam(
        "Content-Type", f"{hoofd}/{onder}", "name", naam,
    ))
    _zet_ruwe_kop(deel, "Content-Disposition", _kop_met_naam(
        "Content-Disposition", "attachment", "filename", naam,
    ))


def bouw_mail(s, ontvanger, onderwerp, tekst, pdf, bestandsnaam, extra=None):
    """Het bericht zoals het de deur uitgaat.

    Bovenaan een mixed-deel: eerst de brief als alternative (platte tekst én
    HTML), daarna elk bestand als bijlage. Geen related — dat is voor plaatjes
    die in de HTML staan, en dan toont Mail ze in de brief in plaats van als
    bijlage.

    `extra` is (bytes, naam, hoofdtype, subtype).
    """
    msg = EmailMessage()
    msg["Subject"] = onderwerp
    msg["From"] = afzender_adres(s)
    msg["To"] = ontvanger
    msg.set_content(tekst or "")
    msg.add_alternative(_html_van_tekst(tekst or ""), subtype="html")
    _voeg_bijlage_toe(msg, pdf, "application", "pdf", bestandsnaam)
    for data, naam, hoofd, onder in extra or []:
        _voeg_bijlage_toe(msg, data, hoofd, onder, naam)
    return msg


def _mail_pdf(s, ontvanger, onderwerp, tekst, pad, bestandsnaam, extra=None):
    """Stuurt de PDF als bijlage, eventueel met extra bestanden erbij als (pad, naam).
    Geeft (gelukt, tekst) terug; die tekst is bedoeld om aan de gebruiker te tonen
    en zegt wat er precies misging."""
    with open(pad, "rb") as f:
        pdf = f.read()
    extra_data = []
    for extra_pad, extra_naam in extra or []:
        if not os.path.exists(extra_pad):
            continue
        soort, _ = mimetypes.guess_type(extra_naam)
        hoofd, _, onder = (soort or "application/octet-stream").partition("/")
        with open(extra_pad, "rb") as f:
            extra_data.append((f.read(), extra_naam, hoofd, onder))
    msg = bouw_mail(s, ontvanger, onderwerp, tekst, pdf, bestandsnaam, extra_data)
    # De kopie zit in de envelop, niet in een Bcc-kop. send_message laat een
    # Bcc-kop wel uit de tekst die de klant krijgt, maar dan staat hij alsnog
    # in het bericht tot dat moment — en een test die het bericht bekijkt zou
    # hem zien. Helemaal niet in het bericht is duidelijker.
    kopie = kopie_adres(ontvanger, s)
    verplicht, envelop = _envelop_adressen(msg, kopie)
    ongeldig = [adres for adres in verplicht if not geldig_mailadres(adres)]
    if not verplicht or ongeldig:
        voorbeeld = ongeldig[0] if ongeldig else (ontvanger or "").strip()
        return False, ("Dit e-mailadres klopt niet"
                       + (f": {voorbeeld}" if voorbeeld else "")
                       + ". Vul een adres in zoals klant@example.org.")

    try:
        with smtplib.SMTP(s["smtp_host"], int(s["smtp_port"]), timeout=20) as server:
            server.starttls()
            if s.get("smtp_user"):
                server.login(s["smtp_user"], s["smtp_pass"])
            # Een dict: de server nam een deel aan en weigerde de rest. Alleen
            # als iedereen geweigerd is, komt SMTPRecipientsRefused. Een
            # geweigerde Aan terwijl de BCC wél weg is, is geen gelukte mail.
            geweigerd = server.send_message(msg, to_addrs=envelop) or {}
    except smtplib.SMTPAuthenticationError:
        return False, ("De mailserver weigert je gebruikersnaam of wachtwoord. Bij iCloud "
                       "en Gmail heb je een app-specifiek wachtwoord nodig, niet je gewone.")
    except smtplib.SMTPRecipientsRefused as fout:
        return False, _tekst_geweigerd(getattr(fout, "recipients", {}) or {}, verplicht)
    except smtplib.SMTPSenderRefused:
        return False, ("De mailserver staat niet toe dat je vanaf dit afzenderadres mailt. "
                       "Vul bij Afzender een adres in dat bij deze mailbox hoort.")
    except socket.gaierror:
        return False, (f"De server {s['smtp_host']} is niet gevonden. Controleer de "
                       "servernaam onder Instellingen → Mailen.")
    except (socket.timeout, TimeoutError, ConnectionError, OSError) as fout:
        return False, (f"Geen verbinding met {s['smtp_host']} op poort {s['smtp_port']} "
                       f"({fout}). Gebruik poort 587.")
    except smtplib.SMTPException as fout:
        return False, f"De mailserver gaf een fout terug: {fout}"

    if isinstance(geweigerd, dict) and geweigerd:
        klein = {adres.casefold() for adres in verplicht}
        if any(str(adres).casefold() in klein for adres in geweigerd):
            return False, _tekst_geweigerd(
                {str(adres): fout for adres, fout in geweigerd.items()}, verplicht,
            )

    return True, ""


def _eigen_zin(tekst):
    """Eén zin die de gebruiker aan de mail toevoegt. Regeleinden worden een spatie,
    anders is het geen zin meer maar een tweede brief."""
    return " ".join((tekst or "").split())[:200].rstrip()


def met_eigen_zin(tekst, eigen_zin):
    """Zet de eigen zin direct na de aanhef. Leeg betekent de standaardtekst."""
    zin = _eigen_zin(eigen_zin)
    if not zin:
        return tekst
    delen = tekst.split("\n\n", 1)
    if len(delen) == 1:
        return tekst + "\n\n" + zin
    return delen[0] + "\n\n" + zin + "\n\n" + delen[1]


def gekozen_bon_ids(form):
    """Welke bonnetjes mee mogen, of None als het formulier daar niets over zegt.

    None betekent: elk bestand van een bon die op deze rekening staat. Het
    verborgen veld `bonnen_gekozen` onderscheidt dat van 'op het mailscherm
    is alles uitgezet'."""
    if form is None or form.get("bonnen_gekozen") != "1":
        return None
    return {int(x) for x in form.getlist("mee") if str(x).isdigit()}


def _bon_dict(rij):
    """Eén meegestuurde bijlage, met wat het mailscherm onder de naam zet.

    `pad` en `naam` zijn wat de mail zelf gebruikt. De rest is alleen voor het
    voorbeeld: een onveilig pad komt hier niet uit, anders zou een kapotte naam
    in de database een bestand buiten de map kunnen meesturen."""
    pad = veilig_pad(BIJLAGE_DIR, rij["bestand"])
    if pad is None:
        return None
    ext = os.path.splitext(rij["bestand"] or "")[1].lower()
    soort = soort_bijlage(ext)
    grootte = formaat_grootte(pad) if os.path.isfile(pad) else ""
    bon = (rij["bon"] or "").strip()
    klus = (rij["klus"] or "").strip()
    naam = zonder_bidi(rij["naam"] or "") or "bijlage"
    # De bestandsnaam staat al als titel. Dezelfde tekst nog eens als bon
    # (dat is de omschrijving die een upload automatisch krijgt) voegt niets toe
    # en maakt van een lange naam alsnog drie regels.
    delen = [soort]
    if grootte:
        delen.append(grootte)
    if bon and bon != (rij["naam"] or "") and bon != naam:
        delen.append(bon)
    if klus:
        delen.append(klus)
    return {
        "id": rij["id"],
        "pad": pad,
        "naam": naam,
        "plaatje": ext in BIJLAGE_PLAATJES,
        "soort": soort,
        "grootte": grootte,
        "bon": bon,
        "klus": klus,
        "info": " · ".join(delen),
    }


def bonnen_bij_factuur(conn, factuur_id):
    """Bestanden van bonnen die op díéze rekening meegerekend zijn.

    Een urenregel van dezelfde klus haalt niet alle foto's van die klus mee.
    Notitiefoto's gaan nooit mee. Een oud vinkje 'niet meesturen' op het
    bestand zelf telt niet meer: staat de bon op de rekening, dan gaat hij
    mee, tenzij je hem op het mailscherm uitvinkt."""
    rijen = conn.execute(
        """SELECT DISTINCT b.id AS id, b.bestand AS bestand, b.naam AS naam,
                  COALESCE(i.omschrijving, '') AS bon, COALESCE(k.naam, '') AS klus
           FROM bijlagen b
           JOIN regels r ON r.inkoop_id = b.inkoop_id
           LEFT JOIN inkopen i ON i.id = b.inkoop_id
           LEFT JOIN klussen k ON k.id = b.klus_id
           WHERE r.factuur_id = ?
             AND b.inkoop_id IS NOT NULL
             AND b.notitie_id IS NULL
           ORDER BY b.id""",
        (factuur_id,),
    ).fetchall()
    return [bon for bon in (_bon_dict(rij) for rij in rijen) if bon is not None]


def meegestuurde_bonnen(conn, factuur_id, alleen_ids=None):
    """Bonnetjes die echt mee de mail in gaan.

    Een bestand dat intussen weg is levert geen bijlage op. Die hoort dus ook
    niet in het voorbeeld te staan. `alleen_ids` beperkt dat tot wat er op het
    controlescherm aangevinkt bleef."""
    uit = []
    for bon in bonnen_bij_factuur(conn, factuur_id):
        if alleen_ids is not None and bon["id"] not in alleen_ids:
            continue
        if os.path.exists(bon["pad"]):
            uit.append(bon)
    return uit


def _extra_bestanden(bonnen):
    return [(bon["pad"], bon["naam"]) for bon in bonnen]


def draai_nummer_terug(factuur_id):
    """Haalt een nummer weg dat bij een mislukte mail was vergeven.

    De klant heeft niets ontvangen, dus de rekening blijft een concept en het
    nummer komt weer vrij. De genummerde PDF gaat weg; de concept-PDF komt terug."""
    conn = get_db()
    factuur = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    if factuur is None or not factuur["nummer"] or factuur["status"] != "concept":
        conn.close()
        return
    genummerd = os.path.join(PDF_DIR, pdf_bestandsnaam(factuur))
    conn.execute("UPDATE facturen SET nummer='' WHERE id=?", (factuur_id,))
    conn.commit()
    conn.close()
    if os.path.exists(genummerd):
        os.remove(genummerd)
    maak_pdf(factuur_id)


def factuur_zoals_in_de_mail(conn, factuur):
    """De rekening zoals de mail hem noemt.

    Een concept heeft nog geen nummer, maar krijgt dat zodra de mail de deur uitgaat.
    Het voorbeeld moet dat nummer al laten zien, anders controleer je een andere
    tekst dan de klant ontvangt. Er wordt hier nog niets opgeslagen: een andere
    rekening kan het nummer intussen pakken, en een mislukte mail draait het terug."""
    if factuur["nummer"]:
        return dict(factuur)
    return dict(factuur, nummer=volgend_nummer(conn))


def mailinhoud_rekening(factuur, bonnen, eigen_zin=""):
    """Onderwerp, tekst en bijlagen van de mail bij deze rekening.

    Het controlescherm en het echte versturen gebruiken dezelfde tekst, zodat wat
    je ziet ook is wat er weggaat."""
    s = get_settings()
    naam = factuurnaam(factuur)
    tekst = (
        f"Beste {factuur['klant_naam']},\n\n"
        f"Hierbij de rekening ({naam}) voor het uitgevoerde werk."
        + (f" De bonnetjes zitten erbij." if bonnen else "")
        + f"\n\nMet vriendelijke groet,\n{s.get('naam') or ''}"
    )
    return {
        "ontvanger": factuur["klant_email"] or "",
        "onderwerp": f"Rekening {naam} - {s.get('naam') or ''}",
        "tekst": met_eigen_zin(tekst, eigen_zin),
        "pdf": pdf_bestandsnaam(factuur),
        "bonnen": bonnen,
    }


def verstuur_email(factuur_id, eigen_zin="", bon_ids=None):
    """Mailt de rekening naar de klant en zet hem op verzonden.

    Het nummer komt er pas als de mailserver de mail heeft aangenomen. Lukt dat
    niet, dan blijft een concept een concept en komt het nummer weer vrij."""
    conn = get_db()
    factuur = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    if factuur is None:
        conn.close()
        abort(404)
    s = get_settings()

    if not factuur["klant_email"]:
        conn.close()
        return False, ("Deze klant heeft geen e-mailadres. Vul dat in bij de rekening "
                       "of bij de klant.")
    if not s.get("smtp_host"):
        conn.close()
        return False, ("Er is nog geen mailserver ingesteld. Vul die in onder "
                       "Instellingen → Mailen.")

    was_concept = not factuur["nummer"]
    if was_concept:
        factuur = maak_definitief(conn, factuur_id)
    # Altijd opnieuw tekenen: logo, IBAN of een betaling kunnen intussen anders zijn.
    maak_pdf(factuur_id)
    bonnen = meegestuurde_bonnen(conn, factuur_id, bon_ids)
    conn.close()

    inhoud = mailinhoud_rekening(factuur, bonnen, eigen_zin)
    pad = os.path.join(PDF_DIR, inhoud["pdf"])
    gelukt, tekst = _mail_pdf(
        s,
        inhoud["ontvanger"],
        inhoud["onderwerp"],
        inhoud["tekst"],
        pad,
        inhoud["pdf"],
        _extra_bestanden(bonnen),
    )
    if not gelukt:
        if was_concept:
            draai_nummer_terug(factuur_id)
        return False, tekst

    conn = get_db()
    conn.execute("UPDATE facturen SET status='verzonden' WHERE id=?", (factuur_id,))
    conn.commit()
    conn.close()
    voorbeeld = os.path.join(PDF_DIR, f"voorbeeld-{factuur_id}.pdf")
    if os.path.exists(voorbeeld):
        os.remove(voorbeeld)
    return True, f"Rekening {factuurnaam(factuur)} gemaild naar {factuur['klant_email']}."


def mailinhoud_herinnering(factuur, betaald, bonnen, eigen_zin=""):
    """De tekst van een herinnering. Voorbeeld en versturen gebruiken dezelfde."""
    s = get_settings()
    openstaand = round(factuur["totaal"] - betaald, 2)
    vervalt = factuur["vervalt_op"] or ""
    if vervalt:
        try:
            te_laat = (vandaag() - date.fromisoformat(vervalt)).days
        except ValueError:
            te_laat = 0
        if te_laat > 0:
            opening = (f"De vervaldatum van {filter_datum_nl(vervalt)} is inmiddels "
                       f"{te_laat} dag{'en' if te_laat != 1 else ''} geleden.")
        else:
            opening = f"De rekening staat open tot {filter_datum_nl(vervalt)}."
    else:
        opening = ""

    bedragregel = f"Het openstaande bedrag is € {nl_bedrag(openstaand)}."
    if betaald > 0:
        bedragregel += (f" Van het totaal van € {nl_bedrag(factuur['totaal'])} is er al "
                        f"€ {nl_bedrag(betaald)} ontvangen, waarvoor dank.")

    open_zin = (f"Deze rekening ({factuur['nummer']}) staat nog open. {opening}".rstrip()
                if opening else
                f"Deze rekening ({factuur['nummer']}) staat nog open.")
    tekst = (
        f"Beste {factuur['klant_naam']},\n\n"
        f"{open_zin}\n\n"
        f"{bedragregel}\n\n"
        "Wellicht is hij aan je aandacht ontsnapt; is hij inmiddels betaald, dan kun je "
        "dit bericht negeren. De rekening zit voor de zekerheid nog een keer bijgevoegd."
        + (f" De bonnetjes zitten erbij." if bonnen else "")
        + f"\n\nMet vriendelijke groet,\n{s.get('naam') or ''}"
    )
    return {
        "ontvanger": factuur["klant_email"] or "",
        "onderwerp": f"Herinnering: rekening {factuur['nummer']} - {s.get('naam') or ''}",
        "tekst": met_eigen_zin(tekst, eigen_zin),
        "pdf": pdf_bestandsnaam(factuur),
        "bonnen": bonnen,
    }


def herinnering_email(factuur_id, eigen_zin="", bon_ids=None):
    """Stuurt een vriendelijke herinnering met de rekening er nog eens bij. Noemt
    hoeveel er nog openstaat; heeft de rekening een termijn, dan ook of die voorbij
    is. De PDF wordt opnieuw getekend, net als bij de eerste mail."""
    conn = get_db()
    factuur = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    if factuur is None:
        conn.close()
        abort(404)
    betaald = betaald_op(conn, factuur_id)
    s = get_settings()

    if factuur["status"] == "betaald":
        conn.close()
        return False, "Deze rekening is al betaald; er valt niets te herinneren."
    if not factuur["nummer"]:
        conn.close()
        return False, ("Dit is nog een concept. Verstuur de rekening eerst; daarna kun "
                       "je een herinnering sturen.")
    if not factuur["klant_email"]:
        conn.close()
        return False, ("Deze klant heeft geen e-mailadres. Vul dat in bij de rekening "
                       "of bij de klant.")
    if not s.get("smtp_host"):
        conn.close()
        return False, ("Er is nog geen mailserver ingesteld. Vul die in onder "
                       "Instellingen → Mailen.")

    maak_pdf(factuur_id)
    bonnen = meegestuurde_bonnen(conn, factuur_id, bon_ids)
    conn.close()

    inhoud = mailinhoud_herinnering(factuur, betaald, bonnen, eigen_zin)
    gelukt, tekst = _mail_pdf(
        s,
        inhoud["ontvanger"],
        inhoud["onderwerp"],
        inhoud["tekst"],
        os.path.join(PDF_DIR, inhoud["pdf"]),
        inhoud["pdf"],
        _extra_bestanden(bonnen),
    )
    if not gelukt:
        return False, tekst
    return True, (f"Herinnering voor rekening {factuur['nummer']} gemaild naar "
                  f"{factuur['klant_email']}.")


def mailinhoud_offerte(offerte, eigen_zin=""):
    s = get_settings()
    tekst = (
        f"Beste {offerte['klant_naam']},\n\n"
        f"Hierbij de offerte ({offerte['nummer']}) voor het besproken werk."
        + (f" De prijs geldt tot {filter_datum_nl(offerte['geldig_tot'])}."
           if offerte["geldig_tot"] else "")
        + f"\n\nMet vriendelijke groet,\n{s.get('naam') or ''}"
    )
    return {
        "ontvanger": offerte["klant_email"] or "",
        "onderwerp": f"Offerte {offerte['nummer']} - {s.get('naam') or ''}",
        "tekst": met_eigen_zin(tekst, eigen_zin),
        "pdf": f"{offerte['nummer']}.pdf",
        "bonnen": [],
    }


def verstuur_offerte_email(offerte_id, eigen_zin=""):
    """Mailt de offerte naar de klant en zet hem op verzonden.

    De PDF wordt vlak voor het versturen opnieuw getekend, zodat een gewijzigd
    logo of adres niet de oude versie meestuurt."""
    conn = get_db()
    offerte = conn.execute("SELECT * FROM offertes WHERE id=?", (offerte_id,)).fetchone()
    s = get_settings()
    conn.close()

    if not offerte["klant_email"]:
        return False, ("Deze klant heeft geen e-mailadres. Vul dat in bij de offerte "
                       "of bij de klant.")
    if not s.get("smtp_host"):
        return False, ("Er is nog geen mailserver ingesteld. Vul die in onder "
                       "Instellingen → Mailen.")

    pad = maak_offerte_pdf(offerte_id)
    inhoud = mailinhoud_offerte(offerte, eigen_zin)
    gelukt, tekst = _mail_pdf(
        s,
        inhoud["ontvanger"],
        inhoud["onderwerp"],
        inhoud["tekst"],
        pad,
        inhoud["pdf"],
    )
    if not gelukt:
        return False, tekst

    conn = get_db()
    if offerte["status"] == "concept":
        conn.execute("UPDATE offertes SET status='verzonden' WHERE id=?", (offerte_id,))
        conn.commit()
    conn.close()
    return True, f"Offerte {offerte['nummer']} gemaild naar {offerte['klant_email']}."


def _pdf_pad(factuur_id):
    conn = get_db()
    factuur = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    conn.close()
    if factuur is None:
        abort(404)
    pad = os.path.join(PDF_DIR, pdf_bestandsnaam(factuur))
    if not os.path.exists(pad):
        maak_pdf(factuur_id)
    return pad, pdf_bestandsnaam(factuur)


@app.route("/factuur/<int:factuur_id>/bekijk")
def bekijk_pdf(factuur_id):
    """Toont de rekening in de browser zelf; in de zijbalk wordt het een download."""
    try:
        pad, bestandsnaam = _pdf_pad(factuur_id)
    except NotFound:
        return niet_gevonden()
    return toon_bestand(pad, "application/pdf", bestandsnaam)


@app.route("/factuur/<int:factuur_id>/vernieuw", methods=["POST"])
def vernieuw_pdf(factuur_id):
    """Tekent de rekening opnieuw, bijvoorbeeld nadat je je logo of IBAN hebt gewijzigd."""
    conn = get_db()
    factuur = conn.execute("SELECT id, nummer FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    conn.close()
    if factuur is None:
        abort(404)
    maak_pdf(factuur_id)
    melding(f"Rekening {factuurnaam(factuur)} is opnieuw gemaakt met je huidige instellingen.")
    return redirect(terug_naar(url_for("index")))


@app.route("/instellingen/vernieuw-alles", methods=["POST"])
def vernieuw_alles():
    conn = get_db()
    ids = [rij["id"] for rij in conn.execute("SELECT id FROM facturen ORDER BY id")]
    offerte_ids = [rij["id"] for rij in conn.execute("SELECT id FROM offertes ORDER BY id")]
    conn.close()
    for factuur_id in ids:
        maak_pdf(factuur_id)
    for offerte_id in offerte_ids:
        maak_offerte_pdf(offerte_id)

    tekst = f"{len(ids)} rekening{'en' if len(ids) != 1 else ''}"
    if offerte_ids:
        tekst += f" en {len(offerte_ids)} offerte{'s' if len(offerte_ids) != 1 else ''}"
    melding(f"{tekst} opnieuw gemaakt met je huidige instellingen.")
    return redirect(url_for("instellingen"))


@app.route("/factuur/<int:factuur_id>/pdf")
def download_pdf(factuur_id):
    try:
        pad, bestandsnaam = _pdf_pad(factuur_id)
    except NotFound:
        return niet_gevonden()
    return zet_bestandskoppen(
        send_file(
            pad, as_attachment=True,
            download_name=veilige_downloadnaam(bestandsnaam),
        )
    )


@app.route("/factuur/<int:factuur_id>/mail")
def mail_voorbeeld(factuur_id):
    """Wat er de deur uitgaat, voordat er iets verstuurd wordt.

    Opslaan en definitief maken mailen niet. Alleen de knop op dit scherm doet dat."""
    conn = get_db()
    factuur = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    if factuur is None:
        conn.close()
        abort(404)
    zoals_gemaild = factuur_zoals_in_de_mail(conn, factuur)
    bonnen = meegestuurde_bonnen(conn, factuur_id)
    conn.close()
    if factuur["nummer"]:
        pdf_pad = maak_pdf(factuur_id)
        pdf_url = url_for("bekijk_pdf", factuur_id=factuur_id)
    else:
        # Los bestand: de opgeslagen concept-PDF blijft "CONCEPT" zeggen. Het
        # voorbeeld toont het voorlopige nummer, zonder dat nummer vast te leggen.
        pdf_pad = os.path.join(PDF_DIR, f"voorbeeld-{factuur_id}.pdf")
        maak_pdf(factuur_id, weergavenummer=zoals_gemaild["nummer"], pad=pdf_pad)
        pdf_url = url_for("mail_pdf", factuur_id=factuur_id)
    return render_template(
        "mailen.html",
        wie=factuur["klant_naam"],
        inhoud=mailinhoud_rekening(zoals_gemaild, bonnen),
        pdf_url=pdf_url,
        document_grootte=formaat_grootte(pdf_pad),
        concept=not factuur["nummer"],
        voorlopig_nummer=not factuur["nummer"],
        al_verzonden=factuur["status"] != "concept",
        reden=waarom_mailen_niet_kan("facturen", factuur_id),
        form_action=url_for("verstuur", factuur_id=factuur_id),
        terug_url=url_for("index"),
        terug_label="Terug naar rekeningen",
        document_label="PDF van de rekening",
        bonnen=bonnen,
        mail_teken=nieuw_mailteken("facturen", factuur_id),
        actief="index",
    )


@app.route("/factuur/<int:factuur_id>/mail-pdf")
def mail_pdf(factuur_id):
    """De PDF zoals de mail hem meestuurt, inclusief een voorlopig nummer."""
    conn = get_db()
    factuur = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    if factuur is None:
        conn.close()
        return niet_gevonden()
    zoals = factuur_zoals_in_de_mail(conn, factuur)
    conn.close()
    if factuur["nummer"]:
        pad = maak_pdf(factuur_id)
        naam = pdf_bestandsnaam(factuur)
    else:
        pad = os.path.join(PDF_DIR, f"voorbeeld-{factuur_id}.pdf")
        maak_pdf(factuur_id, weergavenummer=zoals["nummer"], pad=pad)
        naam = f"{zoals['nummer']}.pdf"
    return toon_bestand(pad, "application/pdf", naam)


@app.route("/factuur/<int:factuur_id>/verstuur", methods=["POST"])
def verstuur(factuur_id):
    mail_rekening(
        factuur_id,
        eigen_zin=request.form.get("eigen_zin", ""),
        bon_ids=gekozen_bon_ids(request.form),
        na=url_for("index"),
    )
    return redirect(terug_naar(url_for("index")))


@app.route("/factuur/<int:factuur_id>/definitief", methods=["POST"])
def definitief(factuur_id):
    """Geeft een concept zijn nummer, zonder het meteen te versturen."""
    conn = get_db()
    factuur = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    if factuur is None:
        conn.close()
        abort(404)
    if factuur["nummer"]:
        conn.close()
        melding(f"Rekening {factuur['nummer']} was al definitief.", "fout")
        return redirect(terug_naar(url_for("index")))

    factuur = maak_definitief(conn, factuur_id)
    conn.close()
    melding(f"De rekening heeft nummer {factuur['nummer']} gekregen.")
    return redirect(terug_naar(url_for("index")))


@app.route("/factuur/<int:factuur_id>/betaald", methods=["POST"])
def markeer_betaald(factuur_id):
    """Helemaal betaald: boekt in één keer wat er nog openstond."""
    conn = get_db()
    factuur = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    if factuur is None:
        conn.close()
        abort(404)

    if factuur["status"] != "betaald":
        rest = round(factuur["totaal"] - betaald_op(conn, factuur_id), 2)
        if rest > 0:
            # Als automatische boeking, zodat "toch niet betaald" hem weer weghaalt
            # zonder aan je eigen deelbetalingen te komen.
            conn.execute(
                """INSERT INTO betalingen (factuur_id, datum, bedrag, notitie, automatisch)
                   VALUES (?, ?, ?, '', 1)""",
                (factuur_id, vandaag().isoformat(), rest),
            )
        # Onthoud waar de rekening vandaan komt, zodat terugzetten geen gok is.
        conn.execute(
            "UPDATE facturen SET status='betaald', status_voor_betaald=? WHERE id=?",
            (factuur["status"], factuur_id),
        )
        conn.commit()
    conn.close()
    if factuur["status"] != "betaald":
        # De strook moet "betaald" zeggen, niet het openstaande bedrag van ervoor.
        maak_pdf(factuur_id)
    return redirect(terug_naar(url_for("index")))


@app.route("/factuur/<int:factuur_id>/niet-betaald", methods=["POST"])
def markeer_niet_betaald(factuur_id):
    """Toch niet betaald: terug naar de status van vóór het afvinken. Alleen wat de
    app zelf boekte gaat weg; deelbetalingen die je zelf hebt ingevoerd blijven staan."""
    conn = get_db()
    factuur = conn.execute(
        "SELECT id, nummer, status_voor_betaald FROM facturen WHERE id=?", (factuur_id,)
    ).fetchone()
    if factuur is None:
        conn.close()
        abort(404)
    conn.execute("DELETE FROM betalingen WHERE factuur_id=? AND automatisch=1", (factuur_id,))
    terug = factuur["status_voor_betaald"] or "concept"
    conn.execute(
        "UPDATE facturen SET status=?, status_voor_betaald='' WHERE id=?", (terug, factuur_id)
    )
    conn.commit()
    conn.close()
    maak_pdf(factuur_id)
    melding(f"Rekening {factuurnaam(factuur)} staat weer open.")
    return redirect(terug_naar(url_for("index")))


@app.route("/factuur/<int:factuur_id>/kopieer", methods=["POST"])
def kopieer(factuur_id):
    """Maakt een nieuw concept met dezelfde klant en dezelfde regels. Handig bij werk
    dat elke maand terugkomt; je hoeft dan alleen de datum en de aantallen na te lopen."""
    conn = get_db()
    origineel = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    if origineel is None:
        conn.close()
        abort(404)

    # Had de originele rekening een termijn, dan een nieuwe default vanaf vandaag;
    # stond er geen einddatum, dan ook geen op de kopie (de oude datum past niet).
    nieuwe_datum = vandaag().isoformat()
    vervalt = standaard_vervalt(nieuwe_datum) if origineel["vervalt_op"] else ""
    cur = conn.execute(
        """INSERT INTO facturen (nummer, datum, vervalt_op, klant_id, klant_naam,
           klant_adres, klant_email, betaalmethode, status, totaal, kenmerk)
           VALUES ('', ?, ?, ?, ?, ?, ?, ?, 'concept', ?, ?)""",
        (
            nieuwe_datum,
            vervalt,
            origineel["klant_id"],
            origineel["klant_naam"],
            origineel["klant_adres"],
            origineel["klant_email"],
            origineel["betaalmethode"],
            origineel["totaal"],
            origineel["kenmerk"],
        ),
    )
    nieuw_id = cur.lastrowid

    # De koppeling met een klus gaat niet mee: die uren zijn al gefactureerd op de
    # rekening waar je van kopieert.
    for r in conn.execute("SELECT * FROM regels WHERE factuur_id=? ORDER BY id", (factuur_id,)):
        conn.execute(
            """INSERT INTO regels (factuur_id, omschrijving, type, aantal, prijs, subtotaal)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (nieuw_id, r["omschrijving"], r["type"], r["aantal"], r["prijs"], r["subtotaal"]),
        )
    conn.commit()
    conn.close()

    maak_pdf(nieuw_id)
    melding(f"Kopie van rekening {factuurnaam(origineel)} staat klaar als nieuw concept. "
          "Loop de datum en de bedragen na voordat je hem verstuurt.")
    return redirect(url_for("bewerk", factuur_id=nieuw_id))


@app.route("/factuur/<int:factuur_id>/betalingen", methods=["GET", "POST"])
def betalingen(factuur_id):
    """Deelbetalingen bijhouden. Handig bij een aanbetaling vooraf of een klant die
    in termijnen betaalt."""
    conn = get_db()
    factuur = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    if factuur is None:
        conn.close()
        abort(404)

    if request.method == "POST":
        try:
            bedrag = round(_getal(request.form.get("bedrag")), 2)
        except ValueError:
            bedrag = 0.0
        if bedrag <= 0:
            conn.close()
            melding("Vul een bedrag in dat groter is dan nul.", "fout")
            return redirect(url_for("betalingen", factuur_id=factuur_id))

        conn.execute(
            """INSERT INTO betalingen (factuur_id, datum, bedrag, notitie, automatisch)
               VALUES (?, ?, ?, ?, 0)""",
            (
                factuur_id,
                geldige_datum(request.form.get("datum")),
                bedrag,
                request.form.get("notitie", "").strip(),
            ),
        )
        herzie_betaalstatus(conn, factuur_id)
        conn.commit()
        conn.close()
        # De strook op de bewaarde PDF klopt nu niet meer; opnieuw tekenen.
        maak_pdf(factuur_id)
        melding(f"€ {nl_bedrag(bedrag)} geboekt.")
        return redirect(url_for("betalingen", factuur_id=factuur_id))

    lijst = betalingen_van(conn, factuur_id)
    betaald = betaald_op(conn, factuur_id)
    conn.close()
    return render_template(
        "betalingen.html", factuur=factuur, naam=factuurnaam(factuur),
        betalingen=lijst, betaald=betaald,
        openstaand=round(factuur["totaal"] - betaald, 2),
        vandaag=vandaag().isoformat(), actief="index",
    )


@app.route("/betaling/<int:betaling_id>/verwijder", methods=["POST"])
def betaling_verwijder(betaling_id):
    conn = get_db()
    betaling = conn.execute("SELECT * FROM betalingen WHERE id=?", (betaling_id,)).fetchone()
    if betaling is None:
        conn.close()
        abort(404)
    factuur_id = betaling["factuur_id"]
    prullenbak_id = naar_prullenbak(
        conn, f"Betaling van € {nl_bedrag(betaling['bedrag'])}", {"betalingen": [betaling]})
    conn.execute("DELETE FROM betalingen WHERE id=?", (betaling_id,))
    herzie_betaalstatus(conn, factuur_id)
    conn.commit()
    conn.close()
    # Zonder deze betaling staat er weer meer open dan de bewaarde PDF zegt.
    maak_pdf(factuur_id)
    melding(f"Betaling van € {nl_bedrag(betaling['bedrag'])} verwijderd.",
            knop=terugknop(prullenbak_id))
    return redirect(url_for("betalingen", factuur_id=factuur_id))


@app.route("/factuur/<int:factuur_id>/herinnering", methods=["GET", "POST"])
def herinnering(factuur_id):
    """Eerst hetzelfde controlescherm als bij de rekening, daarna pas versturen."""
    conn = get_db()
    factuur = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    if factuur is None:
        conn.close()
        abort(404)
    if request.method == "GET":
        betaald = betaald_op(conn, factuur_id)
        bonnen = meegestuurde_bonnen(conn, factuur_id) if factuur["nummer"] else []
        conn.close()
        reden = None
        if factuur["status"] == "betaald":
            reden = "Deze rekening is al betaald; er valt niets te herinneren."
        elif not factuur["nummer"]:
            reden = ("Dit is nog een concept. Verstuur de rekening eerst; daarna kun "
                     "je een herinnering sturen.")
        else:
            reden = waarom_mailen_niet_kan("facturen", factuur_id)
        if factuur["nummer"]:
            pdf_pad = maak_pdf(factuur_id)
        else:
            pdf_pad = ""
        return render_template(
            "mailen.html",
            wie=factuur["klant_naam"],
            inhoud=mailinhoud_herinnering(factuur, betaald, bonnen) if factuur["nummer"] else {
                "ontvanger": factuur["klant_email"] or "",
                "onderwerp": "Herinnering",
                "tekst": "",
                "pdf": "",
                "bonnen": [],
            },
            pdf_url=url_for("bekijk_pdf", factuur_id=factuur_id) if factuur["nummer"] else "",
            document_grootte=formaat_grootte(pdf_pad) if pdf_pad else "",
            concept=False,
            voorlopig_nummer=False,
            al_verzonden=False,
            herinnering=True,
            reden=reden,
            form_action=url_for("herinnering", factuur_id=factuur_id),
            terug_url=url_for("index"),
            terug_label="Terug naar rekeningen",
            document_label="PDF van de rekening",
            bonnen=bonnen,
            mail_teken=nieuw_mailteken("facturen", factuur_id),
            actief="index",
        )

    conn.close()
    mail_rekening(
        factuur_id, herinnering_email, "De herinnering",
        request.form.get("eigen_zin", ""),
        gekozen_bon_ids(request.form),
        na=url_for("index"),
    )
    return redirect(terug_naar(url_for("index")))


@app.route("/factuur/<int:factuur_id>/verwijder", methods=["POST"])
def verwijder(factuur_id):
    conn = get_db()
    factuur = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    if factuur is None:
        conn.close()
        abort(404)
    # Alles wat aan deze rekening hangt gaat mee de prullenbak in, zodat "ongedaan
    # maken" hem compleet terugzet en niet als lege huls.
    prullenbak_id = naar_prullenbak(conn, f"Rekening {factuurnaam(factuur)}", {
        "facturen": [factuur],
        "regels": conn.execute("SELECT * FROM regels WHERE factuur_id=?", (factuur_id,)).fetchall(),
        "betalingen": conn.execute("SELECT * FROM betalingen WHERE factuur_id=?", (factuur_id,)).fetchall(),
    }, koppelingen={
        "factuur_id": factuur_id,
        "uren": [r["id"] for r in conn.execute(
            "SELECT id FROM uren WHERE factuur_id=?", (factuur_id,))],
        "inkopen": [r["id"] for r in conn.execute(
            "SELECT id FROM inkopen WHERE factuur_id=?", (factuur_id,))],
    })
    # Uren en bonnen die op deze rekening stonden komen weer vrij om te factureren.
    conn.execute("UPDATE uren SET factuur_id=NULL WHERE factuur_id=?", (factuur_id,))
    conn.execute("UPDATE inkopen SET factuur_id=NULL WHERE factuur_id=?", (factuur_id,))
    conn.execute("DELETE FROM regels WHERE factuur_id=?", (factuur_id,))
    conn.execute("DELETE FROM betalingen WHERE factuur_id=?", (factuur_id,))
    conn.execute("DELETE FROM facturen WHERE id=?", (factuur_id,))
    conn.commit()
    conn.close()

    # De PDF mag weg: die wordt bij terughalen vanzelf opnieuw getekend.
    pdf = os.path.join(PDF_DIR, pdf_bestandsnaam(factuur))
    if os.path.exists(pdf):
        os.remove(pdf)

    melding(f"Rekening {factuurnaam(factuur)} verwijderd.", knop=terugknop(prullenbak_id))
    return redirect(terug_naar(url_for("index")))


init_db()

_opgeschoond = net_bestaande_gegevens()
if _opgeschoond:
    OPSTARTMELDINGEN.append(
        f"{_opgeschoond} adres{'sen' if _opgeschoond != 1 else ''} of telefoonnummer"
        f"{'s' if _opgeschoond != 1 else ''} stond{'en' if _opgeschoond != 1 else ''} "
        "in een andere schrijfwijze en staat nu overal hetzelfde: straat en huisnummer "
        "op de eerste regel, postcode en plaats op de tweede."
    )

for _oud, _nieuw in herstel_dubbele_nummers():
    OPSTARTMELDINGEN.append(
        f"Rekening {_oud} bestond twee keer door een fout in de nummering. De nieuwste "
        f"heeft nu nummer {_nieuw} gekregen en beide PDF's zijn opnieuw gemaakt."
    )

if __name__ == "__main__":
    # De add-on kent log-niveaus die Python niet heeft (trace/notice/fatal).
    niveaus = {
        "trace": logging.DEBUG,
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "notice": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
        "fatal": logging.CRITICAL,
    }
    logging.basicConfig(
        level=niveaus.get(os.environ.get("LOG_LEVEL", "info").lower(), logging.INFO),
        format="[%(asctime)s] %(levelname)s: %(message)s",
    )
    poort = int(os.environ.get("PORT", "8099"))
    try:
        from waitress import serve

        serve(app, host="0.0.0.0", port=poort, threads=4)
    except ImportError:
        app.run(host="0.0.0.0", port=poort)
