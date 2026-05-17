"""
TGTG daglig sync — henter salgsdata fra Too Good To Go Store Portal
og uploader til Railway-dashboardet.

Opsætning (første gang):
  1. Åbn store.toogoodtogo.com/stores/206880475109994944/sales/206880476083086176
  2. Åbn DevTools → Console → indsæt indholdet af tgtg_export_session.js
  3. Kør: python tgtg_sync.py --from-session tgtg_session.json

Daglig kørsel (ingen browser nødvendig):
  python tgtg_sync.py              # i dag + i går
  python tgtg_sync.py --dato 2026-05-16  # specifik dato
  python tgtg_sync.py --dage 7           # seneste 7 dage
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
SESSION_FILE   = Path(__file__).parent / "tgtg_session.json"

POSE_TYPER = [
    {"item_id": "206880476083086176", "navn": "Lykkepose ( Økologisk)",  "kreditpris": 49},
    {"item_id": "206881838829236480", "navn": "Brødposen ( Økologisk)",  "kreditpris": 45},
    {"item_id": "206882511213524800", "navn": "Wienerbrødsposen",        "kreditpris": 49},
    {"item_id": "210383102918979712", "navn": "4x Fatelavnsboller",      "kreditpris": 40},
    {"item_id": "210383866617850400", "navn": "Kagepose",                "kreditpris": 45},
]
# ─────────────────────────────────────────────────────────────────────────────


def load_session() -> tuple[requests.Session, str]:
    """Indlæs cookies + CSRF fra SESSION_FILE. Kast fejl med vejledning hvis mangler."""
    if not SESSION_FILE.exists():
        print(f"""
FEJL: {SESSION_FILE} ikke fundet.

Gør følgende:
  1. Åbn: store.toogoodtogo.com/stores/{STORE_ID}/sales/{POSE_TYPER[0]['item_id']}
  2. Åbn DevTools (F12) → Console
  3. Kør dette script for at gemme session:

     python tgtg_sync.py --export-session

  Eller kopier/indsæt indholdet af tgtg_export_session.js manuelt.
""")
        raise SystemExit(1)

    data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    sess = requests.Session()
    for name, value in data.get("cookies", {}).items():
        sess.cookies.set(name, value, domain="store.toogoodtogo.com")

    # Refresh CSRF ved at hente en frisk token via auth-session endpoint
    csrf = refresh_csrf(sess, data.get("csrf", ""))
    return sess, csrf


def refresh_csrf(sess: requests.Session, fallback_csrf: str = "") -> str:
    """
    Forsøg at hente et fresh CSRF-token via auth/v3/session.
    Fald tilbage på gestet token hvis det fejler.
    """
    try:
        r = sess.post(
            f"{TGTG_BASE}/web/auth/v3/session",
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "X-Requested-With": "XMLHttpRequest"},
            json={}, timeout=10,
        )
        if r.status_code == 200:
            # Token kan ligge i response-header eller i Set-Cookie
            token = r.headers.get("X-CSRF-Token") or r.headers.get("x-csrf-token")
            if token:
                print(f"[OK] Fresh CSRF fra auth/v3/session")
                return token
    except Exception as e:
        print(f"[WARN] auth/v3/session fejlede: {e}")

    if fallback_csrf:
        print(f"[INFO] Bruger gemt CSRF-token (kan være udløbet)")
        return fallback_csrf

    raise RuntimeError("Ingen CSRF-token tilgængelig. Kør --export-session igen.")


def fetch_sales(session: requests.Session, csrf: str, item_id: str, dato: str) -> int:
    """Hent antal solgte poser for én item/dato."""
    url = f"{TGTG_BASE}/web/mystore/item/v4/{item_id}/sales"
    headers = {
        "Content-Type":     "application/json",
        "Accept":           "application/json",
        "X-CSRF-Token":     csrf,
        "X-Requested-With": "XMLHttpRequest",
    }
    r = session.post(url, headers=headers,
                     json={"paging": {"size": 50, "page": 0}, "period": "DAY", "startDate": dato},
                     timeout=15)
    if r.status_code == 200:
        return r.json().get("paging", {}).get("totalElements", 0)
    print(f"  [WARN] {item_id}/{dato}: HTTP {r.status_code} — {r.text[:80]}")
    return 0


def upload_to_railway(linjer: list):
    r = requests.post(f"{RAILWAY_URL}/api/tgtg/poser", json={
        "secret": WEBHOOK_SECRET,
        "poser":  [{"item_id": p["item_id"], "navn": p["navn"], "kreditpris": p["kreditpris"]}
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
    sess, csrf = load_session()
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

    if linjer:
        upload_to_railway(linjer)
    else:
        print("Ingen salg fundet for de valgte datoer.")


def export_session_js():
    """Print det JavaScript der skal køres i browseren for at gemme session."""
    js = f"""
// Kør dette i DevTools Console på store.toogoodtogo.com
// Det gemmer cookies + CSRF til en fil som tgtg_sync.py kan bruge.
(async () => {{
  // Fang CSRF via en API-kald
  const orig = window.XMLHttpRequest;
  let csrf = null;
  window.XMLHttpRequest = function() {{
    const x = new orig();
    const oh = x.setRequestHeader.bind(x);
    x.setRequestHeader = (n,v) => {{ if (n && n.toLowerCase().includes('csrf')) csrf = v; return oh(n,v); }};
    return x;
  }};
  // Trigger en API-kald ved at navigere
  await new Promise(r => setTimeout(r, 2000));
  window.XMLHttpRequest = orig;

  const session = {{
    csrf: csrf || window._xsrf?.value || '',
    cookies: Object.fromEntries(
      document.cookie.split(';')
        .map(c => c.trim().split('='))
        .filter(c => c.length === 2)
        .map(([k,v]) => [k.trim(), v.trim()])
    ),
    saved: new Date().toISOString()
  }};

  const blob = new Blob([JSON.stringify(session, null, 2)], {{type: 'application/json'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'tgtg_session.json';
  a.click();
  console.log('Session gemt! Flyt tgtg_session.json til dashboard-mappen.');
}})();
"""
    print(js)
    print(f"\nEfter download: flyt tgtg_session.json til:")
    print(f"  {SESSION_FILE}")


def main():
    parser = argparse.ArgumentParser(description="TGTG → Railway sync")
    parser.add_argument("--dato",           help="Specifik dato (YYYY-MM-DD)")
    parser.add_argument("--dage",    type=int, default=2, help="Antal dage bagud (default: 2)")
    parser.add_argument("--export-session", action="store_true",
                        help="Print JavaScript til browser-console for at gemme session")
    args = parser.parse_args()

    if args.export_session:
        export_session_js()
        return

    if args.dato:
        datoer = [args.dato]
    else:
        today = date.today()
        datoer = [(today - timedelta(days=i)).isoformat() for i in range(args.dage - 1, -1, -1)]

    print(f"Synkroniserer {len(datoer)} dato(er): {datoer}")
    sync_dates(datoer)


if __name__ == "__main__":
    main()
