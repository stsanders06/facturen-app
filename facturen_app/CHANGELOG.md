# Changelog

## 1.1.0

- Omgebouwd tot een volwaardige Home Assistant add-on repository: toe te voegen via
  **Add-on Store → Repositories** in plaats van handmatig kopiëren naar `/addons`.
- Gebouwd op de officiële Home Assistant base images (aarch64, amd64, armv7).
- Ingress-ondersteuning: de app draait nu in het Home Assistant zijmenu, zonder dat er
  een poort open hoeft.
- Poort 8099 is standaard dicht en optioneel open te zetten.
- Draait achter waitress in plaats van de Flask ontwikkelserver.
- De sessiesleutel wordt eenmalig gegenereerd en bewaard in `/data` in plaats van een
  vaste standaardwaarde.
- Configureerbaar log-niveau.
- Add-on data wordt meegenomen in Home Assistant back-ups (`backup: hot`).

## 1.0.0

- Eerste versie: rekeningen aanmaken met materiaal- en arbeidsregels, PDF genereren,
  per e-mail versturen, markeren als betaald.
