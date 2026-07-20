import json, sys, os
sys.path.insert(0, ".")
from dotenv import load_dotenv; load_dotenv()
from scripts.amazon_client import AmazonClient, AmazonSPAPIError
SELLER_ID = os.environ["AMAZON_SELLER_ID"]

def dimval(d):
    if not d: return None
    v, u = d.get("value"), (d.get("unit") or "")
    return f"{v} {u}".strip() if v is not None else None

c = AmazonClient()
skus = json.load(open("workdir/range_without_skus.json"))

def candidates(sku):
    cands = [sku]
    if sku.startswith("C-"):
        cands.append(sku[2:])
    return cands

results = []
for s in skus:
    sku, cat = s["sku"], s["category"]
    found_sku, attrs, status = None, None, None
    for cand in candidates(sku):
        try:
            r = c.get(f"/listings/2021-08-01/items/{SELLER_ID}/{cand}",
                      params={"marketplaceIds": c.marketplace_id,
                              "includedData": "summaries,attributes"})
            found_sku, attrs = cand, r.get("attributes", {}) or {}
            status = "OK"; break
        except AmazonSPAPIError as e:
            status = e.status
    rec = {"range_sku": sku, "category": cat, "amazon_sku": found_sku, "status": status}
    if attrs is not None:
        item_d = (attrs.get("item_dimensions") or [None])[0]
        pkg_d  = (attrs.get("item_package_dimensions") or [None])[0]
        chosen = item_d or pkg_d
        rec["dim_source"] = "item" if item_d else "package" if pkg_d else None
        if chosen:
            rec["length"] = dimval(chosen.get("length"))
            rec["width"]  = dimval(chosen.get("width"))
            rec["height"] = dimval(chosen.get("height"))
    results.append(rec)
    if rec.get("dim_source"):
        flag = f"  DIMS({rec['dim_source']}): L={rec.get('length')} W={rec.get('width')} H={rec.get('height')}"
    elif found_sku:
        flag = "  (listing found, NO dimensions)"
    else:
        flag = ""
    print(f"{sku:18s} [{cat:22s}] -> {str(found_sku):14s} {status}{flag}")

json.dump(results, open("workdir/range_amazon_dims_probe.json","w"), indent=2)
print("\nsaved workdir/range_amazon_dims_probe.json")
