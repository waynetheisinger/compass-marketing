import os, sys, json, csv, glob
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.getcwd(), ".env"))
from scripts.mirakl_client import MiraklClient

c = MiraklClient("KINGFISHER")

# ---- 1. Pull ALL B&Q offers (paginate) ----
def ean_of(refs):
    for r in refs or []:
        if (r.get("reference_type") or "").upper() == "EAN":
            return r.get("reference")
    # fall back to first reference of any type
    return (refs or [{}])[0].get("reference") if refs else None

offers = []
offset = 0
while True:
    page = c.get("/offers", params={"max": 100, "offset": offset})
    batch = page.get("offers", [])
    offers.extend(batch)
    total = page.get("total_count", len(offers))
    offset += len(batch)
    if not batch or offset >= total:
        break

bq = []
for o in offers:
    bq.append({
        "shop_sku": o.get("shop_sku"),
        "product_sku": o.get("product_sku"),
        "ean": ean_of(o.get("product_references")),
        "title": o.get("product_title"),
        "brand": o.get("product_brand"),
        "category_code": o.get("category_code"),
        "category_label": o.get("category_label"),
        "active": o.get("active"),
        "qty": o.get("quantity"),
        "price": o.get("price"),
    })

with open("workdir/bq_offers.json", "w") as fh:
    json.dump(bq, fh, indent=2, ensure_ascii=False)

print(f"=== B&Q: {len(bq)} offers ===")
from collections import Counter
print("\nby brand:")
for b, n in Counter(x["brand"] for x in bq).most_common():
    print(f"  {n:3}  {b}")
print("\nby category:")
for (cc, cl), n in Counter((x["category_code"], x["category_label"]) for x in bq).most_common():
    print(f"  {n:3}  {cc}  {cl}")

# ---- 2. Read Tesco catalogue from generated CSVs (sku + barcode cols) ----
tesco = {}
for path in glob.glob("tesco/csv-output/*.csv"):
    with open(path, newline="", encoding="utf-8") as fh:
        rdr = csv.DictReader(fh, delimiter=";")
        for row in rdr:
            sku = (row.get("sku") or "").strip()
            if not sku:
                continue
            tesco[sku] = {"ean": (row.get("barcode") or "").strip(),
                          "file": os.path.basename(path)}
print(f"\n=== Tesco: {len(tesco)} SKUs (from tesco/csv-output) ===")

# ---- 3. Overlap by EAN (most reliable cross-marketplace key) ----
def norm(s):
    return (s or "").strip().upper().lstrip("C-").lstrip("C")  # rough C- prefix strip

bq_by_ean = {x["ean"]: x for x in bq if x["ean"]}
tesco_eans = {v["ean"]: k for k, v in tesco.items() if v["ean"]}

common_ean = set(bq_by_ean) & set(tesco_eans)
print(f"\n=== Overlap by EAN: {len(common_ean)} products on BOTH ===")
for e in sorted(common_ean):
    print(f"  EAN {e}  | B&Q {bq_by_ean[e]['shop_sku']:18} | Tesco {tesco_eans[e]}")

bq_only_ean = set(bq_by_ean) - set(tesco_eans)
tesco_only_ean = set(tesco_eans) - set(bq_by_ean)
print(f"\nB&Q-only (by EAN): {len(bq_only_ean)}    Tesco-only (by EAN): {len(tesco_only_ean)}")
print(f"\nUnion target for The Range (by EAN): "
      f"{len(set(bq_by_ean) | set(tesco_eans))} unique EAN-bearing products")
print(f"  (+ any B&Q offers without an EAN: {sum(1 for x in bq if not x['ean'])})")
