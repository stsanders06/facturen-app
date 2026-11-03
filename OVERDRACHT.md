# Overdracht: bonnenherkenning, 2 november 2026

Dit is het vervolg op `BONNEN-OCR-OVERDRACHT.md` (het plan uit de proef, niet in
deze repo). Het beschrijft wat er gebouwd is, wat er gemeten is, wat is
afgevallen en waarom, en wat er nog openstaat.

De werkafspraken en de valkuilen staan in **CLAUDE.md**; die worden hier niet
herhaald. Wat voor de gebruiker veranderde staat in
`facturen_app/CHANGELOG.md`.

## Wat er staat

Een scan die binnenkomt wordt op de achtergrond gelezen, en het formulier
**Scan als bon** staat voorgevuld klaar: winkel, datum, totaalbedrag en de
regels die op de bon stonden. Stone keurt elke bon zelf goed; velden die niet
zeker zijn hebben een oranje rand, de rest een groene.

Alles draait lokaal op een Raspberry Pi 5. Er gaat geen bon en geen bedrag naar
internet, en de modellen zitten in het image, dus het werkt ook zonder
internetverbinding.

```
iPhone ──► POST /api/scans ──► facturen-app ──► wachtrij (één draad)
                                    │
                                    ▼
                          bon-ocr (eigen container)
                          POST /lees, bytes in de body
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
              tekstlaag?      plaatje in de PDF?  foto
            pdftotext -layout  pdfimages -j      rechtstreeks
                    │               │               │
                    └───────► RapidOCR (tenzij tekstlaag) ─┐
                                                           ▼
                                                    bon_parser.lees_bon
                                                           │
                                            <bestand>.bon.json naast de scan
                                                           │
                                                  "Scan als bon", voorgevuld
```

- `bon_ocr/lezer.py` (251 regels) — de container. Kiest de route, draait de OCR,
  bouwt regels uit de tekstvakken (met scheefstandcorrectie).
- `bon_ocr/bon_parser.py` (600 regels) — van tekst naar velden. **Hier zit het
  onderhoud**, niet in de OCR.
- In `facturen_app/app/main.py`: een wachtrij met één daemon-draad, in dezelfde
  vorm als de bestaande pdf-miniatuur-werker. Geen nieuwe tabel: het resultaat
  is een bestand naast de scan, want het is een voorstel en geen gegeven.

De container staat **niet** op het netwerk: geen `ports`, alleen `expose`. Hij
heeft geen inlog, dus alleen de app mag erbij. Zet daar nooit een poort op.

## Hoe snel, hoeveel geheugen

Gemeten op de Pi 5 zelf, niet op een Mac — dat onderscheid kostte twee bugs.

| | |
|---|---|
| Bon uit een winkel-app (tekstlaag) | **0,05 – 0,10 s** |
| Gescande bon of foto (OCR) | **3,5 – 22 s** |
| Geheugenpiek tijdens het lezen | 626 MB (`mem_limit` staat op 1 GB) |
| Image `bon-ocr` | 698 MB |
| De parser zelf | < 10 ms |

De app wacht maximaal 90 s op de lezer (`BON_OCR_TIJD`), poppler 120 s
(`PDF_TIJD`). Beide zijn vangnetten, geen grenzen die je normaal raakt.

## Hoe goed

Op de meegeleverde nepbonnen in `bon_ocr/tests/bonnen` (17 stuks):

| Set | Resultaat |
|---|---|
| `bon01`–`bon08` — de set waarop de parser is gemaakt | 8 van 8 helemaal goed |
| `blind01`–`blind06` — pas achteraf toegevoegd | **5 van 6** helemaal goed |
| `echt01`–`echt03` — echte bonnen, geanonimiseerd | alles goed op één na |

De blinde zes zijn de eerlijke score. De zesde (`blind03`) heeft een 9%-btw-regel
die de OCR als `:96BTT` leest; daar valt niets uit te halen zonder te gokken, en
de bon meldt dat zelf als onzeker.

**Belangrijker dan de score is dat een fout zichzelf meldt.** Daar is `onzeker`
voor, en `test_een_fout_veld_staat_altijd_in_onzeker` bewaakt het. Die test mag
nooit worden versoepeld: een stille fout is erger dan drie zichtbare.

## Wat is geprobeerd en afgevallen

Niet opnieuw proberen zonder nieuwe informatie.

| Idee | Waarom het afviel |
|---|---|
| **Taalmodel** (Qwen2.5-3B, Qwen3-VL 2B) | 0 van 6 blind, 33–38 s, 2,5–3,9 GB. Tegen 5 van 6 in een seconde met regels. Erger dan de score: ze verwarren stukprijs met regeltotaal en verzinnen plausibele bedragen — precies de fout die langs een controle glipt. |
| **Tesseract** | 1 van 6 blind; op telefoonfoto's onbruikbaar. |
| **Plaatje 180° draaien** bij een omgekeerde bon | Gaf één onzinnige regel met een kloppende som tegenover twee goede regels bij het omkeren van de leesvolgorde — en dat voor het dubbele van de tijd. De OCR maakt op een gedraaid beeld andere fouten. |
| **`pdfimages -png`** | 22,5 s op de Pi voor een scan van 2 MB, tegen 0,04 s met `-j`. Een JPEG hoeft niet omgezet te worden. |
| **Een losse scanner kopen** | De beperking zit in de parser, niet in de beeldkwaliteit. Een digitale bon is bovendien altijd beter dan welke scan ook. |

## Hoe een nieuw geval erbij komt

Gaat een bon van een nieuwe winkel mis, dan is dat vrijwel zeker de parser.

1. Haal de OCR-tekst op: `POST /lees` geeft hem terug in `ocr_regels`.
2. Zet die tekst als `bon_ocr/tests/bonnen/echt<nn>_<winkel>.txt`, **mét de
   leesfouten erin** — die zijn het punt.
3. Vervang naam, adres, klantnummer, kaartnummers en transactiecodes.
   Echte klant- en bedrijfsnamen komen de repo niet in.
4. Zet het juiste antwoord ernaast als `.json` (Nederlandse veldnamen).
5. Pas de parser aan tot het klopt, en controleer dat de andere zestien bonnen
   onveranderd blijven.

## Wat er nog openstaat

- [ ] **Een echte blinde test** met bonnen van winkels die er nog niet bij zaten
      (Gamma, Praxis, Hornbach, Hema, supermarkt, pomp). Die bonnen komen de repo
      niet in; alleen de geanonimiseerde OCR-tekst.
- [ ] **iOS**: het datumveld op *Scan als bon* is nog niet op een iPhone gezien.
      Deze app heeft twee keer eerder een iOS-bug gehad met de breedte van
      datumvelden.
- [ ] **`test_lichtbak.py::test_een_notitiefoto_opent_in_de_lichtbak`** faalt op
      WebKit (focus na navigatie). Bestaand, faalt ook op `main`, niet van deze
      wijzigingen.
- [ ] **Opruimpunten uit PR #46**: koude-start-404 op `/factuur/<id>/miniatuur`,
      nette fout zonder WebKit, filtersegment role/label.
- [ ] **Persistent logboek op de Pi.** `journald` schrijft naar geheugen, dus na
      een herstart is er geen `journalctl -b -1`. Dat kostte een diagnose toen de
      Pi tweemaal van het netwerk viel. `Storage=persistent` in
      `/etc/systemd/journald.conf` lost het op.

## De correctie-log

Bij elke goedgekeurde bon komt er één JSON-regel in `data/bon-correcties.jsonl`:
de OCR-tekst, wat de parser ervan maakte, en wat er uiteindelijk is bewaard.

Dat bestand blijft op de Pi, gaat nergens heen en kan zonder gevolgen weg. Het
is er voor één doel: de parser werkt met regels per winkel en dat onderhoud is
nooit klaar. Met genoeg van die voorbeelden is te meten waar het misgaat, of kan
er een klein model getraind worden dat regels **labelt** (artikel / aantal /
btw / totaal / ruis) terwijl de bedragen uit de tekst geparsed blijven worden.
Nooit een model dat bedragen opschrijft.

Die voorbeelden zijn alleen te verzamelen op het moment dat ze ontstaan. Zonder
deze log begin je over een jaar weer bij nul.

## Ook in deze ronde gebouwd

Buiten de bonnenlezer om, zie `CHANGELOG.md` voor de details:

- **Eén takenlijst per klus** (2.3.0), onder de notities, in plaats van een
  lijstje per notitie. Bestaande taken verhuizen mee; een notitie verwijderen
  laat ze voortaan staan.
- **Het kenmerk van een nieuwe rekening** volgt de klus waar hij over gaat.
  Alleen bij het aanmaken en alleen als het veld leeg blijft.
- **PR #48 (2.1.1)** is gesloten zonder te mergen; die beveiligingsronde zit in
  2.2.0 verwerkt.
