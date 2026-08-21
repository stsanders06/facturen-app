# Facturen App

Simpele rekeningen-app voor losse klussen. Geen BTW-administratie, geen KvK-koppeling:
materiaalkosten + arbeidsloon optellen, PDF maken en versturen. Vooraf een prijs
afgeven kan ook, met een offerte.

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

Het mailwachtwoord staat leesbaar in de database in `/data`, want de app moet ermee
kunnen inloggen bij je mailserver. Gebruik daarom een **app-specifiek wachtwoord** en
niet je gewone wachtwoord: zo'n wachtwoord kun je later intrekken zonder dat je verder
iets kwijtraakt.

## Eerste gebruik

1. Ga naar **Instellingen** in de app: vul naam, adres en IBAN in. Upload je logo.
2. Voor automatisch mailen: vul de SMTP-gegevens in. Voor Gmail gebruik je een
   [app-wachtwoord](https://myaccount.google.com/apppasswords), niet je normale
   wachtwoord.
3. Ga naar **Nieuw** om een rekening te maken: klantgegevens, regels voor materiaal en
   arbeid, betaalmethode kiezen. Bij opslaan wordt automatisch een PDF gegenereerd.
4. Wil je eerst een prijs afgeven, begin dan onder **Offertes**. Zegt de klant ja, dan
   maak je er met één knop een rekening van.
5. Heb je je klanten al ergens anders staan, dan lees je ze in één keer in onder
   **Klanten → Importeren**.

## Concept, definitief en betaald

Een nieuwe rekening is eerst een **concept**. Hij heeft dan nog geen nummer: bovenaan de
PDF staat "CONCEPT" en in de lijst "nog geen nummer". Zo laat een rekening die je toch
niet verstuurt geen gat achter in je nummering — handig, want een boekhouding hoort een
doorlopende reeks te hebben.

Het nummer komt er zodra de rekening vastligt. Dat gebeurt vanzelf als je hem **mailt**,
of met **Definitief maken** in het menu als je hem zelf uitprint of appt. Lukt het mailen
niet, dan blijft hij concept: je raakt geen nummer kwijt aan een mail die nooit is
aangekomen.

Op elke rekening staan de knoppen die je op dat moment waarschijnlijk nodig hebt. De rest
zit achter de **drie puntjes** rechts op de kaart: bekijken, bewerken, downloaden,
kopiëren, betalingen, herinnering, PDF vernieuwen en verwijderen.

### Betalingen bijhouden

Is de rekening in één keer voldaan, dan druk je op **Betaald** en ben je klaar. Betaalt
de klant in termijnen, of heb je vooraf een deel gekregen, kies dan **Betalingen** in het
menu. Daar boek je per keer een datum, een bedrag en een notitie. Zodra alles binnen is,
gaat de rekening vanzelf op betaald; haal je een boeking weg, dan gaat hij weer open.

In de lijst zie je bij zo'n rekening staan hoeveel er al binnen is, en bovenaan de pagina
telt **Openstaand** alleen nog wat er echt moet komen.

### Als er niet betaald wordt

Rekeningen waarvan de vervaldatum voorbij is, krijgen een chip **Te laat** en staan apart
in de balk bovenaan, met een eigen filter. Bij **Openstaand** staat de langst wachtende
rekening bovenaan.

Met **Herinnering sturen** in het menu mail je de klant een vriendelijk berichtje met de
rekening er nog een keer bij. De app rekent zelf uit hoeveel dagen de vervaldatum voorbij
is en noemt alleen het bedrag dat nog openstaat; is er al een deel binnen, dan bedankt de
mail daar netjes voor.

### Terugkerend werk

Doe je elke maand hetzelfde bij dezelfde klant, kies dan **Kopiëren naar nieuwe rekening**
in het menu. Je krijgt een nieuw concept met dezelfde klant en dezelfde regels, met de
datum van vandaag. De koppeling met een klus gaat niet mee: die uren staan al op de
rekening waarvan je kopieert.

## Zoeken

Op de rekeningen, de offertes en de klanten staat een zoekveld. Je zoekt op nummer, naam,
e-mailadres, telefoonnummer of adres. Losse woorden hoeven niet in hetzelfde veld te
staan, dus `jansen 2026-007` vindt precies die ene rekening. Heb je een filter aanstaan,
dan blijft het zoeken binnen dat filter.

## Offertes

Onder het tabblad **Offertes** geef je vooraf een prijs af. Het formulier is hetzelfde
als bij een rekening — dezelfde regels voor materiaal en arbeid — met twee extra velden:

- **Geldig tot**: tot wanneer de prijs geldt, bovenaan naast het nummer en de datum.
  Staat standaard op dertig dagen na de offertedatum. In de lijst zie je welke offertes
  verlopen zijn. Wil je er geen einddatum bij, haal dan het vinkje eronder weg: dan komt
  er niets over geldigheid op de PDF en ook niet in de mail aan de klant.
- **Toelichting**: wat er wel en niet bij de prijs zit, hoe lang het werk duurt, dat
  soort dingen. Komt onder de bedragen op de PDF.

De PDF ziet eruit als je rekening, maar zonder de betaalstrook onderaan: er valt nog
niets te betalen. Mailen, downloaden en opnieuw maken werkt net als bij een rekening.
Offertes hebben hun eigen nummers, met `OFF-` ervoor.

Wat de klant ervan vindt leg je vast met **Geaccepteerd** of **Afgewezen**. Zegt de klant
ja, dan maak je er met **Naar rekening** in één klik een rekening van: de klant, de regels
en het bedrag gaan mee. Je komt in het bewerkscherm terecht, zodat je nog iets kunt
aanpassen voordat je hem verstuurt. De offerte zelf blijft staan als vastlegging van wat
er is afgesproken, met een link naar de rekening die eruit is gekomen.

### Een deel vooraf

Moet je eerst materiaal inkopen, kies dan **Aanbetaling in rekening brengen** in het menu
van de offerte. Je vult een percentage in — dertig is de standaard — en de app maakt daar
een concept-rekening van met één regel erop. Wat dat in euro's is, zie je meteen terwijl
je typt. De offerte blijft gewoon staan, zodat je de rest later kunt factureren.

## Uit een CSV-bestand inlezen

### Klanten

Onder **Klanten → Importeren** lees je in één keer een hele klantenlijst in, bijvoorbeeld
een export uit je oude boekhouding, uit Excel of uit Google Contacten. Zet de kopjes op de
eerste regel; puntkomma's, komma's en tabs worden alle drie herkend.

Alleen een kolom **naam** is verplicht. De app herkent dezelfde kolom onder verschillende
namen — `naam`, `klant`, `bedrijf` of `name`; `email`, `e-mail` of `mail`; `telefoon`,
`mobiel` of `phone`. Staan postcode en plaats in aparte kolommen, dan komen die onder het
adres te staan.

Klanten die er al zijn worden overgeslagen. Wil je ze bijwerken met wat er in het bestand
staat, zet dan het vinkje aan; een lege kolom wist dan niet wat je zelf had ingevuld. Na
afloop zie je hoeveel klanten erbij zijn gekomen, zijn bijgewerkt en zijn overgeslagen.
Er staat een voorbeeldbestand klaar om te downloaden.

### Regels van een rekening of offerte

Bij **Regels** kun je een CSV-bestand kiezen in plaats van alles over te typen — handig
bij een materiaallijst of een bon van de groothandel. Kopjes: **omschrijving**,
**aantal**, **prijs** en eventueel **soort** (`materiaal`, `uur`, `dag` of `vaste prijs`).
Bedragen mogen met een komma of een punt, met of zonder euroteken.

Het bestand wordt in je browser gelezen en niet naar de app gestuurd: de regels
verschijnen meteen in het formulier, je kunt ze nog aanpassen en pas als je opslaat komen
ze op de rekening. Zit er iets niet goed bij, dan haal je die regel met het kruisje weg.

## Uren bijhouden

Onder het tabblad **Uren** maak je een klus aan, bijvoorbeeld "badkamer Kerkstraat". Geef
er eventueel een klant en een uurtarief bij. Op de kluspagina zet je per gewerkte dag een
regel neer: de datum, van hoe laat tot hoe laat en een notitie voor jezelf. De uren worden
opgeteld; werk je door tot na middernacht, dan telt dat gewoon door.

Wat je invult wordt vanzelf bewaard zodra je uit een veld klikt; je hoeft er niet apart
op Opslaan te drukken.

Ga je de klus factureren, kies hem dan bij een nieuwe rekening onder **Regels** bij "Uren
van een klus toevoegen". Er komt één arbeidsregel op de rekening met het aantal uren maal
het uurtarief. De losse dagen en je notities blijven op het Uren-tabblad en komen dus niet
op de rekening te staan. Vanaf de kluspagina kan het ook andersom, met de knop **Op
rekening zetten**.

Uren die op een rekening staan, tellen daarna niet meer mee: in de keuzelijst zie je
alleen nog wat openstaat, en bij zo'n dag staat het rekeningnummer. Verwijder je die
rekening weer, dan komen de uren gewoon vrij om opnieuw te factureren.

Een klus die klaar is zet je op afgerond. Hij zakt dan naar onderen in de lijst, maar de
uren blijven bewaard.

### Foto's en bonnetjes

Onderaan de kluspagina zet je foto's en bonnetjes neer: een bon van de groothandel, een
foto van hoe het eruitzag voordat je begon, of van het eindresultaat. Meerdere tegelijk
kiezen mag; JPG, PNG, HEIC en PDF worden geaccepteerd, samen hooguit 32 MB per keer.

Bij elk bestand staat een knop **Meesturen**. Zet die aan bij een bon die de klant moet
zien: zodra je de uren van deze klus op een rekening zet en die rekening mailt, gaat het
bestand als bijlage mee. Wat je niet aanvinkt, blijft alleen voor jezelf.

De bestanden staan in `/data` en gaan mee in een Home Assistant back-up. Verwijder je de
klus, dan gaan ze mee.

## De klantpagina

Klik op een klant en je ziet alles van die klant bij elkaar: hoeveel er in totaal is
gefactureerd, wat er nog openstaat, of er rekeningen over hun vervaldatum zijn, hoeveel er
nog uitstaat aan offertes en hoeveel uren er nog te factureren zijn. Daaronder staan zijn
klussen, zijn offertes en zijn rekeningen, elk met de knoppen die daarbij horen.

## Data en back-up

Alle data (database, PDF's, logo) staat in de persistente `/data` map van de add-on.
Die overleeft add-on updates en herstarts. Een Home Assistant back-up neemt de data
mee zonder dat de add-on gestopt hoeft te worden, mits je bij het maken van de back-up
de add-on aanvinkt.

## Problemen oplossen

**De add-on start niet.** Zet `log_level` op `debug`, herstart en kijk bij het
**Log** tabblad wat er misgaat.

**Mailen lukt niet.** De app zegt zelf wat er misgaat — server niet gevonden, wachtwoord
geweigerd, afzender niet toegestaan — in de gele balk bovenaan. Controleer host, poort
(gebruik 587, niet 465) en of je een app-wachtwoord gebruikt in plaats van je gewone
wachtwoord. Voor iCloud is de server `smtp.mail.me.com` en moet het afzenderadres bij je
iCloud-account horen.

**"Deze opdracht kwam niet van de app zelf."** Je hebt een pagina gebruikt die al heel
lang openstond, of de add-on is tussendoor herstart. Ververs de pagina en probeer het
opnieuw.

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
