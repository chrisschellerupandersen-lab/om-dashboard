"""
Første-gangs TGTG login via magic link.
Kør: python tgtg_auth.py
Tjek din email (også spam-mappe!) og klik bekræftelseslinket.
Scriptet venter automatisk — du behøver ikke trykke noget.
"""
import json
from tgtg import TgtgClient

EMAIL      = "greve@organicmarket.dk"
TOKEN_FILE = "tgtg_tokens.json"


def main():
    print(f"Sender magic link til {EMAIL} ...")
    print("Tjek din indbakke OG spam-mappe — klik linket i emailen.")
    print("Scriptet poller automatisk og fortsætter når du har klikket.\n")

    client = TgtgClient(email=EMAIL)
    creds  = client.get_credentials()

    with open(TOKEN_FILE, "w") as f:
        json.dump(creds, f, indent=2)
    print(f"\nOK — tokens gemt i {TOKEN_FILE}\n")

    items = client.get_items()
    print(f"Fandt {len(items)} TGTG-pose(r):\n")
    for it in items:
        item  = it.get("item", {})
        store = it.get("store", {})
        price = item.get("price_including_taxes", {})
        kr    = price.get("minor_units", 0) / (10 ** price.get("decimals", 2))
        print(f"  ID:    {item.get('item_id')}")
        print(f"  Navn:  {item.get('name', '—')}")
        print(f"  Butik: {store.get('store_name', '—')}")
        print(f"  Pris:  {kr:.2f} {price.get('code','DKK')}")
        print()


if __name__ == "__main__":
    main()
