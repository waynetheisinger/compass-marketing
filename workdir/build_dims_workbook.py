import json, csv, re
from scripts.amazon_client import AmazonClient

# ---- Shopify catalogue: barcode -> (sku,title), sku -> (title,barcode)
cat_by_barcode={}; cat_by_sku={}
with open("lookups/shopify_catalogue.csv") as f:
    for row in csv.DictReader(f):
        sku=(row.get("sku") or "").strip()
        bc=(row.get("barcode") or "").strip()
        ti=(row.get("title") or "").strip()
        if bc: cat_by_barcode[bc]=(sku,ti)
        if sku: cat_by_sku[sku]=(ti,bc)

# ---- Amazon report for EAN
amz=AmazonClient()
rep=amz.fetch_report_rows("GET_MERCHANT_LISTINGS_ALL_DATA")
amz_ean={}
for r in rep:
    sku=r["seller-sku"]
    if r.get("product-id-type")=="4":
        amz_ean[sku]=r.get("product-id")

# ---- Amazon dims (from earlier scan)
dims=json.load(open("workdir/amazon_dims_result.json"))
def resolve_amz_ean(sku):
    if amz_ean.get(sku): return amz_ean[sku]
    base=re.sub(r"-AMZ$","",sku)
    if base in cat_by_sku and cat_by_sku[base][1]: return cat_by_sku[base][1]
    if sku in cat_by_sku and cat_by_sku[sku][1]: return cat_by_sku[sku][1]
    return None

# product record keyed by EAN (fallback: A:<sku> / B:<sku>)
prods={}
def rec(key):
    return prods.setdefault(key, {
        "ean":"","shopify_sku":"","title":"","channels":set(),
        "amz_skus":[], "amz_asins":[], "bq_skus":[],
        "p_l":None,"p_w":None,"p_h":None,"p_wt":None,
        "s_l":None,"s_w":None,"s_h":None,"s_wt":None,
    })
def take(cur,val):
    return cur if cur is not None else val

# names from report
namekey=[k for k in rep[0].keys() if k.endswith("item-name")][0]
amz_name={r["seller-sku"]:(r.get(namekey) or "") for r in rep}

for d in dims:
    sku=d["sku"]; ean=resolve_amz_ean(sku)
    key=ean or f"A:{sku}"
    p=rec(key)
    if ean: p["ean"]=ean
    p["channels"].add("Amazon")
    p["amz_skus"].append(sku)
    if d.get("asin"): p["amz_asins"].append(d["asin"])
    # product dims <- item_dimensions ; shipping <- package
    p["p_l"]=take(p["p_l"], d.get("item_l")); p["p_w"]=take(p["p_w"], d.get("item_w"))
    p["p_h"]=take(p["p_h"], d.get("item_h")); p["p_wt"]=take(p["p_wt"], d.get("item_wt"))
    p["s_l"]=take(p["s_l"], d.get("pkg_l")); p["s_w"]=take(p["s_w"], d.get("pkg_w"))
    p["s_h"]=take(p["s_h"], d.get("pkg_h")); p["s_wt"]=take(p["s_wt"], d.get("pkg_wt"))
    if not p["title"]:
        p["title"]= (cat_by_barcode.get(ean,("",""))[1] if ean else "") or amz_name.get(sku,"")

# B&Q
bq=json.load(open("workdir/bq_offers_list.json"))
for o in bq:
    ean=(o.get("ean") or "").strip()
    key=ean if ean in prods else (ean or f"B:{o['shop_sku']}")
    p=rec(key)
    if ean: p["ean"]=ean
    p["channels"].add("B&Q")
    p["bq_skus"].append(o.get("shop_sku"))
    if not p["title"]:
        p["title"]=(cat_by_barcode.get(ean,("",""))[1] if ean else "") or (o.get("title") or "")

# resolve shopify sku
for p in prods.values():
    if p["ean"] and p["ean"] in cat_by_barcode:
        p["shopify_sku"]=cat_by_barcode[p["ean"]][0]
        if not p["title"]: p["title"]=cat_by_barcode[p["ean"]][1]

json.dump({k:{**v,"channels":sorted(v["channels"])} for k,v in prods.items()},
          open("workdir/dims_products.json","w"), indent=2, default=list)

# stats
tot=len(prods)
def complete(p):
    return all(p[k] is not None for k in ("p_l","p_w","p_h","p_wt","s_l","s_w","s_h","s_wt"))
comp=sum(1 for p in prods.values() if complete(p))
print(f"Unique products across marketplaces: {tot}")
print(f"  fully complete (all 8): {comp}")
print(f"  need some data: {tot-comp}")
from collections import Counter
ch=Counter()
for p in prods.values(): ch[", ".join(sorted(p['channels']))]+=1
print("  by channel:", dict(ch))
