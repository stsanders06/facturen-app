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
6. Zet **Show in sidebar** aan; de app is dan bereikbaar via het zijmenu van
   Home Assistant (Ingress) — er is geen aparte poort nodig.

## Configuratie

De add-on heeft één optie:

| Optie | Standaard | Betekenis |
| --- | --- | --- |
| `log_level` | `info` | Hoeveel detail er in het add-on log komt. Gebruik `debug` bij problemen. |

De app-instellingen zelf (bedrijfsnaam, IBAN, logo, SMTP) staan niet in de add-on
configuratie maar in de app onder **Instellingen**. Dat scheelt herstarten bij elke
wijziging.

### Poort (optioneel)

Standaard is er geen poort opengezet en gaat alle verkeer via Ingress. Wil je de app
ook buiten Home Assistant om benaderen, zet dan onder **Configuratie → Netwerk** poort
`8099` open. Let op: op die poort zit geen authenticatie, dus doe dat alleen binnen je
eigen netwerk.

## Eerste gebruik

1. Ga naar **Instellingen** in de app: vul naam, adres, IBAN en eventueel een
   Tikkie-link in. Upload je logo.
2. Voor automatisch mailen: vul de SMTP-gegevens in. Voor Gmail gebruik je een
   [app-wachtwoord](https://myaccount.google.com/apppasswords), niet je normale
   wachtwoord.
3. Ga naar **Nieuw** om een rekening te maken: klantgegevens, regels voor materiaal en
   arbeid, betaalmethode kiezen. Bij opslaan wordt automatisch een PDF gegenereerd.

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

**De pagina laadt niet in de sidebar.** Herstart de add-on; Ingress heeft een draaiende
add-on nodig.

## Belangrijk

Dit is geen fiscaal geldige factuur zolang er geen KvK-inschrijving achter zit. Voor
incidentele bijverdiensten is dat meestal geen probleem, maar bij structurele inkomsten
moet je dit opgeven bij de Belastingdienst als resultaat uit overige werkzaamheden.
