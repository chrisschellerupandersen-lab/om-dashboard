"""
TGTG daglig sync — henter salgsdata fra Too Good To Go Store Portal
og uploader til Railway-dashboardet.

Første gang (opret dedikeret TGTG-profil og log ind):
  python tgtg_sync.py --setup

Daglig kørsel:
  python tgtg_sync.py              # i dag + i går
  python tgtg_sync.py --dato 2026-05-16  # specifik dato
  python tgtg_sync.py --dage 7           # seneste 7 dage

Kræver:  pip install playwright requests
"""

import argparse
import json
import os
import time
import requests
from datetime import date, timedelta
from pathlib import Path

# ── Konfiguration ────────────────────────────────────────────────────────────
RAILWAY_URL    = "https://om-dashboard-production-0f3a.up.railway.app"
WEBHOOK_SECRET = "OM-Greve-2026-Hemlig"
STORE_ID       = "206880475109994944"
TGTG_BASE      = "https://store.toogoodtogo.com"

# Dedikeret profil-mappe til TGTG-automation (adskilt fra din normale Chrome)
PROFILE_DIR    = Path(__file__).parent / ".tgtg_chrome_profile"

POSE_TYPER = [
    # kostpris_pose      = faktisk kostpris for indholdet i posen
    # enheder_per_pose   = antal bagværksstykker i posen (bruges til spildberegning i stk)
    #
    # Lykkepose:         1 brød (24 kr) + 3 boller (6,40 kr) + 2 wienerbrød (12 kr) = 67,20 kr  → 6 stk
    # Brødposen:         2 brød (24 kr) + 3 boller (6,40 kr)                         = 67,20 kr  → 5 stk
    # Wienerbrødsposen:  6 wienerbrød (12 kr)                                        = 72,00 kr  → 6 stk
    # Kagepose:          6 kager (15,30 kr)                                          = 91,80 kr  → 6 stk
    # 4x Fatelavnsboller:4 boller                                                    = —         → 4 stk
    {"item_id": "206880476083086176", "navn": "Lykkepose ( Økologisk)",  "kreditpris": 49, "kostpris_pose": 67.20, "enheder_per_pose": 6},
    {"item_id": "206881838829236480", "navn": "Brødposen ( Økologisk)",  "kreditpris": 45, "kostpris_pose": 67.20, "enheder_per_pose": 5},
    {"item_id": "206882511213524800", "navn": "Wienerbrødsposen",        "kreditpris": 49, "kostpris_pose": 72.00, "enheder_per_pose": 6},
    {"item_id": "210383102918979712", "navn": "4x Fatelavnsboller",      "kreditpris": 40, "kostpris_pose":  0.00, "enheder_per_pose": 4},
    {"item_id": "210383866617850400", "navn": "Kagepose",                "kreditpris": 45, "kostpris_pose": 91.80, "enheder_per_pose": 6},
]
# ─────────────────────────────────────────────────────────────────────────────


def open_browser(headless: bool = False):
    """Åbn browser med dedikeret TGTG-profil (ikke din normale Chrome)."""
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    p = sync_playwright().start()
    browser = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
    )
    return p, browser


def setup_profile():
    """
    Åbn browser (synligt) og vent på at brugeren logger ind.
    Sessionen gemmes i PROFILE_DIR til fremtidige kørsler.
    """
    print(f"\nÅbner browser med TGTG-profil i: {PROFILE_DIR}")
    print("1. Log ind på store.toogoodtogo.com med greve@organicmarket.dk")
    print("2. Naviger til Økonomi-siden")
    print("3. Luk browser-vinduet når du er logget ind\n")

    p, browser = open_browser(headless=False)
    page = browser.new_page()
    page.goto(f"{TGTG_BASE}/stores/{STORE_ID}", wait_until="domcontentloaded")

    print("Browser åben — log ind og luk vinduet når du er klar...")
    try:
        # Vent indtil browser lukkes manuelt
        page.wait_for_event("close", timeout=300_000)
    except Exception:
        pass

    browser.close()
    p.stop()
    print(f"[OK] Profil gemt i {PROFILE_DIR}. Kør nu: python tgtg_sync.py")


def get_csrf_token(page) -> str:
    """Navigér til salgs-siden og fang CSRF-token fra XHR-requests."""
    csrf_holder = {}

    def on_request(request):
        token = (request.headers.get("x-csrf-token")
                 or request.headers.get("X-CSRF-Token"))
        if token and not csrf_holder.get("csrf"):
            csrf_holder["csrf"] = token

    page.on("request", on_request)

    sales_url = f"{TGTG_BASE}/stores/{STORE_ID}/sales/{POSE_TYPER[0]['item_id']}"
    page.goto(sales_url, wait_until="networkidle", timeout=30_000)

    # Klik på datofelt for at trigge orders-kald og fange CSRF
    try:
        page.locator("input[type='text']").first.click()
        page.wait_for_timeout(1500)
    except Exception:
        pass

    csrf = csrf_holder.get("csrf")
    if not csrf:
        raise RuntimeError(
            "Ingen CSRF-token fundet.\n"
            f"Er du logget ind? Kør: python tgtg_sync.py --setup"
        )
    return csrf


def fetch_sales(session: requests.Session, csrf: str, item_id: str, dato: str) -> int:
    """Hent antal solgte poser for én item/dato (max 50 pr. side)."""
    r = session.post(
        f"{TGTG_BASE}/web/mystore/item/v4/{item_id}/sales",
        headers={
            "Content-Type":     "application/json",
            "Accept":           "application/json",
            "X-CSRF-Token":     csrf,
            "X-Requested-With": "XMLHttpRequest",
        },
        json={"paging": {"size": 50, "page": 0}, "period": "DAY", "startDate": dato},
        timeout=15,
    )
    if r.status_code == 200:
        return r.json().get("paging", {}).get("totalElements", 0)
    print(f"  [WARN] {item_id}/{dato}: HTTP {r.status_code} — {r.text[:80]}")
    return 0


def upload_to_railway(linjer: list):
    """Upload pose-definitioner og dagssalg til Railway."""
    r = requests.post(f"{RAILWAY_URL}/api/tgtg/poser", json={
        "secret": WEBHOOK_SECRET,
        "poser":  [{"item_id": p["item_id"], "navn": p["navn"],
                    "kreditpris": p["kreditpris"], "kostpris_pose": p.get("kostpris_pose", 0),
                    "enheder_per_pose": p.get("enheder_per_pose", 1)}
                   for p in POSE_TYPER],
    }, timeout=20)
    r.raise_for_status()
    print("[OK] Pose-typer registreret")

    r = requests.post(f"{RAILWAY_URL}/api/tgtg/dagssalg", json={
        "secret": WEBHOOK_SECRET,
        "linjer": linjer,
    }, timeout=30)
    r.raise_for_status()
    result = r.json()
    print(f"[OK] Uploadet {result.get('linjer', '?')} linjer til Railway")


def sync_dates(datoer: list):
    """Hent TGTG-salg for de givne datoer og upload til Railway."""
    p, browser = open_browser(headless=True)
    page = browser.new_page()

    try:
        csrf = get_csrf_token(page)
        print(f"[OK] CSRF fanget")

        # Opret requests-session med cookies fra Playwright-browseren
        sess = requests.Session()
        for cookie in browser.cookies():
            if "toogoodtogo" in cookie.get("domain", ""):
                sess.cookies.set(
                    cookie["name"], cookie["value"],
                    domain=cookie.get("domain", "").lstrip(".")
                )

        linjer = []
        for dato in datoer:
            for pose in POSE_TYPER:
                antal = fetch_sales(sess, csrf, pose["item_id"], dato)
                if antal > 0:
                    linjer.append({
                        "dato":      dato,
                        "pose_navn": pose["navn"],
                        "antal":     antal,
                        "item_id":   pose["item_id"],
                    })
                    print(f"  {dato}  {pose['navn']:<30} × {antal}")
                time.sleep(0.3)

    finally:
        browser.close()
        p.stop()

    if linjer:
        upload_to_railway(linjer)
    else:
        print("Ingen salg fundet for de valgte datoer.")

    return linjer


def main():
    parser = argparse.ArgumentParser(description="TGTG → Railway sync")
    parser.add_argument("--setup", action="store_true",
                        help="Første gang: åbn browser og log ind på TGTG")
    parser.add_argument("--dato", help="Specifik dato (YYYY-MM-DD)")
    parser.add_argument("--dage", type=int, default=2,
                        help="Antal dage bagud (default: 2 = i dag + i går)")
    args = parser.parse_args()

    if args.setup:
        setup_profile()
        return

    if not PROFILE_DIR.exists():
        print("Profil-mappe ikke fundet. Kør først: python tgtg_sync.py --setup")
        raise SystemExit(1)

    if args.dato:
        datoer = [args.dato]
    else:
        today = date.today()
        datoer = [(today - timedelta(days=i)).isoformat() for i in range(args.dage - 1, -1, -1)]

    print(f"Synkroniserer {len(datoer)} dato(er): {datoer}")
    sync_dates(datoer)


if __name__ == "__main__":
    main()
