"""
Parser for Organic Bakery faktura-PDF (e-conomic, afsender rmk@betterbread.dk via
post@e-conomic.com). Fakturaen kommer som PDF-vedhæftning; varelinjerne ligger i
PDF'en (mailteksten er kun et link), så vi udtrækker teksten med pdfplumber.

Vigtige forhold ved PDF'en:
  * Danske tegn (ø/å/æ/Ø) kan IKKE afkodes af pdfplumber og bliver til U+FFFD (�).
    Derfor bruges de stabile ASCII **PROD-koder** (PROD-01 … PROD-22) til at slå
    varenavnet op — ikke den (delvist ødelagte) fritekst.
  * Varelinjer kan ombrydes: antal/stk.pris/pris står altid på linjen med "stk.",
    mens (resten af navnet +) ugedagen kan stå på næste fysiske linje.
  * Én linje pr. ugedag pr. vare; parseren aggregerer til man..søn (som portal-ordren).
  * En 'fragtmm'-linje ("Organic Bakery Levering (fre-søn)") er fragt — ingen "stk.".

Returnerer samme linje-struktur som portal_ordre_parser, så afstemning og økonomi
kan sammenligne faktura mod portal-bestilling vare for vare.
"""
import re
from datetime import date
from typing import Dict, List, Optional

# Afsender-kæden for Organic Bakery-fakturaer (e-conomic sender på bagerens vegne).
FAKTURA_AFSENDER = "post@e-conomic.com"
FAKTURA_REPLY_TO = "rmk@betterbread.dk"

# Stabile PROD-koder → kanoniske varenavne (samme navne som portal_ordre_parser
# lægger i ugebestillinger, så afstemning joiner korrekt). Koderne står på hver
# varelinje som "PROD-NN-".
_PROD_MAP = {
    "01": "Surdejsbrød m. sesam",
    "02": "Focaccia",
    "06": "Croissant",
    "07": "Surdejsbolle",
    "08": "Kardemommesnurre",
    "09": "Pain au Chocolate",
    "10": "Surdejsbolle m. sesam",
    "11": "Surdejsbrød",
    "12": "Tebirkes",
    "13": "Softkernerugbrød",
    "14": "Gulerodskage 1 pers",
    "15": "Gulerodskage 5-6 pers",
    "16": "Cookie",
    "17": "Tebolle m. chokolade",
    "18": "Tebolle alm",
    "19": "Kanelsnurre",
    "21": "Karamelliseret croissant",
    "22": "Surdejsbolle m. birkes",
}

DAGE = ["man", "tir", "ons", "tor", "fre", "loe", "son"]

# Tal på dansk: "10.932,00" → 10932.00 (punktum = tusind, komma = decimal).
_TAL = r"\d[\d.]*,\d{2}"
_QTY_RE   = re.compile(r"(\d+)\s+stk\.\s+(" + _TAL + r")\s+(" + _TAL + r")\s*$")
_FRAGT_RE = re.compile(r"^fragtmm\b.*?\s+(\d+)\s+(" + _TAL + r")\s+(" + _TAL + r")\s*$", re.IGNORECASE)
_PROD_RE  = re.compile(r"PROD-(\d+)-")
# Ugedag — ø/å tolereres via '.' (garblet til �): L.rdag=Lørdag, S.ndag=Søndag.
_UGEDAG_RE = re.compile(r"\b(Mandag|Tirsdag|Onsdag|Torsdag|Fredag|L.rdag|S.ndag)\b", re.IGNORECASE)


def _tal(s: str) -> float:
    return float(s.replace(".", "").replace(",", "."))


def _ugedag_kode(tok: str) -> Optional[str]:
    t = tok.lower()
    if t.startswith("man"): return "man"
    if t.startswith("tir"): return "tir"
    if t.startswith("ons"): return "ons"
    if t.startswith("tor"): return "tor"
    if t.startswith("fre"): return "fre"
    if t.startswith("l"):   return "loe"   # lørdag (l�rdag)
    if t.startswith("s"):   return "son"   # søndag (s�ndag)
    return None


def _find_ugedag(linje: str) -> Optional[str]:
    m = _UGEDAG_RE.search(linje)
    return _ugedag_kode(m.group(1)) if m else None


def _parse_dato(s: str) -> Optional[str]:
    """'07.09.2026' → ISO '2026-09-07'."""
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", s.strip())
    if not m:
        return None
    d, mo, y = m.groups()
    return f"{y}-{mo}-{d}"


def parse_faktura_tekst(tekst: str, override_uge: Optional[int] = None,
                        override_aar: Optional[int] = None) -> Dict:
    """Parser råtekst (fra pdfplumber) af en Organic Bakery-faktura.
    Returnerer header, aggregerede varelinjer (man..søn), fragt og totaler."""
    linjer = [l.rstrip() for l in tekst.replace("\r", "").split("\n")]
    n = len(linjer)

    # ── Header (tag første forekomst; gentages pr. side) ──────────────────────
    def _sog(pat):
        m = re.search(pat, tekst)
        return m.group(1).strip() if m else None

    fakturanr = _sog(r"Fakturanr\.\s*\.*\s*:\s*(\d+)")
    fak_dato  = _parse_dato(_sog(r"Fakturadato\s*\.*\s*:\s*([\d.]+)") or "")
    kundenr   = _sog(r"Kundenr\.\s*\.*\s*:\s*(\d+)")
    # "�vrigref. ...............: #1007" — match på 'vrigref' for at undgå det garblede Ø.
    ref_ordre = _sog(r"vrigref\.\s*\.*\s*:\s*#?(\d+)")

    # ── Totaler + betaling (sidste side) ──────────────────────────────────────
    subtotal = _sog(r"Subtotal\s*:\s*(" + _TAL + r")")
    moms     = _sog(r"moms\s*:\s*(" + _TAL + r")")
    total    = _sog(r"Total\s+DKK\s*:\s*(" + _TAL + r")")
    forfald  = _parse_dato(_sog(r"forfald\s+([\d.]+)") or "")

    # ── Varelinjer + fragt ────────────────────────────────────────────────────
    agg: Dict[str, Dict] = {}   # prod_kode → {dage, kr, stk_pris}
    fragt_kr = 0.0
    fragt_antal = 0

    i = 0
    while i < n:
        linje = linjer[i]

        fm = _FRAGT_RE.match(linje.strip())
        if fm:
            fragt_antal += int(fm.group(1))
            fragt_kr    += _tal(fm.group(3))
            i += 1
            continue

        qm = _QTY_RE.search(linje)
        pm = _PROD_RE.search(linje)
        if qm and pm:
            prod = pm.group(1)
            antal = int(qm.group(1))
            stk_pris = _tal(qm.group(2))
            pris = _tal(qm.group(3))
            # Ugedag: på selve linjen, ellers på næste 1-2 fortsættelseslinjer.
            dag = _find_ugedag(linje)
            if not dag:
                for k in range(i + 1, min(i + 3, n)):
                    nxt = linjer[k]
                    if _QTY_RE.search(nxt) or _PROD_RE.search(nxt) or _FRAGT_RE.match(nxt.strip()):
                        break
                    dag = _find_ugedag(nxt)
                    if dag:
                        break
            rec = agg.setdefault(prod, {"dage": {}, "kr": 0.0, "stk_pris": stk_pris})
            if dag:
                rec["dage"][dag] = rec["dage"].get(dag, 0) + antal
            rec["kr"] += pris
            rec["stk_pris"] = stk_pris
        i += 1

    # ── Byg varelinjer ────────────────────────────────────────────────────────
    varelinjer: List[Dict] = []
    for prod, rec in agg.items():
        dage = rec["dage"]
        total_antal = sum(dage.values())
        varelinjer.append({
            "prod_kode":    f"PROD-{prod}",
            "varenavn":     _PROD_MAP.get(prod, f"PROD-{prod} (ukendt)"),
            "varenummer":   "",
            "pris_ex_moms": rec["stk_pris"],
            **{d: dage.get(d, 0) for d in DAGE},
            "total_antal":  total_antal,
            "total_pris":   round(rec["kr"], 2),
        })
    varelinjer.sort(key=lambda x: x["varenavn"])

    aar = int(override_aar) if override_aar else None
    uge = int(override_uge) if override_uge else None
    if (uge is None or aar is None) and fak_dato:
        # Fakturaen dækker ugen der LIGE er afsluttet (varerne er leveret tir-søn i
        # ugen før fakturadatoen). Tag søndagen umiddelbart før fakturadatoen og brug
        # dens ISO-uge. Ved override vinder override.
        from datetime import timedelta as _td
        d = date.fromisoformat(fak_dato)
        sidste_son = d - _td(days=d.weekday() + 1)   # mandag(0) → forrige søndag
        iso = sidste_son.isocalendar()
        if aar is None: aar = iso[0]
        if uge is None: uge = iso[1]

    varer_ex_fragt = round(sum(l["total_pris"] for l in varelinjer), 2)
    subtotal_kr = _tal(subtotal) if subtotal else round(varer_ex_fragt + fragt_kr, 2)

    return {
        "er_bakery":     ("organic bakery" in tekst.lower()),
        "fakturanr":     int(fakturanr) if fakturanr else None,
        "faktura_dato":  fak_dato,
        "kundenr":       int(kundenr) if kundenr else None,
        "ref_ordre":     ref_ordre,       # portal-ordrenummer, fx "1007"
        "forfald":       forfald,
        "uge":           uge,
        "aar":           aar,
        "linjer":        varelinjer,
        "fragt_kr":      round(fragt_kr, 2),
        "fragt_antal":   fragt_antal,
        "varer_ex_fragt": varer_ex_fragt,
        "subtotal_ex_moms": subtotal_kr,
        "moms_kr":       _tal(moms) if moms else None,
        "total_kr":      _tal(total) if total else None,
        "total_stk":     sum(l["total_antal"] for l in varelinjer),
    }


def parse_faktura_pdf(pdf_bytes: bytes, override_uge: Optional[int] = None,
                      override_aar: Optional[int] = None) -> Dict:
    """Åbner PDF'en (bytes) med pdfplumber, samler tekst fra alle sider og parser."""
    import io
    import pdfplumber
    tekst_dele = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for p in pdf.pages:
            t = p.extract_text() or ""
            tekst_dele.append(t)
    return parse_faktura_tekst("\n".join(tekst_dele), override_uge, override_aar)


if __name__ == "__main__":
    import json
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "Faktura_9.pdf"
    with open(path, "rb") as f:
        res = parse_faktura_pdf(f.read())
    print(json.dumps(res, ensure_ascii=False, indent=2))
