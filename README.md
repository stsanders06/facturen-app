# Facturen App (Home Assistant addon)

Simpele rekeningen-app voor losse klussen. Geen BTW-administratie, geen KvK-koppeling,
gewoon materiaalkosten + arbeidsloon optellen, PDF maken en versturen.

## Installatie

1. Zet deze map (`invoice-addon`) op je Home Assistant systeem in `/addons/facturen_app`.
   Makkelijkste manier: installeer de "Samba share" of "SSH & Web Terminal" addon vanuit
   de HA addon store, en kopieer de map naar `\\<ha-ip>\addons\facturen_app` of via `scp`.
2. Ga in Home Assistant naar **Instellingen > Add-ons > Add-on Store**, klik rechtsboven
   op de drie puntjes en kies **Reload** (of herstart Supervisor).
3. De addon "Facturen App" verschijnt onderaan bij **Local add-ons**. Installeer hem.
4. Start de addon. Ga naar `http://<ha-ip>:8099` in je browser.

## Eerste gebruik

1. Ga naar **Instellingen** in de app: vul naam, adres, IBAN, en eventueel Tikkie-link in.
   Upload je logo.
2. Voor automatisch mailen: vul SMTP-gegevens in. Voor Gmail gebruik je een
   [app-wachtwoord](https://myaccount.google.com/apppasswords), niet je normale wachtwoord.
3. Ga naar **Nieuw** om een rekening te maken: klantgegevens, regels voor materiaal en
   arbeid, betaalmethode kiezen. Bij opslaan wordt automatisch een PDF gegenereerd.

## Data en back-up

Alle data (database, PDF's, logo) staat in de persistente `/data` map van de addon.
Die overleeft addon-updates en herstarts van Home Assistant, maar wordt niet automatisch
meegenomen in een HA-snapshot tenzij je "Add-on data" meeneemt in je back-up instellingen.
Controleer dat.

## Belangrijk

Dit is geen fiscaal geldige factuur zolang er geen KvK-inschrijving achter zit. Voor
incidentele bijverdiensten is dat meestal geen probleem, maar bij structurele inkomsten
moet je dit gewoon opgeven bij de Belastingdienst als resultaat uit overige werkzaamheden.
