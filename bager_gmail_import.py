"""
Bager Gmail Import — henter automatisk bager-fakturaer fra Gmail og
importerer dem til Railway-dashboardet.

Finder emails fra rmk@organicmarket.dk med PDF-vedhæftning, parser
PDF'en og uploader til /api/bager/retur-opdater.

Kræver (én gang):
    pip install google-auth google-auth-oauthlib google-api-python-client pdfplumber requests

Første gang — opret Gmail OAuth:
    Se OPSAETNING.md eller kør:  python bager_gmail_import.py --setup

Brug:
    python bager_gmail_import.py              # importer nye fakturaer
    python bager_gmail_import.py --vis        # vis uden upload
    python bager_gmail_import.py --alle       # genhent alle (ikke kun nye)
    python bager_gmail_import.py --pdf sti.pdf  # parse enkelt PDF-fil
"""

from __future__ import annotations
import argparse
import base64
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Optional

# ── Konfiguration ─────────────────────────────────────────────────────────────
RAILWAY_URL    = "https://om-dashboard-production-0f3a.up.railway.app"
WEBHOOK_SECRET = "OM-Greve-2026-Hemlig"
GMAIL_SENDER   = "rmk@organicmarket.dk"
CREDENTIALS_FILE = Path(__file__).parent / "gmail_credentials.json"
TOKEN_FILE       = Path(__file__).parent / "gmail_token.json"
IMPORTERET_FILE  = Path(__file__).parent / "bager_importerede.json"

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
# ─────────────────────────────────────────────────────────────────────────────


# ── Gmail OAuth ───────────────────────────────────────────────────────────────

def _gmail_service():
    """Returnerer autentificeret Gmail API-service."""
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        print("[FEJL] Mangler pakker. Kør:")
        print("  pip install google-auth google-auth-oauthlib google-api-python-client")
        sys.exit(1)

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                print(f"[FEJL] Ingen credentials-fil fundet: {CREDENTIALS_FILE}")
                print()
                print("Opret Gmail OAuth credentials:")
                print("  1. Ga til https://console.cloud.google.com/")
                print("  2. Opret nyt projekt (fx 'OM Dashboard')")
                print("  3. Ga til 'APIs & Services' -> 'Enable APIs' -> aktiver 'Gmail API'")
                print("  4. Ga til 'Credentials' -> 'Create Credentials' -> 'OAuth 2.0 Client ID'")
                print("  5. Vaelg 'Desktop app', download JSON")
                print(f"  6. Gem som: {CREDENTIALS_FILE}")
                print("  7. Kor scriptet igen")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

    return build("gmail", "v1", credentials=creds)


def _hent_pdf_bytes(service, message_id: str, attachment_id: str) -> bytes:
    """Download PDF-vedhæftning fra Gmail."""
    att = service.users().messages().attachments().get(
        userId="me", messageId=message_id, id=attachment_id
    ).execute()
    data = att.get("data", "")
    return base64.urlsafe_b64decode(data + "==")


def _find_emails(service, kun_nye: bool = True) -> list[dict]:
    """Søg efter bageri-fakturaer i Gmail. Returnerer liste af dicts med email-info."""
    importerede = _load_importerede()
    query = f"from:{GMAIL_SENDER} has:attachment filename:pdf"
    result = service.users().messages().list(
        userId="me", q=query, maxResults=50
    ).execute()
    messages = result.get("messages", [])

    fakturaer = []
    for msg in messages:
        msg_id = msg["id"]
        if kun_nye and msg_id in importerede:
            continue

        full = service.users().messages().get(
            userId="me", id=msg_id, format="full"
        ).execute()

        headers = {h["name"]: h["value"] for h in full["payload"]["headers"]}
        subject  = headers.get("Subject", "")
        date_str = headers.get("Date", "")

        # Kun emails med "uge" i emnelinjen
        m_uge = re.search(r"uge\s*(\d+)", subject, re.IGNORECASE)
        if not m_uge:
            continue

        uge = int(m_uge.group(1))

        # År fra dato i emailen (fx "Thu, 15 May 2026")
        m_aar = re.search(r"(202\d)", date_str)
        aar = int(m_aar.group(1)) if m_aar else 2026

        # Find PDF-vedhæftning
        att_id = None
        att_name = ""
        for part in _iter_parts(full["payload"]):
            if part.get("mimeType") == "application/pdf":
                att_id = part["body"].get("attachmentId")
                att_name = part.get("filename", "faktura.pdf")
                break

        if not att_id:
            continue

        fakturaer.append({
            "msg_id":  msg_id,
            "uge":     uge,
            "aar":     aar,
            "subject": subject,
            "att_id":  att_id,
            "att_name": att_name,
        })

    return sorted(fakturaer, key=lambda x: (x["aar"], x["uge"]))


def _iter_parts(payload):
    """Gennemgå alle dele af en MIME-besked rekursivt."""
    yield payload
    for part in payload.get("parts", []):
        yield from _iter_parts(part)


# ── PDF-parsing ───────────────────────────────────────────────────────────────

def _parse_pdf(pdf_bytes: bytes, uge: int = 0, aar: int = 2026) -> Optional[dict]:
    """
    Ekstraher nøgletal fra bager-faktura PDF.
    Returnerer dict med felter til bager_regnskab, eller None ved fejl.
    """
    try:
        import pdfplumber
    except ImportError:
        print("[FEJL] pdfplumber ikke installeret. Kør: pip install pdfplumber")
        sys.exit(1)

    tekst = ""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    try:
        with pdfplumber.open(tmp_path) as pdf:
            for page in pdf.pages:
                tekst += (page.extract_text() or "") + "\n"
    finally:
        os.unlink(tmp_path)

    return _parse_tekst(tekst, uge, aar)


def _tal(s) -> float:
    """Parsér dansk tal (1.234,56 eller 1234.56)."""
    if s is None:
        return 0.0
    s = str(s).strip()
    s = re.sub(r"[^\d,.\-]", "", s)
    if not s or s == "-":
        return 0.0
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        parts = s.lstrip("-").split(".")
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            pass  # decimal
        else:
            s = s.replace(".", "")
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _find_tal(pattern: str, tekst: str, flags=re.IGNORECASE) -> float:
    """Find første tal efter regex-mønster."""
    m = re.search(r"(?:" + pattern + r")[:\s]*([0-9][0-9.,\s]*)", tekst, flags)
    if m and m.group(1) is not None:
        parts = m.group(1).strip().split()
        if parts:
            return _tal(parts[0])
    return 0.0


def _hent_sidst_tal_paa_linje(linje: str) -> float:
    """Finder det sidste tal på en linje (typisk beløbet i en fakturalinje)."""
    # Find alle tal på linjen (dansk format: 1.234,56 eller -1.234,56)
    tal = re.findall(r"-?[0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]+)?|-?[0-9]+(?:,[0-9]+)?", linje)
    if tal:
        return abs(_tal(tal[-1]))  # Returner absolut beløb (kreditter er negative i PDF)
    return 0.0


def _parse_tekst(tekst: str, uge: int, aar: int) -> dict:
    """
    Parser nøgletal fra bager-faktura PDF.

    Format: "Beskrivelse pct 1 -beloeb -beloeb" på separate linjer.
    Eks: "Retur wienerbrød u. kerner 13,5% 1 -641,52 -641,52"
    """
    # Uge fra PDF (hvis ikke allerede fundet fra emnelinjen)
    if not uge:
        m = re.search(r"uge\s*(\d+)", tekst, re.IGNORECASE)
        if m:
            uge = int(m.group(1))

    retur_wiener = 0.0
    retur_boller = 0.0
    tgtg = 0.0
    b_kvali = 0.0
    faktura = 0.0

    for linje in tekst.splitlines():
        l = linje.strip()
        l_lower = l.lower()

        # Retur wienerbrød — beløb på linjen
        if re.search(r"retur\s+wien", l_lower):
            retur_wiener = _hent_sidst_tal_paa_linje(l)

        # Retur boller — beløb på linjen
        elif re.search(r"retur\s+boller", l_lower):
            retur_boller = _hent_sidst_tal_paa_linje(l)

        # TGTG kreditering
        elif re.search(r"tgtg|too\s*good\s*to\s*go", l_lower):
            tgtg = _hent_sidst_tal_paa_linje(l)

        # B-kvalitetskreditering / kvalitets-kredit
        elif re.search(r"kvali|b-?kredit|kvalitets", l_lower):
            if not re.search(r"retur|wiener|boller|tgtg", l_lower):
                b_kvali = _hent_sidst_tal_paa_linje(l)

        # Levering iflg. specifikation — bruttobeløb (netto_kr = faktura − retur_ialt = Subtotal)
        elif re.search(r"levering", l_lower):
            faktura = _hent_sidst_tal_paa_linje(l)

        # Fallback: Subtotal hvis ingen leveringslinje
        elif not faktura and re.search(r"subtotal", l_lower):
            faktura = _hent_sidst_tal_paa_linje(l)

        # Fallback: Total DKK
        elif not faktura and re.search(r"total\s+dkk\s*:", l_lower):
            faktura = _hent_sidst_tal_paa_linje(l)

    # Retur i alt = sum af alle kreditposter
    retur_ialt = round(retur_wiener + retur_boller + tgtg + b_kvali, 2)

    return {
        "uge":          uge,
        "aar":          aar,
        "retur_wiener": round(retur_wiener, 2),
        "retur_boller": round(retur_boller, 2),
        "tgtg":         round(tgtg, 2),
        "b_kvali":      round(b_kvali, 2),
        "retur_ialt":   retur_ialt,
        "faktura":      round(faktura, 2),
        "_raa_tekst":   tekst,
    }


# ── Upload ────────────────────────────────────────────────────────────────────

def _upload(linjer: list[dict]) -> None:
    import requests  # type: ignore
    payload = [
        {k: v for k, v in l.items() if not k.startswith("_")}
        for l in linjer
    ]
    r = requests.post(
        f"{RAILWAY_URL}/api/bager/retur-opdater",
        json={"secret": WEBHOOK_SECRET, "linjer": payload},
        timeout=20,
    )
    r.raise_for_status()
    result = r.json()
    print(f"[OK] Uploadet {result.get('linjer', '?')} uger til Railway")


# ── Importerede (deduplication) ───────────────────────────────────────────────

def _load_importerede() -> set:
    if IMPORTERET_FILE.exists():
        try:
            return set(json.loads(IMPORTERET_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return set()


def _mark_importeret(msg_id: str) -> None:
    imp = _load_importerede()
    imp.add(msg_id)
    IMPORTERET_FILE.write_text(json.dumps(sorted(imp), indent=2), encoding="utf-8")


# ── Formattering ──────────────────────────────────────────────────────────────

def _vis_resultat(data: dict) -> None:
    print(f"\n  Uge {data['uge']}/{data['aar']}")
    print(f"  Retur wienerbrød : {data['retur_wiener']:>6} stk")
    print(f"  Retur boller     : {data['retur_boller']:>6} stk")
    print(f"  TGTG             : {data['tgtg']:>6} stk")
    print(f"  B-kvali kredit   : {data['b_kvali']:>10,.2f} kr")
    print(f"  Retur i alt      : {data['retur_ialt']:>10,.2f} kr")
    print(f"  Faktura          : {data['faktura']:>10,.2f} kr")


# ── Hoved-flow ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Bager Gmail → Railway")
    parser.add_argument("--vis",   action="store_true", help="Vis uden upload")
    parser.add_argument("--alle",  action="store_true", help="Hent alle (ikke kun nye)")
    parser.add_argument("--auto",  action="store_true", help="Upload automatisk uden bekraeftelse")
    parser.add_argument("--raatekst", action="store_true", help="Vis raa PDF-tekst")
    parser.add_argument("--pdf",   help="Parse enkelt lokal PDF-fil direkte")
    parser.add_argument("--uge",   type=int, help="Filtrer til specifik uge")
    args = parser.parse_args()

    # ── Direkte PDF-fil ───────────────────────────────────────────────────────
    if args.pdf:
        p = Path(args.pdf)
        if not p.exists():
            print(f"[FEJL] Fil ikke fundet: {p}")
            sys.exit(1)
        uge = args.uge or 0
        m = re.search(r"(\d+)", p.stem)
        if m and not uge:
            uge = int(m.group(1))
        data = _parse_pdf(p.read_bytes(), uge=uge, aar=2026)
        if args.raatekst:
            print("\n── Rå PDF-tekst ─────────────────────────────────────")
            print(data.get("_raa_tekst", ""))
            print("─────────────────────────────────────────────────────\n")
        _vis_resultat(data)
        if not args.vis:
            if not args.auto:
                svar = input("\nUpload til Railway? [J/n] ").strip().lower()
                if svar not in ("", "j", "ja", "y", "yes"):
                    print("Afbrudt.")
                    return
            _upload([data])
        return

    # ── Gmail-flow ────────────────────────────────────────────────────────────
    print("Forbinder til Gmail…")
    service = _gmail_service()

    fakturaer = _find_emails(service, kun_nye=not args.alle)
    if args.uge:
        fakturaer = [f for f in fakturaer if f["uge"] == args.uge]

    if not fakturaer:
        print("Ingen nye fakturaer fundet.")
        return

    print(f"\nFundet {len(fakturaer)} faktura(er):\n")
    parsed = []
    for f in fakturaer:
        print(f"  -> {f['subject']}  ({f['att_name']})")
        pdf_bytes = _hent_pdf_bytes(service, f["msg_id"], f["att_id"])
        data = _parse_pdf(pdf_bytes, uge=f["uge"], aar=f["aar"])
        data["_msg_id"] = f["msg_id"]

        if args.raatekst:
            print("\n── Rå PDF-tekst ──────────────────────────────────────")
            print(data.get("_raa_tekst", ""))
            print("──────────────────────────────────────────────────────\n")

        _vis_resultat(data)

        # Advar hvis faktura er 0 — sandsynligvis fejl i parsing
        if data["faktura"] == 0:
            print("  ⚠  Faktura = 0 — PDF-format ikke genkendt. Kør med --raatekst")

        parsed.append(data)

    print(f"\n{'-'*40}")
    total_fakt = sum(d["faktura"] for d in parsed)
    print(f"Total faktura: {total_fakt:>10,.2f} kr\n")

    if args.vis:
        print("(--vis tilstand — ingen upload)")
        return

    if not args.auto:
        svar = input("Upload til Railway? [J/n] ").strip().lower()
        if svar not in ("", "j", "ja", "y", "yes"):
            print("Afbrudt.")
            return

    _upload(parsed)

    # Markér som importeret
    for d in parsed:
        if "_msg_id" in d:
            _mark_importeret(d["_msg_id"])

    print("Importerede fakturaer gemt i bager_importerede.json")


if __name__ == "__main__":
    main()
