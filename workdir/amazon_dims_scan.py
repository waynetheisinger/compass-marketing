import os, json, time, csv
from scripts.amazon_client import AmazonClient
client = AmazonClient()
seller_id = os.environ["AMAZON_SELLER_ID"]

listings = client.fetch_report_rows("GET_MERCHANT_LISTINGS_ALL_DATA")
skus = [(r["seller-sku"], r.get("asin1",""), r.get("status",""), r.get("fulfillment-channel","")) for r in listings]

def dims_ok(block):
    """block is a list like item_package_dimensions; return (h,l,w) present?"""
    if not block: return (None,None,None)
    b = block[0]
    def v(key):
        d = b.get(key)
        return d.get("value") if isinstance(d, dict) else None
    return (v("height"), v("length"), v("width"))

def wt(block):
    if not block: return None
    b = block[0]
    return b.get("value")

rows = []
for i,(sku,asin,status,fc) in enumerate(skus):
    try:
        data = client.get(f"/listings/2021-08-01/items/{seller_id}/{sku}",
            params={"marketplaceIds": client.marketplace_id, "includedData": "attributes"})
        a = data.get("attributes", {})
    except Exception as e:
        rows.append({"sku":sku,"asin":asin,"status":status,"fc":fc,"error":str(e)[:80]})
        continue
    ph,pl,pw = dims_ok(a.get("item_package_dimensions"))
    ih,il,iw = dims_ok(a.get("item_dimensions"))
    pkg_wt = wt(a.get("item_package_weight"))
    item_wt = wt(a.get("item_weight"))
    rows.append({"sku":sku,"asin":asin,"status":status,"fc":fc,
        "pkg_h":ph,"pkg_l":pl,"pkg_w":pw,"pkg_wt":pkg_wt,
        "item_h":ih,"item_l":il,"item_w":iw,"item_wt":item_wt})
    time.sleep(0.3)

with open("workdir/amazon_dims_result.json","w") as f:
    json.dump(rows,f,indent=2)

def missing_pkg(r):
    if "error" in r: return False
    return (r["pkg_h"] is None or r["pkg_l"] is None or r["pkg_w"] is None or r["pkg_wt"] is None)

print(f"Scanned {len(rows)} listings\n")
errs = [r for r in rows if "error" in r]
miss = [r for r in rows if missing_pkg(r)]
ok   = [r for r in rows if "error" not in r and not missing_pkg(r)]
print(f"Complete (pkg dims + weight all present): {len(ok)}")
print(f"MISSING one or more pkg dim/weight: {len(miss)}")
print(f"Errors: {len(errs)}\n")
print("=== MISSING PACKAGE DIMENSIONS/WEIGHT ===")
for r in sorted(miss, key=lambda x:x["sku"]):
    gaps=[]
    if r["pkg_h"] is None: gaps.append("height")
    if r["pkg_l"] is None: gaps.append("length")
    if r["pkg_w"] is None: gaps.append("width")
    if r["pkg_wt"] is None: gaps.append("weight")
    print(f'{r["sku"]:22} {r["asin"]:12} {r["status"]:8} {r["fc"]:12} missing: {", ".join(gaps)}')
if errs:
    print("\n=== ERRORS ===")
    for r in errs:
        print(f'{r["sku"]:22} {r["asin"]:12} {r["error"]}')
