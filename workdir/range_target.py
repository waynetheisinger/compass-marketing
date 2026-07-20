import json
from collections import Counter

bq = json.load(open("workdir/bq_offers.json"))

# Scope: active AND in stock (qty > 0). Brand-agnostic.
target = [x for x in bq if x.get("active") and (x.get("qty") or 0) > 0]

# Dedup by EAN (safety — collapse any shared-EAN offers).
seen, deduped = set(), []
for x in sorted(target, key=lambda r: (r["category_label"] or "", r["shop_sku"] or "")):
    if x["ean"] in seen:
        continue
    seen.add(x["ean"])
    deduped.append(x)

json.dump(deduped, open("workdir/range_target.json", "w"), indent=2, ensure_ascii=False)

print(f"=== The Range target: {len(deduped)} active in-stock products ===\n")
bycat = {}
for x in deduped:
    bycat.setdefault((x["category_code"], x["category_label"]), []).append(x)

for (cc, cl) in sorted(bycat, key=lambda k: k[1]):
    rows = bycat[(cc, cl)]
    print(f"[{cl}]  {cc}  ({len(rows)})")
    for x in rows:
        print(f"    {x['shop_sku']:18} {x['brand']:10} q={x['qty']:<5} £{x['price']}")
    print()

print(f"=== {len(bycat)} categories = templates to download into range/templates/ ===")
for (cc, cl) in sorted(bycat, key=lambda k: k[1]):
    print(f"  - {cl}  ({cc})")

print("\nbrand split:")
for b, n in Counter(x['brand'] for x in deduped).most_common():
    print(f"  {n:3}  {b}")
