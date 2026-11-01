"""Een nieuw wachtwoord zetten als je het kwijt bent.

Bij de add-on is de zijbalk van Home Assistant de herstelroute: daar ben je al
binnen en is het oude wachtwoord niet nodig. Als losse container is er geen
zijbalk. Wie op de computer zelf een opdracht kan geven, mag er ook zonder het
oude wachtwoord in; daarom kan het hier, en niet via de website.

    docker compose exec facturen wachtwoord-herstellen
"""
import getpass
import os
import sqlite3
import sys
from datetime import date

from werkzeug.security import generate_password_hash

# Gelijk aan WACHTWOORD_MINIMUM in main.py. main.py importeren kan niet: dat
# opent bij het laden de database en ruimt op, en dat hoort niet bij herstellen.
MINIMUM = 8


def herstel(db_pad, wachtwoord, naam=None):
    """Zet het wachtwoord van het account en geef de gebruikersnaam terug.

    Is er nog geen account, dan maakt dit er een met de gegeven naam; zonder
    naam is dat een fout, want de app zelf vraagt er dan om."""
    if len(wachtwoord) < MINIMUM:
        raise ValueError(f"Kies een wachtwoord van minstens {MINIMUM} tekens.")
    if not os.path.exists(db_pad):
        raise ValueError(f"Er is geen administratie gevonden in {os.path.dirname(db_pad)}.")
    conn = sqlite3.connect(db_pad)
    try:
        rij = conn.execute("SELECT id, naam FROM gebruikers ORDER BY id LIMIT 1").fetchone()
        if rij is None:
            if not (naam or "").strip():
                raise ValueError("Er is nog geen account. Open de app in je browser en "
                                 "kies daar een gebruikersnaam en wachtwoord.")
            naam = naam.strip()
            conn.execute(
                "INSERT INTO gebruikers (naam, wachtwoord, aangemaakt) VALUES (?, ?, ?)",
                (naam, generate_password_hash(wachtwoord), date.today().isoformat()),
            )
        else:
            naam = rij[1]
            conn.execute("UPDATE gebruikers SET wachtwoord=? WHERE id=?",
                         (generate_password_hash(wachtwoord), rij[0]))
        conn.commit()
    finally:
        conn.close()
    return naam


def main():
    data_dir = os.environ.get("DATA_DIR", "/data")
    db_pad = os.path.join(data_dir, "facturen.db")
    eerste = getpass.getpass("Nieuw wachtwoord: ")
    tweede = getpass.getpass("Nog een keer: ")
    if eerste != tweede:
        print("De twee wachtwoorden zijn niet hetzelfde. Er is niets veranderd.")
        return 1
    try:
        naam = herstel(db_pad, eerste)
    except ValueError as fout:
        print(f"{fout} Er is niets veranderd.")
        return 1
    print(f"Gelukt. Log in als {naam} met je nieuwe wachtwoord.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
