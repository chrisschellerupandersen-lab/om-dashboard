import io
import csv
from datetime import datetime
from typing import List, Dict, Any
import openpyxl

# Shopbox varesalgsrapport pipe-format (type=1, fileType=txt)
# Datarækker starter med en tom celle (+1 offset fra header).
# "External Reference" (header col 16) kan indeholde | som skaber variable ekstra kolonner.
# Løsning: faste positioner for felter FØR ExtRef, relative fra slutningen for felter EFTER.
SHOPBOX_PIPE_COLS = {
    "antal":      7,   # Mængde:            header col 6  + 1 offset
    "omsaetning": 8,   # Total amount:      header col 7  + 1
    "kostpris":   11,  # Cost of goods sold: header col 10 + 1
    "avance":     12,  # Gross profit:      header col 11 + 1
    "avance_pct": 13,  # Gross Profit Margin: header col 12 + 1
    "varenavn":   15,  # Item name:         header col 14 + 1 (før ExtRef)
    "varenummer": 16,  # Item Sku Code:     header col 15 + 1 (før ExtRef)
}

KOLONNE_MAP = {
    "varenummer": ["item sku code", "item sku", "varenr", "varenummer", "sku"],
    "varenavn":   ["item name", "varenavn", "varebetegnelse", "navn"],
    "antal":      ["maengde", "mængde", "antal", "qty", "quantity"],
    "omsaetning": ["total amount", "omsaetning", "omsætning", "total salg"],
    "kostpris":   ["cost of goods sold", "kostpris", "cost"],
    "avance":     ["gross profit", "avance", "profit"],
    "avance_pct": ["gross profit margin", "avance %", "avance%", "margin"],
    "dato":       ["dato", "date"],
    "kategori":   ["category name", "kategori", "category"],
}


def _find_col(headers: List[str], kandidater: List[str]) -> int:
    lower = [h.lower().strip() if h else "" for h in headers]
    # 1. Eksakt match
    for k in kandidater:
        k_lower = k.lower().strip()
        for i, h in enumerate(lower):
            if k_lower == h:
                return i
    # 2. Kandidat er indeholdt i header
    for k in kandidater:
        k_lower = k.lower().strip()
        for i, h in enumerate(lower):
            if h and k_lower in h:
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


def _er_shopbox_pipe_format(headers: List[str]) -> bool:
    joined = "|".join(h.lower() for h in headers)
    return "item name" in joined and "total amount" in joined and "dato" in joined


def _parse_rækker_shopbox(alle_rækker: List[List]) -> List[Dict[str, Any]]:
    """Parser med Shopbox kolonnepositioner.
    Tal-kolonner: faste positioner. Dato/kategori: søges fra slutningen per række,
    fordi External Reference kan indeholde variable antal | tegn.
    """
    col = SHOPBOX_PIPE_COLS
    transaktioner = []
    min_cols = max(col.values()) + 1

    for row in alle_rækker:
        n = len(row)
        if n < min_cols:
            continue

        varenavn = str(row[col["varenavn"]]).strip() if row[col["varenavn"]] else ""
        if not varenavn or varenavn.lower() in ("item name", "varenavn"):
            continue

        # Søg dato fra slutningen — springer tomme celler og tidsfelter over
        dato = None
        dato_idx = -1
        for i in range(1, min(12, n)):
            d = _dato(row[n - i])
            if d is not None:
                dato = d
                dato_idx = n - i
                break

        if not dato:
            continue

        # Kategori er feltet umiddelbart før dato; tid er feltet umiddelbart efter
        kategori = str(row[dato_idx - 1]).strip() if dato_idx > 0 else ""
        time_start = -1
        if dato_idx + 1 < n:
            t = str(row[dato_idx + 1]).strip()
            if len(t) >= 5 and ":" in t:
                try:
                    time_start = int(t[:2])
                except ValueError:
                    pass

        transaktioner.append({
            "dato":       dato,
            "varenummer": str(row[col["varenummer"]]).strip(),
            "varenavn":   varenavn,
            "kategori":   kategori,
            "antal":      _tal(row[col["antal"]]),
            "omsætning":  _tal(row[col["omsaetning"]]),
            "kostpris":   _tal(row[col["kostpris"]]),
            "avance":     _tal(row[col["avance"]]),
            "avance_pct": _tal(row[col["avance_pct"]]),
            "time_start": time_start,
        })

    print(f"[INFO] Shopbox parser: {len(transaktioner)} transaktioner parsed")
    if transaktioner:
        t0 = transaktioner[0]
        print(f"[INFO] Første transaktion: dato={t0['dato']}, varenavn={repr(t0['varenavn'])}, kategori={repr(t0['kategori'])}")
    else:
        if alle_rækker:
            for r in alle_rækker[:5]:
                if len(r) >= min_cols and str(r[col["varenavn"]]).strip():
                    print(f"[WARN] Ingen dato fundet i række: varenavn={repr(r[col['varenavn']])}, sidste 8: {r[-8:]}")
                    break

    return transaktioner


def _parse_rækker_generisk(alle_rækker: List[List]) -> List[Dict[str, Any]]:
    """Generisk parser med automatisk kolonnedetektion."""
    header_idx = 0
    for i, row in enumerate(alle_rækker):
        if sum(1 for c in row if c and str(c).strip()) >= 3:
            header_idx = i
            break

    headers = [str(c).strip() if c else "" for c in alle_rækker[header_idx]]
    col = {felt: _find_col(headers, kandidater) for felt, kandidater in KOLONNE_MAP.items()}

    transaktioner = []
    for row in alle_rækker[header_idx + 1:]:
        if not row or sum(1 for c in row if c and str(c).strip()) < 2:
            continue

        def get(felt: str, default=""):
            idx = col.get(felt, -1)
            return row[idx] if 0 <= idx < len(row) else default

        varenavn  = str(get("varenavn", "")).strip()
        dato      = _dato(get("dato"))

        if not varenavn or not dato:
            continue

        transaktioner.append({
            "dato":       dato,
            "varenummer": str(get("varenummer", "")).strip(),
            "varenavn":   varenavn,
            "kategori":   str(get("kategori", "")).strip(),
            "antal":      _tal(get("antal")),
            "omsætning":  _tal(get("omsaetning")),
            "kostpris":   _tal(get("kostpris")),
            "avance":     _tal(get("avance")),
            "avance_pct": _tal(get("avance_pct")),
        })

    return transaktioner


def _parse_xlsx(file_bytes: bytes) -> List[Dict[str, Any]]:
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    ws = wb.active
    rows = [list(row) for row in ws.iter_rows(values_only=True)]
    return _parse_rækker_generisk(rows)


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

    # splitlines() håndterer \r\n, \r og \n korrekt
    linjer      = [l for l in tekst.splitlines() if l.strip()]
    alle_rækker = [[cell.strip() for cell in linje.split(sep)] for linje in linjer]

    print(f"[DEBUG] Linjer: {len(alle_rækker)}, sep={repr(sep)}")
    if len(alle_rækker) > 1:
        print(f"[DEBUG] Header[:5]: {alle_rækker[0][:5]}")
        print(f"[DEBUG] Rk1[:5]: {alle_rækker[1][:5]}")

    if not alle_rækker:
        return []

    # Find header-rækken
    header_idx = 0
    for i, row in enumerate(alle_rækker):
        if sum(1 for c in row if c and str(c).strip()) >= 3:
            header_idx = i
            break

    headers = alle_rækker[header_idx]

    if sep == "|" and _er_shopbox_pipe_format(headers):
        print(f"[INFO] Shopbox pipe-format detekteret, bruger kendte kolonnepositioner")
        return _parse_rækker_shopbox(alle_rækker[header_idx + 1:])

    return _parse_rækker_generisk(alle_rækker)


def parse_shopbox_xlsx(file_bytes: bytes) -> List[Dict[str, Any]]:
    try:
        return _parse_xlsx(file_bytes)
    except Exception:
        return _parse_csv(file_bytes)
