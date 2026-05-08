"""
Uploader Varestamdata.xlsx til Railway.
Kør: python stamdata_sync.py
"""
import requests
import openpyxl
from pathlib import Path

FIL           = Path(r"G:\Mit drev\Organic Marked\Shopbox\Varestamdata.xlsx")
RAILWAY_URL   = "https://om-dashboard-production-0f3a.up.railway.app"
WEBHOOK_SECRET = "OM-Greve-2026-Hemlig"


def parse():
    wb = openpyxl.load_workbook(str(FIL), data_only=True)
    ws = wb.active
    linjer = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue  # spring header over
        sku, varenavn, type_, pris = row[0], row[1], row[2], row[3]
        if not varenavn or not type_:
            continue
        linjer.append({
            "sku":          str(int(sku)) if isinstance(sku, (int, float)) and sku else "",
            "varenavn":     str(varenavn).strip(),
            "type":         str(type_).strip(),
            "pris_ex_moms": float(pris) if pris else 0.0,
        })
    return linjer


def synk():
    linjer = parse()
    print(f"Parser: {len(linjer)} varer fra {FIL.name}")
    r = requests.post(
        f"{RAILWAY_URL}/api/stamdata/bulk",
        json={"secret": WEBHOOK_SECRET, "linjer": linjer},
        timeout=30,
    )
    r.raise_for_status()
    res = r.json()
    print(f"OK — {res.get('linjer')} varer uploadet")


if __name__ == "__main__":
    synk()
