"""
MobilePay daglig sync — henter omsætning fra Vipps MobilePay Report API
og uploader til Railway-dashboardet.

Find ledger-IDs:
  python mobilepay_sync.py --list-ledgers

Importer al historisk data (første gang):
  python mobilepay_sync.py --historisk

Daglig kørsel:
  python mobilepay_sync.py              # i dag + i går
  python mobilepay_sync.py --dato 2026-05-16
  python mobilepay_sync.py --dage 7

Konfiguration i .env-fil:
  MP_CLIENT_ID     = fra portal.vippsmobilepay.com → Developers
  MP_CLIENT_SECRET = fra portal.vippsmobilepay.com → Developers
  MP_MSN           = merchant serial number (f.eks. 2084977)
  MP_LEDGER_ID     = find med --list-ledgers (f.eks. 1452776)

Kræver:  pip install requests
"""

import argparse
import os
import time
import requests
from datetime import date, timedelta
from pathlib import Path
from collections import defaultdict

# ── Indlæs .env ───────────────────────────────────────────────────────────────
_env = Path(__file__).parent / ".env"
if _env.exists():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

# ── Konfiguration ─────────────────────────────────────────────────────────────
RAILWAY_URL    = "https://om-dashboard-production-0f3a.up.railway.app"
WEBHOOK_SECRET = "OM-Greve-2026-Hemlig"

MP_CLIENT_ID     = os.environ.get("MP_CLIENT_ID", "")
MP_CLIENT_SECRET = os.environ.get("MP_CLIENT_SECRET", "")
MP_MSN           = os.environ.get("MP_MSN", "")
MP_LEDGER_ID     = os.environ.get("MP_LEDGER_ID", "")

MP_BASE      = "https://api.vipps.no"
TOKEN_URL    = f"{MP_BASE}/accesstoken/get"
# ─────────────────────────────────────────────────────────────────────────────

_token_cache = {"token": None, "expires_at": 0}


def get_token() -> str:
    global _token_cache
    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]
    if not MP_CLIENT_ID or not MP_CLIENT_SECRET:
        raise RuntimeError("MP_CLIENT_ID eller MP_CLIENT_SECRET mangler i .env")
    r = requests.post(TOKEN_URL, headers={
        "client_id":     MP_CLIENT_ID,
        "client_secret": MP_CLIENT_SECRET,
        "Content-Type":  "application/json",
    }, json={}, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"Token fejlede: HTTP {r.status_code}\n{r.text[:200]}")
    data = r.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"Ingen access_token i svar: {data}")
    expires_in = int(data.get("expires_in", 86400))
    _token_cache = {"token": token, "expires_at": time.time() + expires_in}
    return token


def _hdrs() -> dict:
    return {
        "Authorization":           f"Bearer {get_token()}",
        "Merchant-Serial-Number":  MP_MSN,
        "Accept":                  "application/json",
    }


def list_ledgers():
    r = requests.get(f"{MP_BASE}/settlement/v1/ledgers", headers=_hdrs(), timeout=15)
    if r.status_code != 200:
        print(f"[FEJL] HTTP {r.status_code}: {r.text[:200]}")
        return
    items = r.json().get("items", [])
    if not items:
        print("Ingen ledgers fundet.")
        return
    print(f"\nFundne ledgers ({len(items)}):\n")
    for l in items:
        lid  = l.get("ledgerId", "?")
        curr = l.get("currency", "")
        units = [u.get("name", "") for u in l.get("salesUnits", [])]
        print(f"  {lid}  {curr}  {', '.join(units)}")
    print(f"\nSæt i .env:  MP_LEDGER_ID={items[0]['ledgerId']}")


def fetch_dag(dato: str) -> float:
    """Hent total indgående omsætning (inkl. moms) for én dato via funds/dates."""
    if not MP_LEDGER_ID:
        raise RuntimeError("MP_LEDGER_ID mangler — kør: python mobilepay_sync.py --list-ledgers")
    url = f"{MP_BASE}/report/v2/ledgers/{MP_LEDGER_ID}/funds/dates/{dato}"
    r = requests.get(url, headers=_hdrs(), timeout=15)
    if r.status_code == 404:
        return 0.0
    if r.status_code != 200:
        print(f"  [WARN] {dato}: HTTP {r.status_code} — {r.text[:80]}")
        return 0.0
    items = r.json().get("items", [])
    total_oere = sum(
        item["amount"] for item in items
        if item.get("entryType") == "capture" and item.get("amount", 0) > 0
    )
    return round(total_oere / 100, 2)


def fetch_historisk() -> dict:
    """Hent AL historisk data via feed. Returnerer {dato: kr_inkl}."""
    if not MP_LEDGER_ID:
        raise RuntimeError("MP_LEDGER_ID mangler — kør: python mobilepay_sync.py --list-ledgers")
    print("Henter historisk data via feed...")
    day_totals: dict = defaultdict(float)
    cursor = ""
    page = 0
    while True:
        url = f"{MP_BASE}/report/v2/ledgers/{MP_LEDGER_ID}/funds/feed"
        if cursor:
            url += f"?cursor={cursor}"
        r = requests.get(url, headers=_hdrs(), timeout=15)
        if r.status_code != 200:
            print(f"[WARN] Feed side {page}: HTTP {r.status_code}")
            break
        data    = r.json()
        items   = data.get("items", [])
        has_more = data.get("hasMore", False)
        cursor  = data.get("cursor", "")
        for item in items:
            if item.get("entryType") == "capture" and item.get("amount", 0) > 0:
                day_totals[item["ledgerDate"]] += item["amount"] / 100
        page += 1
        if not has_more:
            break
    return {d: round(v, 2) for d, v in day_totals.items()}


def upload_til_railway(linjer: list):
    r = requests.post(
        f"{RAILWAY_URL}/api/mobilepay/dagssalg",
        json={"secret": WEBHOOK_SECRET, "linjer": linjer},
        timeout=20,
    )
    r.raise_for_status()
    result = r.json()
    print(f"[OK] Uploadet {result.get('linjer', '?')} dage til Railway")


def sync_dates(datoer: list):
    linjer = []
    for dato in datoer:
        omsat = fetch_dag(dato)
        linjer.append({"dato": dato, "omsaetning_inkl": omsat, "kilde": "api"})
        print(f"  {dato}  {omsat:>10,.2f} kr" if omsat > 0 else f"  {dato}  —")
    upload_til_railway(linjer)
    return linjer


def sync_historisk():
    day_totals = fetch_historisk()
    if not day_totals:
        print("Ingen historisk data fundet.")
        return
    print(f"\nFundne {len(day_totals)} dage med data:")
    for d in sorted(day_totals):
        print(f"  {d}  {day_totals[d]:>10,.2f} kr")
    linjer = [{"dato": d, "omsaetning_inkl": v, "kilde": "api-feed"}
              for d, v in day_totals.items()]
    upload_til_railway(linjer)


def main():
    parser = argparse.ArgumentParser(description="MobilePay → Railway sync")
    parser.add_argument("--list-ledgers", action="store_true",
                        help="Vis alle tilgængelige ledger-IDs")
    parser.add_argument("--historisk", action="store_true",
                        help="Importer al historisk data via feed")
    parser.add_argument("--dato", help="Specifik dato (YYYY-MM-DD)")
    parser.add_argument("--dage", type=int, default=2,
                        help="Antal dage bagud (default: 2)")
    args = parser.parse_args()

    if args.list_ledgers:
        list_ledgers()
        return

    if args.historisk:
        sync_historisk()
        return

    if args.dato:
        datoer = [args.dato]
    else:
        today  = date.today()
        datoer = [(today - timedelta(days=i)).isoformat()
                  for i in range(args.dage - 1, -1, -1)]

    print(f"Synkroniserer {len(datoer)} dato(er): {datoer}")
    sync_dates(datoer)


if __name__ == "__main__":
    main()
