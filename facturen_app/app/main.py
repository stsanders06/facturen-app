import csv
import io
import logging
import mimetypes
import os
import secrets
import socket
import sqlite3
import smtplib
from datetime import date, timedelta
from email.message import EmailMessage
from itertools import zip_longest
from flask import (
    Flask, abort, request, redirect, url_for, render_template, send_file, flash, session
)
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from werkzeug.utils import secure_filename

# Versie van de app; staat onderaan elke pagina zodat je kunt zien wat er draait.
# Hoort gelijk te lopen met de version in config.yaml. Draait de app in Home
# Assistant, dan wint wat de Supervisor zegt dat hij heeft geïnstalleerd.
VERSIE = os.environ.get("ADDON_VERSION") or "1.11.1"

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
DB_PATH = os.path.join(DATA_DIR, "facturen.db")
PDF_DIR = os.path.join(DATA_DIR, "pdfs")
LOGO_DIR = os.path.join(DATA_DIR, "logo")
BIJLAGE_DIR = os.path.join(DATA_DIR, "bijlagen")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(PDF_DIR, exist_ok=True)
os.makedirs(LOGO_DIR, exist_ok=True)
os.makedirs(BIJLAGE_DIR, exist_ok=True)


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
# en je kunt er meerdere tegelijk kiezen; vandaar deze ruimere grens voor het geheel.
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024

# Wat je als bonnetje of werkfoto bij een klus mag zetten.
BIJLAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".pdf"}

# Wat een browser zelf als plaatje kan laten zien; de rest krijgt een bestandsicoon.
BIJLAGE_PLAATJES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def csrf_token():
    """Kenmerk dat meegaat met elk formulier, zodat de app opdrachten van andere
    websites herkent en weigert. Poort 8099 heeft geen wachtwoord, dus zonder dit
    kan elke site die je bezoekt in de achtergrond iets laten verwijderen."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


app.jinja_env.globals["csrf_token"] = csrf_token
app.jinja_env.globals["versie"] = VERSIE


@app.before_request
def controleer_csrf():
    if request.method != "POST":
        return None
    verwacht = session.get("csrf_token")
    gekregen = request.form.get("csrf_token", "")
    if not verwacht or not secrets.compare_digest(verwacht, gekregen):
        abort(400, "Deze opdracht kwam niet van de app zelf.")
    return None


@app.errorhandler(413)
def te_groot(_fout):
    flash("Dat is te veel in één keer. Samen mogen de bestanden hooguit 32 MB zijn; "
          "kies er wat minder tegelijk.")
    return redirect(request.referrer or url_for("instellingen"))

MAANDEN = [
    "jan", "feb", "mrt", "apr", "mei", "jun",
    "jul", "aug", "sep", "okt", "nov", "dec",
]

# Aantal dagen dat de klant heeft om te betalen; komt als "Vóór ..." op de strook.
BETAALTERMIJN_DAGEN = 14

# Hoe lang een offerte standaard geldig blijft. Zonder einddatum kan een klant er
# volgend jaar nog mee aankomen terwijl de materiaalprijzen allang anders zijn.
OFFERTE_GELDIG_DAGEN = 30

# Wat er onderaan een offerte staat, in plaats van de betaalstrook van een rekening.
OFFERTE_REGEL = (
    "Deze offerte is vrijblijvend. Prijzen zijn vrijgesteld van btw i.v.m. "
    "particuliere levering van diensten."
)

# Wat er bij het opstarten is rechtgezet; wordt één keer aan de gebruiker getoond.
OPSTARTMELDINGEN = []

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


def vervaldatum(datum):
    """Factuurdatum plus de betaaltermijn, als ISO-datum."""
    try:
        return (date.fromisoformat(str(datum)) + timedelta(days=BETAALTERMIJN_DAGEN)).isoformat()
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


def duur_in_uren(van, tot):
    """Aantal uren tussen twee kloktijden, als kommagetal. Een tijd die vóór de
    starttijd ligt, telt als de volgende ochtend — handig bij een late klus."""
    try:
        van_u, van_m = (int(deel) for deel in str(van).split(":")[:2])
        tot_u, tot_m = (int(deel) for deel in str(tot).split(":")[:2])
    except ValueError:
        return 0.0
    minuten = (tot_u * 60 + tot_m) - (van_u * 60 + van_m)
    if minuten < 0:
        minuten += 24 * 60
    return round(minuten / 60, 2)


@app.template_filter("uren")
def filter_uren(waarde):
    """8.5 wordt '8,5' en 8.0 wordt '8'."""
    return f"{float(waarde or 0):g}".replace(".", ",")


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
            smtp_van TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS facturen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nummer TEXT NOT NULL,
            datum TEXT NOT NULL,
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
            FOREIGN KEY (factuur_id) REFERENCES facturen (id)
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
            tot TEXT NOT NULL,
            notitie TEXT DEFAULT '',
            factuur_id INTEGER,
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
            -- 1 als hij mee moet als de klus op een rekening gaat, bijvoorbeeld een
            -- bon die de klant wil zien.
            meesturen INTEGER DEFAULT 0,
            FOREIGN KEY (klus_id) REFERENCES klussen (id)
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
        """
    )
    conn.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")

    # Kolommen die later zijn toegevoegd, bijzetten in bestaande databases.
    bestaand = {rij["name"] for rij in conn.execute("PRAGMA table_info(settings)")}
    for kolom, definitie in [("tenaamstelling", "TEXT DEFAULT ''")]:
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

    conn.commit()
    conn.close()


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
    jaar = date.today().year
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
    jaar = date.today().year
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
    vandaag = date.today().isoformat()
    per_factuur = {
        rij["factuur_id"]: rij["som"]
        for rij in conn.execute(
            "SELECT factuur_id, SUM(bedrag) AS som FROM betalingen GROUP BY factuur_id"
        )
    }

    if klant_id is None:
        rijen = conn.execute("SELECT * FROM facturen ORDER BY id DESC")
    else:
        rijen = conn.execute(
            "SELECT * FROM facturen WHERE klant_id=? ORDER BY id DESC", (klant_id,)
        )

    lijst = []
    for rij in rijen:
        factuur = dict(rij)
        # Hoe hij heet in de lijst: zijn nummer, of "concept 6" zolang hij er geen heeft.
        factuur["naam"] = factuurnaam(rij)
        factuur["vervalt"] = vervaldatum(rij["datum"])
        factuur["verlopen"] = (
            rij["status"] != "betaald" and factuur["vervalt"] < vandaag
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
        flash(OPSTARTMELDINGEN.pop(0))

    conn = get_db()
    facturen = factuurlijst(conn)
    conn.close()

    jaar = str(date.today().year)
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
    # is de nieuwste bovenaan handiger.
    if keuze in ("openstaand", "verlopen"):
        zichtbaar = sorted(zichtbaar, key=lambda f: f["datum"])

    zoek = request.args.get("q", "").strip()
    if zoek:
        zichtbaar = zoek_in(zichtbaar, zoek, ["nummer", "klant_naam", "klant_email"])

    return render_template("index.html", facturen=zichtbaar, overzicht=overzicht,
                           keuze=keuze, zoek=zoek, totaal_aantal=len(facturen),
                           actief="index")


@app.route("/instellingen", methods=["GET", "POST"])
def instellingen():
    if request.method == "POST":
        conn = get_db()
        logo_bestand = request.form.get("bestaand_logo", "")
        logo_file = request.files.get("logo")
        if logo_file and logo_file.filename:
            filename = secure_filename(logo_file.filename)
            doel = os.path.join(LOGO_DIR, filename)
            logo_file.save(doel)
            if bruikbaar_logo(doel):
                logo_bestand = filename
            else:
                # Anders staat er straks een factuur zonder logo zonder dat je weet waarom.
                os.remove(doel)
                flash(f"{filename} kan niet op de rekening worden getekend. Gebruik een "
                      "PNG of JPG; een HEIC-foto van een iPhone of een SVG werkt niet.")
        conn.execute(
            """UPDATE settings SET naam=?, adres=?, telefoon=?, email=?, iban=?,
               tenaamstelling=?, logo_bestand=?, smtp_host=?, smtp_port=?, smtp_user=?,
               smtp_pass=?, smtp_van=? WHERE id=1""",
            (
                request.form.get("naam", ""),
                request.form.get("adres", ""),
                request.form.get("telefoon", ""),
                request.form.get("email", ""),
                request.form.get("iban", ""),
                request.form.get("tenaamstelling", ""),
                logo_bestand,
                request.form.get("smtp_host", ""),
                int(request.form.get("smtp_port") or 587),
                request.form.get("smtp_user", ""),
                request.form.get("smtp_pass", ""),
                request.form.get("smtp_van", ""),
            ),
        )
        conn.commit()
        conn.close()
        flash("Instellingen opgeslagen.")
        return redirect(url_for("instellingen"))

    return render_template("instellingen.html", s=get_settings(), actief="instellingen")


def klantenlijst():
    """Alle klanten met hoeveel er per klant is gefactureerd en wat er nog openstaat."""
    conn = get_db()
    klanten = conn.execute(
        """SELECT k.*,
                  COUNT(f.id) AS aantal_facturen,
                  COALESCE(SUM(f.totaal), 0) AS omzet,
                  COALESCE(SUM(CASE WHEN f.status <> 'betaald' THEN f.totaal ELSE 0 END), 0)
                      AS openstaand
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
        form.get("adres", "").strip(),
        form.get("email", "").strip(),
        form.get("telefoon", "").strip(),
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
        flash("Kies eerst een CSV-bestand.")
        return redirect(url_for("klanten_import"))

    lezer = _csv_lezer(bestand.read())
    kolommen, extra_adres = _kolomnamen(lezer.fieldnames)
    if "naam" not in kolommen:
        flash("In dit bestand staat geen kolom met een naam. Zorg dat de eerste regel "
              "de kopjes bevat, met in elk geval een kolom 'naam'.")
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
        adres = "\n".join(deel for deel in adres_delen if deel)
        email, telefoon, notitie = waarde("email"), waarde("telefoon"), waarde("notitie")

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

    melding = f"{nieuw} klant{'en' if nieuw != 1 else ''} toegevoegd"
    if bijgewerkt:
        melding += f", {bijgewerkt} bijgewerkt"
    if overgeslagen:
        melding += (f", {overgeslagen} overgeslagen (naam leeg of stond er al)")
    flash(melding + ".")
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
    schrijver.writerow(["Jan Jansen", "Dorpsstraat 1\n5900 AA Venlo",
                        "jan@voorbeeld.nl", "0612345678", "Vaste klant"])
    return send_file(
        io.BytesIO(uitvoer.getvalue().encode("utf-8-sig")),
        mimetype="text/csv", as_attachment=True, download_name="klanten-voorbeeld.csv",
    )


@app.route("/klanten/nieuw", methods=["GET", "POST"])
def klant_nieuw():
    if request.method == "POST":
        naam, adres, email, telefoon, notitie = klant_uit_form(request.form)
        if not naam:
            flash("Vul een naam in om de klant op te slaan.")
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
        flash(f"Klant {naam} opgeslagen.")
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
        "SELECT * FROM offertes WHERE klant_id=? ORDER BY id DESC", (klant_id,)
    ).fetchall()
    conn.close()

    vandaag = date.today().isoformat()
    onbetaald = [f for f in facturen if f["status"] != "betaald"]
    klussen_van_klant = [k for k in klussenlijst() if k["klant_id"] == klant_id]
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
        "uren_open": round(sum(k["uren_open"] for k in klussen_van_klant), 2),
        "uren_bedrag": round(sum(k["bedrag_open"] for k in klussen_van_klant), 2),
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
            flash("Vul een naam in om de klant op te slaan.")
            return redirect(url_for("klant_bewerk", klant_id=klant_id))
        conn.execute(
            """UPDATE klanten SET naam=?, adres=?, email=?, telefoon=?, notitie=?
               WHERE id=?""",
            (naam, adres, email, telefoon, notitie, klant_id),
        )
        conn.commit()
        conn.close()
        flash("Klantgegevens bijgewerkt.")
        return redirect(url_for("klant", klant_id=klant_id))

    conn.close()
    return render_template("klant_form.html", klant=gegevens, actief="klanten")


@app.route("/klant/<int:klant_id>/verwijder", methods=["POST"])
def klant_verwijder(klant_id):
    conn = get_db()
    gegevens = conn.execute("SELECT naam FROM klanten WHERE id=?", (klant_id,)).fetchone()
    if gegevens is None:
        conn.close()
        abort(404)
    # De rekeningen zelf blijven staan: daar hoort de klantnaam bij zoals hij
    # op de rekening is gedrukt. Alleen de koppeling gaat weg — ook die van klussen,
    # anders houden die een klantnummer dat nergens meer heen wijst.
    conn.execute("UPDATE facturen SET klant_id=NULL WHERE klant_id=?", (klant_id,))
    conn.execute("UPDATE klussen SET klant_id=NULL WHERE klant_id=?", (klant_id,))
    conn.execute("DELETE FROM klanten WHERE id=?", (klant_id,))
    conn.commit()
    conn.close()
    flash(f"Klant {gegevens['naam']} verwijderd. De rekeningen zijn blijven staan.")
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
        dag["uren"] = duur_in_uren(rij["van"], rij["tot"])
        totaal += dag["uren"]
        dagen.append(dag)
    return klus, dagen, round(totaal, 2)


def klussenlijst():
    """Alle klussen met hun opgetelde uren en de periode waarin is gewerkt.
    Lopende klussen staan bovenaan."""
    conn = get_db()
    klussen = conn.execute(
        """SELECT kl.*, k.naam AS klant_naam FROM klussen kl
           LEFT JOIN klanten k ON k.id = kl.klant_id
           ORDER BY (kl.status = 'afgerond'), kl.id DESC"""
    ).fetchall()
    uren = conn.execute("SELECT * FROM uren ORDER BY datum").fetchall()
    conn.close()

    leeg = {"uren": 0.0, "open": 0.0, "dagen": 0, "van": None, "tot": None}
    per_klus = {}
    for rij in uren:
        gegevens = per_klus.setdefault(rij["klus_id"], dict(leeg))
        duur = duur_in_uren(rij["van"], rij["tot"])
        gegevens["uren"] += duur
        if rij["factuur_id"] is None:
            gegevens["open"] += duur
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
        lijst.append(rij)
    return lijst


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
    overzicht = {
        "open_uren": round(sum(k["uren_open"] for k in lijst), 2),
        "open_bedrag": round(sum(k["bedrag_open"] for k in lijst), 2),
        "lopend": sum(1 for k in lijst if k["status"] != "afgerond"),
    }
    return render_template("klussen.html", klussen=lijst, overzicht=overzicht,
                           actief="klussen")


@app.route("/klussen/nieuw", methods=["GET", "POST"])
def klus_nieuw():
    if request.method == "POST":
        naam, klant_id, uurtarief, notitie = klus_uit_form(request.form)
        if not naam:
            flash("Geef de klus een naam om hem op te slaan.")
            return redirect(url_for("klus_nieuw"))
        conn = get_db()
        cur = conn.execute(
            """INSERT INTO klussen (naam, klant_id, uurtarief, notitie, status, gestart)
               VALUES (?, ?, ?, ?, 'open', ?)""",
            (naam, klant_id, uurtarief, notitie, date.today().isoformat()),
        )
        conn.commit()
        klus_id = cur.lastrowid
        conn.close()
        flash(f"Klus {naam} aangemaakt. Zet hieronder je eerste dag erbij.")
        return redirect(url_for("klus", klus_id=klus_id))

    return render_template("klus_form.html", klus=None, klanten=klantenlijst(),
                           actief="klussen")


@app.route("/klus/<int:klus_id>")
def klus(klus_id):
    conn = get_db()
    gegevens, dagen, totaal = klus_met_uren(conn, klus_id)
    if gegevens is None:
        conn.close()
        abort(404)
    bestanden = bijlagen_van(conn, klus_id)
    conn.close()

    open_uren = round(sum(d["uren"] for d in dagen if d["factuur_id"] is None), 2)
    return render_template(
        "klus.html", klus=gegevens, dagen=dagen, totaal=totaal, open_uren=open_uren,
        bijlagen=bestanden,
        bedrag=round(totaal * (gegevens["uurtarief"] or 0), 2),
        vandaag=date.today().isoformat(), actief="klussen",
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
            flash("Geef de klus een naam om hem op te slaan.")
            return redirect(url_for("klus_bewerk", klus_id=klus_id))
        conn.execute(
            "UPDATE klussen SET naam=?, klant_id=?, uurtarief=?, notitie=? WHERE id=?",
            (naam, klant_id, uurtarief, notitie, klus_id),
        )
        conn.commit()
        conn.close()
        flash("Klus bijgewerkt.")
        return redirect(url_for("klus", klus_id=klus_id))

    conn.close()
    return render_template("klus_form.html", klus=gegevens, klanten=klantenlijst(),
                           actief="klussen")


@app.route("/klus/<int:klus_id>/status", methods=["POST"])
def klus_status(klus_id):
    """Wisselt tussen lopend en afgerond; afgeronde klussen zakken naar onderen."""
    conn = get_db()
    gegevens = conn.execute("SELECT status FROM klussen WHERE id=?", (klus_id,)).fetchone()
    if gegevens is None:
        conn.close()
        abort(404)
    nieuw_status = "open" if gegevens["status"] == "afgerond" else "afgerond"
    conn.execute("UPDATE klussen SET status=? WHERE id=?", (nieuw_status, klus_id))
    conn.commit()
    conn.close()
    flash("Klus weer op lopend gezet." if nieuw_status == "open" else "Klus afgerond.")
    return redirect(request.referrer or url_for("klussen"))


@app.route("/klus/<int:klus_id>/verwijder", methods=["POST"])
def klus_verwijder(klus_id):
    conn = get_db()
    gegevens = conn.execute("SELECT naam FROM klussen WHERE id=?", (klus_id,)).fetchone()
    if gegevens is None:
        conn.close()
        abort(404)
    # De bestanden op schijf horen mee te gaan; anders blijven ze als losse
    # weesbestanden in /data achter.
    bestanden = [r["bestand"] for r in conn.execute(
        "SELECT bestand FROM bijlagen WHERE klus_id=?", (klus_id,))]
    conn.execute("DELETE FROM bijlagen WHERE klus_id=?", (klus_id,))
    conn.execute("DELETE FROM uren WHERE klus_id=?", (klus_id,))
    conn.execute("DELETE FROM klussen WHERE id=?", (klus_id,))
    conn.commit()
    conn.close()

    for bestand in bestanden:
        pad = os.path.join(BIJLAGE_DIR, bestand)
        if os.path.exists(pad):
            os.remove(pad)

    flash(f"Klus {gegevens['naam']} en de bijbehorende uren zijn verwijderd.")
    return redirect(url_for("klussen"))


def bijlagen_van(conn, klus_id):
    """De foto's en bonnetjes bij een klus, met of ze als plaatje te tonen zijn."""
    lijst = []
    for rij in conn.execute(
        "SELECT * FROM bijlagen WHERE klus_id=? ORDER BY id", (klus_id,)
    ):
        bijlage = dict(rij)
        bijlage["plaatje"] = os.path.splitext(rij["bestand"])[1].lower() in BIJLAGE_PLAATJES
        lijst.append(bijlage)
    return lijst


@app.route("/klus/<int:klus_id>/bijlage", methods=["POST"])
def bijlage_erbij(klus_id):
    """Bonnetjes en werkfoto's bij een klus zetten. Ze blijven bij de klus horen; de
    rekening blijft een nette lijst met regels."""
    conn = get_db()
    if conn.execute("SELECT id FROM klussen WHERE id=?", (klus_id,)).fetchone() is None:
        conn.close()
        abort(404)

    erbij, geweigerd = 0, []
    for bestand in request.files.getlist("bijlage"):
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
            """INSERT INTO bijlagen (klus_id, bestand, naam, toegevoegd, meesturen)
               VALUES (?, ?, ?, ?, 0)""",
            (klus_id, opslagnaam, veilig, date.today().isoformat()),
        )
        erbij += 1

    conn.commit()
    conn.close()

    if erbij:
        flash(f"{erbij} bestand{'en' if erbij != 1 else ''} bij de klus gezet.")
    if geweigerd:
        flash(f"Niet toegevoegd: {', '.join(geweigerd)}. Kies een foto (JPG, PNG, HEIC) "
              "of een PDF.")
    if not erbij and not geweigerd:
        flash("Er was geen bestand gekozen.")
    return redirect(url_for("klus", klus_id=klus_id))


@app.route("/bijlage/<int:bijlage_id>")
def bijlage(bijlage_id):
    """Toont de foto of het bonnetje in de browser."""
    conn = get_db()
    rij = conn.execute("SELECT * FROM bijlagen WHERE id=?", (bijlage_id,)).fetchone()
    conn.close()
    if rij is None:
        abort(404)
    pad = os.path.join(BIJLAGE_DIR, rij["bestand"])
    if not os.path.exists(pad):
        abort(404)
    return send_file(pad, as_attachment=False, download_name=rij["naam"])


@app.route("/bijlage/<int:bijlage_id>/meesturen", methods=["POST"])
def bijlage_meesturen(bijlage_id):
    """Zet aan of uit of dit bestand meegaat als bijlage bij de rekening."""
    conn = get_db()
    rij = conn.execute("SELECT * FROM bijlagen WHERE id=?", (bijlage_id,)).fetchone()
    if rij is None:
        conn.close()
        abort(404)
    conn.execute("UPDATE bijlagen SET meesturen=? WHERE id=?",
                 (0 if rij["meesturen"] else 1, bijlage_id))
    conn.commit()
    conn.close()
    return redirect(url_for("klus", klus_id=rij["klus_id"]))


@app.route("/bijlage/<int:bijlage_id>/verwijder", methods=["POST"])
def bijlage_verwijder(bijlage_id):
    conn = get_db()
    rij = conn.execute("SELECT * FROM bijlagen WHERE id=?", (bijlage_id,)).fetchone()
    if rij is None:
        conn.close()
        abort(404)
    conn.execute("DELETE FROM bijlagen WHERE id=?", (bijlage_id,))
    conn.commit()
    conn.close()

    pad = os.path.join(BIJLAGE_DIR, rij["bestand"])
    if os.path.exists(pad):
        os.remove(pad)
    flash(f"{rij['naam']} verwijderd.")
    return redirect(url_for("klus", klus_id=rij["klus_id"]))


@app.route("/klus/<int:klus_id>/dag", methods=["POST"])
def dag_erbij(klus_id):
    """Eén gewerkte dag bij de klus zetten: datum, van-tot en eventueel een notitie."""
    conn = get_db()
    bestaat = conn.execute("SELECT id FROM klussen WHERE id=?", (klus_id,)).fetchone()
    if bestaat is None:
        conn.close()
        abort(404)

    van = request.form.get("van", "").strip()
    tot = request.form.get("tot", "").strip()
    if not van or not tot:
        conn.close()
        flash("Vul een begin- en eindtijd in om de dag op te slaan.")
        return redirect(url_for("klus", klus_id=klus_id))

    conn.execute(
        "INSERT INTO uren (klus_id, datum, van, tot, notitie) VALUES (?, ?, ?, ?, ?)",
        (
            klus_id,
            geldige_datum(request.form.get("datum")),
            van,
            tot,
            request.form.get("notitie", "").strip(),
        ),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("klus", klus_id=klus_id))


@app.route("/uur/<int:uur_id>/bewerk", methods=["POST"])
def dag_bewerk(uur_id):
    conn = get_db()
    gegevens = conn.execute("SELECT klus_id FROM uren WHERE id=?", (uur_id,)).fetchone()
    if gegevens is None:
        conn.close()
        abort(404)

    van = request.form.get("van", "").strip()
    tot = request.form.get("tot", "").strip()
    if not van or not tot:
        conn.close()
        flash("Vul een begin- en eindtijd in om de dag op te slaan.")
        return redirect(url_for("klus", klus_id=gegevens["klus_id"]))

    conn.execute(
        "UPDATE uren SET datum=?, van=?, tot=?, notitie=? WHERE id=?",
        (
            geldige_datum(request.form.get("datum")),
            van,
            tot,
            request.form.get("notitie", "").strip(),
            uur_id,
        ),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("klus", klus_id=gegevens["klus_id"]))


@app.route("/uur/<int:uur_id>/verwijder", methods=["POST"])
def dag_verwijder(uur_id):
    conn = get_db()
    gegevens = conn.execute("SELECT klus_id FROM uren WHERE id=?", (uur_id,)).fetchone()
    if gegevens is None:
        conn.close()
        abort(404)
    conn.execute("DELETE FROM uren WHERE id=?", (uur_id,))
    conn.commit()
    conn.close()
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
    Regels zonder omschrijving worden overgeslagen."""
    totaal = 0.0
    regels = []
    for o, t, a, p, klus in zip_longest(
        form.getlist("omschrijving"),
        form.getlist("type"),
        form.getlist("aantal"),
        form.getlist("prijs"),
        form.getlist("regel_klus"),
        fillvalue="",
    ):
        if not o:
            continue
        try:
            a = _getal(a)
            p = _getal(p)
        except ValueError:
            continue
        if t == "arbeid_klus":
            # Vaste prijs voor de hele klus: het bedrag is de prijs, geen aantal keer tarief.
            a = 1.0
        subtotaal = a * p
        totaal += subtotaal
        regels.append((o, t, a, p, subtotaal, int(klus) if str(klus).isdigit() else None))
    return regels, round(totaal, 2)


def geldige_datum(waarde, terugval=None):
    """Een datum die niet als jaar-maand-dag te lezen is, breekt later de sortering
    en de weergave. Zo'n waarde vervangen we door de terugval."""
    try:
        return date.fromisoformat(str(waarde)).isoformat()
    except (TypeError, ValueError):
        return terugval or date.today().isoformat()


def geboekte_klussen(factuur_id=None):
    """De klussen met uren die nog niet op een rekening staan, om als één regel toe
    te voegen. Bij het bewerken van een rekening tellen de uren die er al op staan
    gewoon mee, anders zou de klus daar verdwijnen."""
    lijst = []
    for klus in klussenlijst():
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
            lijst.append(klus)
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
            (naam, form.get("klant_adres", "").strip(), form.get("klant_email", "").strip()),
        ).lastrowid
    return None


def bewaar_regels(conn, factuur_id, regels):
    conn.execute("DELETE FROM regels WHERE factuur_id=?", (factuur_id,))
    for o, t, a, p, subtotaal, klus_id in regels:
        conn.execute(
            """INSERT INTO regels (factuur_id, omschrijving, type, aantal, prijs, subtotaal,
               klus_id) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (factuur_id, o, t, a, p, subtotaal, klus_id),
        )
    boek_uren(conn, factuur_id, [r[5] for r in regels if r[5]])


def boek_uren(conn, factuur_id, klus_ids):
    """Legt vast welke gewerkte dagen op deze rekening staan, zodat dezelfde uren
    niet per ongeluk een tweede keer worden gefactureerd."""
    conn.execute("UPDATE uren SET factuur_id=NULL WHERE factuur_id=?", (factuur_id,))
    for klus_id in klus_ids:
        conn.execute(
            """UPDATE uren SET factuur_id=? WHERE klus_id=? AND factuur_id IS NULL""",
            (factuur_id, klus_id),
        )


@app.route("/nieuw", methods=["GET", "POST"])
def nieuw():
    if request.method == "POST":
        regels, totaal = lees_regels(request.form)
        if not regels:
            flash("Vul minstens één regel in met een omschrijving; anders is er niets "
                  "te factureren.")
            return redirect(url_for("nieuw"))

        conn = get_db()
        klant_id = bepaal_klant(conn, request.form)

        # Nog geen nummer: dat komt pas als de rekening definitief wordt. Zo laat een
        # concept dat je weggooit geen gat achter in de reeks.
        cur = conn.execute(
            """INSERT INTO facturen (nummer, datum, klant_id, klant_naam, klant_adres,
               klant_email, betaalmethode, status, totaal)
               VALUES ('', ?, ?, ?, ?, ?, ?, 'concept', ?)""",
            (
                geldige_datum(request.form.get("datum")),
                klant_id,
                request.form.get("klant_naam", ""),
                request.form.get("klant_adres", ""),
                request.form.get("klant_email", ""),
                request.form.get("betaalmethode", "bank"),
                totaal,
            ),
        )
        factuur_id = cur.lastrowid
        bewaar_regels(conn, factuur_id, regels)
        conn.commit()
        conn.close()

        maak_pdf(factuur_id)

        if request.form.get("verstuur") == "ja":
            # Versturen maakt de rekening vanzelf definitief; dan pas een nummer.
            flash(verstuur_email(factuur_id)[1])
        else:
            flash("Rekening opgeslagen als concept. Hij krijgt zijn nummer zodra je "
                  "hem verstuurt of definitief maakt.")

        return redirect(url_for("index"))

    # Vanuit een klantpagina kun je meteen een rekening voor die klant beginnen.
    vooraf = request.args.get("klant", "")
    gekozen = None
    if vooraf.isdigit():
        conn = get_db()
        gekozen = conn.execute("SELECT * FROM klanten WHERE id=?", (vooraf,)).fetchone()
        conn.close()

    return render_template(
        "nieuw.html", vandaag=date.today().isoformat(), actief="nieuw",
        factuur=None, regels=[], klanten=klantenlijst(), gekozen_klant=gekozen,
        klussen=geboekte_klussen(), vooraf_klus=request.args.get("klus", ""),
    )


@app.route("/factuur/<int:factuur_id>/bewerk", methods=["GET", "POST"])
def bewerk(factuur_id):
    conn = get_db()
    factuur = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    if factuur is None:
        conn.close()
        abort(404)

    if request.method == "POST":
        regels, totaal = lees_regels(request.form)
        if not regels:
            conn.close()
            flash("Vul minstens één regel in met een omschrijving; anders is er niets "
                  "te factureren.")
            return redirect(url_for("bewerk", factuur_id=factuur_id))

        klant_id = bepaal_klant(conn, request.form)
        conn.execute(
            """UPDATE facturen SET datum=?, klant_id=?, klant_naam=?, klant_adres=?,
               klant_email=?, betaalmethode=?, totaal=? WHERE id=?""",
            (
                geldige_datum(request.form.get("datum"), factuur["datum"]),
                klant_id,
                request.form.get("klant_naam", ""),
                request.form.get("klant_adres", ""),
                request.form.get("klant_email", ""),
                request.form.get("betaalmethode", "bank"),
                totaal,
                factuur_id,
            ),
        )
        bewaar_regels(conn, factuur_id, regels)
        conn.commit()
        conn.close()

        # De PDF hoort bij de oude gegevens, dus opnieuw tekenen.
        maak_pdf(factuur_id)
        flash(f"Rekening {factuurnaam(factuur)} bijgewerkt.")

        if request.form.get("verstuur") == "ja":
            flash(verstuur_email(factuur_id)[1])

        return redirect(url_for("index"))

    regels = conn.execute(
        "SELECT * FROM regels WHERE factuur_id=? ORDER BY id", (factuur_id,)
    ).fetchall()
    conn.close()
    return render_template("nieuw.html", vandaag=factuur["datum"], actief="nieuw",
                           factuur=factuur, regels=regels, klanten=klantenlijst(),
                           gekozen_klant=None, klussen=geboekte_klussen(factuur_id),
                           vooraf_klus="")


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


def bewaar_offerte_regels(conn, offerte_id, regels):
    conn.execute("DELETE FROM offerte_regels WHERE offerte_id=?", (offerte_id,))
    for o, t, a, p, subtotaal, _klus_id in regels:
        conn.execute(
            """INSERT INTO offerte_regels (offerte_id, omschrijving, type, aantal, prijs,
               subtotaal) VALUES (?, ?, ?, ?, ?, ?)""",
            (offerte_id, o, t, a, p, subtotaal),
        )


@app.route("/offertes")
def offertes():
    conn = get_db()
    lijst = conn.execute("SELECT * FROM offertes ORDER BY id DESC").fetchall()
    conn.close()

    vandaag = date.today().isoformat()
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
        zichtbaar = zoek_in(zichtbaar, zoek, ["nummer", "klant_naam", "klant_email"])

    return render_template("offertes.html", offertes=zichtbaar, overzicht=overzicht,
                           keuze=keuze, zoek=zoek, totaal_aantal=len(lijst),
                           vandaag=vandaag, actief="offertes")


@app.route("/offertes/nieuw", methods=["GET", "POST"])
def offerte_nieuw():
    if request.method == "POST":
        regels, totaal = lees_regels(request.form)
        if not regels:
            flash("Vul minstens één regel in met een omschrijving; anders staat er "
                  "niets in de offerte.")
            return redirect(url_for("offerte_nieuw"))

        conn = get_db()
        nummer = volgend_offertenummer(conn)
        klant_id = bepaal_klant(conn, request.form)
        datum = geldige_datum(request.form.get("datum"))

        cur = conn.execute(
            """INSERT INTO offertes (nummer, datum, geldig_tot, klant_id, klant_naam,
               klant_adres, klant_email, status, totaal, toelichting)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'concept', ?, ?)""",
            (
                nummer,
                datum,
                lees_geldigheid(request.form, datum),
                klant_id,
                request.form.get("klant_naam", ""),
                request.form.get("klant_adres", ""),
                request.form.get("klant_email", ""),
                totaal,
                request.form.get("toelichting", "").strip(),
            ),
        )
        offerte_id = cur.lastrowid
        bewaar_offerte_regels(conn, offerte_id, regels)
        conn.commit()
        conn.close()

        maak_offerte_pdf(offerte_id)
        flash(f"Offerte {nummer} aangemaakt.")

        if request.form.get("verstuur") == "ja":
            flash(verstuur_offerte_email(offerte_id)[1])

        return redirect(url_for("offertes"))

    vooraf = request.args.get("klant", "")
    gekozen = None
    if vooraf.isdigit():
        conn = get_db()
        gekozen = conn.execute("SELECT * FROM klanten WHERE id=?", (vooraf,)).fetchone()
        conn.close()

    vandaag = date.today().isoformat()
    return render_template(
        "nieuw.html", mode="offerte", vandaag=vandaag, actief="offertes",
        factuur=None, regels=[], klanten=klantenlijst(), gekozen_klant=gekozen,
        klussen=[], vooraf_klus="", geldig=geldig_tot(vandaag),
        standaard_geldig=geldig_tot(vandaag),
    )


@app.route("/offerte/<int:offerte_id>/bewerk", methods=["GET", "POST"])
def offerte_bewerk(offerte_id):
    conn = get_db()
    offerte = offerte_of_404(conn, offerte_id)

    if request.method == "POST":
        regels, totaal = lees_regels(request.form)
        if not regels:
            conn.close()
            flash("Vul minstens één regel in met een omschrijving; anders staat er "
                  "niets in de offerte.")
            return redirect(url_for("offerte_bewerk", offerte_id=offerte_id))

        klant_id = bepaal_klant(conn, request.form)
        datum = geldige_datum(request.form.get("datum"), offerte["datum"])
        conn.execute(
            """UPDATE offertes SET datum=?, geldig_tot=?, klant_id=?, klant_naam=?,
               klant_adres=?, klant_email=?, totaal=?, toelichting=? WHERE id=?""",
            (
                datum,
                lees_geldigheid(request.form, datum),
                klant_id,
                request.form.get("klant_naam", ""),
                request.form.get("klant_adres", ""),
                request.form.get("klant_email", ""),
                totaal,
                request.form.get("toelichting", "").strip(),
                offerte_id,
            ),
        )
        bewaar_offerte_regels(conn, offerte_id, regels)
        conn.commit()
        conn.close()

        maak_offerte_pdf(offerte_id)
        flash(f"Offerte {offerte['nummer']} bijgewerkt.")

        if request.form.get("verstuur") == "ja":
            flash(verstuur_offerte_email(offerte_id)[1])

        return redirect(url_for("offertes"))

    regels = conn.execute(
        "SELECT * FROM offerte_regels WHERE offerte_id=? ORDER BY id", (offerte_id,)
    ).fetchall()
    conn.close()
    return render_template("nieuw.html", mode="offerte", vandaag=offerte["datum"],
                           actief="offertes", factuur=offerte, regels=regels,
                           klanten=klantenlijst(), gekozen_klant=None, klussen=[],
                           vooraf_klus="", geldig=offerte["geldig_tot"],
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
    pad, nummer = _offerte_pdf_pad(offerte_id)
    return send_file(pad, mimetype="application/pdf", as_attachment=False,
                     download_name=f"{nummer}.pdf")


@app.route("/offerte/<int:offerte_id>/pdf")
def download_offerte(offerte_id):
    pad, nummer = _offerte_pdf_pad(offerte_id)
    return send_file(pad, as_attachment=True, download_name=f"{nummer}.pdf")


@app.route("/offerte/<int:offerte_id>/vernieuw", methods=["POST"])
def vernieuw_offerte(offerte_id):
    conn = get_db()
    offerte = offerte_of_404(conn, offerte_id)
    conn.close()
    maak_offerte_pdf(offerte_id)
    flash(f"Offerte {offerte['nummer']} is opnieuw gemaakt met je huidige instellingen.")
    return redirect(request.referrer or url_for("offertes"))


@app.route("/offerte/<int:offerte_id>/verstuur", methods=["POST"])
def verstuur_offerte(offerte_id):
    flash(verstuur_offerte_email(offerte_id)[1])
    return redirect(request.referrer or url_for("offertes"))


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
    flash(f"Offerte {offerte['nummer']}: {OFFERTE_STATUS[nieuw_status].lower()}.")
    return redirect(request.referrer or url_for("offertes"))


@app.route("/offerte/<int:offerte_id>/naar-rekening", methods=["POST"])
def offerte_naar_rekening(offerte_id):
    """Maakt van een geaccepteerde offerte een rekening met dezelfde regels. De
    offerte blijft staan als vastlegging van wat er is afgesproken."""
    conn = get_db()
    offerte = offerte_of_404(conn, offerte_id)
    if offerte["factuur_id"]:
        bestaat = conn.execute(
            "SELECT id FROM facturen WHERE id=?", (offerte["factuur_id"],)
        ).fetchone()
        if bestaat:
            conn.close()
            flash(f"Offerte {offerte['nummer']} is al omgezet naar een rekening.")
            return redirect(url_for("bewerk", factuur_id=offerte["factuur_id"]))

    regels = conn.execute(
        "SELECT * FROM offerte_regels WHERE offerte_id=? ORDER BY id", (offerte_id,)
    ).fetchall()
    cur = conn.execute(
        """INSERT INTO facturen (nummer, datum, klant_id, klant_naam, klant_adres,
           klant_email, betaalmethode, status, totaal)
           VALUES ('', ?, ?, ?, ?, ?, 'bank', 'concept', ?)""",
        (
            date.today().isoformat(),
            offerte["klant_id"],
            offerte["klant_naam"],
            offerte["klant_adres"],
            offerte["klant_email"],
            offerte["totaal"],
        ),
    )
    factuur_id = cur.lastrowid
    for r in regels:
        conn.execute(
            """INSERT INTO regels (factuur_id, omschrijving, type, aantal, prijs,
               subtotaal) VALUES (?, ?, ?, ?, ?, ?)""",
            (factuur_id, r["omschrijving"], r["type"], r["aantal"], r["prijs"],
             r["subtotaal"]),
        )
    conn.execute(
        "UPDATE offertes SET factuur_id=?, status='geaccepteerd' WHERE id=?",
        (factuur_id, offerte_id),
    )
    conn.commit()
    conn.close()

    maak_pdf(factuur_id)
    flash(f"Offerte {offerte['nummer']} staat nu als concept-rekening klaar. "
          "Controleer hem en verstuur hem als hij klopt; dan krijgt hij zijn nummer.")
    return redirect(url_for("bewerk", factuur_id=factuur_id))


@app.route("/offerte/<int:offerte_id>/aanbetaling", methods=["GET", "POST"])
def offerte_aanbetaling(offerte_id):
    """Maakt een rekening voor een deel van de offerte, bijvoorbeeld dertig procent
    vooraf. De offerte blijft gewoon staan, zodat je de rest later kunt factureren."""
    conn = get_db()
    offerte = offerte_of_404(conn, offerte_id)

    if request.method != "POST":
        conn.close()
        return render_template("aanbetaling.html", offerte=offerte, actief="offertes")

    try:
        deel = _getal(request.form.get("percentage"))
    except ValueError:
        deel = 0.0
    if not 0 < deel <= 100:
        conn.close()
        flash("Vul een percentage in tussen 1 en 100.")
        return redirect(url_for("offerte_aanbetaling", offerte_id=offerte_id))

    bedrag = round(offerte["totaal"] * deel / 100, 2)
    omschrijving = (request.form.get("omschrijving", "").strip()
                    or f"Aanbetaling {deel:g}% van offerte {offerte['nummer']}")

    cur = conn.execute(
        """INSERT INTO facturen (nummer, datum, klant_id, klant_naam, klant_adres,
           klant_email, betaalmethode, status, totaal)
           VALUES ('', ?, ?, ?, ?, ?, 'bank', 'concept', ?)""",
        (
            date.today().isoformat(),
            offerte["klant_id"],
            offerte["klant_naam"],
            offerte["klant_adres"],
            offerte["klant_email"],
            bedrag,
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
    flash(f"Aanbetaling van € {nl_bedrag(bedrag)} staat als concept-rekening klaar. "
          "De offerte blijft staan, zodat je de rest later kunt factureren.")
    return redirect(url_for("bewerk", factuur_id=factuur_id))


@app.route("/offerte/<int:offerte_id>/verwijder", methods=["POST"])
def offerte_verwijder(offerte_id):
    conn = get_db()
    offerte = offerte_of_404(conn, offerte_id)
    conn.execute("DELETE FROM offerte_regels WHERE offerte_id=?", (offerte_id,))
    conn.execute("DELETE FROM offertes WHERE id=?", (offerte_id,))
    conn.commit()
    conn.close()

    pdf = os.path.join(PDF_DIR, f"{offerte['nummer']}.pdf")
    if os.path.exists(pdf):
        os.remove(pdf)

    flash(f"Offerte {offerte['nummer']} verwijderd.")
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


def maak_pdf(factuur_id):
    """Tekent de rekening en geeft het pad naar de PDF terug."""
    conn = get_db()
    factuur = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    regels = conn.execute("SELECT * FROM regels WHERE factuur_id=?", (factuur_id,)).fetchall()
    s = get_settings()
    conn.close()

    doc = dict(factuur)
    doc["soort"] = "factuur"
    doc["weergavenummer"] = factuur["nummer"] or "CONCEPT"
    return _teken_document(os.path.join(PDF_DIR, pdf_bestandsnaam(factuur)), doc, regels, s)


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
        self.c = canvas.Canvas(pad, pagesize=A4)
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
    vel.y -= 30 * mm
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

    vel.y -= 16 * mm
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

    vel.y = min(links_y, rechts_y) - 12 * mm


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
    vel.y -= 8 * mm


def _teken_regels(vel, regels):
    """De regels zelf. Past er niets meer op, dan gaat het door op een vervolgvel."""
    c = vel.c
    _teken_kolomkoppen(vel)

    for r in regels:
        if vel.y < 78 * mm:
            vel.nieuwe_pagina(vervolg=True)
            _teken_kolomkoppen(vel)

        soort_info = soort(r["type"])
        c.setFillColor(INKT)
        c.setFont("Helvetica", 9.5)
        c.drawString(vel.links, vel.y,
                     vel.kort(r["omschrijving"], "Helvetica", 9.5,
                              vel.kolom_aantal - vel.links - 6 * mm))
        # Bij een vaste prijs per klus zeggen aantal en tarief niets; alleen het bedrag.
        if soort_info["eenheid"]:
            aantal = f"{r['aantal']:g}".replace(".", ",")
            c.drawRightString(vel.kolom_aantal, vel.y, f"{aantal} {soort_info['eenheid']}")
            c.drawRightString(vel.kolom_prijs, vel.y, nl_bedrag(r["prijs"]))
        c.drawRightString(vel.rechts, vel.y, nl_bedrag(r["subtotaal"]))

        c.setFont("Helvetica", 7.5)
        c.setFillColor(GRIJS)
        c.drawString(vel.links, vel.y - 4 * mm, soort_info["naam"])

        vel.y -= 9 * mm
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


def _teken_toelichting(vel, toelichting):
    """Wat er wel en niet bij de prijs zit; staat vooral op een offerte."""
    vel.y -= 14 * mm
    vel.label("Toelichting", vel.links, vel.y)
    vel.y -= 5.5 * mm
    vel.c.setFont("Helvetica", 9)
    vel.c.setFillColor(GRIJS_DONKER)
    for alinea in toelichting.split("\n"):
        for regel in _regels_afbreken(vel.c, alinea.strip(), "Helvetica", 9,
                                      vel.rechts - vel.links):
            vel.c.drawString(vel.links, vel.y, regel)
            vel.y -= 4.6 * mm


def _teken_betaalstrook(vel, doc, s, vervalt):
    """De afscheurbare strook onderaan een rekening, met alles wat de klant nodig
    heeft om te betalen. Bij contant afrekenen staat er 'Voldaan' en geen IBAN."""
    c = vel.c
    if vel.y < 80 * mm:
        vel.nieuwe_pagina()

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

    vel.label("Overmaken naar", vel.links, strook_y - 8 * mm)
    regel_y = strook_y - 14 * mm
    for naam, waarde in [
        ("IBAN", s.get("iban", "")),
        ("T.n.v.", tenaamstelling(s)),
        ("Vóór", filter_datum_nl(vervalt)),
    ]:
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
    vel.label("Te betalen", vak_x + vak_b - 5 * mm, vak_y + vak_h - 6 * mm, rechts_uit=True)
    c.setFillColor(ORANJE)
    c.setFont("Helvetica-Bold", 16)
    c.drawRightString(vak_x + vak_b - 5 * mm, vak_y + 5 * mm,
                      f"€ {nl_bedrag(doc['totaal'])}")


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

    # Een offerte hoeft geen einddatum te hebben; laat je het veld leeg, dan staat er
    # niets over geldigheid op het vel.
    vervalt = doc.get("geldig_tot") if offerte else vervaldatum(doc["datum"])
    kopregel = f"{naam}   ·   {filter_datum_nl(doc['datum'])}"
    if offerte and vervalt:
        kopregel += f"   ·   geldig tot {filter_datum_nl(vervalt)}"

    _teken_merkregel(vel, s)
    _teken_titel(vel, kopregel)
    _teken_partijen(vel, s, doc)
    _teken_regels(vel, regels)
    _teken_totaal(vel, doc["totaal"])

    toelichting = (doc.get("toelichting") or "").strip()
    if toelichting:
        _teken_toelichting(vel, toelichting)

    if not offerte:
        _teken_betaalstrook(vel, doc, s, vervalt)

    _teken_voetregel(vel, offerte)
    return vel.bewaar()


def _mail_pdf(s, ontvanger, onderwerp, tekst, pad, bestandsnaam, extra=None):
    """Stuurt de PDF als bijlage, eventueel met extra bestanden erbij als (pad, naam).
    Geeft (gelukt, melding) terug; de melding is bedoeld om aan de gebruiker te tonen
    en zegt wat er precies misging."""
    msg = EmailMessage()
    msg["Subject"] = onderwerp
    msg["From"] = s.get("smtp_van") or s.get("smtp_user")
    msg["To"] = ontvanger
    msg.set_content(tekst)
    with open(pad, "rb") as f:
        msg.add_attachment(
            f.read(), maintype="application", subtype="pdf", filename=bestandsnaam
        )

    for extra_pad, extra_naam in extra or []:
        if not os.path.exists(extra_pad):
            continue
        soort, _ = mimetypes.guess_type(extra_naam)
        hoofd, _, onder = (soort or "application/octet-stream").partition("/")
        with open(extra_pad, "rb") as f:
            msg.add_attachment(f.read(), maintype=hoofd, subtype=onder,
                               filename=extra_naam)

    try:
        with smtplib.SMTP(s["smtp_host"], int(s["smtp_port"]), timeout=20) as server:
            server.starttls()
            if s.get("smtp_user"):
                server.login(s["smtp_user"], s["smtp_pass"])
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        return False, ("De mailserver weigert je gebruikersnaam of wachtwoord. Bij iCloud "
                       "en Gmail heb je een app-specifiek wachtwoord nodig, niet je gewone.")
    except smtplib.SMTPRecipientsRefused:
        return False, f"De mailserver weigert het adres {ontvanger}."
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

    return True, ""


def bonnen_bij_factuur(conn, factuur_id):
    """De bestanden die mee moeten met deze rekening: de bijlagen die op 'meesturen'
    staan bij de klussen waarvan de uren op deze rekening staan."""
    rijen = conn.execute(
        """SELECT DISTINCT b.bestand, b.naam FROM bijlagen b
           JOIN regels r ON r.klus_id = b.klus_id
           WHERE r.factuur_id = ? AND b.meesturen = 1
           ORDER BY b.id""",
        (factuur_id,),
    ).fetchall()
    return [(os.path.join(BIJLAGE_DIR, r["bestand"]), r["naam"]) for r in rijen]


def verstuur_email(factuur_id):
    """Mailt de rekening naar de klant en zet hem op verzonden. Een concept krijgt
    hierbij zijn nummer: de rekening gaat de deur uit, dus vanaf nu ligt hij vast."""
    conn = get_db()
    factuur = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    if factuur is None:
        conn.close()
        abort(404)
    s = get_settings()

    # Eerst kijken of het mailen überhaupt kan; anders krijgt een concept een nummer
    # voor een mail die nooit is verstuurd.
    if factuur["klant_email"] and s.get("smtp_host"):
        factuur = maak_definitief(conn, factuur_id)
    bonnen = bonnen_bij_factuur(conn, factuur_id)
    conn.close()

    if not factuur["klant_email"]:
        return False, ("Deze klant heeft geen e-mailadres. Vul dat in bij de rekening "
                       "of bij de klant.")
    if not s.get("smtp_host"):
        return False, ("Er is nog geen mailserver ingesteld. Vul die in onder "
                       "Instellingen → Mailen.")

    bestandsnaam = pdf_bestandsnaam(factuur)
    pad = os.path.join(PDF_DIR, bestandsnaam)
    if not os.path.exists(pad):
        maak_pdf(factuur_id)

    naam = factuurnaam(factuur)
    gelukt, melding = _mail_pdf(
        s,
        factuur["klant_email"],
        f"Rekening {naam} - {s.get('naam', '')}",
        f"Beste {factuur['klant_naam']},\n\n"
        f"Hierbij de rekening ({naam}) voor het uitgevoerde werk."
        + (f" De bonnetjes zitten erbij."
           if bonnen else "")
        + f"\n\nMet vriendelijke groet,\n{s.get('naam', '')}",
        pad,
        bestandsnaam,
        bonnen,
    )
    if not gelukt:
        return False, melding

    conn = get_db()
    conn.execute("UPDATE facturen SET status='verzonden' WHERE id=?", (factuur_id,))
    conn.commit()
    conn.close()
    return True, f"Rekening {naam} gemaild naar {factuur['klant_email']}."


def herinnering_email(factuur_id):
    """Stuurt een vriendelijke herinnering met de rekening er nog eens bij. Noemt
    hoeveel er nog openstaat en hoe lang de vervaldatum al voorbij is."""
    conn = get_db()
    factuur = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    if factuur is None:
        conn.close()
        abort(404)
    betaald = betaald_op(conn, factuur_id)
    s = get_settings()
    conn.close()

    if factuur["status"] == "betaald":
        return False, "Deze rekening is al betaald; er valt niets te herinneren."
    if not factuur["nummer"]:
        return False, ("Dit is nog een concept. Verstuur de rekening eerst; daarna kun "
                       "je een herinnering sturen.")
    if not factuur["klant_email"]:
        return False, ("Deze klant heeft geen e-mailadres. Vul dat in bij de rekening "
                       "of bij de klant.")
    if not s.get("smtp_host"):
        return False, ("Er is nog geen mailserver ingesteld. Vul die in onder "
                       "Instellingen → Mailen.")

    bestandsnaam = pdf_bestandsnaam(factuur)
    pad = os.path.join(PDF_DIR, bestandsnaam)
    if not os.path.exists(pad):
        maak_pdf(factuur_id)

    openstaand = round(factuur["totaal"] - betaald, 2)
    vervalt = vervaldatum(factuur["datum"])
    try:
        te_laat = (date.today() - date.fromisoformat(vervalt)).days
    except ValueError:
        te_laat = 0

    if te_laat > 0:
        opening = (f"De vervaldatum van {filter_datum_nl(vervalt)} is inmiddels "
                   f"{te_laat} dag{'en' if te_laat != 1 else ''} geleden.")
    else:
        opening = f"De rekening staat open tot {filter_datum_nl(vervalt)}."

    # Is er al iets binnen, dan hoort de herinnering niet om het hele bedrag te vragen.
    bedragregel = f"Het openstaande bedrag is € {nl_bedrag(openstaand)}."
    if betaald > 0:
        bedragregel += (f" Van het totaal van € {nl_bedrag(factuur['totaal'])} is er al "
                        f"€ {nl_bedrag(betaald)} ontvangen, waarvoor dank.")

    gelukt, melding = _mail_pdf(
        s,
        factuur["klant_email"],
        f"Herinnering: rekening {factuur['nummer']} - {s.get('naam', '')}",
        f"Beste {factuur['klant_naam']},\n\n"
        f"Deze rekening ({factuur['nummer']}) staat nog open. {opening}\n\n"
        f"{bedragregel}\n\n"
        "Wellicht is hij aan je aandacht ontsnapt; is hij inmiddels betaald, dan kun je "
        "dit bericht negeren. De rekening zit voor de zekerheid nog een keer bijgevoegd."
        f"\n\nMet vriendelijke groet,\n{s.get('naam', '')}",
        pad,
        bestandsnaam,
    )
    if not gelukt:
        return False, melding
    return True, (f"Herinnering voor rekening {factuur['nummer']} gemaild naar "
                  f"{factuur['klant_email']}.")


def verstuur_offerte_email(offerte_id):
    """Mailt de offerte naar de klant en zet hem op verzonden."""
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

    pad = os.path.join(PDF_DIR, f"{offerte['nummer']}.pdf")
    if not os.path.exists(pad):
        maak_offerte_pdf(offerte_id)

    gelukt, melding = _mail_pdf(
        s,
        offerte["klant_email"],
        f"Offerte {offerte['nummer']} - {s.get('naam', '')}",
        f"Beste {offerte['klant_naam']},\n\n"
        f"Hierbij de offerte ({offerte['nummer']}) voor het besproken werk."
        + (f" De prijs geldt tot {filter_datum_nl(offerte['geldig_tot'])}."
           if offerte["geldig_tot"] else "")
        + f"\n\nMet vriendelijke groet,\n{s.get('naam', '')}",
        pad,
        f"{offerte['nummer']}.pdf",
    )
    if not gelukt:
        return False, melding

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
    """Toont de rekening in de browser zelf, zonder hem te downloaden."""
    pad, bestandsnaam = _pdf_pad(factuur_id)
    return send_file(pad, mimetype="application/pdf", as_attachment=False,
                     download_name=bestandsnaam)


@app.route("/factuur/<int:factuur_id>/vernieuw", methods=["POST"])
def vernieuw_pdf(factuur_id):
    """Tekent de rekening opnieuw, bijvoorbeeld nadat je je logo of IBAN hebt gewijzigd."""
    conn = get_db()
    factuur = conn.execute("SELECT id, nummer FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    conn.close()
    if factuur is None:
        abort(404)
    maak_pdf(factuur_id)
    flash(f"Rekening {factuurnaam(factuur)} is opnieuw gemaakt met je huidige instellingen.")
    return redirect(request.referrer or url_for("index"))


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

    melding = f"{len(ids)} rekening{'en' if len(ids) != 1 else ''}"
    if offerte_ids:
        melding += f" en {len(offerte_ids)} offerte{'s' if len(offerte_ids) != 1 else ''}"
    flash(f"{melding} opnieuw gemaakt met je huidige instellingen.")
    return redirect(url_for("instellingen"))


@app.route("/factuur/<int:factuur_id>/pdf")
def download_pdf(factuur_id):
    pad, bestandsnaam = _pdf_pad(factuur_id)
    return send_file(pad, as_attachment=True, download_name=bestandsnaam)


@app.route("/factuur/<int:factuur_id>/verstuur", methods=["POST"])
def verstuur(factuur_id):
    _, melding = verstuur_email(factuur_id)
    flash(melding)
    return redirect(request.referrer or url_for("index"))


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
        flash(f"Rekening {factuur['nummer']} was al definitief.")
        return redirect(request.referrer or url_for("index"))

    factuur = maak_definitief(conn, factuur_id)
    conn.close()
    flash(f"De rekening heeft nummer {factuur['nummer']} gekregen.")
    return redirect(request.referrer or url_for("index"))


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
                (factuur_id, date.today().isoformat(), rest),
            )
        # Onthoud waar de rekening vandaan komt, zodat terugzetten geen gok is.
        conn.execute(
            "UPDATE facturen SET status='betaald', status_voor_betaald=? WHERE id=?",
            (factuur["status"], factuur_id),
        )
        conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("index"))


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
    flash(f"Rekening {factuurnaam(factuur)} staat weer open.")
    return redirect(request.referrer or url_for("index"))


@app.route("/factuur/<int:factuur_id>/kopieer", methods=["POST"])
def kopieer(factuur_id):
    """Maakt een nieuw concept met dezelfde klant en dezelfde regels. Handig bij werk
    dat elke maand terugkomt; je hoeft dan alleen de datum en de aantallen na te lopen."""
    conn = get_db()
    origineel = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    if origineel is None:
        conn.close()
        abort(404)

    cur = conn.execute(
        """INSERT INTO facturen (nummer, datum, klant_id, klant_naam, klant_adres,
           klant_email, betaalmethode, status, totaal)
           VALUES ('', ?, ?, ?, ?, ?, ?, 'concept', ?)""",
        (
            date.today().isoformat(),
            origineel["klant_id"],
            origineel["klant_naam"],
            origineel["klant_adres"],
            origineel["klant_email"],
            origineel["betaalmethode"],
            origineel["totaal"],
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
    flash(f"Kopie van rekening {factuurnaam(origineel)} staat klaar als nieuw concept. "
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
            flash("Vul een bedrag in dat groter is dan nul.")
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
        flash(f"€ {nl_bedrag(bedrag)} geboekt.")
        return redirect(url_for("betalingen", factuur_id=factuur_id))

    lijst = betalingen_van(conn, factuur_id)
    betaald = betaald_op(conn, factuur_id)
    conn.close()
    return render_template(
        "betalingen.html", factuur=factuur, naam=factuurnaam(factuur),
        betalingen=lijst, betaald=betaald,
        openstaand=round(factuur["totaal"] - betaald, 2),
        vandaag=date.today().isoformat(), actief="index",
    )


@app.route("/betaling/<int:betaling_id>/verwijder", methods=["POST"])
def betaling_verwijder(betaling_id):
    conn = get_db()
    betaling = conn.execute("SELECT * FROM betalingen WHERE id=?", (betaling_id,)).fetchone()
    if betaling is None:
        conn.close()
        abort(404)
    factuur_id = betaling["factuur_id"]
    conn.execute("DELETE FROM betalingen WHERE id=?", (betaling_id,))
    herzie_betaalstatus(conn, factuur_id)
    conn.commit()
    conn.close()
    flash(f"Betaling van € {nl_bedrag(betaling['bedrag'])} verwijderd.")
    return redirect(url_for("betalingen", factuur_id=factuur_id))


@app.route("/factuur/<int:factuur_id>/herinnering", methods=["POST"])
def herinnering(factuur_id):
    _, melding = herinnering_email(factuur_id)
    flash(melding)
    return redirect(request.referrer or url_for("index"))


@app.route("/factuur/<int:factuur_id>/verwijder", methods=["POST"])
def verwijder(factuur_id):
    conn = get_db()
    factuur = conn.execute("SELECT id, nummer FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    if factuur is None:
        conn.close()
        abort(404)
    # Uren die op deze rekening stonden komen weer vrij om te factureren.
    conn.execute("UPDATE uren SET factuur_id=NULL WHERE factuur_id=?", (factuur_id,))
    conn.execute("DELETE FROM regels WHERE factuur_id=?", (factuur_id,))
    conn.execute("DELETE FROM betalingen WHERE factuur_id=?", (factuur_id,))
    conn.execute("DELETE FROM facturen WHERE id=?", (factuur_id,))
    conn.commit()
    conn.close()

    # Het PDF-bestand hoort mee te gaan; anders blijft het als los bestand achter.
    pdf = os.path.join(PDF_DIR, pdf_bestandsnaam(factuur))
    if os.path.exists(pdf):
        os.remove(pdf)

    flash(f"Rekening {factuurnaam(factuur)} verwijderd.")
    return redirect(request.referrer or url_for("index"))


init_db()

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
