import json
from scripts.mirakl_client import MiraklClient

for name in ("KINGFISHER","TESCO","THERANGE"):
    try:
        c = MiraklClient(name)
        # Mirakl OF01: GET /offers  -> {offers:[...], total_count}
        r = c.get("/offers", params={"max": 100})
        offers = r.get("offers", [])
        tc = r.get("total_count")
        print(f"[{name}] total_count={tc} first_page={len(offers)}")
        if offers:
            o = offers[0]
            print("   sample keys:", [k for k in o.keys()][:20])
            print("   sample:", {k:o.get(k) for k in ("shop_sku","product_sku","offer_id","product_title","ean","state_code","active") if k in o})
    except Exception as e:
        print(f"[{name}] ERROR: {str(e)[:160]}")
