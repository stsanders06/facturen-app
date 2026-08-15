import logging
import os
import secrets
import sqlite3
import smtplib
from datetime import date, timedelta
from email.message import EmailMessage
from flask import (
    Flask, abort, request, redirect, url_for, render_template, send_file, flash
)
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from werkzeug.utils import secure_filename

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
DB_PATH = os.path.join(DATA_DIR, "facturen.db")
PDF_DIR = os.path.join(DATA_DIR, "pdfs")
LOGO_DIR = os.path.join(DATA_DIR, "logo")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(PDF_DIR, exist_ok=True)
os.makedirs(LOGO_DIR, exist_ok=True)


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

MAANDEN = [
    "jan", "feb", "mrt", "apr", "mei", "jun",
    "jul", "aug", "sep", "okt", "nov", "dec",
]

# Aantal dagen dat de klant heeft om te betalen; komt als "Vóór ..." op de strook.
BETAALTERMIJN_DAGEN = 14

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
            tikkie_link TEXT DEFAULT '',
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
            FOREIGN KEY (factuur_id) REFERENCES facturen (id)
        );
        """
    )
    conn.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")

    # Kolommen die later zijn toegevoegd, bijzetten in bestaande databases.
    bestaand = {rij["name"] for rij in conn.execute("PRAGMA table_info(settings)")}
    for kolom, definitie in [("tenaamstelling", "TEXT DEFAULT ''")]:
        if kolom not in bestaand:
            conn.execute(f"ALTER TABLE settings ADD COLUMN {kolom} {definitie}")

    conn.commit()
    conn.close()


def tenaamstelling(s):
    """Naam van de rekeninghouder voor op de factuur. Vaak dezelfde als je eigen
    naam, maar bij een en/of-rekening of een rekening op naam van je partner niet."""
    return (s.get("tenaamstelling") or "").strip() or s.get("naam", "")


def get_settings():
    conn = get_db()
    row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    conn.close()
    return dict(row) if row else {}


def volgend_nummer():
    conn = get_db()
    jaar = date.today().year
    count = conn.execute(
        "SELECT COUNT(*) FROM facturen WHERE nummer LIKE ?", (f"{jaar}-%",)
    ).fetchone()[0]
    conn.close()
    return f"{jaar}-{count + 1:03d}"


@app.route("/")
def index():
    conn = get_db()
    facturen = conn.execute("SELECT * FROM facturen ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("index.html", facturen=facturen, actief="index")


@app.route("/instellingen", methods=["GET", "POST"])
def instellingen():
    if request.method == "POST":
        conn = get_db()
        logo_bestand = request.form.get("bestaand_logo", "")
        logo_file = request.files.get("logo")
        if logo_file and logo_file.filename:
            filename = secure_filename(logo_file.filename)
            logo_file.save(os.path.join(LOGO_DIR, filename))
            logo_bestand = filename
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


@app.route("/nieuw", methods=["GET", "POST"])
def nieuw():
    if request.method == "POST":
        conn = get_db()
        nummer = volgend_nummer()
        omschrijvingen = request.form.getlist("omschrijving")
        types = request.form.getlist("type")
        aantallen = request.form.getlist("aantal")
        prijzen = request.form.getlist("prijs")

        totaal = 0.0
        regels = []
        for o, t, a, p in zip(omschrijvingen, types, aantallen, prijzen):
            if not o:
                continue
            a = float(a or 0)
            p = float(p or 0)
            subtotaal = a * p
            totaal += subtotaal
            regels.append((o, t, a, p, subtotaal))

        cur = conn.execute(
            """INSERT INTO facturen (nummer, datum, klant_naam, klant_adres, klant_email,
               betaalmethode, status, totaal) VALUES (?, ?, ?, ?, ?, ?, 'concept', ?)""",
            (
                nummer,
                request.form.get("datum") or date.today().isoformat(),
                request.form.get("klant_naam", ""),
                request.form.get("klant_adres", ""),
                request.form.get("klant_email", ""),
                request.form.get("betaalmethode", "bank"),
                totaal,
            ),
        )
        factuur_id = cur.lastrowid
        for o, t, a, p, subtotaal in regels:
            conn.execute(
                """INSERT INTO regels (factuur_id, omschrijving, type, aantal, prijs, subtotaal)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (factuur_id, o, t, a, p, subtotaal),
            )
        conn.commit()
        conn.close()

        maak_pdf(factuur_id)

        if request.form.get("verstuur") == "ja":
            verstuur_email(factuur_id)

        return redirect(url_for("index"))

    return render_template("nieuw.html", vandaag=date.today().isoformat(), actief="nieuw")


def maak_pdf(factuur_id):
    """Tekent de rekening: ruime opzet met een oranje rand en merkregel bovenaan,
    en onderaan een afscheurbare betaalstrook met alles wat nodig is om te betalen."""
    conn = get_db()
    factuur = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    regels = conn.execute("SELECT * FROM regels WHERE factuur_id=?", (factuur_id,)).fetchall()
    s = get_settings()
    conn.close()

    pad = os.path.join(PDF_DIR, f"{factuur['nummer']}.pdf")
    c = canvas.Canvas(pad, pagesize=A4)
    c.setTitle(f"Rekening {factuur['nummer']}")
    breedte, hoogte = A4

    links = 26 * mm
    rechts = breedte - 18 * mm
    kolom_aantal = rechts - 62 * mm
    kolom_prijs = rechts - 32 * mm

    def rand():
        """De oranje bies langs de linkerkant, op elke pagina."""
        c.setFillColor(ORANJE)
        c.rect(0, 0, 6 * mm, hoogte, stroke=0, fill=1)

    def kort(tekst, font, grootte, maxbreedte):
        tekst = tekst or ""
        if c.stringWidth(tekst, font, grootte) <= maxbreedte:
            return tekst
        while tekst and c.stringWidth(tekst + "\u2026", font, grootte) > maxbreedte:
            tekst = tekst[:-1]
        return tekst + "\u2026"

    def label(tekst, x, y, rechts_uit=False):
        """Kleine grijze kapitalen met ruime letterafstand."""
        c.setFont("Helvetica-Bold", 6.5)
        c.setFillColor(GRIJS)
        letters = " ".join(tekst.upper())
        (c.drawRightString if rechts_uit else c.drawString)(x, y, letters)

    rand()
    y = hoogte - 22 * mm

    # ---- Merkregel: logo, naam, contact ----
    tekst_x = links
    logo_bestand = s.get("logo_bestand") or ""
    logo_pad = os.path.join(LOGO_DIR, logo_bestand) if logo_bestand else None
    if logo_pad and os.path.exists(logo_pad):
        try:
            c.drawImage(logo_pad, links, y - 12 * mm, width=16 * mm, height=14 * mm,
                        preserveAspectRatio=True, anchor="sw", mask="auto")
            tekst_x = links + 20 * mm
        except Exception:
            tekst_x = links

    c.setFillColor(INKT)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(tekst_x, y - 3 * mm, s.get("naam", ""))
    c.setFont("Helvetica", 8.5)
    c.setFillColor(GRIJS_DONKER)
    contact = " \u00b7 ".join(
        deel for deel in [
            ((s.get("adres") or "").split("\n") or [""])[0].strip(),
            (s.get("telefoon") or "").strip(),
            (s.get("email") or "").strip(),
        ] if deel
    )
    c.drawString(tekst_x, y - 8 * mm, kort(contact, "Helvetica", 8.5, rechts - tekst_x))

    # ---- Titel ----
    y -= 30 * mm
    c.setFillColor(ORANJE)
    c.setFont("Helvetica", 30)
    c.drawString(links, y, "Factuur")

    y -= 7 * mm
    c.setFillColor(GRIJS_DONKER)
    c.setFont("Helvetica", 9)
    vervalt = vervaldatum(factuur["datum"])
    c.drawString(links, y, f"{factuur['nummer']}   \u00b7   {filter_datum_nl(factuur['datum'])}")

    # ---- Van en Voor naast elkaar ----
    y -= 16 * mm
    label("Van", links, y)
    label("Voor", links + 68 * mm, y)
    y -= 5.5 * mm

    c.setFillColor(INKT)
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(links, y, s.get("naam", ""))
    c.drawString(links + 68 * mm, y, factuur["klant_naam"] or "")

    c.setFont("Helvetica", 9)
    c.setFillColor(GRIJS_DONKER)
    links_y = rechts_y = y - 4.8 * mm
    for regel in (s.get("adres") or "").split("\n"):
        if regel.strip():
            c.drawString(links, links_y, kort(regel.strip(), "Helvetica", 9, 62 * mm))
            links_y -= 4.4 * mm
    for regel in (factuur["klant_adres"] or "").split("\n"):
        if regel.strip():
            c.drawString(links + 68 * mm, rechts_y, kort(regel.strip(), "Helvetica", 9, 62 * mm))
            rechts_y -= 4.4 * mm
    if factuur["klant_email"]:
        c.drawString(links + 68 * mm, rechts_y, kort(factuur["klant_email"], "Helvetica", 9, 62 * mm))
        rechts_y -= 4.4 * mm

    y = min(links_y, rechts_y) - 12 * mm

    # ---- Regels ----
    def kolomkoppen(y):
        label("Omschrijving", links, y)
        label("Aantal", kolom_aantal, y, rechts_uit=True)
        label("Prijs", kolom_prijs, y, rechts_uit=True)
        label("Bedrag", rechts, y, rechts_uit=True)
        y -= 3 * mm
        c.setStrokeColor(INKT)
        c.setLineWidth(0.8)
        c.line(links, y, rechts, y)
        return y - 8 * mm

    y = kolomkoppen(y)

    soorten = {"materiaal": "Materiaal", "arbeid": "Arbeid"}
    for r in regels:
        if y < 78 * mm:
            c.showPage()
            rand()
            # Op een vervolgpagina opnieuw vertellen waar je naar kijkt.
            c.setFillColor(GRIJS)
            c.setFont("Helvetica", 8.5)
            c.drawString(links, hoogte - 18 * mm,
                         f"Factuur {factuur['nummer']} · vervolg")
            y = kolomkoppen(hoogte - 28 * mm)

        c.setFillColor(INKT)
        c.setFont("Helvetica", 9.5)
        c.drawString(links, y, kort(r["omschrijving"], "Helvetica", 9.5, kolom_aantal - links - 6 * mm))
        c.drawRightString(kolom_aantal, y, f"{r['aantal']:g}".replace(".", ","))
        c.drawRightString(kolom_prijs, y, nl_bedrag(r["prijs"]))
        c.drawRightString(rechts, y, nl_bedrag(r["subtotaal"]))

        c.setFont("Helvetica", 7.5)
        c.setFillColor(GRIJS)
        c.drawString(links, y - 4 * mm, soorten.get(r["type"], r["type"] or ""))

        y -= 9 * mm
        c.setStrokeColor(LIJN)
        c.setLineWidth(0.5)
        c.line(links, y, rechts, y)
        y -= 7 * mm

    # ---- Totaal ----
    label("Totaal", rechts - 42 * mm, y + 1 * mm, rechts_uit=True)
    c.setFillColor(ORANJE)
    c.setFont("Helvetica-Bold", 17)
    c.drawRightString(rechts, y - 1 * mm, f"\u20ac {nl_bedrag(factuur['totaal'])}")

    # ---- Betaalstrook, altijd onderaan het vel ----
    if y < 80 * mm:
        c.showPage()
        rand()

    strook_y = 62 * mm
    c.setStrokeColor(STIPPEL)
    c.setLineWidth(0.8)
    c.setDash(2, 3)
    c.line(links, strook_y, rechts, strook_y)
    c.setDash()

    contant = factuur["betaalmethode"] == "cash"
    gespreid = " ".join("VOLDAAN" if contant else "BETAALSTROOK")
    tekstbreedte = c.stringWidth(gespreid, "Helvetica-Bold", 6.5)
    midden = links + (rechts - links) / 2
    c.setFillColor(WIT)
    c.rect(midden - tekstbreedte / 2 - 3 * mm, strook_y - 1.6 * mm,
           tekstbreedte + 6 * mm, 3.4 * mm, stroke=0, fill=1)
    c.setFillColor(GRIJS)
    c.setFont("Helvetica-Bold", 6.5)
    c.drawCentredString(midden, strook_y - 1 * mm, gespreid)

    if contant:
        c.setFillColor(INKT)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(links, strook_y - 16 * mm, "Contant afgehandeld.")
        c.setFont("Helvetica", 9)
        c.setFillColor(GRIJS_DONKER)
        c.drawString(links, strook_y - 22 * mm, "Deze rekening is ter plekke voldaan.")
    else:
        label("Overmaken naar", links, strook_y - 8 * mm)
        regel_y = strook_y - 14 * mm
        for naam, waarde in [
            ("IBAN", s.get("iban", "")),
            ("T.n.v.", tenaamstelling(s)),
            ("O.v.v.", factuur["nummer"]),
            ("V\u00f3\u00f3r", filter_datum_nl(vervalt)),
        ]:
            c.setFont("Helvetica", 9)
            c.setFillColor(GRIJS)
            c.drawString(links, regel_y, naam)
            c.setFillColor(INKT)
            c.drawString(links + 18 * mm, regel_y, waarde or "")
            regel_y -= 5.2 * mm

        vak_b, vak_h = 58 * mm, 20 * mm
        vak_x, vak_y = rechts - vak_b, strook_y - 26 * mm
        c.setStrokeColor(ORANJE)
        c.setLineWidth(1.4)
        c.roundRect(vak_x, vak_y, vak_b, vak_h, 2 * mm, stroke=1, fill=0)
        label("Te betalen", vak_x + vak_b - 5 * mm, vak_y + vak_h - 6 * mm, rechts_uit=True)
        c.setFillColor(ORANJE)
        c.setFont("Helvetica-Bold", 16)
        c.drawRightString(vak_x + vak_b - 5 * mm, vak_y + 5 * mm,
                          f"\u20ac {nl_bedrag(factuur['totaal'])}")

    c.save()
    return pad


def verstuur_email(factuur_id):
    conn = get_db()
    factuur = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    s = get_settings()
    conn.close()

    if not factuur["klant_email"] or not s.get("smtp_host"):
        return False

    pad = os.path.join(PDF_DIR, f"{factuur['nummer']}.pdf")
    if not os.path.exists(pad):
        maak_pdf(factuur_id)

    msg = EmailMessage()
    msg["Subject"] = f"Rekening {factuur['nummer']} - {s.get('naam', '')}"
    msg["From"] = s.get("smtp_van") or s.get("smtp_user")
    msg["To"] = factuur["klant_email"]
    msg.set_content(
        f"Beste {factuur['klant_naam']},\n\n"
        f"Hierbij de rekening ({factuur['nummer']}) voor het uitgevoerde werk.\n\n"
        f"Met vriendelijke groet,\n{s.get('naam', '')}"
    )
    with open(pad, "rb") as f:
        msg.add_attachment(
            f.read(), maintype="application", subtype="pdf", filename=f"{factuur['nummer']}.pdf"
        )

    with smtplib.SMTP(s["smtp_host"], int(s["smtp_port"])) as server:
        server.starttls()
        server.login(s["smtp_user"], s["smtp_pass"])
        server.send_message(msg)

    conn = get_db()
    conn.execute("UPDATE facturen SET status='verzonden' WHERE id=?", (factuur_id,))
    conn.commit()
    conn.close()
    return True


def _pdf_pad(factuur_id):
    conn = get_db()
    factuur = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    conn.close()
    if factuur is None:
        abort(404)
    pad = os.path.join(PDF_DIR, f"{factuur['nummer']}.pdf")
    if not os.path.exists(pad):
        maak_pdf(factuur_id)
    return pad, factuur["nummer"]


@app.route("/factuur/<int:factuur_id>/bekijk")
def bekijk_pdf(factuur_id):
    """Toont de rekening in de browser zelf, zonder hem te downloaden."""
    pad, nummer = _pdf_pad(factuur_id)
    return send_file(pad, mimetype="application/pdf", as_attachment=False,
                     download_name=f"{nummer}.pdf")


@app.route("/factuur/<int:factuur_id>/pdf")
def download_pdf(factuur_id):
    pad, nummer = _pdf_pad(factuur_id)
    return send_file(pad, as_attachment=True, download_name=f"{nummer}.pdf")


@app.route("/factuur/<int:factuur_id>/verstuur", methods=["POST"])
def verstuur(factuur_id):
    ok = verstuur_email(factuur_id)
    if ok:
        flash("Rekening verzonden.")
    else:
        flash("Versturen mislukt: check klant e-mail en SMTP instellingen.")
    return redirect(url_for("index"))


@app.route("/factuur/<int:factuur_id>/betaald", methods=["POST"])
def markeer_betaald(factuur_id):
    conn = get_db()
    conn.execute("UPDATE facturen SET status='betaald' WHERE id=?", (factuur_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/factuur/<int:factuur_id>/verwijder", methods=["POST"])
def verwijder(factuur_id):
    conn = get_db()
    conn.execute("DELETE FROM regels WHERE factuur_id=?", (factuur_id,))
    conn.execute("DELETE FROM facturen WHERE id=?", (factuur_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


init_db()

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
