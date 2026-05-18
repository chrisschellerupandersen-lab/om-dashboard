"""
MobilePay daglig sync — henter omsætning fra Vipps MobilePay Report API
og uploader til Railway-dashboardet.

Første gang (find dine ledger-IDs):
  python mobilepay_sync.py --list-ledgers

Daglig kørsel:
  python mobilepay_sync.py              # i dag + i går
  python mobilepay_sync.py --dato 2026-05-16
  python mobilepay_sync.py --dage 7

Konfiguration i .env-fil (samme mappe som scriptet):
  MP_CLIENT_ID     = fra portal.vippsmobilepay.com → Developers
  MP_CLIENT_SECRET = fra portal.vippsmobilepay.com → Developers
  MP_MSN           = dit merchant serial number (f.eks. 2084977)
  MP_LEDGER_ID     = find med --list-ledgers

Kræver:  pip install requests
"""

import argparse
import os
import time
import requests
from datetime import date, timedelta
from pathlib import Path

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

# Vipps MobilePay API endpoints
MP_BASE       = "https://api.vippsmobilepay.com"
TOKEN_URL     = f"{MP_BASE}/accesstoken/get"
# ─────────────────────────────────────────────────────────────────────────────

# Token-cache
_token_cache = {"token": None, "expires_at": 0}


def get_token() -> str:
    """Hent OAuth2 Bearer-token via client credentials. Caches til udløb."""
    global _token_cache
    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    if not MP_CLIENT_ID or not MP_CLIENT_SECRET:
        raise RuntimeError(
            "MP_CLIENT_ID eller MP_CLIENT_SECRET mangler i .env-filen."
        )

    # Vipps MobilePay: client_id + client_secret sendes som headers
    r = requests.post(
        TOKEN_URL,
        headers={
            "client_id":     MP_CLIENT_ID,
            "client_secret": MP_CLIENT_SECRET,
            "Content-Type":  "application/json",
        },
        timeout=15,
    )

    if r.status_code != 200:
        raise RuntimeError(
            f"Token-hentning fejlede: HTTP {r.status_code}\n{r.text[:300]}"
        )

    data = r.json()
    token = data.get("access_token") or data.get("token")
    if not token:
        raise RuntimeError(f"Ingen access_token i svar: {data}")

    expires_in = int(data.get("expires_in", 3600))
    _token_cache = {"token": token, "expires_at": time.time() + expires_in}
    return token


def _auth_headers() -> dict:
    return {
        "Authorization": f"Bearer {get_token()}",
        "Accept":        "application/json",
        "Merchant-Serial-Number": MP_MSN,
    }


def list_ledgers():
    """Vis alle ledgers tilknyttet API-nøglen."""
    r = requests.get(
        f"{MP_BASE}/report/v2/ledgers",
        headers=_auth_headers(),
        timeout=15,
    )
    if r.status_code != 200:
        print(f"[FEJL] HTTP {r.status_code}: {r.text[:300]}")
        return

    data = r.json()
    ledgers = (
        data.get("ledgers")
        or data.get("items")
        or (data if isinstance(data, list) else [])
    )

    if not ledgers:
        print("Ingen ledgers fundet. Tjek credentials og MSN.")
        print("Råsvar:", data)
        return

    print(f"\nFundne ledgers ({len(ledgers)}):\n")
    for l in ledgers:
        lid  = l.get("ledgerId") or l.get("id") or "?"
        name = l.get("name") or l.get("currency") or l.get("type") or ""
        curr = l.get("currency") or ""
        print(f"  {lid}  {curr}  {name}")

    first_id = ledgers[0].get("ledgerId") or ledgers[0].get("id") or ""
    print(f"\nSæt i .env:  MP_LEDGER_ID={first_id}")


def fetch_dag(dato: str) -> float:
    """Hent total indgående omsætning (inkl. moms) for én dato (YYYY-MM-DD)."""
    if not MP_LEDGER_ID:
        raise RuntimeError(
            "MP_LEDGER_ID mangler.\n"
            "Kør:  python mobilepay_sync.py --list-ledgers\n"
            "Sæt derefter MP_LEDGER_ID=... i .env"
        )

    url = f"{MP_BASE}/report/v2/ledgers/{MP_LEDGER_ID}/funds/dates/{dato}"
    r = requests.get(url, headers=_auth_headers(), timeout=15)

    if r.status_code == 404:
        return 0.0
    if r.status_code != 200:
        print(f"  [WARN] {dato}: HTTP {r.status_code} — {r.text[:100]}")
        return 0.0

    data = r.json()
    entries = (
        data.get("items")
        or data.get("entries")
        or (data if isinstance(data, list) else [])
    )

    total_oere = 0
    for e in entries:
        entry_type = (e.get("entryType") or e.get("type") or "").lower()
        # Kun indgående salg — ikke refunds/fees
        if entry_type in ("capture", "sale", "payment", ""):
            amount = e.get("amount") or e.get("grossAmount") or 0
            if isinstance(amount, dict):
                amount = amount.get("value") or amount.get("amount") or 0
            if isinstance(amount, (int, float)) and amount > 0:
                total_oere += amount

    # API returnerer beløb i øre
    return round(total_oere / 100, 2)


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
        linjer.append({
            "dato":            dato,
            "omsaetning_inkl": omsat,
            "kilde":           "api",
        })
        if omsat > 0:
            print(f"  {dato}  {omsat:>10,.2f} kr")
        else:
            print(f"  {dato}  —")

    upload_til_railway(linjer)
    return linjer


def main():
    parser = argparse.ArgumentParser(description="MobilePay → Railway sync")
    parser.add_argument("--list-ledgers", action="store_true",
                        help="Vis alle tilgængelige ledger-IDs")
    parser.add_argument("--dato", help="Specifik dato (YYYY-MM-DD)")
    parser.add_argument("--dage", type=int, default=2,
                        help="Antal dage bagud (default: 2 = i dag + i går)")
    args = parser.parse_args()

    if args.list_ledgers:
        list_ledgers()
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
