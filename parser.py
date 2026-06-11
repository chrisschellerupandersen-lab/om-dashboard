import io
import csv
import re
from datetime import datetime
from typing import List, Dict, Any
import openpyxl

# Shopbox Order hash: "NNNNN-YYMMDDHHMMSS" — unikt per kassebon
_ORDER_HASH_RE = re.compile(r'^\d{3,}-\d{8,}$')

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


def _vn_norm(val) -> str:
    """Normaliserer varenummer til ren tal-streng ('10078.0' → '10078'). Tomt → ''."""
    s = str(val or "").strip()
    try:
        return str(int(float(s)))
    except (ValueError, TypeError):
        return s


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

        # Order hash (unikt per kassebon): Shopbox placerer det 2 felter efter dato.
        # Format: "NNNNN-YYMMDDHHMMSS" — bruges til at tælle unikke kunder.
        # Fallback: felt 3 (gammel bon_nr-position).
        bon_nr = ""
        if dato_idx + 2 < n:
            oh = str(row[dato_idx + 2]).strip()
            if _ORDER_HASH_RE.match(oh):
                bon_nr = oh
        if not bon_nr and len(row) > 3 and row[3]:
            bon_nr = str(row[3]).strip()

        transaktioner.append({
            "dato":       dato,
            "varenummer": _vn_norm(row[col["varenummer"]]),
            "varenavn":   varenavn,
            "kategori":   kategori,
            "antal":      _tal(row[col["antal"]]),
            "omsætning":  _tal(row[col["omsaetning"]]),
            "kostpris":   _tal(row[col["kostpris"]]),
            "avance":     _tal(row[col["avance"]]),
            "avance_pct": _tal(row[col["avance_pct"]]),
            "time_start": time_start,
            "bon_nr":     bon_nr,
        })

    print(f"[INFO] Shopbox parser: {len(transaktioner)} transaktioner parsed")
    if transaktioner:
        t0 = transaktioner[0]
        print(f"[INFO] Første transaktion: dato={t0['dato']}, varenavn={repr(t0['varenavn'])}, kategori={repr(t0['kategori'])}, bon_nr={repr(t0['bon_nr'])}")
        unique_bon = len({t['bon_nr'] for t in transaktioner if t['bon_nr']})
        print(f"[INFO] Unikke bon_nr: {unique_bon} ud af {len(transaktioner)} rækker")
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
            "varenummer": _vn_norm(get("varenummer", "")),
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


def _fix_bundle_split(transaktioner: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Shopbox fordeler menu-bundle-priser på tre måder:
      1) Én komponent får hele bundle-prisen, resten får 0 kr
      2) Komponenter får detailpris, én komponent får negativt restbeløb
      3) Komponenter får uens priser men ingen er nul/negativ (Shopbox type 3)

    Algoritme:
      Case A (nul/negativ til stede): alle seeds behandles samlet — find mindste
        gruppe af positive varer der gør totalen til et positivt heltal.
      Case B (alle positive): find grupper af varer med ikke-hel per-styk pris
        der summerer til et positivt heltal (min. 2 varer) og omfordel.
    """
    from collections import defaultdict
    from itertools import combinations

    def er_hel(v: float, tol: float = 0.02) -> bool:
        return v > 0 and abs(v - round(v)) < tol

    def find_gruppe(seeds: list, kandidater: list, min_k: int = 0) -> list | None:
        seed_sum = sum(t["omsætning"] for t in seeds)
        for size in range(min_k, len(kandidater) + 1):
            for subset in combinations(kandidater, size):
                total = seed_sum + sum(t["omsætning"] for t in subset)
                if er_hel(total):
                    return list(seeds) + list(subset)
        return None

    def fordel(gruppe: list) -> list:
        total_oms  = sum(t["omsætning"] for t in gruppe)
        total_kost = sum(t["kostpris"]  for t in gruppe)
        if total_kost <= 0 or total_oms <= 0:
            return [dict(t) for t in gruppe]
        return [{**t, "omsætning": round(total_oms * t["kostpris"] / total_kost, 2)}
                for t in gruppe]

    grupper: dict = defaultdict(list)
    ingen_bon: list = []
    for t in transaktioner:
        if t.get("bon_nr"):
            grupper[(t["dato"], t["bon_nr"])].append(t)
        else:
            ingen_bon.append(t)

    resultat = list(ingen_bon)

    for gruppe in grupper.values():
        nul_neg = [t for t in gruppe if t["omsætning"] <= 0 and t["kostpris"] > 0]
        positiv = [t for t in gruppe if t["omsætning"] >  0]

        if nul_neg:
            # Case A: nul/negative seeds — find donors fra positive varer
            bundle = find_gruppe(nul_neg, positiv, min_k=0)
            if bundle:
                bids = {id(t) for t in bundle}
                resultat.extend(fordel(bundle))
                for t in gruppe:
                    if id(t) not in bids:
                        resultat.append(dict(t))
            else:
                resultat.extend([dict(t) for t in gruppe])
            continue

        # Case B: alle positive — find grupper med ikke-hel per-styk pris
        def ikke_hel_pu(t: dict) -> bool:
            return (t["antal"] > 0 and t["kostpris"] > 0
                    and not er_hel(t["omsætning"] / t["antal"]))

        kandidater   = [t for t in gruppe if ikke_hel_pu(t)]
        individuelle = [t for t in gruppe if not ikke_hel_pu(t)]

        if len(kandidater) < 2:
            resultat.extend(gruppe)
            continue

        i_bundle: set = set()
        while True:
            tilg = [k for k in kandidater if id(k) not in i_bundle]
            if len(tilg) < 2:
                break
            bundle = find_gruppe([], tilg, min_k=2)
            if not bundle:
                break
            for t in bundle:
                i_bundle.add(id(t))
            resultat.extend(fordel(bundle))

        for t in kandidater:
            if id(t) not in i_bundle:
                resultat.append(dict(t))
        resultat.extend(individuelle)

    return resultat


def parse_shopbox_xlsx(file_bytes: bytes) -> List[Dict[str, Any]]:
    try:
        transaktioner = _parse_xlsx(file_bytes)
    except Exception:
        transaktioner = _parse_csv(file_bytes)
    return _fix_bundle_split(transaktioner)
