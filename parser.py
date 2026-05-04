import io
import csv
from datetime import datetime
from typing import List, Dict, Any
import openpyxl

KOLONNE_MAP = {
    "varenummer": ["item sku code", "item sku", "varenr", "varenummer", "sku"],
    "varenavn":   ["item name", "varenavn", "varebetegnelse", "navn"],
    "antal":      ["mængde", "antal", "qty", "quantity"],
    "omsætning":  ["total amount", "omsætning", "omsaetning", "total salg"],
    "kostpris":   ["cost of goods sold", "kostpris", "cost"],
    "avance":     ["gross profit", "avance", "profit"],
    "avance_pct": ["gross profit margin", "avance %", "avance%", "margin"],
    "dato":       ["dato", "date"],
    "kategori":   ["category name", "kategori", "category"],
}


def _find_col(headers: List[str], kandidater: List[str]) -> int:
    lower = [h.lower().strip() if h else "" for h in headers]
    for k in kandidater:
        k_lower = k.lower().strip()
        for i, h in enumerate(lower):
            if k_lower == h or k_lower in h or h in k_lower:
                return i
    return -1


def _tal(val) -> float:
    if val is None:
        return 0.0
    try:
        return float(str(val).replace(",", ".").replace(" ", "").replace("\xa0", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _dato(val) -> str | None:
    if not val:
        return None
    s = str(val).strip()
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(s[:10], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _parse_rækker(alle_rækker: List[List]) -> List[Dict[str, Any]]:
    if not alle_rækker:
        return []

    # Find header-rækken
    header_idx = 0
    for i, row in enumerate(alle_rækker):
        if sum(1 for c in row if c and str(c).strip()) >= 3:
            header_idx = i
            break

    headers = [str(c).strip() if c else "" for c in alle_rækker[header_idx]]
    col = {felt: _find_col(headers, kandidater) for felt, kandidater in KOLONNE_MAP.items()}

    print(f"[DEBUG] Kolonner fundet: { {k:v for k,v in col.items() if v >= 0} }")
    if len(alle_rækker) > header_idx + 1:
        print(f"[DEBUG] Første datarække: {alle_rækker[header_idx + 1]}")

    transaktioner = []
    for row in alle_rækker[header_idx + 1:]:
        if not row or sum(1 for c in row if c and str(c).strip()) < 2:
            continue

        def get(felt: str, default=""):
            idx = col.get(felt, -1)
            return row[idx] if 0 <= idx < len(row) else default

        varenavn  = str(get("varenavn", "")).strip()
        omsætning = _tal(get("omsætning"))
        dato_rå   = get("dato")
        dato      = _dato(dato_rå)

        if not varenavn or not dato:
            if len(transaktioner) == 0:
                print(f"[DEBUG] Filtreret: varenavn={repr(varenavn)} dato_rå={repr(dato_rå)} dato={dato}")
            continue

        transaktioner.append({
            "dato":       dato,
            "varenummer": str(get("varenummer", "")).strip(),
            "varenavn":   varenavn,
            "kategori":   str(get("kategori", "")).strip(),
            "antal":      _tal(get("antal")),
            "omsætning":  omsætning,
            "kostpris":   _tal(get("kostpris")),
            "avance":     _tal(get("avance")),
            "avance_pct": _tal(get("avance_pct")),
        })

    return transaktioner


def _parse_xlsx(file_bytes: bytes) -> List[Dict[str, Any]]:
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    ws = wb.active
    return _parse_rækker([list(row) for row in ws.iter_rows(values_only=True)])


def _parse_csv(file_bytes: bytes) -> List[Dict[str, Any]]:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            tekst = file_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            continue

    sample = tekst[:2000]
    pipes = sample.count("|")
    tabs  = sample.count("\t")
    semis = sample.count(";")

    if pipes >= max(tabs, semis) and pipes > 5:
        sep = "|"
    elif tabs >= semis:
        sep = "\t"
    else:
        sep = ";"

    reader    = csv.reader(io.StringIO(tekst), delimiter=sep)
    alle_rækker = [[cell.strip() for cell in row] for row in reader]
    return _parse_rækker(alle_rækker)


def parse_shopbox_xlsx(file_bytes: bytes) -> List[Dict[str, Any]]:
    try:
        return _parse_xlsx(file_bytes)
    except Exception:
        return _parse_csv(file_bytes)
