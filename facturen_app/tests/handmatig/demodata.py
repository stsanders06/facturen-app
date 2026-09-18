"""Vult de demo-database met een administratie die op een echte lijkt: klanten,
offertes en rekeningen in elke stand, klussen in elke fase, notities met foto's."""
import os
import sqlite3
import sys
from datetime import date, timedelta

DB = "/tmp/facturen-demo/facturen.db"
BIJLAGEN = "/tmp/facturen-demo/bijlagen"

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000140000000f00802000000"
    + "00" * 4
)

def vandaag(dagen=0):
    return (date.today() + timedelta(days=dagen)).isoformat()

conn = sqlite3.connect(DB)
conn.execute("UPDATE settings SET naam='Venlo Hogedruk', adres='Kaldenkerkerweg 214\n5915 AC Venlo', "
             "telefoon='077 351 24 80', email='info@venlohogedruk.nl', iban='NL91 ABNA 0417 1643 00' WHERE id=1")

klanten = [
    ("Bouwbedrijf Janssen", "Straelseweg 112\n5911 CN Venlo", "administratie@janssenbouw.nl", "077 351 24 80"),
    ("J. Peters", "Molenstraat 8\n5913 XJ Venlo", "j.peters@example.com", "06 21 44 90 12"),
    ("VvE Kazernestraat", "Kazernestraat 45\n5928 NL Venlo", "bestuur@vvekazernestraat.nl", ""),
    ("Garage Nabben", "Hogeweg 3\n5916 PA Venlo", "", "077 382 11 90"),
    ("Fam. de Wit", "Kerkstraat 21\n5931 AB Tegelen", "dewit@example.com", "06 55 12 88 03"),
]
klant_id = {}
for naam, adres, mail, tel in klanten:
    klant_id[naam] = conn.execute(
        "INSERT INTO klanten (naam, adres, email, telefoon) VALUES (?, ?, ?, ?)",
        (naam, adres, mail, tel)).lastrowid

def rekening(nummer, klant, datum, status, regels, kenmerk="", betaald=0.0):
    k = klant_id[klant]
    rij = conn.execute("SELECT adres, email FROM klanten WHERE id=?", (k,)).fetchone()
    totaal = round(sum(a * p for _, _, a, p in regels), 2)
    # Demo-rekeningen krijgen een termijn, zodat "Te laat" in de demodata zichtbaar blijft.
    from datetime import date as _date, timedelta as _td
    vervalt = (_date.fromisoformat(datum) + _td(days=14)).isoformat()
    fid = conn.execute(
        """INSERT INTO facturen (nummer, datum, vervalt_op, klant_id, klant_naam, klant_adres,
           klant_email, status, totaal, kenmerk) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (nummer, datum, vervalt, k, klant, rij[0], rij[1], status, totaal, kenmerk)).lastrowid
    for oms, soort, aantal, prijs in regels:
        conn.execute("""INSERT INTO regels (factuur_id, omschrijving, type, aantal, prijs, subtotaal)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                     (fid, oms, soort, aantal, prijs, round(aantal * prijs, 2)))
    if betaald:
        conn.execute("""INSERT INTO betalingen (factuur_id, datum, bedrag, notitie, automatisch)
                        VALUES (?, ?, ?, 'Deelbetaling per bank', 0)""",
                     (fid, vandaag(-9), betaald))
    return fid

# Een concept met veertien regels, om het inklappen te kunnen zien.
lang = [(naam, "materiaal", 1, prijs) for naam, prijs in [
    ("Reinigingsmiddel gevelvriendelijk 5 l", 18.50), ("Afdekfolie 4 x 5 m", 12.95),
    ("Schilderstape 50 mm", 6.40), ("Kwastenset", 14.25), ("Werkhandschoenen", 9.80),
    ("Sproeikop 25 graden", 21.00), ("Slangkoppeling 3/8", 7.35), ("Emmer 20 l", 5.60),
    ("Microvezeldoeken (10)", 11.20), ("Ontvetter 2 l", 16.90), ("Schuurpapier korrel 120", 4.75),
    ("Voegenplamuur", 8.30), ("Sanitairkit wit", 6.95), ("Voorrijkosten", 35.00)]]
rekening("", "Bouwbedrijf Janssen", vandaag(0), "concept", lang, kenmerk="Gevel Straelseweg")

rekening("2026-014", "Bouwbedrijf Janssen", vandaag(-28), "verzonden",
         [("Gevel reinigen, hogedruk 200 bar", "arbeid_uur", 11, 55.0),
          ("Reinigingsmiddel", "materiaal", 2, 18.5),
          ("Voorrijkosten", "arbeid_klus", 1, 35.0)],
         kenmerk="Gevel Straelseweg", betaald=292.0)
rekening("2026-016", "J. Peters", vandaag(-12), "verzonden",
         [("Oprit en tuinpad reinigen", "arbeid_dag", 2, 310.0)], kenmerk="Oprit")
rekening("2026-017", "VvE Kazernestraat", vandaag(-7), "verzonden",
         [("Terras en bergingen", "arbeid_uur", 4.5, 52.5)])
rekening("2026-013", "Fam. de Wit", vandaag(-41), "betaald",
         [("Zonnepanelen reinigen", "arbeid_klus", 1, 275.0)])
rekening("2026-012", "Garage Nabben", vandaag(-53), "betaald",
         [("Bestrating loods", "arbeid_dag", 3, 310.0), ("Zand aanvullen", "materiaal", 5, 8.9)])

# Offertes in elke stand.
def offerte(nummer, klant, datum, status, regels, geldig=None, factuur_id=None, kenmerk=""):
    k = klant_id[klant]
    rij = conn.execute("SELECT adres, email FROM klanten WHERE id=?", (k,)).fetchone()
    totaal = round(sum(a * p for _, _, a, p in regels), 2)
    oid = conn.execute(
        """INSERT INTO offertes (nummer, datum, geldig_tot, klant_id, klant_naam, klant_adres,
           klant_email, status, totaal, toelichting, factuur_id, kenmerk)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (nummer, datum, geldig or "", k, klant, rij[0], rij[1], status, totaal,
         "Inclusief materiaal en afvoer. Steiger wordt door ons geregeld.", factuur_id, kenmerk)).lastrowid
    for oms, soort, aantal, prijs in regels:
        conn.execute("""INSERT INTO offerte_regels (offerte_id, omschrijving, type, aantal, prijs, subtotaal)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                     (oid, oms, soort, aantal, prijs, round(aantal * prijs, 2)))
    return oid

offerte("O2026-006", "Bouwbedrijf Janssen", vandaag(-18), "verzonden",
        [("Gevel reinigen inclusief steiger", "arbeid_klus", 1, 1450.0)], geldig=vandaag(12))
offerte("O2026-007", "VvE Kazernestraat", vandaag(-40), "verzonden",
        [("Alle bergingen en het binnenterrein", "arbeid_dag", 4, 310.0)], geldig=vandaag(-5))
offerte("O2026-005", "J. Peters", vandaag(-60), "geaccepteerd",
        [("Oprit en tuinpad", "arbeid_dag", 2, 310.0)], geldig=vandaag(-30))
offerte("", "Garage Nabben", vandaag(-2), "concept",
        [("Voorterrein reinigen", "arbeid_uur", 8, 55.0)], kenmerk="Nog bespreken")

# Klussen in elke fase.
def klus(naam, klant, status, gestart, tarief=55.0, aangevraagd=None, intake="", dagen=()):
    kid = conn.execute(
        """INSERT INTO klussen (naam, klant_id, uurtarief, notitie, status, gestart,
           aangevraagd_op, intake_op) VALUES (?, ?, ?, '', ?, ?, ?, ?)""",
        (naam, klant_id.get(klant), tarief, status, gestart, aangevraagd or gestart, intake)).lastrowid
    for datum, van, tot, notitie in dagen:
        conn.execute("""INSERT INTO uren (klus_id, datum, van, tot, notitie)
                        VALUES (?, ?, ?, ?, ?)""", (kid, datum, van, tot, notitie))
    return kid

lopend = klus("Gevel reinigen Straelseweg", "Bouwbedrijf Janssen", "open", vandaag(-8),
              aangevraagd=vandaag(-18), intake=vandaag(-14),
              dagen=[(vandaag(-8), "08:00", "12:30", "Steiger opgebouwd, voorzijde"),
                     (vandaag(-7), "08:00", "11:00", "Zijgevel en kozijnen"),
                     (vandaag(-6), "13:00", "15:30", "Achtergevel, nabehandeling"),
                     (vandaag(-5), "08:00", "09:00", "Steiger afgebouwd")])
klus("Oprit Garage Nabben", "Garage Nabben", "open", vandaag(-3), tarief=52.5,
     dagen=[(vandaag(-3), "09:00", "16:30", "Hele oprit gedaan")])
klus("Dakgoot Molenstraat", "J. Peters", "aangevraagd", vandaag(-9), aangevraagd=vandaag(-9))
klus("Terras Kazernestraat", "VvE Kazernestraat", "aangevraagd", vandaag(-3),
     aangevraagd=vandaag(-3), intake=vandaag(-1))
klus("Zonnepanelen Fam. de Wit", "Fam. de Wit", "afgerond", vandaag(-45), tarief=55.0,
     dagen=[(vandaag(-45), "08:00", "13:00", "")])
klus("Losse klus zonder klant", None, "open", vandaag(-1), tarief=0)

# Notities met foto's bij de lopende klus.
os.makedirs(BIJLAGEN, exist_ok=True)
def foto(naam, notitie_id=None, klus_id=lopend, meesturen=0):
    bestand = f"demo-{naam}"
    with open(os.path.join(BIJLAGEN, bestand), "wb") as f:
        f.write(PNG)
    conn.execute("""INSERT INTO bijlagen (klus_id, bestand, naam, toegevoegd, meesturen, notitie_id)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                 (klus_id, bestand, naam, vandaag(-8), meesturen, notitie_id))

n1 = conn.execute("""INSERT INTO notities (klus_id, wanneer, tekst) VALUES (?, ?, ?)""",
    (lopend, vandaag(-14),
     "Drie punten uit het gesprek:\n- achtergevel hoort er ook bij\n"
     "- steiger mag blijven staan tot vrijdag\n- poort op slot, sleutel bij de buren")).lastrowid
foto("situatie-voor.png", n1)
foto("gevel-detail.png", n1)
conn.execute("""INSERT INTO notities (klus_id, wanneer, tekst) VALUES (?, ?, ?)""",
             (lopend, vandaag(-6), "Voegwerk bij het kozijn is zachter dan gedacht, "
              "op lagere druk gedaan."))
foto("bon-reinigingsmiddel.png", None, lopend, meesturen=1)
foto("bon-huur-steiger.png", None, lopend, meesturen=0)

conn.commit()
print("demo-administratie klaar")
