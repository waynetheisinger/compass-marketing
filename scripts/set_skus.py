"""
Set SKUs on products that currently have none.
Run once — idempotent (checks existing SKU before writing).

Usage:
    python -m scripts.set_skus
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.shopify_client import ShopifyClient

# (product_id, sku) — numeric IDs, script builds GIDs internally
UPDATES = [
    ("15207521681781", "MIIMO-1000B"),
    ("15207772586357", "HRM-1500BC"),
    ("15207829668213", "HRM-2500BC"),
    ("15207869317493", "HRM-4000BC-LIVE"),
    ("15208032502133", "HHB-36-BXB"),
    ("15208069955957", "UMK-425-LE"),
    ("15208688550261", "HRN-536-VY"),
    ("15208858845557", "HRD-536-HX"),
    ("15209170665845", "HHC36-BXB"),
    ("15209175023989", "HF-2417-HTE"),
    ("15209337454965", "FG320"),
    ("15209421406581", "HRG-466-PK"),
    ("15441531896181", "WEBB-WBV"),
    ("15601516118389", "WEISWB"),
    ("15605340602741", "WEPS400"),
    ("15630671118709", "LBP-56V125"),
    ("15630965670261", "PAS-2620ES"),
    ("15631019377013", "MTA-AH-HD"),
    ("15633348100469", "XHT-240"),
    ("15638964273525", "SEALEY-WPP2"),
    ("15639045112181", "WT30"),
    ("15639093641589", "EU22I"),
    ("15656788558197", "UMS-425-LN"),
    ("15664450240885", "PARK-900-WX"),
    ("15675403469173", "CS-3410"),
    ("15729633755509", "SVC-PUNCTURE-PREV"),
    ("15736754536821", "ACC-DEFL-84CM"),
    ("8321662681255",  "SP53"),
]

GET_VARIANTS = """
query getVariants($id: ID!) {
  product(id: $id) {
    title
    variants(first: 100) {
      nodes {
        id
        sku
        inventoryItem {
          id
        }
      }
    }
  }
}
"""

UPDATE_INVENTORY_ITEM = """
mutation updateSku($id: ID!, $input: InventoryItemInput!) {
  inventoryItemUpdate(id: $id, input: $input) {
    inventoryItem {
      id
      sku
    }
    userErrors {
      field
      message
    }
  }
}
"""

def main():
    ok = 0
    skipped = 0
    errors = 0

    with ShopifyClient() as client:
        for product_id, sku in UPDATES:
            gid = f"gid://shopify/Product/{product_id}"
            data = client.execute(GET_VARIANTS, {"id": gid})
            product = data["product"]
            variants = product["variants"]["nodes"]

            # Skip if all variants already have SKUs
            if all(v["sku"] for v in variants):
                print(f"  SKIP  {product['title']} — all variants already have SKUs")
                skipped += 1
                continue

            variant_errors = []
            for v in variants:
                if v["sku"]:
                    continue
                result = client.execute(UPDATE_INVENTORY_ITEM, {
                    "id": v["inventoryItem"]["id"],
                    "input": {"sku": sku},
                })
                ue = result["inventoryItemUpdate"]["userErrors"]
                if ue:
                    variant_errors.append(ue)

            if variant_errors:
                print(f"  ERROR {product['title']}: {variant_errors}")
                errors += 1
            else:
                print(f"  OK    {sku}  →  {product['title']}")
                ok += 1

    print(f"\nDone — {ok} updated, {skipped} skipped, {errors} errors")

if __name__ == "__main__":
    main()
