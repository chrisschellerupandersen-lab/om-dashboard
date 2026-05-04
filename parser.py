import io
from typing import List, Dict, Any
import openpyxl

KOLONNE_MAP = {
    "varenummer": ["varenr", "varenr.", "varenummer", "item no", "sku"],
    "varenavn":   ["varenavn", "varebetegnelse", "betegnelse", "navn", "description"],
    "antal":      ["antal", "qty", "quantity", "solgt antal"],
    "omsætning":  ["omsætning", "omsaetning", "salgspris", "salg i alt", "revenue"],
    "kostpris":   ["kostpris", "kost", "kostbeløb", "cost"],
    "avance":     ["avance", "profit", "db", "dækningsbidrag"],
    "avance_pct": ["avance %", "avance%", "avanceprocent", "db %", "margin"],
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
        return float(str(val).replace(",", ".").replace(" ", "").replace("\xa0", ""))
    except (ValueError, TypeError):
        return 0.0


def parse_shopbox_xlsx(file_bytes: bytes) -> List[Dict[str, Any]]:
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    ws = wb.active

    alle_rækker = list(ws.iter_rows(values_only=True))
    if not alle_rækker:
        return []

    # Find header-rækken (første række med mindst 3 ikke-tomme celler)
    header_idx = 0
    for i, row in enumerate(alle_rækker):
        if sum(1 for c in row if c is not None) >= 3:
            header_idx = i
            break

    headers = [str(c).strip() if c is not None else "" for c in alle_rækker[header_idx]]
    col = {felt: _find_col(headers, kandidater) for felt, kandidater in KOLONNE_MAP.items()}

    produkter = []
    for row in alle_rækker[header_idx + 1:]:
        if not row or sum(1 for c in row if c is not None) < 2:
            continue

        første = str(row[0]).lower().strip() if row[0] else ""
        if any(w in første for w in ["total", "i alt", "sum", "subtotal"]):
            continue

        def get(felt: str, default=""):
            idx = col.get(felt, -1)
            return row[idx] if 0 <= idx < len(row) else default

        varenavn = str(get("varenavn", "")).strip()
        omsætning = _tal(get("omsætning"))

        if not varenavn and omsætning == 0:
            continue

        produkter.append({
            "varenummer": str(get("varenummer", "")).strip(),
            "varenavn":   varenavn,
            "antal":      _tal(get("antal")),
            "omsætning":  omsætning,
            "kostpris":   _tal(get("kostpris")),
            "avance":     _tal(get("avance")),
            "avance_pct": _tal(get("avance_pct")),
        })

    return produkter
