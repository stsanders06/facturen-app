"""Loopt alle pagina's af op vijf breedtes en meldt wat er niet klopt.

Geen pytest-test: hier hoort een draaiende app bij. Zo gebruik je hem:

    .venv/bin/pip install playwright && .venv/bin/playwright install chromium
    INGRESS_NETWERK=127.0.0.0/8 DATA_DIR=/tmp/facturen-demo PORT=8573 \
      .venv/bin/python facturen_app/app/main.py &
    .venv/bin/python facturen_app/tests/handmatig/doorloop.py

Kijkt naar vier dingen die je op een screenshot pas ziet als je erop let: horizontaal
schuiven, elementen die buiten hun blok steken, bedragen die na het euroteken
afbreken, en fouten in de console. Op telefoonbreedte meet hij er de knoppen bij: wat
kleiner is dan 32px raak je met een vinger niet betrouwbaar.

De schermafbeeldingen komen in de map "schermen" naast dit bestand te staan.
"""
import sys
from playwright.sync_api import sync_playwright

BASIS = "http://127.0.0.1:8573"
import pathlib

MAP = str(pathlib.Path(__file__).resolve().parent / "schermen")
pathlib.Path(MAP).mkdir(exist_ok=True)

BREEDTES = [("320", 320, 760), ("390", 390, 844), ("430", 430, 932),
            ("768", 768, 1024), ("1280", 1280, 900)]

PAGINAS = [
    ("rekeningen", "/"),
    ("rekening-nieuw", "/nieuw"),
    ("rekening-bewerk", "/factuur/1/bewerk"),
    ("betalingen", "/factuur/2/betalingen"),
    ("offertes", "/offertes"),
    ("offerte-nieuw", "/offertes/nieuw"),
    ("offerte-bewerk", "/offerte/1/bewerk"),
    ("aanbetaling", "/offerte/1/aanbetaling"),
    ("klussen", "/klussen"),
    ("klus-lopend", "/klus/1"),
    ("klus-aanvraag", "/klus/3"),
    ("klus-nieuw", "/klussen/nieuw"),
    ("klanten", "/klanten"),
    ("klant", "/klant/1"),
    ("klant-nieuw", "/klanten/nieuw"),
    ("klanten-import", "/klanten/import"),
    ("instellingen", "/instellingen"),
]

METING = """() => {
  const fouten = [];
  const doc = document.documentElement;
  if (doc.scrollWidth > doc.clientWidth + 1) {
    fouten.push(`pagina schuift horizontaal: ${doc.scrollWidth} > ${doc.clientWidth}`);
  }
  const breed = doc.clientWidth;
  document.querySelectorAll('body *').forEach(el => {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || s.position === 'fixed') return;
    // Wat in een dichtgeklapt menu zit is niet in beeld; open passen ze wel.
    if (el.closest('details:not([open])')) return;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return;
    if (r.right > breed + 1) {
      const kan = el.closest('[style*="overflow"], .schuif, table, pre, .tabs, .filters, .zoekbalk');
      if (!kan) {
        fouten.push(`steekt ${Math.round(r.right - breed)}px uit: ` +
          el.tagName.toLowerCase() + (el.className && typeof el.className === 'string'
            ? '.' + el.className.trim().split(/\\s+/).join('.') : '') +
          ' — "' + (el.textContent || '').trim().slice(0, 40) + '"');
      }
    }
    if (r.left < -1) {
      fouten.push(`staat links buiten beeld: ` + el.tagName.toLowerCase());
    }
  });
  // Knoppen en links die te klein zijn om met een vinger te raken.
  if (breed <= 430) {
    document.querySelectorAll('button, a.btn, .tabs a, input[type=submit]').forEach(el => {
      if (el.closest('details:not([open])')) return;
      const r = el.getBoundingClientRect();
      if (r.height > 0 && r.height < 32) {
        fouten.push(`raakvlak ${Math.round(r.height)}px hoog: "` +
          (el.textContent || '').trim().slice(0, 30) + '"');
      }
    });
  }
  // Een bedrag dat over twee regels valt heeft meer dan één rechthoek: "€" op de
  // ene regel en het getal op de volgende leest als een fout.
  document.querySelectorAll('b, small, span, td, .bedrag').forEach(el => {
    if (el.children.length) return;
    const tekst = (el.textContent || '').trim();
    if (!/^€/.test(tekst)) return;
    if (el.getClientRects().length > 1) fouten.push(`bedrag valt uiteen: "${tekst}"`);
  });
  return fouten;
}"""


def main():
    alleen = sys.argv[1] if len(sys.argv) > 1 else None
    problemen = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for label, breed, hoog in BREEDTES:
            if alleen and alleen != label:
                continue
            context = browser.new_context(viewport={"width": breed, "height": hoog},
                                          device_scale_factor=2 if breed <= 430 else 1)
            pagina = context.new_page()
            meldingen = []
            pagina.on("console", lambda m: meldingen.append(m) if m.type == "error" else None)
            pagina.on("pageerror", lambda e: problemen.append(f"[js] {e}"))

            for naam, pad in PAGINAS:
                meldingen.clear()
                antwoord = pagina.goto(BASIS + pad, wait_until="networkidle")
                if antwoord.status != 200:
                    problemen.append(f"[{breed}] {naam}: status {antwoord.status}")
                    continue
                for fout in pagina.evaluate(METING):
                    problemen.append(f"[{breed}] {naam}: {fout}")
                for m in meldingen:
                    problemen.append(f"[{breed}] {naam}: console — {m.text[:120]}")
                pagina.screenshot(path=f"{MAP}/{breed}-{naam}.png", full_page=True)
            context.close()
        browser.close()

    if problemen:
        print(f"{len(problemen)} punten gevonden:\n")
        for punt in problemen:
            print(" -", punt)
    else:
        print("geen problemen gevonden")


main()
