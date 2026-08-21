# Changelog

## 1.12.0 (in de maak)

- **Rustiger kaarten.** Op elke rekening stond een rij van acht knoppen en op elke
  offerte negen. Nu staat alleen de eerstvolgende stap er los — mailen bij een verse
  rekening, betaald bij een verzonden rekening, geaccepteerd bij een offerte die
  uitstaat — en zit de rest achter de drie puntjes rechts.
- **Zoeken.** Op de rekeningen, offertes en klanten zit een zoekveld: op nummer, naam,
  e-mailadres, telefoonnummer of adres. Losse woorden mogen uit verschillende velden
  komen, dus "jansen 2026-007" vindt precies die ene. Zoeken blijft binnen het filter
  dat je hebt gekozen.
- **Te laat betaald valt op.** Rekeningen die over hun vervaldatum zijn, krijgen een
  chip "Te laat", staan als bedrag apart in de balk bovenaan en hebben een eigen filter.
  Bij "Openstaand" staat voortaan de oudste bovenaan, want die wacht het langst.
- Op elke onbetaalde rekening staat nu naast de datum tot wanneer hij mag blijven staan.

### Onder de motorkap

- **Tests.** De app heeft nu een testset die de nummering, de urenberekening, het
  inlezen van CSV-bestanden en de PDF nakijkt. Draait automatisch bij elke wijziging.
- De code die de PDF tekent stond in één functie van ruim tweehonderd regels; die is
  opgesplitst in losse stukken per onderdeel van het vel. Aan de PDF zelf verandert
  niets — die is regel voor regel hetzelfde gebleven.
- De laatste resten van Tikkie zijn opgeruimd: de ongebruikte kolom verdwijnt uit
  bestaande databases en de documentatie noemt hem niet meer.

## 1.11.1

- Het akkoordvak onderaan de offerte is eruit. Onder de bedragen staat nu alleen nog
  de toelichting en de voetregel; tot wanneer de prijs geldt staat bovenaan naast het
  nummer en de datum.
- **Die geldigheid is nu een keuze.** Met het vinkje bij een offerte bepaal je of er een
  einddatum op komt. Laat je het uit, dan staat er niets over geldigheid op de PDF en
  ook niet in de mail aan de klant.

## 1.11.0

- **Offertes.** Een nieuw tabblad naast Rekeningen. Je maakt een offerte precies zoals
  een rekening — dezelfde regels voor materiaal en arbeid — met daarbij een datum tot
  wanneer de prijs geldt en een veld voor een toelichting. De PDF ziet eruit als de
  rekening, maar met een akkoordvak onderaan in plaats van een betaalstrook: de klant
  kan hem tekenen en terugsturen.
- Je legt vast wat de klant ervan vond (verzonden, geaccepteerd, afgewezen) en ziet in
  één oogopslag wat er nog uitstaat en wat er verlopen is.
- **Van offerte naar rekening met één knop.** De regels, de klant en het bedrag gaan
  mee; je komt in het bewerkscherm terecht zodat je nog iets kunt aanpassen voordat je
  verstuurt. De offerte blijft staan als vastlegging van wat er is afgesproken.
- Offertes hebben hun eigen nummerreeks (`OFF-2026-001`) en kunnen net als een rekening
  gemaild, gedownload en opnieuw gemaakt worden.
- **Klanten importeren uit een CSV-bestand.** Onder Klanten → Importeren lees je een
  export uit je oude boekhouding, Excel of Google Contacten in. Kolomkoppen worden
  herkend in meerdere schrijfwijzen (naam/klant/bedrijf, email/e-mail, telefoon/mobiel),
  puntkomma's, komma's en tabs werken alle drie, en staan postcode en plaats in aparte
  kolommen dan komen die onder het adres. Klanten die er al zijn worden overgeslagen,
  of bijgewerkt als je daarvoor kiest — een lege kolom wist dan niet wat je zelf had
  ingevuld.
- **Regels importeren uit een CSV-bestand.** Bij een rekening of offerte kun je een
  materiaallijst inlezen in plaats van alles over te typen. Dat gebeurt in je browser,
  dus je ziet de regels meteen staan en kunt ze nog aanpassen voordat je opslaat.
  Bedragen mogen met een komma of een punt, met of zonder eurotekens.
- Voor beide imports staat een voorbeeldbestand klaar om te downloaden.
- Een bedrag met een komma in een regel werd stilletjes overgeslagen; die regel telt nu
  gewoon mee.

## 1.10.8

- De meetpagina is weer verwijderd; die had zijn werk gedaan. Het versienummer blijft
  onderaan elke pagina staan, zodat je kunt zien of een update is doorgekomen.

## 1.10.7

- De compensatie voor het uitstekende datum- en tijdveld op de iPhone gebeurt nu door
  de breedte vooraf te verminderen, in plaats van door de rand en de marge naar het
  vakje eromheen te verplaatsen. Beide werken op het toestel, maar deze ziet er beter
  uit. De regel geldt alleen op iOS; andere browsers rekenen wél volgens `box-sizing`
  en houden gewoon de volle breedte.
- De correctie geldt nu voor **alle** datum- en tijdvelden in de app, dus ook de
  factuurdatum bij een nieuwe rekening — daar stak hij net zo goed uit.

## 1.10.6

- **De oorzaak van het uitstekende tijdveld gevonden, dankzij de meting op het toestel.**
  Het veld werd steeds precies 22 pixels te breed: 187 in een vak van 165, en eerder 360
  in een vak van 338. Die 22 zijn de marge van 10 pixels aan weerszijden plus 2 pixels
  rand. Safari op de iPhone telt die namelijk bóvenop de opgegeven breedte in plaats van
  erin, ook al staat `box-sizing: border-box` ingesteld. Daarom hielp geen enkele
  breedte-instructie. De rand en de marge zitten nu op het vakje eromheen en het veld
  zelf is kaal, zodat 100% ook echt 100% is. Aan de buitenkant ziet het er hetzelfde uit.
- De meetpagina test nu vier manieren naast elkaar en zegt welke jouw toestel
  respecteert, zodat dit niet meer op gissen aankomt.

## 1.10.5

- **Het uitstekende tijdveld op de iPhone is nu bij de wortel aangepakt.** Uit de meting
  op het toestel zelf bleek dat iOS zo'n veld 187 pixels breed maakte terwijl er 164
  beschikbaar was, en dat het zich niets aantrok van de opgegeven breedte. Van en Tot
  staan nu in een raster van twee kolommen van elk precies de helft; zo'n kolom kán niet
  breder worden dan die helft, hoe breed iOS de widget ook wil hebben. Daarbovenop mag
  een veld nooit meer buiten zijn eigen vak komen.
- Getest op negen schermbreedtes van 280 tot 430 pixels: overal staan Van en Tot naast
  elkaar en past alles binnen het vlak.
- De meetpagina toont nu ook de beschikbare ruimte en de breedte van elk vak, zodat te
  zien is of een veld te breed is of zijn vak.

## 1.10.4

- Het datumveld op de kluspagina rekte zich op een telefoon uit tot zo'n 330 pixels,
  met de datum eenzaam in het midden en het urental helemaal aan de rand. Het neemt nu
  de breedte die het nodig heeft.
- De waarde in een datum- of tijdveld staat links uitgelijnd, in één lijn met de
  notitie eronder. iOS zette die standaard in het midden.

## 1.10.3

- **Onderaan elke pagina staat nu welke versie er draait.** In Home Assistant is dat de
  versie die de Supervisor daadwerkelijk heeft geïnstalleerd, zodat je kunt zien of een
  update echt is doorgekomen — het wissen van je browsergegevens verandert daar namelijk
  niets aan.
- Nieuwe **meetpagina** (linkje naast het versienummer). Die meet op je eigen toestel hoe
  breed het datum- en tijdveld worden, of de pagina is ingezoomd en of er iets buiten zijn
  vlak steekt. Bedoeld om weergaveproblemen te kunnen naspeuren die alleen op een telefoon
  optreden en niet in een browser op een computer.

## 1.10.2

- **De eigenlijke oorzaak van het niet-passende tijdveld gevonden.** Safari op de iPhone
  zoomt de hele pagina in zodra je een veld aantikt waarvan de tekst kleiner is dan 16
  pixels. Onze velden stonden op 15, dus na één tik werd het scherm effectief 327 pixels
  breed in plaats van 430 — en in die kleinere ruimte paste de tijdwidget niet meer.
  Velden hebben op een aanraakscherm nu 16 pixels tekst, waardoor er niet meer wordt
  ingezoomd.
- De tijdvelden hebben geen vaste minimumbreedte meer. Zo'n ondergrens liet het veld
  juist krimpen tot onder zijn eigen inhoud; nu weigert de browser te krimpen voorbij
  wat het veld nodig heeft en wijkt "Tot" vanzelf naar een eigen regel. Dat werkt
  ongeacht hoe breed iOS die widget maakt en of je klok op 24 of 12 uur staat.

## 1.10.1

- Op een iPhone paste het veld **Tot** niet in de rij en stak het buiten het grijze blok
  uit. Safari geeft een tijd- of datumveld een eigen minimumbreedte die niet in een
  smalle kolom past. De rij is nu zo opgebouwd dat **Van** en **Tot** allebei de helft
  van de volle breedte krijgen — op een scherm van 430 pixels is dat 165 pixels per veld
  in plaats van 108. Past het echt niet, dan gaat Tot naar een eigen regel in plaats van
  buiten het blok te steken.
- De kolommen voor datum en tijd zijn ook op een groot scherm iets ruimer gemaakt.

## 1.10.0

De hele app is nagelopen op fouten, op de weergave op een telefoon en op
kwetsbaarheden. Wat daaruit kwam, is hier opgelost.

### Rechtgezet

- **Factuurnummers liepen terug na het verwijderen van een rekening.** Het volgende
  nummer werd geteld op het aantal rekeningen in plaats van op het hoogste nummer, dus
  na een verwijdering kreeg een nieuwe rekening een nummer dat al bestond — en omdat de
  PDF naar het nummer heet, werd de PDF van de oude rekening overschreven. Er wordt nu
  doorgeteld op het hoogste nummer. Staan er al dubbele nummers in je administratie, dan
  worden die bij de eerste start hersteld: de oudste houdt zijn nummer, de nieuwere
  krijgt een vrij nummer, en alle PDF's worden opnieuw getekend. Je krijgt daar een
  melding van te zien.
- **Een naam met een apostrof ('t Huys, d'Hondt) brak de bevestigingsvraag**, waardoor
  een klant of klus zonder enige vraag werd verwijderd zodra je op Verwijderen klikte.
- **Mislukt mailen gaf een lege foutpagina.** Nu krijg je te lezen wat er misgaat:
  server niet gevonden, wachtwoord geweigerd, afzender niet toegestaan, en zo verder.
  Bij "direct mailen na opslaan" blijft de rekening gewoon bewaard.
- **PDF's van verwijderde rekeningen bleven als los bestand achter.** Die gaan nu mee.
- **Een logo dat niet op de rekening getekend kan worden** (een HEIC-foto van een
  iPhone, een SVG) werd geaccepteerd waarna het logo stilletjes van de factuur verdween.
  Zo'n bestand wordt nu geweigerd met uitleg.
- **Dezelfde uren konden twee keer op een rekening.** Gefactureerde dagen worden nu
  vastgelegd bij die rekening: de keuzelijst toont alleen nog wat openstaat, en op de
  kluspagina zie je bij zo'n dag het rekeningnummer staan. Verwijder je de rekening, dan
  komen de uren weer vrij.
- Een klus hield een verwijderde klant vast; een rekening zonder regels kon worden
  aangemaakt; een onleesbare datum werd rauw opgeslagen. Alle drie opgelost.
- Bij het bewerken van een betaalde rekening verschijnt een waarschuwing.

### Op de telefoon

- De kopbalk was 101 pixels hoog en nam twee regels; nu 49 pixels op één regel.
- **Verwijderen lag vier pixels naast Betaald**, allebei 30 pixels hoog. Alle knoppen
  zijn nu minstens 44 pixels (de richtlijn van Apple en Google) en Verwijderen staat
  apart, aan de andere kant.
- Een gewerkte dag was een blok van zo'n 300 pixels; dat is gehalveerd. De kolomkoppen
  staan één keer boven de lijst in plaats van bij elke regel, en het kruisje om een dag
  te verwijderen staat niet meer verweesd op een eigen regel.

### Makkelijker

- Bovenaan de rekeningen staat wat er **openstaat** en wat er dit jaar is gefactureerd,
  met knoppen om te filteren op alles, openstaand of betaald.
- Uren worden **automatisch bewaard** zodra je een veld verlaat; je hoeft niet meer per
  dag op Opslaan te klikken.
- De Uren-tab toont hoeveel uur er in totaal nog niet gefactureerd is.
- Kies je een klus bij een nieuwe rekening, dan wordt de klant van die klus meteen
  ingevuld. Op de klantpagina staan nu ook zijn klussen.
- **Mailen vraagt om een bevestiging**, met het e-mailadres erbij.
- Op de rekening zelf staat nu "Rekening" in plaats van "Factuur".
- Periodes lezen korter: "10 – 12 aug 2026" in plaats van "10 aug 2026 t/m 12 aug 2026".

### Veiligheid

- Opdrachten van andere websites worden geweigerd: elk formulier krijgt een kenmerk mee
  dat gecontroleerd wordt. Zonder dat kon elke site die je bezocht in de achtergrond een
  rekening laten verwijderen, omdat poort 8099 geen wachtwoord heeft.
- Uploads zijn begrensd op 8 MB, zodat de opslag van Home Assistant niet vol te
  schrijven is via het logoveld.
- Flask, Werkzeug, waitress en reportlab zijn bijgewerkt. Daarmee zijn acht bekende
  kwetsbaarheden weg, waaronder twee in de webserver zelf.

## 1.9.0

- Nieuw tabblad **Uren**. Je maakt een klus aan (met eventueel een klant en een
  uurtarief) en houdt daar per dag bij hoelang je hebt gewerkt: datum, van-tot en een
  notitie voor jezelf. De uren worden vanzelf opgeteld; een eindtijd na middernacht
  telt gewoon door.
- Gewerkte dagen zijn direct in de lijst aan te passen of te verwijderen, en terwijl je
  tijden intikt zie je meteen hoeveel uur dat is.
- Klussen zijn af te ronden; afgeronde klussen zakken naar onderen maar blijven staan.
- Bij een rekening kies je onder **Regels** een klus om de uren toe te voegen. Er komt
  één arbeidsregel op met het totaal aantal uren maal het uurtarief — de losse dagen
  blijven op het Uren-tabblad en komen dus niet op de factuur. Vanaf een klus kun je met
  **Op rekening zetten** ook meteen een rekening beginnen.
- Een rekening die per ongeluk op betaald staat, zet je met **Toch niet betaald** weer
  open. Hij komt terug op de status van daarvoor: concept of verzonden.

## 1.8.0

- Onderaan elke rekening staat nu in kleine letters: "Deze factuur is vrijgesteld van btw
  i.v.m. particuliere levering van diensten."
- Rekeningen zijn opnieuw te maken nadat je je instellingen hebt gewijzigd. Per rekening
  met de knop **Vernieuwen**, of in één keer met **Alle rekeningen opnieuw maken** onderaan
  de instellingenpagina. Bedragen en regels blijven daarbij ongewijzigd; alleen je eigen
  gegevens, logo en IBAN worden bijgewerkt.
- De hele kaart van een rekening is nu een knop die de factuur opent; de losse knop
  **Bekijken** is daarmee vervallen. Hetzelfde geldt voor de klantenkaarten, die openen de
  klant. De knoppen op de kaart blijven gewoon werken.

## 1.7.0

- Nieuw tabblad **Klanten**. Je bewaart per klant naam, adres, e-mail, telefoon en een
  notitie voor jezelf. Per klant zie je hoeveel rekeningen er zijn, wat er in totaal is
  gefactureerd en wat er nog openstaat.
- Bij een nieuwe rekening kies je een bestaande klant uit een lijst; de gegevens worden
  dan vanzelf ingevuld. Vul je een onbekende klant in, dan wordt die standaard bewaard
  voor de volgende keer — dat vinkje kun je uitzetten voor een eenmalige klus.
- Vanaf een klantpagina start je met één klik een nieuwe rekening voor die klant, en de
  klantnaam in de rekeningenlijst linkt naar de klant.
- Bestaande rekeningen worden eenmalig omgezet: per klantnaam wordt een klant aangemaakt
  en de rekeningen worden eraan gekoppeld, inclusief adres en e-mailadres.
- Een klant verwijderen laat zijn rekeningen staan; alleen de koppeling verdwijnt. De
  naam op een al gemaakte rekening verandert niet mee als je de klant hernoemt.
- De pijltjes bij aantal en prijs lopen nu met hele stappen in plaats van met stapjes van
  een cent. Halve uren of centen typen kan nog gewoon.

## 1.6.0

- Arbeid kan nu op drie manieren op de rekening: **per uur**, **per dag** of als **vaste
  prijs voor de hele klus**. De velden passen zich aan je keuze aan: je vult uren met een
  uurtarief in, dagen met een dagtarief, of alleen een bedrag bij een vaste prijs.
- Op de factuur staat de eenheid achter het aantal (3 st, 2,5 u, 2 dg) en eronder waar
  het om gaat: Materiaal, Arbeid per uur, Arbeid per dag of Arbeid, vaste prijs.
- Bij een vaste prijs staat er geen aantal en geen tarief op de regel, alleen het bedrag.
- Bestaande arbeidsregels zijn automatisch omgezet naar arbeid per uur, want zo werden
  ze eerder gerekend.

## 1.5.0

- Rekeningen zijn nu aan te passen: naast elke rekening staat **Bewerken**, waarmee je
  klantgegevens, datum, betaalmethode en regels kunt wijzigen. Het factuurnummer en de
  status blijven staan, en de PDF wordt meteen opnieuw getekend.
- De regel "O.v.v." is van de betaalstrook gehaald.

## 1.4.0

- Nieuw ontwerp voor de rekening zelf: ruime opzet met een oranje bies langs de zijkant,
  je logo en naam bovenaan, en "Van" en "Voor" naast elkaar.
- Onderaan staat een betaalstrook, gescheiden door een stippellijn, met de IBAN, de
  tenaamstelling, het kenmerk en de vervaldatum. Het te betalen bedrag staat daarnaast
  in een omkaderd vak, zodat het niet over het hoofd te zien is.
- Rekeningen hebben nu een vervaldatum: veertien dagen na de factuurdatum.
- Bij contant afgerekende klussen heet de strook "Voldaan" en staan er geen
  bankgegevens op.
- Aantallen staan in Nederlandse notatie (1,5 in plaats van 1.5) en een lange
  omschrijving wordt netjes afgekapt in plaats van over de kolommen te lopen.
- Loopt de rekening over meerdere pagina's, dan herhaalt de vervolgpagina het
  factuurnummer en de kolomkoppen.

## 1.3.0

- De tenaamstelling van je bankrekening is nu een eigen instelling. Stond er eerst
  altijd je bedrijfsnaam bij "T.n.v.", nu kun je invullen op wiens naam de rekening
  echt staat. Laat je het leeg, dan blijft je eigen naam staan.
- Tikkie is eruit gehaald: de link uit de instellingen en de betaalmethode uit het
  formulier. Bestaande rekeningen die op Tikkie stonden, tonen voortaan gewoon je
  bankgegevens.
- Nieuwe knop **Bekijken** opent de rekening in een tabblad van je browser, zonder de
  PDF eerst te moeten downloaden. **Downloaden** blijft ernaast staan.
- Het tabblad **Nieuw** is uit de menubalk gehaald; de knop "+ Nieuwe rekening" op de
  rekeningenpagina doet hetzelfde.

## 1.2.0

- Compleet nieuwe interface in de vormgeving van Home Assistant zelf: kaarten in plaats
  van een tabel, HA's blauw en typografie, en een donkere variant die meegaat met de
  instelling van je systeem.
- Poort 8099 staat nu standaard open, zodat je de app gewoon in je browser kunt openen
  op `http://<ip-van-home-assistant>:8099`. Via de zijbalk van Home Assistant werkt hij
  ook nog steeds.
- Het invoerscherm telt het totaal mee terwijl je typt, regels zijn te verwijderen en
  worden op een telefoon onder elkaar gezet in plaats van in een smalle rij.
- Bedragen staan in Nederlandse notatie met een komma.

## 1.1.0

- Omgebouwd tot een volwaardige Home Assistant add-on repository: toe te voegen via
  **Add-on Store → Repositories** in plaats van handmatig kopiëren naar `/addons`.
- Gebouwd op de officiële Home Assistant base images (aarch64, amd64). `armv7` is
  vervallen: Home Assistant ondersteunt die architectuur niet meer sinds 2025.12.
- Ingress-ondersteuning: de app draait nu in het Home Assistant zijmenu, zonder dat er
  een poort open hoeft.
- Poort 8099 is standaard dicht en optioneel open te zetten.
- Draait achter waitress in plaats van de Flask ontwikkelserver.
- De sessiesleutel wordt eenmalig gegenereerd en bewaard in `/data` in plaats van een
  vaste standaardwaarde.
- Configureerbaar log-niveau.

## 1.0.0

- Eerste versie: rekeningen aanmaken met materiaal- en arbeidsregels, PDF genereren,
  per e-mail versturen, markeren als betaald.
