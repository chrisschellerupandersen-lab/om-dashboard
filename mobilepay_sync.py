"""
MobilePay daglig sync — henter omsætning fra Vipps MobilePay Report API
og uploader til Railway-dashboardet.

Første gang (find dine ledger-IDs):
  python mobilepay_sync.py --list-ledgers

Daglig kørsel:
  python mobilepay_sync.py              # i dag + i går
  python mobilepay_sync.py --dato 2026-05-16
  python mobilepay_sync.py --dage 7

Konfiguration via miljøvariabler eller .env-fil:
  MP_API_KEY        = din API-nøgle fra portal.mobilepay.dk / portal.vippsmobilepay.com
  MP_LEDGER_ID      = ledger-ID (find med --list-ledgers)

Kræver:  pip install requests python-dotenv
"""

import argparse
import os
import requests
from datetime import date, timedelta
from pathlib import Path

# ── Indlæs .env hvis den findes ──────────────────────────────────────────────
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

MP_API_KEY   = os.environ.get("MP_API_KEY", "")
MP_LEDGER_ID = os.environ.get("MP_LEDGER_ID", "")

# Vipps MobilePay Report API (dansk produktion)
MP_BASE = "https://api.vippsmobilepay.com"
# ─────────────────────────────────────────────────────────────────────────────


def _headers() -> dict:
    if not MP_API_KEY:
        raise RuntimeError(
            "MP_API_KEY mangler.\n"
            "Sæt den i .env-filen: MP_API_KEY=din_nøgle_her"
        )
    return {
        "Authorization": f"Bearer {MP_API_KEY}",
        "Accept":        "application/json",
    }


def list_ledgers():
    """Vis alle ledgers tilknyttet API-nøglen."""
    r = requests.get(f"{MP_BASE}/report/v2/ledgers", headers=_headers(), timeout=15)
    if r.status_code != 200:
        print(f"[FEJL] HTTP {r.status_code}: {r.text[:200]}")
        return
    data = r.json()
    ledgers = data.get("ledgers") or data.get("items") or (data if isinstance(data, list) else [])
    if not ledgers:
        print("Ingen ledgers fundet. Tjek at din API-nøgle er korrekt.")
        print("Råsvar:", data)
        return
    print(f"\nFundne ledgers ({len(ledgers)}):\n")
    for l in ledgers:
        lid  = l.get("ledgerId") or l.get("id") or "?"
        name = l.get("name") or l.get("currency") or ""
        print(f"  {lid}  {name}")
    print(f"\nSæt i .env:  MP_LEDGER_ID={ledgers[0].get('ledgerId', ledgers[0].get('id', ''))}")


def fetch_dag(dato: str) -> float:
    """Hent total indgående omsætning (inkl. moms) for én dato.
    Returnerer 0.0 hvis ingen data. Dato-format: YYYY-MM-DD.
    """
    if not MP_LEDGER_ID:
        raise RuntimeError(
            "MP_LEDGER_ID mangler.\n"
            "Kør først: python mobilepay_sync.py --list-ledgers\n"
            "Sæt derefter i .env: MP_LEDGER_ID=..."
        )

    url = f"{MP_BASE}/report/v2/ledgers/{MP_LEDGER_ID}/funds/dates/{dato}"
    r = requests.get(url, headers=_headers(), timeout=15)

    if r.status_code == 404:
        return 0.0  # ingen transaktioner den dag
    if r.status_code != 200:
        print(f"  [WARN] {dato}: HTTP {r.status_code} — {r.text[:100]}")
        return 0.0

    data = r.json()

    # Summér alle positive beløb (captures/salg) — beløb er i øre
    total_oere = 0
    entries = data.get("items") or data.get("entries") or (data if isinstance(data, list) else [])
    for e in entries:
        entry_type = (e.get("entryType") or e.get("type") or "").lower()
        amount = e.get("amount") or e.get("grossAmount") or 0
        # Tag kun indgående salg (ikke refunds/fees)
        if entry_type in ("capture", "sale", "payment", ""):
            if isinstance(amount, dict):
                amount = amount.get("value") or amount.get("amount") or 0
            if amount > 0:
                total_oere += amount

    # Konvertér fra øre til kr
    return round(total_oere / 100, 2)


def upload_til_railway(linjer: list):
    """Upload daglige MP-beløb til Railway."""
    r = requests.post(
        f"{RAILWAY_URL}/api/mobilepay/dagssalg",
        json={"secret": WEBHOOK_SECRET, "linjer": linjer},
        timeout=20,
    )
    r.raise_for_status()
    result = r.json()
    print(f"[OK] Uploadet {result.get('linjer', '?')} dage til Railway")


def sync_dates(datoer: list):
    """Hent MP-omsætning for de givne datoer og upload til Railway."""
    linjer = []
    for dato in datoer:
        omsat = fetch_dag(dato)
        if omsat > 0:
            linjer.append({
                "dato":            dato,
                "omsaetning_inkl": omsat,
                "kilde":           "api",
            })
            print(f"  {dato}  {omsat:>10,.2f} kr")
        else:
            print(f"  {dato}  —")

    if linjer:
        upload_til_railway(linjer)
    else:
        print("Ingen MobilePay-omsætning fundet for de valgte datoer.")
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
