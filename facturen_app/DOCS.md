# Facturen App

Simpele rekeningen-app voor losse klussen. Geen BTW-administratie, geen KvK-koppeling:
materiaalkosten + arbeidsloon optellen, PDF maken en versturen.

## Installatie

1. Ga naar **Instellingen → Add-ons → Add-on Store**.
2. Klik rechtsboven op de drie puntjes → **Repositories**.
3. Voeg toe: `https://github.com/stsanders06/facturen-app`
4. De add-on **Facturen App** verschijnt in de store. Klik erop en kies **Installeren**.
   De eerste keer duurt dit een paar minuten, omdat het image lokaal gebouwd wordt.
5. Zet **Start on boot** en **Watchdog** aan als je dat wilt, en klik **Starten**.
6. Open de app op **`http://<ip-van-home-assistant>:8099`** in je browser. Zet daarnaast
   **Show in sidebar** aan als je hem ook vanuit het zijmenu van Home Assistant wilt
   openen; beide werken tegelijk.

## Configuratie

De add-on heeft één optie:

| Optie | Standaard | Betekenis |
| --- | --- | --- |
| `log_level` | `info` | Hoeveel detail er in het add-on log komt. Gebruik `debug` bij problemen. |

De app-instellingen zelf (bedrijfsnaam, IBAN, logo, SMTP) staan niet in de add-on
configuratie maar in de app onder **Instellingen**. Dat scheelt herstarten bij elke
wijziging.

### De app openen

Er zijn twee manieren, die allebei standaard aanstaan:

- **In je browser:** `http://<ip-van-home-assistant>:8099`. Handig om te bookmarken op je
  telefoon of laptop, en je hoeft niet eerst in Home Assistant in te loggen.
- **In de zijbalk van Home Assistant:** zet **Show in sidebar** aan bij de add-on.

Let op: op poort 8099 zit geen wachtwoord, dus iedereen in je netwerk kan erbij. Gebruik
hem dus alleen op je eigen netwerk en zet hem niet open naar internet. Wil je dat niet,
maak dan het poortveld leeg onder **Configuratie → Netwerk**; dan werkt alleen de zijbalk
van Home Assistant nog, die wél achter je HA-login zit.

## Eerste gebruik

1. Ga naar **Instellingen** in de app: vul naam, adres, IBAN en eventueel een
   Tikkie-link in. Upload je logo.
2. Voor automatisch mailen: vul de SMTP-gegevens in. Voor Gmail gebruik je een
   [app-wachtwoord](https://myaccount.google.com/apppasswords), niet je normale
   wachtwoord.
3. Ga naar **Nieuw** om een rekening te maken: klantgegevens, regels voor materiaal en
   arbeid, betaalmethode kiezen. Bij opslaan wordt automatisch een PDF gegenereerd.

## Uren bijhouden

Onder het tabblad **Uren** maak je een klus aan, bijvoorbeeld "badkamer Kerkstraat". Geef
er eventueel een klant en een uurtarief bij. Op de kluspagina zet je per gewerkte dag een
regel neer: de datum, van hoe laat tot hoe laat en een notitie voor jezelf. De uren worden
opgeteld; werk je door tot na middernacht, dan telt dat gewoon door.

Ga je de klus factureren, kies hem dan bij een nieuwe rekening onder **Regels** bij "Uren
van een klus toevoegen". Er komt één arbeidsregel op de rekening met het totaal aantal
uren maal het uurtarief. De losse dagen en je notities blijven op het Uren-tabblad en
komen dus niet op de factuur te staan. Vanaf de kluspagina kan het ook andersom, met de
knop **Op rekening zetten**.

Een klus die klaar is zet je op afgerond. Hij zakt dan naar onderen in de lijst, maar de
uren blijven bewaard.

## Data en back-up

Alle data (database, PDF's, logo) staat in de persistente `/data` map van de add-on.
Die overleeft add-on updates en herstarts. Een Home Assistant back-up neemt de data
mee zonder dat de add-on gestopt hoeft te worden, mits je bij het maken van de back-up
de add-on aanvinkt.

## Problemen oplossen

**De add-on start niet.** Zet `log_level` op `debug`, herstart en kijk bij het
**Log** tabblad wat er misgaat.

**Mailen lukt niet.** Controleer host, poort (meestal 587) en of je een app-wachtwoord
gebruikt. De app meldt "Versturen mislukt" als de klant geen e-mailadres heeft of als
er geen SMTP-host is ingevuld.

**De pagina laadt niet in je browser.** Typ het adres voluit inclusief `http://` en met
een schuine streep aan het eind: `http://192.168.1.50:8099/`. Browsers maken een
ingetypt adres tegenwoordig vaak automatisch `https://`, en daar luistert de add-on niet
op — je krijgt dan een lege pagina zonder duidelijke foutmelding. Werkt het daarna nog
steeds niet, controleer dan of poort `8099` is ingevuld onder **Configuratie → Netwerk**
en stop en start de add-on daarna (alleen opslaan is niet genoeg).

**De pagina laadt niet in de sidebar.** Herstart de add-on; Ingress heeft een draaiende
add-on nodig.

## Belangrijk

Dit is geen fiscaal geldige factuur zolang er geen KvK-inschrijving achter zit. Voor
incidentele bijverdiensten is dat meestal geen probleem, maar bij structurele inkomsten
moet je dit opgeven bij de Belastingdienst als resultaat uit overige werkzaamheden.
