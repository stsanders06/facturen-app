"""Loopt de handelingen af die een gebruiker echt doet, en kijkt of ze aankomen.

Hoort bij doorloop.py: die meet hoe het eruitziet, deze of het werkt. Zelfde opzet,
dus een draaiende app op poort 8573 met de demo-administratie erin (demodata.py).
"""
from playwright.sync_api import sync_playwright

BASIS = "http://127.0.0.1:8573"
fouten = []


def zeg(wat, goed, extra=""):
    print(("  ok  " if goed else "FOUT  ") + wat + (f" — {extra}" if extra else ""))
    if not goed:
        fouten.append(wat + (f" ({extra})" if extra else ""))


with sync_playwright() as p:
    b = p.chromium.launch()

    # ---- Laptop ----
    c = b.new_context(viewport={"width": 1280, "height": 900})
    pg = c.new_page()
    pg.on("pageerror", lambda e: fouten.append(f"js-fout: {e}"))

    print("\n== Rekeningen ==")
    pg.goto(BASIS + "/", wait_until="networkidle")
    pg.locator("article.factuur .kaartlink").first.click()
    pg.wait_for_load_state("networkidle")
    zeg("klik op een kaart opent het bewerkscherm", "/bewerk" in pg.url, pg.url)

    pg.goto(BASIS + "/", wait_until="networkidle")
    pg.locator("details.menu > summary").first.click()
    pdf = pg.locator("details.menu[open] a", has_text="PDF bekijken")
    zeg("PDF bekijken staat in het menu", pdf.count() == 1)

    print("\n== Regels bewerken ==")
    pg.goto(BASIS + "/factuur/1/bewerk", wait_until="networkidle")
    voor = pg.locator("#regels .regel").count()
    pg.locator("#regels .regel").nth(0).locator("[name=omschrijving]").fill("Aangepaste eerste regel")
    pg.click("button[type=submit]:has-text('Wijzigingen opslaan')")
    pg.wait_for_load_state("networkidle")
    pg.goto(BASIS + "/factuur/1/bewerk", wait_until="networkidle")
    na = pg.locator("#regels .regel").count()
    zeg("verborgen regels gaan mee bij het opslaan", voor == na, f"{voor} → {na}")
    eerste = pg.locator("#regels .regel").first.locator("[name=omschrijving]").input_value()
    zeg("de wijziging is bewaard", eerste == "Aangepaste eerste regel", eerste)

    print("\n== Uren van een klus ==")
    pg.goto(BASIS + "/nieuw", wait_until="networkidle")
    pg.locator("details.menu > summary", has_text="Uren van een klus").click()
    pg.locator(".klus-keuze").first.click()
    pg.wait_for_timeout(150)
    ingevuld = pg.locator("#regels .regel").last.locator("[name=omschrijving]").input_value()
    zeg("een klus zet zichzelf als regel neer", ingevuld != "", ingevuld)
    klant = pg.locator("#klant_id").input_value()
    zeg("de klant van de klus wordt ingevuld", klant != "", klant)

    print("\n== CSV-menu ==")
    pg.goto(BASIS + "/nieuw", wait_until="networkidle")
    pg.locator("details.menu > summary", has_text="CSV inlezen").click()
    zeg("kiezen en voorbeeld staan in het menu",
        pg.locator("#csv-knop").is_visible()
        and pg.locator("details.menu[open] a", has_text="Voorbeeldbestand").count() == 1)

    print("\n== Notities ==")
    pg.goto(BASIS + "/klus/1", wait_until="networkidle")
    veld = pg.locator(".notitie-form textarea")
    hoog_voor = veld.bounding_box()["height"]
    veld.fill("Regel een\nRegel twee\nRegel drie\nRegel vier")
    pg.wait_for_timeout(120)
    hoog_na = veld.bounding_box()["height"]
    zeg("het tekstvak groeit mee", hoog_na > hoog_voor + 20, f"{hoog_voor:.0f} → {hoog_na:.0f}")

    pg.click(".notitie-form button[type=submit]")
    pg.wait_for_load_state("networkidle")
    zeg("een notitie over meerdere regels komt erin",
        "Regel vier" in pg.content())

    pg.locator("[data-bewerkt]").first.click()
    pg.wait_for_timeout(100)
    open_form = pg.locator(".tekst-form:not([hidden])")
    zeg("Bewerken opent het invulveld", open_form.count() == 1)
    open_form.locator("textarea").fill("Aangepast met de knop")
    open_form.locator("button[type=submit]").click()
    pg.wait_for_load_state("networkidle")
    zeg("de aangepaste tekst staat er", "Aangepast met de knop" in pg.content())

    print("\n== Offertes ==")
    pg.goto(BASIS + "/offertes", wait_until="networkidle")
    pg.locator("article.offerte .kaartlink").first.click()
    pg.wait_for_load_state("networkidle")
    zeg("klik op een offerte opent het bewerkscherm", "/bewerk" in pg.url, pg.url)
    c.close()

    # ---- Telefoon ----
    print("\n== Telefoon (390) ==")
    c = b.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
    pg = c.new_page()
    pg.on("pageerror", lambda e: fouten.append(f"js-fout (telefoon): {e}"))

    pg.goto(BASIS + "/", wait_until="networkidle")
    pg.locator("details.menu > summary").first.click()
    pg.wait_for_timeout(120)
    binnen = pg.evaluate("""() => {
      const l = document.querySelector('details.menu[open] .menu-lijst');
      const r = l.getBoundingClientRect();
      return r.left >= 0 && r.right <= document.documentElement.clientWidth;
    }""")
    zeg("het puntjesmenu past binnen het scherm", binnen)

    pg.goto(BASIS + "/klus/1", wait_until="networkidle")
    knoppen = pg.evaluate("""() => {
      const klein = [];
      document.querySelectorAll('.notities button, .bijlage-acties button').forEach(el => {
        const r = el.getBoundingClientRect();
        if (r.height > 0 && r.height < 36) klein.push(el.textContent.trim() + ' ' + Math.round(r.height));
      });
      return klein;
    }""")
    zeg("knoppen zijn met een vinger te raken", knoppen == [], "; ".join(knoppen))

    pg.goto(BASIS + "/factuur/1/bewerk", wait_until="networkidle")
    schuift = pg.evaluate("""() => document.documentElement.scrollWidth >
                              document.documentElement.clientWidth + 1""")
    zeg("de bewerkpagina schuift niet zijwaarts", not schuift)
    c.close()
    b.close()

print("\n" + ("ALLES GOED" if not fouten else f"{len(fouten)} PROBLEMEN:"))
for f in fouten:
    print(" -", f)
