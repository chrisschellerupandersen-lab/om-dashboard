"""
Prisscraper til Organic Market B2B-portalen (Shopify).

Henter for alle varer:
  • indkøbspris  — vises kun når man er LOGGET IND, og indsættes af JavaScript
                   (står som "61,32 kr.") → derfor kræves en rigtig browser
  • salgspris    — den offentlige pris ("110,00 DKK")
  • SKU          — hentes fra det offentlige /products.json og matcher Shopbox

Kør lokalt (login bliver på din maskine):

    pip install playwright requests
    playwright install chromium

    set PORTAL_BRUGER=greve@organicmarket.dk
    set PORTAL_KODE=dinkode
    python pris_scraper.py                 # gemmer JSON lokalt
    python pris_scraper.py --upload        # + sender til dashboardet
    python pris_scraper.py --vis           # kør med synlig browser (fejlsøgning)

Adgangskoden læses KUN fra miljøvariabler — den må aldrig skrives i denne fil.
"""
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

import requests

BASIS_URL      = "https://organicmarket-b2b.dk"
RAILWAY_URL    = os.environ.get("RAILWAY_URL", "https://om-dashboard-production-0f3a.up.railway.app")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "OM-Greve-2026-Hemlig")

BRUGER = os.environ.get("PORTAL_BRUGER", "")
KODE   = os.environ.get("PORTAL_KODE", "")

UD_FIL = Path(__file__).parent / f"prissnapshot_{date.today().isoformat()}.json"


# ── 1) SKU-kort fra det offentlige JSON (kræver ikke login) ───────────────────

def hent_sku_kort() -> dict:
    """{handle: {sku, titel, salg_json}} for alle produkter."""
    kort = {}
    for side in range(1, 8):
        r = requests.get(f"{BASIS_URL}/products.json",
                         params={"limit": 250, "page": side}, timeout=30)
        r.raise_for_status()
        produkter = r.json().get("products", [])
        if not produkter:
            break
        for p in produkter:
            v = (p.get("variants") or [{}])[0]
            if v.get("sku"):
                kort[p["handle"]] = {
                    "sku":   str(v["sku"]).strip(),
                    "titel": p.get("title", ""),
                    "salg_json": _tal(v.get("price")),
                }
    return kort


def _tal(v):
    """'61,32' / '110.00' / 1234.5 → float. None hvis tom."""
    if v is None or v == "":
        return None
    s = str(v).strip()
    if re.search(r",\d{2}$", s):          # dansk format: 1.234,56
        s = s.replace(".", "").replace(",", ".")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


# ── 2) Priser fra de renderede sider (kræver login + JavaScript) ──────────────

# Kører i browseren: læs hvert produktkort og opsaml priser efter format.
JS_UDTRÆK = r"""
() => {
  const ud = {};
  document.querySelectorAll('a[href*="/products/"]').forEach(a => {
    const kort = a.closest('li,article,div.card-wrapper,div');
    if (!kort) return;
    const h = (a.getAttribute('href') || '').split('/products/')[1]?.split('?')[0];
    if (!h) return;
    const tekst = kort.innerText || '';
    const traef = [...tekst.matchAll(/(\d{1,4}(?:\.\d{3})*,\d{2})\s*(kr\.?|DKK)/gi)];
    if (!traef.length) return;
    ud[h] = ud[h] || { indkoeb: null, salg: null };
    traef.forEach(m => {
      const vaerdi = parseFloat(m[1].replace(/\./g, '').replace(',', '.'));
      if (/kr/i.test(m[2])) { if (ud[h].indkoeb == null) ud[h].indkoeb = vaerdi; }
      else                  { if (ud[h].salg    == null) ud[h].salg    = vaerdi; }
    });
  });
  return ud;
}
"""


def hent_priser(vis_browser: bool = False) -> dict:
    """{handle: {indkoeb, salg}} — kræver login, kører rigtig browser."""
    from playwright.sync_api import sync_playwright

    if not BRUGER or not KODE:
        sys.exit("FEJL: sæt miljøvariablerne PORTAL_BRUGER og PORTAL_KODE først.")

    priser: dict = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not vis_browser)
        side = browser.new_page()

        # Login
        print("Logger ind …", end=" ", flush=True)
        side.goto(f"{BASIS_URL}/account/login", wait_until="domcontentloaded")
        side.fill('input[name="customer[email]"]', BRUGER)
        side.fill('input[name="customer[password]"]', KODE)
        side.click('button[type="submit"], input[type="submit"]')
        side.wait_for_load_state("networkidle")
        if side.locator('input[name="customer[password]"]').count() > 0:
            browser.close()
            sys.exit("FEJL: login mislykkedes — tjek PORTAL_BRUGER / PORTAL_KODE.")
        print("ok")

        # Gennemgå kollektionssiderne
        for n in range(1, 40):
            side.goto(f"{BASIS_URL}/collections/all?page={n}", wait_until="domcontentloaded")
            try:
                # vent på at JavaScript har indsat indkøbsprisen ("kr.")
                side.wait_for_function(
                    "() => /\\d,\\d{2}\\s*kr/i.test(document.body.innerText)", timeout=15000)
            except Exception:
                pass
            side.wait_for_timeout(600)
            fundet = side.evaluate(JS_UDTRÆK)
            nye = {h: v for h, v in fundet.items() if h not in priser}
            if not nye:
                break
            priser.update(nye)
            med_ind = sum(1 for v in priser.values() if v.get("indkoeb") is not None)
            print(f"  side {n:>2}: +{len(nye):>3} varer  (i alt {len(priser)}, med indkøbspris {med_ind})")

        browser.close()
    return priser


# ── 3) Flet + gem/upload ──────────────────────────────────────────────────────

def main():
    vis    = "--vis" in sys.argv
    upload = "--upload" in sys.argv

    print("Henter SKU-kort fra products.json …", end=" ", flush=True)
    sku_kort = hent_sku_kort()
    print(f"{len(sku_kort)} varer")

    priser = hent_priser(vis_browser=vis)

    linjer, uden_indkoeb = [], 0
    for handle, meta in sku_kort.items():
        p = priser.get(handle) or {}
        indkoeb = p.get("indkoeb")
        if indkoeb is None:
            uden_indkoeb += 1
        linjer.append({
            "sku":       meta["sku"],
            "varenavn":  meta["titel"],
            "handle":    handle,
            "indkoebspris": indkoeb,
            "salgspris":    p.get("salg") if p.get("salg") is not None else meta["salg_json"],
        })

    data = {"dato": date.today().isoformat(), "kilde": "organicmarket-b2b", "linjer": linjer}
    UD_FIL.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    med = len(linjer) - uden_indkoeb
    print(f"\nGemt {UD_FIL.name}: {len(linjer)} varer — {med} med indkøbspris, {uden_indkoeb} uden")
    if uden_indkoeb and med == 0:
        print("  ⚠ Ingen indkøbspriser fundet — var du logget korrekt ind? Prøv: python pris_scraper.py --vis")

    if upload:
        print("Sender til dashboardet …", end=" ", flush=True)
        r = requests.post(f"{RAILWAY_URL}/api/prissnapshot/bulk",
                          json={"secret": WEBHOOK_SECRET, **data}, timeout=60)
        r.raise_for_status()
        print(f"OK — {r.json().get('linjer')} gemt")


if __name__ == "__main__":
    main()
