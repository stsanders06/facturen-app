# Facturen App — werkafspraken

Home Assistant add-on: een simpele rekeningen- en offerte-app voor losse klussen.
Flask + SQLite + reportlab, alles in `facturen_app/app/main.py` en de templates ernaast.
Geen build-stap, geen frontend-framework: server-side Jinja met CSS in de templates zelf.

Daarnaast `bon_ocr/`: een tweede container die een gescande bon leest (RapidOCR +
een regelparser) zodat 'Scan als bon' voorgevuld klaarstaat. Draait alleen bij
docker compose, niet in Home Assistant, en is nooit vanaf het netwerk bereikbaar.
Zie DOCKER.md, hoofdstuk 5.

## Draaien en testen

Er is geen venv in de repo. Zelf een maken:

```bash
python3 -m venv .venv
.venv/bin/pip install -r facturen_app/app/requirements.txt \
                     -r facturen_app/tests/requirements.txt
```

Tests draaien vanaf de hoofdmap (`pytest.ini` wijst naar `facturen_app/tests` en
`bon_ocr/tests`):

```bash
.venv/bin/python -m pytest
```

De parsertests gebruiken meegeleverde OCR-tekst, dus daar is geen onnxruntime of
container voor nodig. Wil je de lezer zelf draaien, dan heb je een eigen venv
nodig met `bon_ocr/requirements.txt`; `pdfimages` en `pdftoppm` (poppler) moeten
op je pad staan:

```bash
PORT=8599 ocr-venv/bin/python bon_ocr/lezer.py
curl --data-binary @bon.jpg -H "Content-Type: image/jpeg" http://127.0.0.1:8599/lees
```

Wil je de app met de lezer eraan: zet `BON_OCR_URL=http://127.0.0.1:8599/lees`
bij het starten.

**Let op bij een tweede conftest.py:** `bon_ocr/tests` heeft er expres géén.
Twee bestanden met die naam worden dezelfde module, en dan verliest de hele
suite van `facturen_app` zijn fixtures. Het pad staat daarom bovenin
`test_bon_parser.py`.

De app lokaal starten met een eigen datamap, zodat je niet in `/data` schrijft:

```bash
DATA_DIR=/tmp/facturen-demo PORT=8573 .venv/bin/python facturen_app/app/main.py
```

Op die poort vraagt de app om in te loggen. Wil je dat overslaan tijdens het testen,
dan heb je twee dingen nodig: de header die Home Assistant Ingress meestuurt, én
`INGRESS_NETWERK` op je eigen adres. **De header alleen is expres niet genoeg** — dat
was een gat waarmee iedereen op het netwerk zonder wachtwoord binnenkwam, dus de app
kijkt sinds 1.16.0 ook of het verzoek uit het netwerk van Home Assistant komt.

```bash
INGRESS_NETWERK=127.0.0.0/8 DATA_DIR=/tmp/facturen-demo PORT=8573 \
  .venv/bin/python facturen_app/app/main.py
curl -H "X-Ingress-Path: /" http://127.0.0.1:8573/
```

Voor het testen in een browser kun je de app achter een klein WSGI-laagje hangen dat
die header voor elk verzoek zet. Gebruik `"/"` als waarde: waar genoeg om de inlog over
te slaan, leeg genoeg om de URLs normaal te houden.

Zet `INGRESS_NETWERK` nooit in `config.yaml` of in een script dat meegaat naar Home
Assistant: daar hoort alleen het standaardnetwerk te gelden.

## Afspraken

- **Geef Stone altijd de complete code om te plakken.** Geen "doe dan X op
  de Pi" of een commando met `<adres>` erin, maar het hele blok, inclusief
  `ssh` ervoor als het op de Pi moet gebeuren. Zie hoofdstuk Uitbrengen.
- **Alles in het Nederlands**: variabelen, functienamen, commentaar, meldingen aan de
  gebruiker en commitberichten. Ook de tests, inclusief de testnamen.
- **Commentaar legt uit waaróm**, niet wat er staat. Kijk hoe het er nu staat: bij
  `volgend_nummer` staat waarom er op het hoogste nummer wordt doorgeteld en niet op het
  aantal, bij de iOS-regels in `klus.html` staat wat er op het toestel is nagemeten. Zulk
  commentaar houden.
- **Meldingen aan de gebruiker zijn gewone taal.** "De mailserver weigert je
  gebruikersnaam of wachtwoord", niet "SMTP authentication failed".
- **Elke wijziging krijgt tests.** Er staan er nu 1455.

### Taken horen bij de klus

Sinds 2.3.0 hangt een taak aan `klussen` en niet meer aan `notities`: één lijst
onder de notities in plaats van een lijstje per aantekening. De routes staan
onder `/klus/<id>/taak/...`. `_taken_naar_de_klus` verhuist een oude database
eenmalig; die functie kijkt naar de kolom `klus_id` en doet daarna niets meer.
Een notitie verwijderen laat de taken dus staan, een klus verwijderen niet.

### Een veld in een kaart omranden

`_layout.html` zet op `.card .field input:not(...):not(...)...` een doorzichtige
rand. Die selector heeft zoveel specificiteit dat een eigen `border`-regel er
niet van wint zonder `!important`. Gebruik `outline` met een negatieve
`outline-offset`: dat is een andere eigenschap, dus geen strijd, hij volgt de
afgeronde hoeken, en de breedte van het veld verandert niet.

### Een bon met materialen op de rekening

Sinds 2.6.0 zet het bonnenmenu op het rekeningformulier een bon met materialen
neer als losse materiaalregels. `regels.inkoop_id` is uniek; die houdt de
eerste regel van een bon, en de volgende regels van dezelfde bon wijzen ernaar
met `regels.bon_id`. `_herbouw_regels` neemt `bon_id` mee. In het formulier en in de tupels tot
`bewaar_regels` dragen beide soorten de bon op dezelfde plek (`regel_inkoop`,
index 6), zodat de controles op klant en "staat al elders" voor allebei
gelden. Pas `bewaar_regels` splitst ze.

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
- [x] **Hele app doorgelopen** in 1.20.0, op 320, 390, 430, 768 en 1280px. De scripts
      staan in `facturen_app/tests/handmatig/`: `doorloop.py` meet hoe het eruitziet,
      `klikpaden.py` of het werkt, en `demodata.py` vult een administratie die op een
      echte lijkt. Ze horen niet bij pytest — er hoort een draaiende app bij; bovenaan
      `doorloop.py` staat hoe je ze start. Playwright zit niet in de requirements, dat
      installeer je erbij:

      ```bash
      .venv/bin/pip install playwright && .venv/bin/playwright install chromium
      ```

      **Valkuil:** met `debug=False` cachet Jinja de templates en zie je een wijziging
      pas na een herstart. Zet in je startscript `TEMPLATES_AUTO_RELOAD` aan, en start
      de app opnieuw zodra je aan `main.py` komt.
- [ ] **iOS is niet getest.** Deze app heeft twee keer eerder een iOS-specifieke bug
      gehad met de breedte van datum- en tijdvelden (zie het commentaar onderaan
      `klus.html`). Dat soort fouten vangt geen enkele emulator; alleen kijken op het
      echte toestel helpt. Nieuwe formuliervelden dus altijd even op de telefoon nalopen.
      **Het datumveld op 'Scan als bon' (2.2.0) is nog niet op een iPhone gezien.**
- [x] **Op de Pi 5 gemeten** in 2.2.1: een bon van 2 MB kost 10 tot 22 seconden en
      piekt op 626 MB. Dat is vier keer trager dan geschat; de timeout ging naar 90 s
      en `mem_limit` naar 1 GB. Het image is 698 MB.
- [ ] **Een echte blinde test staat nog open:** bonnen van winkels die er nog niet
      bij zaten (Gamma, Praxis, Hornbach, Hema, supermarkt, pomp). Die bonnen komen
      de repo niet in; `bon_ocr/tests/bonnen` bevat alleen nepbonnen.

### Over de bonnenlezer

Het hele verhaal — architectuur, gemeten tijden, wat is afgevallen en waarom, en
hoe je een nieuwe winkel toevoegt — staat in [OVERDRACHT.md](OVERDRACHT.md).
Hieronder alleen wat je tijdens het werken moet weten.

- **De parser is het onderhoud, niet de OCR.** In de proef las RapidOCR alle zes
  echte bonnen goed en had de parser er nul helemaal goed. Gaat er iets mis met
  een nieuwe winkel, kijk dan eerst naar `bon_ocr/bon_parser.py` en pas daarna
  naar de OCR. `bon_ocr/tests/bonnen/*.txt` is de tekst die de OCR oplevert; een
  nieuw geval erbij zetten is een tekstbestand plus het juiste antwoord.
- **De `onzeker`-lijst is de kern.** Een veld dat niet klopt moet daarin staan,
  anders ziet Stone een leesfout aan voor een goed gelezen bon. De test
  `test_een_fout_veld_staat_altijd_in_onzeker` bewaakt dat; die mag nooit
  worden versoepeld.
- **Blind is 5 van de 6.** Dat is de eerlijke score (de andere acht bonnen zijn
  de set waarop de parser is gemaakt). Omhoog mag, omlaag niet.
- **Een bon kan ondersteboven binnenkomen.** RapidOCR draait losse regels zelf
  om, dus de tekst is leesbaar, maar de regelvolgorde staat op zijn kop en
  binnen een regel staan de kolommen gespiegeld. `lees_bon` probeert daarom
  beide richtingen en houdt de beste (minste onzekere velden; bij gelijkspel
  wint de gewone volgorde). Het plaatje omdraaien en opnieuw door de OCR halen
  is geprobeerd: dat gaf een slechter resultaat voor het dubbele van de tijd,
  omdat de OCR op het gedraaide beeld andere fouten maakt.
- **Meet tijden op de Pi, niet op een Mac.** Twee keer ging het hierop mis: de
  time-out van de app stond op 30 s terwijl een bon er 22 kost, en `PDF_TIJD`
  stond op 20 s terwijl `pdfimages -png` er 22,5 over deed. Een bon kwam dan
  terug als onleesbaar terwijl er niets mis mee was. `pdfimages -j` doet
  hetzelfde in 0,04 s, want een JPEG hoeft alleen uit het bestand gehaald te
  worden en niet omgezet.
- **Niet elke bon is een plaatje.** Een bon uit de app van een winkel is een PDF
  met een tekstlaag; die tekst gebruiken is foutloos en duizend keer sneller dan
  OCR. `tekst_uit_pdf` gaat daarom vóór `plaatje_uit_pdf`. Let op de valkuil die
  dit opleverde: `pdfimages` haalt uit zo'n PDF alleen het logo en de streepjes-
  code, en "het grootste plaatje" was dus het logo. Vandaar `_groot_genoeg`.
- **Wat een echte bon anders maakt dan een nepbon** (gevonden in 2.2.1, allemaal op
  één Karwei-bon): de OCR verhaspelt woorden, dus vergelijk ze met `_lijkt_op` en
  niet met `in`. Een winkel kent meer soorten aftrek dan "korting", en sommige zijn
  al in de prijs verwerkt (`AL_VERREKEND`) terwijl andere er nog af moeten. En er
  staan meer datums op een bon dan alleen die van de aankoop. Zulke gevallen horen
  als fixture in `bon_ocr/tests/bonnen/echt*`, mét de leesfouten erin, en met de
  naam, het adres en de kaartnummers vervangen.
- **Geen taalmodel.** Gemeten en afgevallen: RapidOCR met Qwen2.5-3B haalde 0 van
  de 6 in 38 s en 2,5 GB, Qwen3-VL 2B idem in 33 s en 3,9 GB, tegen 5 van de 6 in
  ongeveer een seconde met regels. Kleinere modellen lossen dat niet op: ze
  verwarren stukprijs met regeltotaal en verzinnen plausibele bedragen, en dat is
  precies de fout die langs een controle glipt. Pas heroverwegen als
  `data/bon-correcties.jsonl` genoeg echte voorbeelden heeft, en dan als model
  dat regels labelt — nooit als model dat bedragen opschrijft.

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
  **Wel sinds 2.6.0, op verzoek van Stone:** Tikkie als *betaalwijze*
  (`betaalmethode='tikkie'`, `betalingen.wijze`). Dat is alleen een
  aantekening hoe er betaald wordt; er staat geen link of QR op de PDF.

## Uitbrengen

`config.yaml` en de constante `VERSIE` in `main.py` horen hetzelfde versienummer te
hebben. Zet in `CHANGELOG.md` wat er voor de gebruiker verandert — in gewone
taal, niet in technische termen.

**De app draait niet meer in Home Assistant, maar los met docker compose op een
eigen Pi.** De CI test bij een push naar `main` alleen of het bouwt; er komt
niets vanzelf op de Pi. Bijwerken doe je op de Pi zelf, zie DOCKER.md hoofdstuk 3
(back-up, dan `git pull && docker compose up -d --build`).
