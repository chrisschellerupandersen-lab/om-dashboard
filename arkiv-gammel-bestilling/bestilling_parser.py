"""Parser for Organic Market ugebestillinger (Excel .xlsx)."""
import re
import openpyxl
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List


def _tal(val) -> float:
    if val is None:
        return 0.0
    s = str(val).replace(",", ".").replace("\xa0", "").strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _uge_fra_filnavn(path: str) -> int | None:
    m = re.search(r'uge\s*(\d+)', Path(path).stem, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _aar_fra_mtime(path: str) -> int:
    return datetime.fromtimestamp(Path(path).stat().st_mtime).year


def parse_bestilling_xlsx(path: str) -> Dict[str, Any]:
    """Returnerer {"uge": int, "aar": int, "linjer": [...]}."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    rows = [list(row) for row in ws.iter_rows(values_only=True)]

    # Uge fra filnavn (pålideligst — indholdet kan bære gammel uge fra skabelon)
    uge = _uge_fra_filnavn(path)

    # År fra titel i række 1 ellers filtid
    aar = _aar_fra_mtime(path)
    if rows:
        title = " ".join(str(c) for c in rows[0] if c)
        m_aar = re.search(r'(202\d)', title)
        if m_aar:
            aar = int(m_aar.group(1))

    def _bestem_sektion(varenavn: str, varenummer: str = '') -> int:
        """Kategoriser produkt i 1 af 4 sektioner baseret på varenavn.
        Bagerens varenumre ≠ Shopbox SKU'er, så kun varenavn bruges.
        """
        n = varenavn.lower()

        # Kager — tjekkes FØR boller så 'fastelavnsbolle' ikke snupper kager
        if any(k in n for k in ('studenterbr', 'stammer', 'napoleonshat',
                                 'cookie', 'kokostoppe', 'romkugl', 'muffin',
                                 'brownie', 'honningbomb', 'honninghjerter',
                                 'snitter', 'kage')):
            return 4

        # Wienerbrød — tjekkes FØR boller (fastelavnsbolle er wienerbrød)
        if any(k in n for k in ('croissant', 'snegl', 'snurrer', 'frøsnapper',
                                 'wienerstang', 'kanelstang', 'spandauer',
                                 'marcipan', 'romsnegl', 'wienerbr',
                                 'tebirkes', 'grovbirkes', 'fastelavns')):
            return 3

        # Boller
        if any(k in n for k in ('bolle', 'musli', 'teboller', 'grøskar',
                                 'hveder')):
            return 2

        # Default: Brød, flute & focaccia
        return 1

    linjer: List[Dict[str, Any]] = []
    for i, row in enumerate(rows):
        if i < 3:
            continue
        if len(row) < 12:
            continue

        varenavn = row[2]
        if varenavn is None:
            continue
        varenavn = str(varenavn).strip()
        if not varenavn or varenavn.lower() in ("varetype", ""):
            continue
        if "i alt" in varenavn.lower():
            continue

        total_antal = _tal(row[11])
        # Medtag linjer der har mindst én dags bestilling
        man = _tal(row[4]); tir = _tal(row[5]); ons = _tal(row[6])
        tor = _tal(row[7]); fre = _tal(row[8]); loe = _tal(row[9]); son = _tal(row[10])
        if (man + tir + ons + tor + fre + loe + son) == 0 and total_antal == 0:
            continue

        # Varenummer: gem som rent heltal-streng (undgår "10040.0")
        vnr = row[1]
        if vnr is not None:
            try:
                vnr = str(int(float(vnr)))
            except (ValueError, TypeError):
                vnr = str(vnr).strip()
        else:
            vnr = ""

        linjer.append({
            "varenummer":   vnr,
            "varenavn":     varenavn,
            "pris_ex_moms": _tal(row[3]),
            "man": man, "tir": tir, "ons": ons, "tor": tor,
            "fre": fre, "loe": loe, "son": son,
            "total_antal":  total_antal,
            "total_pris":   _tal(row[12] if len(row) > 12 else None),
            "sektion":      _bestem_sektion(varenavn, vnr),
        })

    return {"uge": uge, "aar": aar, "linjer": linjer}
