import json, csv, glob, os
from collections import Counter

bq = json.load(open("workdir/bq_offers.json"))

# Tesco EANs from generated CSVs
tesco_eans = set()
for path in glob.glob("tesco/csv-output/*.csv"):
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            e = (row.get("barcode") or "").strip()
            if e:
                tesco_eans.add(e)

# duplicate EANs within B&Q
ean_counts = Counter(x["ean"] for x in bq if x["ean"])
dups = {e: n for e, n in ean_counts.items() if n > 1}
print("=== duplicate EAN(s) within B&Q ===")
for e, n in dups.items():
    print(f"  EAN {e} appears {n}x:")
    for x in bq:
        if x["ean"] == e:
            print(f"     {x['shop_sku']:18} {x['brand']:10} {x['category_label']} | {x['title'][:50]}")

# B&Q-only (not on Tesco), grouped by category
bq_only = [x for x in bq if x["ean"] not in tesco_eans]
print(f"\n=== {len(bq_only)} B&Q-only offers (NOT on Tesco) ===")
bycat = {}
for x in bq_only:
    bycat.setdefault(x["category_label"], []).append(x)
for cat in sorted(bycat):
    print(f"\n  [{cat}]  ({len(bycat[cat])})")
    for x in sorted(bycat[cat], key=lambda r: r["shop_sku"] or ""):
        flag = "" if x["active"] else "  (INACTIVE)"
        print(f"     {x['shop_sku']:18} {x['brand']:10} q={x['qty']:<4} £{x['price']}{flag}")

# distinct categories across the whole 85 -> templates needed
cats = sorted(set((x["category_code"], x["category_label"]) for x in bq))
print(f"\n=== {len(cats)} distinct B&Q categories = templates needed for The Range ===")
