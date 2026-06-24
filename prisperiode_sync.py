"""
Uploader dato-styrede kostpriser (vare_pris_periode) til Railway.
Læser en JSON-fil med {linjer:[{varenavn, pris_ex_moms, gyldig_fra}]}.

Kør:  python prisperiode_sync.py [fil.json]
Standard-fil: bagerpriser_uge28.json
"""
import sys
import json
import requests
from pathlib import Path

RAILWAY_URL    = "https://om-dashboard-production-0f3a.up.railway.app"
WEBHOOK_SECRET = "OM-Greve-2026-Hemlig"


def synk(fil: str = "bagerpriser_uge28.json"):
    data = json.loads(Path(fil).read_text(encoding="utf-8"))
    linjer = data.get("linjer", [])
    if not linjer:
        print(f"Ingen linjer i {fil}")
        return
    gfra = {l.get("gyldig_fra") for l in linjer}
    print(f"Uploader {len(linjer)} dato-styrede priser fra {fil} (gyldig_fra: {', '.join(sorted(gfra))})")
    r = requests.post(
        f"{RAILWAY_URL}/api/prisperiode/bulk",
        json={"secret": WEBHOOK_SECRET, "linjer": linjer},
        timeout=30,
    )
    r.raise_for_status()
    print(f"OK — {r.json().get('linjer')} priser uploadet")


if __name__ == "__main__":
    synk(sys.argv[1] if len(sys.argv) > 1 else "bagerpriser_uge28.json")
