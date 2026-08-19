# Facturen App — Home Assistant add-on repository

Home Assistant add-on repository met de **Facturen App**: een simpele rekeningen-app
voor losse klussen. Offertes en rekeningen maken, materiaalkosten en arbeidsloon
optellen, PDF genereren en automatisch mailen.

## Toevoegen aan Home Assistant

[![Open your Home Assistant instance and show the add add-on repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fstsanders06%2Ffacturen-app)

Of handmatig:

1. **Instellingen → Add-ons → Add-on Store**
2. Rechtsboven de drie puntjes → **Repositories**
3. Plak `https://github.com/stsanders06/facturen-app` en klik **Toevoegen**
4. Installeer de add-on **Facturen App** uit de lijst en start hem
5. Open `http://<ip-van-home-assistant>:8099/` in je browser — voluit met `http://` en
   met de schuine streep aan het eind, anders maakt je browser er `https://` van en
   krijg je een lege pagina. Of zet **Show in sidebar** aan om de app in het zijmenu van
   Home Assistant te gebruiken

## Add-ons in deze repository

| Add-on | Beschrijving |
| --- | --- |
| [Facturen App](./facturen_app) | Uren per klus bijhouden, offertes en rekeningen maken, PDF genereren en per e-mail versturen |

Zie [de documentatie](./facturen_app/DOCS.md) voor installatie en gebruik.

## Ondersteunde architecturen

`aarch64` en `amd64` — dus onder andere Raspberry Pi 3/4/5 (64-bit), Home Assistant
Green, Yellow en x86-installaties. `armv7` wordt niet ondersteund: Home Assistant
heeft die architectuur per 2025.12 uitgefaseerd.

## Licentie

[MIT](./LICENSE)
