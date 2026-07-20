from scripts.ebay_client import EBayClient
c = EBayClient()
try:
    r = c.get("/sell/inventory/v1/inventory_item", params={"limit": 5})
    print("inventory_item total:", r.get("total"), "size:", r.get("size"))
    for it in (r.get("inventoryItems") or [])[:3]:
        print("  sku:", it.get("sku"), "| title:", (it.get("product",{}) or {}).get("title"))
except Exception as e:
    print("inventory_item ERROR:", str(e)[:200])
