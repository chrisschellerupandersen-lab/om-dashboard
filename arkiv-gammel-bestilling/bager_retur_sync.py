"""
Uploader Retur Bager.xlsx til Railway.
Kør: python bager_retur_sync.py
"""
import re, requests, sys
import openpyxl
from pathlib import Path

FIL          = Path(r"G:\Mit drev\Organic Marked\Shopbox\Retur Bager.xlsx")
RAILWAY_URL  = "https://om-dashboard-production-0f3a.up.railway.app"
WEBHOOK_SECRET = "OM-Greve-2026-Hemlig"


def _tal(val):
    if val is None:
        return 0.0
    try:
        return float(str(val).replace(",", ".").strip())
    except (ValueError, TypeError):
        return 0.0


def _uge(val):
    if val is None:
        return None
    m = re.search(r"(\d+)", str(val))
    return int(m.group(1)) if m else None


def parse():
    wb = openpyxl.load_workbook(str(FIL), data_only=True)
    ws = wb.active
    linjer = []
    for row in ws.iter_rows(values_only=True):
        uge_raw = row[2]
        if uge_raw is None or str(uge_raw).strip().lower() in ("retur", ""):
            continue
        uge = _uge(uge_raw)
        if uge is None:
            continue
        aar = row[9]
        if aar is None:
            continue
        linjer.append({
            "uge":          uge,
            "aar":          int(aar),
            "retur_wiener": _tal(row[3]),
            "retur_boller": _tal(row[4]),
            "tgtg":         _tal(row[5]),
            "b_kvali":      _tal(row[6]),
            "retur_ialt":   _tal(row[7]),
            "faktura":      _tal(row[8]),
        })
    return linjer


def synk():
    linjer = parse()
    print(f"Parser: {len(linjer)} uger fra {FIL.name}")
    r = requests.post(
        f"{RAILWAY_URL}/api/bager/retur-opdater",
        json={"secret": WEBHOOK_SECRET, "linjer": linjer},
        timeout=30,
    )
    r.raise_for_status()
    res = r.json()
    print(f"OK — {res.get('linjer')} linjer uploadet")


if __name__ == "__main__":
    synk()
