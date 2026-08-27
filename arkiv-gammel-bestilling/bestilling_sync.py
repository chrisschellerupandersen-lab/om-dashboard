"""
Lokalt sync-script: læser ugebestillinger fra Google Drive og uploader til Railway.

Kør:  python bestilling_sync.py

Første gang: sæt RAILWAY_URL nedenfor til din Railway-adresse.
Scriptet gemmer en tilstandsfil (bestilling_sync_state.json) så allerede
uploadede filer springer over næste gang, medmindre de er ændret.
"""
import json
import os
import sys
import requests
from pathlib import Path
from bestilling_parser import parse_bestilling_xlsx

# ── KONFIGURATION ─────────────────────────────────────────────────────────────

MAPPE = Path(r"G:\Mit drev\Organic Marked\Organic Market\Indkøb\Uge bestilling")

# Find fra miljøvariabel eller rediger direkte her
RAILWAY_URL    = os.environ.get("RAILWAY_URL", "https://om-dashboard-production-0f3a.up.railway.app")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "OM-Greve-2026-Hemlig")

STATE_FIL = Path(__file__).parent / "bestilling_sync_state.json"

# ── HJÆLPEFUNKTIONER ──────────────────────────────────────────────────────────

def indlæs_state() -> dict:
    if STATE_FIL.exists():
        return json.loads(STATE_FIL.read_text(encoding="utf-8"))
    return {}

def gem_state(state: dict):
    STATE_FIL.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

# ── SYNC ──────────────────────────────────────────────────────────────────────

def synk(tvang: bool = False):
    if "INDSÆT" in RAILWAY_URL:
        print("FEJL: Sæt din Railway-URL i RAILWAY_URL øverst i scriptet.")
        sys.exit(1)

    state = indlæs_state()

    # Sorter efter ændringstidspunkt så tillæg/opdateringer uploades sidst
    filer = sorted(
        [f for f in MAPPE.glob("*.xlsx") if not f.name.startswith("~$")],
        key=lambda f: f.stat().st_mtime
    )

    print(f"Fundet {len(filer)} filer i {MAPPE}\n")
    ok = fejl = sprunget = 0

    for fil in filer:
        mtime = str(fil.stat().st_mtime)

        if not tvang and state.get(fil.name) == mtime:
            print(f"  – {fil.name}  (uændret, springer over)")
            sprunget += 1
            continue

        print(f"  ^ {fil.name}", end="  ", flush=True)
        try:
            data = parse_bestilling_xlsx(str(fil))
        except Exception as e:
            print(f"PARSE-FEJL: {e}")
            fejl += 1
            continue

        if data["uge"] is None:
            print("Kunne ikke finde ugenummer — springer over")
            fejl += 1
            continue

        if not data["linjer"]:
            print("Ingen bestillingslinjer — springer over")
            sprunget += 1
            continue

        payload = {"secret": WEBHOOK_SECRET, **data}
        try:
            r = requests.post(
                f"{RAILWAY_URL}/api/bestilling/opdater",
                json=payload,
                timeout=30,
            )
            r.raise_for_status()
            res = r.json()
            print(f"OK  uge={data['uge']} {data['aar']}  {len(data['linjer'])} linjer")
            state[fil.name] = mtime
            gem_state(state)
            ok += 1
        except requests.HTTPError as e:
            print(f"HTTP-FEJL {e.response.status_code}: {e.response.text[:120]}")
            fejl += 1
        except Exception as e:
            print(f"FEJL: {e}")
            fejl += 1

    print(f"\nFærdig — {ok} uploadet, {sprunget} sprunget over, {fejl} fejl")

if __name__ == "__main__":
    tvang = "--force" in sys.argv or "-f" in sys.argv
    if tvang:
        print("--force: uploader alle filer uanset ændringer\n")
    synk(tvang=tvang)
