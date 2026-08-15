# Changelog

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
