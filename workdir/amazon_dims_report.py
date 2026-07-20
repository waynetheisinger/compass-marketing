import json
from scripts.amazon_client import AmazonClient
client = AmazonClient()

# names from report (item-name key carries a BOM prefix)
listings = client.fetch_report_rows("GET_MERCHANT_LISTINGS_ALL_DATA")
namekey = [k for k in listings[0].keys() if k.endswith("item-name")][0]
names = {r["seller-sku"]: (r.get(namekey) or "") for r in listings}

rows = json.load(open("workdir/amazon_dims_result.json"))
def missing(r):
    return [g for g,k in (("H","pkg_h"),("L","pkg_l"),("W","pkg_w"),("Wt","pkg_wt")) if r.get(k) is None]

miss = [(r, missing(r)) for r in rows if "error" not in r and missing(r)]
active   = [(r,g) for r,g in miss if r["status"]=="Active"]
inactive = [(r,g) for r,g in miss if r["status"]!="Active"]

def show(group):
    for r,g in sorted(group, key=lambda x:x[0]["sku"]):
        nm = names.get(r["sku"],"")[:52]
        full = "ALL 4" if len(g)==4 else "+".join(g)
        print(f'{r["sku"]:20} {r["asin"]:12} [{full:>7}]  {nm}')

print(f"ACTIVE listings missing dims/weight: {len(active)}")
print("="*90)
show(active)
print(f"\nINACTIVE listings missing dims/weight: {len(inactive)}")
print("="*90)
show(inactive)
