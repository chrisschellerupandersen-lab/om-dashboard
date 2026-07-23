"""
Shopbox-salgssync via det officielle REST API (ingen CSV/xlsx, ingen browser).

Henter kassebonner (/baskets) direkte fra Shopbox og sender salgslinjerne til
dashboardet. Fordi det er rene HTTP-kald, kan det køre automatisk på Railway.

Docs: https://docs.shopbox.com/  ·  Base: https://api-dev.shopbox.com/api/v3
Auth: accessToken som query-param + client (butiks-id, heltal).

────────────────────────────────────────────────────────────────────────────
OPSÆTNING (miljøvariabler — token må ALDRIG stå i koden/git):
    setx SHOPBOX_TOKEN   "din-api-token"
    setx SHOPBOX_CLIENT  "dit-butiks-id"      (heltal)
    setx SHOPBOX_BASE    "https://api.shopbox.com/api/v3"   (valgfri; default = dev)

BRUG:
    py shopbox_sync.py --inspect          # 1) vis dataformat (kør denne FØRST)
    py shopbox_sync.py                     # i går + i dag → upload
    py shopbox_sync.py --dato 2026-07-22
    py shopbox_sync.py --dage 7
    py shopbox_sync.py --dato 2026-07-22 --dry   # map uden at uploade
────────────────────────────────────────────────────────────────────────────
"""
import os
import sys
import json
from datetime import date, datetime, timedelta

import requests

RAILWAY_URL    = os.environ.get("RAILWAY_URL", "https://om-dashboard-production-0f3a.up.railway.app")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "OM-Greve-2026-Hemlig")

BASE    = os.environ.get("SHOPBOX_BASE", "https://api-dev.shopbox.com/api/v3").rstrip("/")
TOKEN   = os.environ.get("SHOPBOX_TOKEN", "")
CLIENT  = os.environ.get("SHOPBOX_CLIENT", "")
# Beløb i basket-linjer er heltal (typisk øre). Divideres med denne. Bekræft med --inspect.
BELOEB_DIVISOR = float(os.environ.get("SHOPBOX_AMOUNT_DIVISOR", "100"))


def _tjek_konfig():
    if not TOKEN or not CLIENT:
        sys.exit("FEJL: sæt SHOPBOX_TOKEN og SHOPBOX_CLIENT som miljøvariabler først.")


def api_get(path: str, **params) -> dict:
    params.setdefault("accessToken", TOKEN)
    params.setdefault("client", CLIENT)
    r = requests.get(f"{BASE}{path}", params=params, timeout=60)
    if r.status_code == 401:
        sys.exit("FEJL: 401 — token afvist. Er SHOPBOX_TOKEN gyldig og client-id korrekt?")
    r.raise_for_status()
    return r.json()


# ── Hjælpere til at gætte struktur robust ─────────────────────────────────────

def _dato_af_basket(b: dict) -> str | None:
    for k in ("crdate", "tstamp", "created", "date", "timestamp"):
        v = b.get(k)
        if isinstance(v, (int, float)) and v > 1_000_000_000:
            return datetime.fromtimestamp(v).strftime("%Y-%m-%d")
    for k in ("crdate_formatted", "date_formatted", "created_at"):
        v = b.get(k)
        if isinstance(v, str) and len(v) >= 10:
            return v[:10]
    return None


def _tid_af_basket(b: dict) -> int:
    for k in ("crdate", "tstamp", "created", "timestamp"):
        v = b.get(k)
        if isinstance(v, (int, float)) and v > 1_000_000_000:
            return datetime.fromtimestamp(v).hour
    return -1


def _linjer_i_basket(b: dict) -> list:
    for k in ("products", "items", "basketProducts", "lines", "basket_products"):
        v = b.get(k)
        if isinstance(v, list):
            return v
    return []


def _f(v, div=1.0):
    try:
        return round(float(v) / div, 2)
    except (ValueError, TypeError):
        return 0.0


# ── Inspektion: vis det rigtige dataformat ────────────────────────────────────

def inspect():
    _tjek_konfig()
    print(f"Base: {BASE}  ·  client: {CLIENT}\n")
    j = api_get("/baskets", page=1)
    print("Top-level nøgler:", list(j.keys()))
    data = j.get("data") or j.get("baskets") or (j if isinstance(j, list) else [])
    print("Antal baskets i side 1:", len(data))
    if not data:
        print("Ingen baskets returneret — tjek client-id og token.")
        return
    b = data[0]
    print("\nBASKET-nøgler:", list(b.keys()))
    print("  gættet dato :", _dato_af_basket(b))
    print("  gættet tid  :", _tid_af_basket(b))
    linjer = _linjer_i_basket(b)
    print("  varelinjer i basket:", len(linjer),
          "(hvis 0 → linjerne hentes måske separat pr. basket)")
    if linjer:
        print("  LINJE-nøgler:", list(linjer[0].keys()))
    print("\nRå første basket (uddrag):")
    print(json.dumps(b, ensure_ascii=False, indent=1)[:1500])
    # Test hvilket dato-parameter der filtrerer
    i_gaar = (date.today() - timedelta(days=1)).isoformat()
    print(f"\nTester dato-filtre mod {i_gaar} (antal baskets):")
    for p in ("from", "to", "date", "dateFrom", "startDate", "crdate_from", "created_from"):
        try:
            jj = api_get("/baskets", page=1, **{p: i_gaar})
            n = len(jj.get("data") or [])
            print(f"  ?{p}={i_gaar:<12} → {n}")
        except Exception as e:
            print(f"  ?{p}= … fejl: {str(e)[:50]}")


# ── Hentning + mapping ────────────────────────────────────────────────────────

def hent_baskets_for(datoer: set) -> list:
    """Paginér gennem baskets og behold dem hvis dato er i sættet.
    Klient-side dato-filter → uafhængigt af hvilket query-param API'et bruger."""
    maal = set(datoer)
    ud, side = [], 1
    tomme = 0
    while side <= 200:
        j = api_get("/baskets", page=side)
        data = j.get("data") or (j if isinstance(j, list) else [])
        if not data:
            break
        ramt = 0
        for b in data:
            d = _dato_af_basket(b)
            if d in maal:
                ud.append(b); ramt += 1
        # Stop hvis vi er kommet forbi de ønskede dage (nyeste-først antaget)
        aeldste = min((_dato_af_basket(b) or "9999") for b in data)
        if aeldste < min(maal):
            tomme += 1
            if tomme >= 2:
                break
        side += 1
    return ud


def map_til_transaktioner(baskets: list) -> list:
    trans = []
    for b in baskets:
        if b.get("canceled"):
            continue
        d = _dato_af_basket(b)
        if not d:
            continue
        tid = _tid_af_basket(b)
        bon = str(b.get("uid") or b.get("id") or b.get("basket") or "")
        for l in _linjer_i_basket(b):
            navn = str(l.get("product_name") or l.get("name") or "").strip()
            if not navn:
                continue
            trans.append({
                "dato":         d,
                "varenummer":   str(l.get("product") or l.get("sku") or "").strip(),
                "varenavn":     navn,
                "kategori":     str(l.get("category") or l.get("category_name") or "").strip(),
                "antal":        int(_f(l.get("quantity") or 1)),
                "omsætning":    _f(l.get("total") or l.get("amount") or 0, BELOEB_DIVISOR),
                "kostpris":     0,      # dashboardet udleder kostpris fra varestamdata
                "avance":       0,
                "avance_pct":   0,
                "time_start":   tid,
                "bon_nr":       bon,
            })
    return trans


def main():
    args = sys.argv[1:]
    if "--inspect" in args:
        inspect(); return
    _tjek_konfig()

    if "--dato" in args:
        datoer = {args[args.index("--dato") + 1]}
    elif "--dage" in args:
        n = int(args[args.index("--dage") + 1])
        datoer = {(date.today() - timedelta(days=i)).isoformat() for i in range(n)}
    else:
        datoer = {date.today().isoformat(), (date.today() - timedelta(days=1)).isoformat()}

    print(f"Henter baskets for {len(datoer)} dato(er): {sorted(datoer)}")
    baskets = hent_baskets_for(datoer)
    trans = map_til_transaktioner(baskets)
    print(f"  {len(baskets)} baskets → {len(trans)} salgslinjer")
    if not trans:
        print("  Ingen linjer — kør 'py shopbox_sync.py --inspect' for at tjekke formatet.")
        return

    if "--dry" in args:
        print("  (--dry) eksempel-linje:", json.dumps(trans[0], ensure_ascii=False))
        return

    r = requests.post(f"{RAILWAY_URL}/api/salg/ingest",
                      json={"secret": WEBHOOK_SECRET, "transaktioner": trans}, timeout=120)
    r.raise_for_status()
    res = r.json()
    print(f"OK — {res.get('raekker')} linjer indlæst for dage {res.get('dage')}")


if __name__ == "__main__":
    main()
