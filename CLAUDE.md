# Facturen App — werkafspraken

Home Assistant add-on: een simpele rekeningen- en offerte-app voor losse klussen.
Flask + SQLite + reportlab, alles in `facturen_app/app/main.py` en de templates ernaast.
Geen build-stap, geen frontend-framework: server-side Jinja met CSS in de templates zelf.

## Draaien en testen

Er is geen venv in de repo. Zelf een maken:

```bash
python3 -m venv .venv
.venv/bin/pip install -r facturen_app/app/requirements.txt \
                     -r facturen_app/tests/requirements.txt
```

Tests draaien vanaf de hoofdmap (`pytest.ini` wijst naar `facturen_app/tests`):

```bash
.venv/bin/python -m pytest
```

De app lokaal starten met een eigen datamap, zodat je niet in `/data` schrijft:

```bash
DATA_DIR=/tmp/facturen-demo PORT=8573 .venv/bin/python facturen_app/app/main.py
```

Op die poort vraagt de app om in te loggen. Wil je dat overslaan tijdens het testen,
zet dan de header die Home Assistant Ingress normaal meestuurt:

```bash
curl -H "X-Ingress-Path: /" http://127.0.0.1:8573/
```

Voor het testen in een browser kun je de app achter een klein WSGI-laagje hangen dat
die header voor elk verzoek zet. Gebruik `"/"` als waarde: waar genoeg om de inlog over
te slaan, leeg genoeg om de URLs normaal te houden.

## Afspraken

- **Alles in het Nederlands**: variabelen, functienamen, commentaar, meldingen aan de
  gebruiker en commitberichten. Ook de tests, inclusief de testnamen.
- **Commentaar legt uit waaróm**, niet wat er staat. Kijk hoe het er nu staat: bij
  `volgend_nummer` staat waarom er op het hoogste nummer wordt doorgeteld en niet op het
  aantal, bij de iOS-regels in `klus.html` staat wat er op het toestel is nagemeten. Zulk
  commentaar houden.
- **Meldingen aan de gebruiker zijn gewone taal.** "De mailserver weigert je
  gebruikersnaam of wachtwoord", niet "SMTP authentication failed".
- **Elke wijziging krijgt tests.** Er staan er nu 254.

### Valkuil bij de tests

`conftest.py` maakt de database schoon tussen tests via de lijst `TABELLEN`. **Voeg je een
tabel toe, zet hem daar dan meteen bij**, anders lekt data van de ene test naar de andere
en krijg je fouten die alleen optreden als de hele suite draait, niet los. Datzelfde geldt
voor bestanden op schijf: de opruiming leegt `PDF_DIR` en `BIJLAGE_DIR`.

## Nog te doen

### Nakijken

- [x] **Smalle weergave (telefoonbreedte) nagekeken** in 1.12.1, met een headless
      Chromium op 320, 390, 430, 620, 900 en 1280px breed. Vier fouten gevonden en
      verholpen; geen pagina hoeft nog horizontaal te schuiven. De regels die dat
      voorkomen staan onder test in `test_smalle_weergave.py`. De Chrome-extensie deed
      het niet; met Playwright kun je wél op elke breedte meten en screenshots maken.
- [ ] **iOS is niet getest.** Deze app heeft twee keer eerder een iOS-specifieke bug
      gehad met de breedte van datum- en tijdvelden (zie het commentaar onderaan
      `klus.html`). Dat soort fouten vangt geen enkele emulator; alleen kijken op het
      echte toestel helpt. Nieuwe formuliervelden dus altijd even op de telefoon nalopen.

### Ideeën die zijn blijven liggen

Uit een eerdere lijst wel besproken maar bewust niet gebouwd. Niet vergeten, wel bewust
uitgesteld:

- **Vaste regels / prijslijst** — eigen materialen en tarieven opslaan en met
  autocomplete invoegen, in plaats van elke keer overtypen.
- **Jaaroverzicht en export** — omzet per maand en jaar, plus een CSV-export van alle
  rekeningen voor de aangifte of de boekhouder.
- **BTW optioneel aanzetten** — een schakelaar in Instellingen (21/9/0%, BTW-nummer op de
  PDF), zodat de app bruikbaar blijft als er wél een inschrijving komt.
- **Sensors naar Home Assistant** — `sensor.openstaand_bedrag` en dergelijke, zodat er
  automatiseringen en dashboardkaarten op te bouwen zijn.
- **Notificatie bij een verlopen rekening** via de Home Assistant-app.
- **Back-up-knop** — de hele database als één bestand exporteren en importeren, naast de
  back-up van Home Assistant zelf.

### Niet doen

- **Tikkie.** Er is geen API voor particuliere Tikkie-accounts, en een los verzoek is
  hooguit 35 dagen geldig en maximaal 30 betalingen — een vaste link in de instellingen
  gaat dus regelmatig dood en dat is vervelender dan geen QR. Is er in 1.3.0 uitgegaan en
  in 1.12.0 zijn de laatste resten opgeruimd. Niet opnieuw voorstellen.

## Uitbrengen

`config.yaml` en de constante `VERSIE` in `main.py` horen hetzelfde versienummer te
hebben. Bij een push naar `main` bouwt de CI het image; Home Assistant biedt de update
daarna zelf aan. Zet in `CHANGELOG.md` wat er voor de gebruiker verandert — in gewone
taal, niet in technische termen.
