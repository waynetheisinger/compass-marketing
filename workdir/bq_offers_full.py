import json
from scripts.mirakl_client import MiraklClient
c = MiraklClient("KINGFISHER")
offers=[]; off=0
while True:
    r = c.get("/offers", params={"max":100,"offset":off})
    page = r.get("offers",[])
    offers += page
    off += len(page)
    if off >= (r.get("total_count") or 0) or not page: break
print("total offers:", len(offers))
# inspect additional fields on first offer for dimension-like keys
o = offers[0]
print("additional_fields sample:", json.dumps(o.get("offer_additional_fields"))[:300])
# collect
out=[]
for o in offers:
    out.append({
        "shop_sku": o.get("shop_sku"),
        "ean": o.get("product_sku"),
        "title": o.get("product_title"),
        "active": o.get("active"),
        "category": o.get("category_label"),
        "logistic_class": o.get("logistic_class"),
    })
json.dump(out, open("workdir/bq_offers_list.json","w"), indent=2)
# any dim-ish additional field keys across offers?
keys=set()
for o in offers:
    for af in (o.get("offer_additional_fields") or []):
        keys.add(af.get("code"))
print("additional field codes:", sorted(keys))
