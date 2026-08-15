import os
import sqlite3
import smtplib
from datetime import date
from email.message import EmailMessage
from flask import Flask, request, redirect, url_for, render_template, send_file, flash
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

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "verander-mij")


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
    conn.commit()
    conn.close()


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
    return render_template("index.html", facturen=facturen)


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
               tikkie_link=?, logo_bestand=?, smtp_host=?, smtp_port=?, smtp_user=?,
               smtp_pass=?, smtp_van=? WHERE id=1""",
            (
                request.form.get("naam", ""),
                request.form.get("adres", ""),
                request.form.get("telefoon", ""),
                request.form.get("email", ""),
                request.form.get("iban", ""),
                request.form.get("tikkie_link", ""),
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

    return render_template("instellingen.html", s=get_settings())


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

    return render_template("nieuw.html", vandaag=date.today().isoformat())


def maak_pdf(factuur_id):
    conn = get_db()
    factuur = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    regels = conn.execute("SELECT * FROM regels WHERE factuur_id=?", (factuur_id,)).fetchall()
    s = get_settings()
    conn.close()

    pad = os.path.join(PDF_DIR, f"{factuur['nummer']}.pdf")
    c = canvas.Canvas(pad, pagesize=A4)
    breedte, hoogte = A4
    y = hoogte - 20 * mm

    logo_pad = os.path.join(LOGO_DIR, s.get("logo_bestand", "")) if s.get("logo_bestand") else None
    if logo_pad and os.path.exists(logo_pad):
        try:
            c.drawImage(logo_pad, 20 * mm, y - 20 * mm, width=40 * mm, height=20 * mm, preserveAspectRatio=True, mask="auto")
        except Exception:
            pass

    c.setFont("Helvetica-Bold", 16)
    c.drawRightString(breedte - 20 * mm, y, "REKENING")
    y -= 8 * mm
    c.setFont("Helvetica", 10)
    c.drawRightString(breedte - 20 * mm, y, f"Nummer: {factuur['nummer']}")
    y -= 5 * mm
    c.drawRightString(breedte - 20 * mm, y, f"Datum: {factuur['datum']}")

    y -= 20 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20 * mm, y, s.get("naam", ""))
    y -= 5 * mm
    c.setFont("Helvetica", 10)
    for regel in (s.get("adres") or "").split("\n"):
        if regel.strip():
            c.drawString(20 * mm, y, regel.strip())
            y -= 5 * mm
    if s.get("telefoon"):
        c.drawString(20 * mm, y, s["telefoon"])
        y -= 5 * mm
    if s.get("email"):
        c.drawString(20 * mm, y, s["email"])
        y -= 5 * mm

    y -= 10 * mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(20 * mm, y, "Aan:")
    y -= 5 * mm
    c.setFont("Helvetica", 10)
    c.drawString(20 * mm, y, factuur["klant_naam"] or "")
    y -= 5 * mm
    for regel in (factuur["klant_adres"] or "").split("\n"):
        if regel.strip():
            c.drawString(20 * mm, y, regel.strip())
            y -= 5 * mm

    y -= 12 * mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(20 * mm, y, "Omschrijving")
    c.drawString(110 * mm, y, "Aantal")
    c.drawString(135 * mm, y, "Prijs")
    c.drawRightString(breedte - 20 * mm, y, "Subtotaal")
    y -= 3 * mm
    c.line(20 * mm, y, breedte - 20 * mm, y)
    y -= 7 * mm

    c.setFont("Helvetica", 10)
    for r in regels:
        c.drawString(20 * mm, y, r["omschrijving"][:45])
        c.drawString(110 * mm, y, f"{r['aantal']:g}")
        c.drawString(135 * mm, y, f"\u20ac {r['prijs']:.2f}")
        c.drawRightString(breedte - 20 * mm, y, f"\u20ac {r['subtotaal']:.2f}")
        y -= 6 * mm
        if y < 40 * mm:
            c.showPage()
            y = hoogte - 20 * mm

    y -= 4 * mm
    c.line(20 * mm, y, breedte - 20 * mm, y)
    y -= 8 * mm
    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(breedte - 20 * mm, y, f"Totaal: \u20ac {factuur['totaal']:.2f}")

    y -= 15 * mm
    c.setFont("Helvetica-Bold", 10)
    betaalmethode = factuur["betaalmethode"]
    if betaalmethode == "bank":
        c.drawString(20 * mm, y, "Te betalen per bank:")
        y -= 5 * mm
        c.setFont("Helvetica", 10)
        c.drawString(20 * mm, y, f"IBAN: {s.get('iban', '')}")
        y -= 5 * mm
        c.drawString(20 * mm, y, f"T.n.v.: {s.get('naam', '')}")
        y -= 5 * mm
        c.drawString(20 * mm, y, f"O.v.v.: {factuur['nummer']}")
    elif betaalmethode == "tikkie":
        c.drawString(20 * mm, y, "Te betalen via Tikkie:")
        y -= 5 * mm
        c.setFont("Helvetica", 10)
        c.drawString(20 * mm, y, s.get("tikkie_link", ""))
    else:
        c.drawString(20 * mm, y, "Contant afgehandeld.")

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


@app.route("/factuur/<int:factuur_id>/pdf")
def download_pdf(factuur_id):
    conn = get_db()
    factuur = conn.execute("SELECT * FROM facturen WHERE id=?", (factuur_id,)).fetchone()
    conn.close()
    pad = os.path.join(PDF_DIR, f"{factuur['nummer']}.pdf")
    if not os.path.exists(pad):
        maak_pdf(factuur_id)
    return send_file(pad, as_attachment=True, download_name=f"{factuur['nummer']}.pdf")


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
    app.run(host="0.0.0.0", port=8099)
