"""
Henter dagligt TGTG-salg og uploader til Railway.
Kør: python tgtg_sync.py
Kræver tgtg_tokens.json (genereres af tgtg_auth.py første gang).
"""
import json
import requests
from datetime import date, timedelta
from pathlib import Path
from tgtg import TgtgClient

TOKEN_FILE     = Path("tgtg_tokens.json")
RAILWAY_URL    = "https://om-dashboard-production-0f3a.up.railway.app"
WEBHOOK_SECRET = "OM-Greve-2026-Hemlig"

# Pose-definitioner: TGTG-navn (del af) → kreditpris per stk
# item_id udfyldes automatisk efter første auth
POSER = [
    {"navn": "Brødpose",        "kreditpris": 41.22},
    {"navn": "Lykkepost",       "kreditpris": 67.75},
    {"navn": "Wienerbrødspost", "kreditpris": 60.12},
    {"navn": "Kagepose",        "kreditpris": 50.03},
]


def _get_client() -> TgtgClient:
    if not TOKEN_FILE.exists():
        raise FileNotFoundError(
            f"{TOKEN_FILE} ikke fundet — kør tgtg_auth.py først"
        )
    creds = json.loads(TOKEN_FILE.read_text())
    return TgtgClient(
        access_token            = creds["access_token"],
        refresh_token           = creds["refresh_token"],
        user_id                 = creds.get("user_id",""),
        cookie                  = creds.get("cookie",""),
        access_token_lifetime   = creds.get("access_token_lifetime", 14400),
    )


def _gem_tokens(client: TgtgClient):
    """Gem opdaterede tokens efter refresh."""
    ny = client.get_credentials()
    TOKEN_FILE.write_text(json.dumps(ny, indent=2))


def synk_poser(client: TgtgClient):
    """Upload pose-definitioner til Railway (matcher på navn)."""
    items = client.get_items()
    # Map TGTG item_id til vores pose-navne via delvis navnematch
    for p in POSER:
        for it in items:
            item_navn = it.get("item", {}).get("name", "")
            if any(ord.lower() in item_navn.lower() for ord in p["navn"].split()):
                p["item_id"] = str(it.get("item", {}).get("item_id", ""))
                break
        else:
            p["item_id"] = p.get("item_id", "")

    r = requests.post(f"{RAILWAY_URL}/api/tgtg/poser",
                      json={"secret": WEBHOOK_SECRET, "poser": POSER}, timeout=20)
    r.raise_for_status()
    print(f"Pose-definitioner uploadet: {len(POSER)} typer")
    return {p["navn"]: p for p in POSER}


def synk_ordrer(client: TgtgClient, pose_map: dict, dage_tilbage: int = 7):
    """Hent ordrer og upload dagssalg."""
    try:
        ordrer = client.get_orders(page_size=100)
    except Exception as e:
        print(f"Kunne ikke hente ordrer: {e}")
        return

    # Grupper per dato + pose
    fra_dato = date.today() - timedelta(days=dage_tilbage)
    salg: dict = {}  # (dato, pose_navn) → antal

    for ordre in ordrer:
        try:
            # Ordredato (afhentningsdato) → vi trækker 1 dag fra fordi poserne
            # altid er lavet dagen FØR salget (gårsdagens produktion)
            pickup = ordre.get("pickup_date") or ordre.get("order_date") or ""
            if isinstance(pickup, dict):
                pickup = pickup.get("date", "")[:10]
            elif isinstance(pickup, str):
                pickup = pickup[:10]
            if not pickup or pickup < str(fra_dato):
                continue
            # Skift til produktionsdato: salgsdag - 1
            produktions_dato = (date.fromisoformat(pickup) - timedelta(days=1)).isoformat()
            pickup = produktions_dato

            # Kun gennemførte ordrer
            status = ordre.get("state", "")
            if status not in ("PICKED_UP", "SUCCESS", "RATED"):
                continue

            item_id   = str(ordre.get("item_id") or ordre.get("item", {}).get("item_id", ""))
            pose_navn = ""
            for navn, p in pose_map.items():
                if p.get("item_id") == item_id:
                    pose_navn = navn
                    break

            if not pose_navn:
                # Prøv via item-navn
                item_navn = ordre.get("item", {}).get("name", "")
                for navn in pose_map:
                    if any(o.lower() in item_navn.lower() for o in navn.split()):
                        pose_navn = navn
                        break

            if not pose_navn:
                pose_navn = ordre.get("item", {}).get("name", "Ukendt")

            key = (pickup, pose_navn)
            salg[key] = salg.get(key, 0) + 1
        except Exception:
            continue

    if not salg:
        print("Ingen nye ordrer at uploade.")
        return

    linjer = [
        {"dato": dato, "pose_navn": pose_navn, "antal": antal,
         "item_id": pose_map.get(pose_navn, {}).get("item_id", "")}
        for (dato, pose_navn), antal in sorted(salg.items())
    ]

    r = requests.post(f"{RAILWAY_URL}/api/tgtg/dagssalg",
                      json={"secret": WEBHOOK_SECRET, "linjer": linjer}, timeout=20)
    r.raise_for_status()
    res = r.json()
    print(f"TGTG dagssalg uploadet: {res.get('linjer')} linjer")
    for (dato, pose_navn), antal in sorted(salg.items()):
        kr = pose_map.get(pose_navn, {}).get("kreditpris", 0) * antal
        print(f"  {dato}  {pose_navn:<20} {antal} stk  →  {kr:.2f} kr")


def main():
    client = _get_client()
    print("Forbundet til TGTG\n")
    pose_map = synk_poser(client)
    synk_ordrer(client, pose_map)
    _gem_tokens(client)
    print("\nFærdig.")


if __name__ == "__main__":
    main()
