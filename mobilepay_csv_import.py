"""
MobilePay portal CSV-import — parser eksport fra portal.vippsmobilepay.com
og uploader daglig omsætning til Railway-dashboardet.

Understøtter to eksport-formater:
  - Afregningsrapport: Salgssted,MSN,Land,...,Bogføringsdato,Type,Beløb,...
  - Salgsoversigt:     Dato,Salgssted,Salg,...

Eksporter fra portalen:
  Gå til Salgssted-oversigten → vælg dato-periode → klik Eksportér CSV

Brug:
  python mobilepay_csv_import.py settlement_export.csv
  python mobilepay_csv_import.py settlement_export.csv --vis         # kun vis, upload ikke
  python mobilepay_csv_import.py settlement_export.csv --fra 2026-01-01

Kræver:  pip install requests  (+ openpyxl hvis Excel-fil)
"""

import argparse
import csv
import io
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# ── Konfiguration ─────────────────────────────────────────────────────────────
RAILWAY_URL    = "https://om-dashboard-production-0f3a.up.railway.app"
WEBHOOK_SECRET = "OM-Greve-2026-Hemlig"
# ─────────────────────────────────────────────────────────────────────────────


def _tal(s):
    # type: (str) -> float
    """Parsér tal — håndterer dansk format (1.234,56) og engelsk (-84.16)."""
    if s is None:
        return 0.0
    s = str(s).strip()
    s = re.sub(r'[^\d,.\-]', '', s)   # fjern 'kr.', mellemrum osv.
    if not s or s == '-':
        return 0.0
    # Hvis komma er til stede → dansk format: "1.234,56"
    if ',' in s:
        s = s.replace('.', '').replace(',', '.')
    else:
        # Ingen komma — afgør om punktum er decimal- eller tusind-separator
        parts = s.lstrip('-').split('.')
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            # "84.16" eller "8.5" → decimal — behold som den er
            pass
        else:
            # "1.234" (3 decimaler) → tusind-separator → fjern
            s = s.replace('.', '')
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _dato(s):
    # type: (str) -> object
    """Parsér dato 'DD.MM.YYYY', 'YYYY-MM-DD' eller 'YYYY-MM-DD HH:MM:SS' → 'YYYY-MM-DD'"""
    s = str(s).strip()
    # Trim tid-del hvis til stede: "2026-01-16 01:27:31" → "2026-01-16"
    s = s.split(' ')[0].split('T')[0]
    for fmt in ('%d.%m.%Y', '%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _find_kolonne(headers, kandidater):
    # type: (list, list) -> object
    """Find kolonneindeks ved at matche mod mulige kolonnenavne (case-insensitiv)."""
    hl = [h.lower().strip() for h in headers]
    for k in kandidater:
        kl = k.lower().strip()
        # Forsøg eksakt match først
        for i, h in enumerate(hl):
            if h == kl:
                return i
        # Derefter delvis match
        for i, h in enumerate(hl):
            if kl in h or h in kl:
                return i
    return None


def parse_csv(path):
    # type: (str) -> list
    """Parsér MobilePay portal CSV/Excel — returnerer [{dato, omsaetning_netto, gebyr}].

    For afregningsrapporter:
    - Bruger kun "Betaling gennemført" entries (udelukker gebyrer, planlagte udbetalinger osv.)
    - Nettobeløb = det beløb butikken modtager (efter gebyr)
    - Gebyr = Beløb - Nettobeløb
    """
    p = Path(path)

    # ── Excel (.xlsx) ─────────────────────────────────────────────────────────
    if p.suffix.lower() in ('.xlsx', '.xls'):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(str(p), data_only=True)
            ws = wb.active
            rows = [list(row) for row in ws.iter_rows(values_only=True)]
        except ImportError:
            raise RuntimeError("openpyxl ikke installeret. Kør: pip install openpyxl")
        headers = None
        data_rows = []
        for row in rows:
            if headers is None:
                if any(c is not None for c in row):
                    headers = [str(c) if c is not None else '' for c in row]
            else:
                data_rows.append([str(c) if c is not None else '' for c in row])
    else:
        # ── CSV ───────────────────────────────────────────────────────────────
        raw = p.read_bytes()
        text = None
        for enc in ('utf-8-sig', 'cp1252', 'latin-1'):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise RuntimeError("Kan ikke afkode fil — prøv at gemme som UTF-8")

        # Detektér separator
        sample = text[:2000]
        sep = ';' if sample.count(';') > sample.count(',') else ','
        reader = csv.reader(io.StringIO(text), delimiter=sep)
        rows_raw = list(reader)
        headers = rows_raw[0] if rows_raw else []
        data_rows = rows_raw[1:]

    if not headers:
        raise RuntimeError("Ingen kolonneoverskrifter fundet i filen.")

    # ── Kolonne-detektion ─────────────────────────────────────────────────────
    # Dato: 'Bogføringsdato' (afregningsrapport) eller 'Dato'/'Date'
    i_dato = _find_kolonne(headers, ['bogføringsdato', 'dato', 'date', 'dag', 'planlagt udbetalingsdato'])

    # Type-kolonne: filtrerer "Betaling gennemført"
    i_type = _find_kolonne(headers, ['type'])

    # Beløb (brutto, inkl. gebyr): primær beløb-kolonne
    i_belob = _find_kolonne(headers, ['beløb', 'amount'])

    # Nettobeløb (efter gebyr): beløb som butikken modtager
    i_netto = _find_kolonne(headers, ['nettobeløb'])

    if i_dato is None:
        raise RuntimeError(
            f"Kan ikke finde Dato-kolonne.\nFundne kolonner: {headers}"
        )
    if i_belob is None:
        raise RuntimeError(
            f"Kan ikke finde Beløb-kolonne.\nFundne kolonner: {headers}"
        )

    print(f"Parsér '{p.name}'  ({len(data_rows)} rækker)")
    print(f"Kolonner: Dato={headers[i_dato]!r}  Beløb={headers[i_belob]!r}", end='')
    if i_netto is not None:
        print(f"  Netto={headers[i_netto]!r}")
    else:
        print("  (ingen Nettobeløb-kolonne)")
    if i_type is not None:
        print(f"Type={headers[i_type]!r}")

    linjer = []
    for row in data_rows:
        if not any(c.strip() if isinstance(c, str) else c for c in row):
            continue

        # Hvis der er en Type-kolonne, filtrer til kun "Betaling gennemført"
        if i_type is not None:
            type_val = row[i_type].strip() if i_type < len(row) else ''
            if 'betaling gennemf' not in type_val.lower():
                continue

        try:
            dato = _dato(row[i_dato]) if i_dato < len(row) else None
            belob = _tal(row[i_belob]) if i_belob < len(row) else 0.0
            netto = _tal(row[i_netto]) if i_netto is not None and i_netto < len(row) else belob
        except Exception:
            continue

        if dato is None or belob == 0:
            continue

        # Afregningsrapport-beløb kan være negative (udbetaling fra MobilePay → konto)
        # → vi gemmer absolut-værdien
        belob = abs(belob)
        netto = abs(netto)

        # Gebyr = forskel mellem brutto og netto
        gebyr = round(belob - netto, 2)

        linjer.append({
            "dato":               dato,
            "omsaetning_netto": round(netto, 2),
            "gebyr":             max(0, gebyr),  # Sikr at gebyr aldrig er negativt
            "kilde":             "csv-portal",
        })

    # Aggregér hvis flere rækker per dato
    agg = {}
    for l in linjer:
        d = l["dato"]
        if d not in agg:
            agg[d] = {"netto": 0.0, "gebyr": 0.0}
        agg[d]["netto"] += l["omsaetning_netto"]
        agg[d]["gebyr"] += l["gebyr"]

    result = [
        {
            "dato":               d,
            "omsaetning_netto": round(v["netto"], 2),
            "gebyr":             round(v["gebyr"], 2),
            "kilde":             "csv-portal"
        }
        for d, v in sorted(agg.items())
    ]
    return result


def upload_til_railway(linjer):
    # type: (list) -> None
    import requests
    r = requests.post(
        f"{RAILWAY_URL}/api/mobilepay/dagssalg",
        json={"secret": WEBHOOK_SECRET, "linjer": linjer},
        timeout=20,
    )
    r.raise_for_status()
    result = r.json()
    print(f"[OK] Uploadet {result.get('linjer', '?')} dage til Railway")


def main():
    parser = argparse.ArgumentParser(description="MobilePay portal CSV → Railway")
    parser.add_argument("fil", help="CSV- eller Excel-fil eksporteret fra portalen")
    parser.add_argument("--vis",  action="store_true", help="Vis data uden at uploade")
    parser.add_argument("--fra",  help="Medtag kun datoer fra og med (YYYY-MM-DD)")
    parser.add_argument("--til",  help="Medtag kun datoer til og med (YYYY-MM-DD)")
    args = parser.parse_args()

    if not Path(args.fil).exists():
        print(f"[FEJL] Fil ikke fundet: {args.fil}")
        sys.exit(1)

    try:
        linjer = parse_csv(args.fil)
    except RuntimeError as e:
        print(f"[FEJL] {e}")
        sys.exit(1)

    # Dato-filter
    if args.fra:
        linjer = [l for l in linjer if l["dato"] >= args.fra]
    if args.til:
        linjer = [l for l in linjer if l["dato"] <= args.til]

    if not linjer:
        print("Ingen data at importere.")
        return

    print(f"\n{len(linjer)} dage klar til import:\n")
    total_netto = 0.0
    total_gebyr = 0.0
    for l in linjer:
        netto = l["omsaetning_netto"]
        gebyr = l["gebyr"]
        brutto = netto + gebyr
        print(f"  {l['dato']}  Netto: {netto:>10,.2f}  Gebyr: {gebyr:>8,.2f}  (Brutto: {brutto:>10,.2f})")
        total_netto += netto
        total_gebyr += gebyr
    print(f"  {'-'*70}")
    print(f"  Total       Netto: {total_netto:>10,.2f}  Gebyr: {total_gebyr:>8,.2f}  (Brutto: {total_netto + total_gebyr:>10,.2f})\n")

    if args.vis:
        print("(--vis tilstand — ingen upload)")
        return

    upload_til_railway(linjer)


if __name__ == "__main__":
    main()
