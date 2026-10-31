"""Meet de Wallet-vorm op iPhone- en Mac-breedtes.

Geen pytest: er hoort een draaiende app bij. Voorbeeld:

    INGRESS_NETWERK=127.0.0.0/8 DATA_DIR=/tmp/facturen-wallet PORT=8585 \
      .venv/bin/python facturen_app/app/main.py
    .venv/bin/python facturen_app/tests/handmatig/wallet_matrix.py

WebKit én Chromium, licht en donker. Een pagina faalt als ze horizontaal
schuift, de tabbalk de laatste regel dekt, een telefoonknop lager is dan
44 px, of de console een fout geeft. De lijsten met een filter staan erin
omdat het segment daar op een telefoon zijwaarts schuift.
"""
import shutil
import sys

from playwright.sync_api import sync_playwright

BASIS = "http://127.0.0.1:8585"
PAGINAS = [
    "/vandaag", "/", "/offertes", "/klussen", "/scans", "/klanten",
    "/instellingen", "/nieuw", "/offertes/nieuw", "/klussen/nieuw",
    "/klanten/nieuw", "/klus/2", "/factuur/2/bewerk", "/factuur/2/betalingen",
    "/factuur/2/mail", "/offerte/1/bewerk",
    "/?status=betaald", "/offertes?status=verlopen",
]
BREEDTES = [320, 390, 430, 1024, 1280, 1440]


def meet(page, breedte):
    return page.evaluate(
        """(telefoon) => {
          const doc = document.documentElement;
          const overflow = doc.scrollWidth > doc.clientWidth + 1;
          window.scrollTo(0, doc.scrollHeight);
          const tab = document.querySelector('.tabbar');
          const tabZichtbaar = tab && getComputedStyle(tab).display !== 'none';
          const tabBox = tabZichtbaar ? tab.getBoundingClientRect() : null;
          const versie = document.querySelector('.versie');
          const v = versie ? versie.getBoundingClientRect() : null;
          const dekt = !!(tabBox && v && v.bottom > tabBox.top + 2 && v.top < tabBox.bottom);
          const klein = [];
          document.querySelectorAll('.btn, .plus, .tabbar .tab-pil > a, .tabbar summary, button').forEach(el => {
            const st = getComputedStyle(el);
            if (st.display === 'none' || st.visibility === 'hidden') return;
            const r = el.getBoundingClientRect();
            if (r.width < 2 || r.height < 2 || r.bottom < 0 || r.top > innerHeight) return;
            const type = (el.getAttribute('type') || '').toLowerCase();
            if (type === 'hidden') return;
            const min = telefoon ? 44 : 32;
            if (r.height < min - 0.5 && r.width < min - 0.5) {
              klein.push((el.getAttribute('aria-label') || el.innerText || el.tagName).trim().slice(0, 40));
            }
          });
          const label = document.querySelector('.tabbar .tab-pil > a span');
          const labelZichtbaar = !!(label && label.getClientRects().length);
          return {overflow, dekt, klein: klein.slice(0, 6), labelZichtbaar};
        }""",
        breedte < 900,
    )


def main():
    fouten = []
    with sync_playwright() as p:
        browsers = [("webkit", p.webkit.launch())]
        # Chrome staat niet op elke machine op dezelfde plek; zonder pad
        # sloeg de matrix Chromium stil over.
        chrome = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
        try:
            browsers.append(("chromium", p.chromium.launch(
                executable_path=chrome,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )))
        except Exception as exc:
            print("chromium overgeslagen:", exc)
        for naam, browser in browsers:
            for schema in ("light", "dark"):
                for breedte in BREEDTES:
                    hoogte = 844 if breedte < 900 else 800
                    ctx = browser.new_context(
                        viewport={"width": breedte, "height": hoogte},
                        color_scheme=schema,
                    )
                    ctx.set_extra_http_headers({"X-Ingress-Path": "/"})
                    page = ctx.new_page()
                    page.set_default_timeout(12000)
                    js = []
                    page.on("pageerror", lambda e: js.append(str(e)))
                    page.on("console", lambda m: m.type == "error" and js.append(m.text))
                    paden = PAGINAS if breedte in (390, 1280) else [
                        "/vandaag", "/", "/offertes", "/klanten", "/nieuw", "/instellingen"]
                    for pad in paden:
                        page.goto(BASIS + pad, wait_until="domcontentloaded")
                        uit = meet(page, breedte)
                        if uit["overflow"] or uit["dekt"] or uit["klein"] or js:
                            fouten.append((naam, schema, breedte, pad, uit, js[:1]))
                        if breedte == 390 and pad == "/vandaag" and not uit["labelZichtbaar"]:
                            fouten.append((naam, schema, breedte, pad, "geen tabbladnaam", []))
                        js.clear()
                    ctx.close()
            browser.close()
    if fouten:
        for regel in fouten:
            print(regel)
        sys.exit(1)
    print("matrix schoon")


if __name__ == "__main__":
    main()
