# Changelog

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
