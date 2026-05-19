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
import requests
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
    """Parsér MobilePay portal CSV/Excel — returnerer [{dato, omsaetning_inkl}]."""
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

    # Beløb: 'Beløb' (afregningsrapport) eller 'Salg'/'Omsætning'
    # Vigtigt: 'beløb' SKAL stå før 'salg' så 'Salgssted' ikke matches
    i_salg = _find_kolonne(headers, ['beløb', 'nettobeløb', 'omsætning', 'amount', 'salg', 'sale'])

    if i_dato is None or i_salg is None:
        raise RuntimeError(
            f"Kan ikke finde Dato/Beløb-kolonner.\nFundne kolonner: {headers}"
        )

    print(f"Parsér '{p.name}'  ({len(data_rows)} rækker)")
    print(f"Kolonner: Dato={headers[i_dato]!r}  Beløb={headers[i_salg]!r}")

    linjer = []
    for row in data_rows:
        if not any(c.strip() if isinstance(c, str) else c for c in row):
            continue
        try:
            dato = _dato(row[i_dato]) if i_dato < len(row) else None
            salg = _tal(row[i_salg]) if i_salg < len(row) else 0.0
        except Exception:
            continue
        if dato is None or salg == 0:
            continue
        # Afregningsrapport-beløb er negative (udbetaling fra MobilePay → konto)
        # → vi gemmer absolut-værdien som omsætning
        salg = abs(salg)
        linjer.append({
            "dato":            dato,
            "omsaetning_inkl": round(salg, 2),
            "kilde":           "csv-portal",
        })

    # Aggregér hvis flere rækker per dato
    agg = {}
    for l in linjer:
        d = l["dato"]
        agg[d] = agg.get(d, 0.0) + l["omsaetning_inkl"]

    result = [{"dato": d, "omsaetning_inkl": round(v, 2), "kilde": "csv-portal"}
              for d, v in sorted(agg.items())]
    return result


def upload_til_railway(linjer):
    # type: (list) -> None
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
    total = 0.0
    for l in linjer:
        print(f"  {l['dato']}  {l['omsaetning_inkl']:>10,.2f} kr")
        total += l["omsaetning_inkl"]
    print(f"  {'-'*30}")
    print(f"  Total        {total:>10,.2f} kr\n")

    if args.vis:
        print("(--vis tilstand — ingen upload)")
        return

    upload_til_railway(linjer)


if __name__ == "__main__":
    main()
