#!/usr/bin/env python3
"""Disable Shopify inventory tracking on Spring Sale variants with ≤0 stock.

Walks the ``Spring SALE and Special OFFERS`` collection and, for each variant
whose ``inventoryQuantity`` is null or ≤ 0 AND whose ``inventoryItem.tracked``
is currently true, sets ``inventoryItem.tracked = false`` via
``productVariantsBulkUpdate``. The variant remains orderable on the
storefront afterwards (uncountable stock).

Dry-run by default. Pass ``--apply`` to write.

Run from the repo root:

    PYTHONPATH=. pyenv exec python scripts/sku_matcher/_untrack_zero_stock_spring_sale.py [--apply]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from scripts.shopify_client import ShopifyClient


COLLECTION_HANDLE = "spring-sale-and-special-offers"


_LIST = """
query springSaleProducts($cursor: String) {
  collectionByHandle(handle: "spring-sale-and-special-offers") {
    products(first: 100, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id
        title
        handle
        variants(first: 50) {
          nodes {
            id
            sku
            inventoryItem { id tracked }
            inventoryQuantity
          }
        }
      }
    }
  }
}
"""


# productVariantsBulkUpdate accepts inventoryItem.tracked nested inside the
# variant payload. The mutation is per-product (variants must share productId).
_UPDATE = """
mutation untrack($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) {
    productVariants { id inventoryItem { tracked } }
    userErrors { field message }
  }
}
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="Actually disable tracking (default: dry-run)")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    suffix = "_apply" if "--apply" in sys.argv else ""
    ap.add_argument("--out", default=f"workdir/shopify-ops/spring_sale_untrack_{today}{suffix}.csv")
    ap.add_argument("--log-file", default=f"workdir/shopify-ops/spring_sale_untrack_{today}{suffix}.jsonl")
    args = ap.parse_args()

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)

    print(f"mode: {'LIVE — TRACKING WILL BE DISABLED' if args.apply else 'DRY-RUN'}",
          file=sys.stderr)

    # Collect candidates, grouped by product (bulk mutation is per-product).
    by_product: dict[str, dict] = {}
    n_variants = 0
    n_pos_or_null_skip_positive = 0
    n_already_untracked = 0
    n_targets = 0

    with ShopifyClient() as client:
        cursor = None
        while True:
            data = client.execute(_LIST, {"cursor": cursor})
            coll = data["collectionByHandle"]
            for p in coll["products"]["nodes"]:
                for v in p["variants"]["nodes"]:
                    n_variants += 1
                    inv = v.get("inventoryQuantity")
                    tracked = v["inventoryItem"]["tracked"]
                    if inv is not None and inv > 0:
                        n_pos_or_null_skip_positive += 1
                        continue
                    if not tracked:
                        n_already_untracked += 1
                        continue
                    n_targets += 1
                    rec = by_product.setdefault(p["id"], {
                        "product_id": p["id"],
                        "product_title": p["title"],
                        "handle": p["handle"],
                        "variants": [],
                    })
                    rec["variants"].append({
                        "variant_id": v["id"],
                        "sku": v.get("sku") or "",
                        "inventory_quantity": inv,
                    })
            pi = coll["products"]["pageInfo"]
            if not pi["hasNextPage"]:
                break
            cursor = pi["endCursor"]

        print(f"  Spring Sale variants scanned:           {n_variants}", file=sys.stderr)
        print(f"  positive inventory (untouched):         {n_pos_or_null_skip_positive}", file=sys.stderr)
        print(f"  already not-tracked (untouched):        {n_already_untracked}", file=sys.stderr)
        print(f"  candidates (≤0 or null AND tracked):    {n_targets}", file=sys.stderr)
        print(f"  spanning unique products:               {len(by_product)}\n", file=sys.stderr)

        if not args.apply:
            # Dump candidates to CSV and exit.
            with open(args.out, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["product_id", "product_title", "variant_id",
                            "shopify_sku", "inventory_quantity", "action"])
                for pid, p in by_product.items():
                    for v in p["variants"]:
                        w.writerow([pid, p["product_title"], v["variant_id"],
                                    v["sku"], v["inventory_quantity"], "would_untrack"])
            print(f"  CSV report: {args.out}", file=sys.stderr)
            return 0

        # Apply pass: one mutation per product.
        n_applied = 0
        n_failed = 0
        results = []
        with open(args.log_file, "w", encoding="utf-8") as log_fh:
            for pid, p in by_product.items():
                payload = [{
                    "id": v["variant_id"],
                    "inventoryItem": {"tracked": False},
                } for v in p["variants"]]
                try:
                    resp = client.execute(_UPDATE, {"productId": pid, "variants": payload})
                    ue = resp["productVariantsBulkUpdate"].get("userErrors") or []
                    if ue:
                        err = "; ".join(f"{e.get('field')}: {e.get('message')}" for e in ue)
                        for v in p["variants"]:
                            row = {"action": "apply_failed",
                                   "product_id": pid, "product_title": p["product_title"],
                                   "variant_id": v["variant_id"], "shopify_sku": v["sku"],
                                   "inventory_quantity": v["inventory_quantity"],
                                   "error": err}
                            results.append(row)
                            log_fh.write(json.dumps(row) + "\n")
                            n_failed += 1
                    else:
                        for v in p["variants"]:
                            row = {"action": "untracked",
                                   "product_id": pid, "product_title": p["product_title"],
                                   "variant_id": v["variant_id"], "shopify_sku": v["sku"],
                                   "inventory_quantity": v["inventory_quantity"],
                                   "error": ""}
                            results.append(row)
                            log_fh.write(json.dumps(row) + "\n")
                            n_applied += 1
                except Exception as e:                                      # noqa: BLE001
                    for v in p["variants"]:
                        row = {"action": "apply_failed",
                               "product_id": pid, "product_title": p["product_title"],
                               "variant_id": v["variant_id"], "shopify_sku": v["sku"],
                               "inventory_quantity": v["inventory_quantity"],
                               "error": f"unexpected: {type(e).__name__}: {e}"}
                        results.append(row)
                        log_fh.write(json.dumps(row) + "\n")
                        n_failed += 1

        with open(args.out, "w", newline="", encoding="utf-8") as f:
            if results:
                w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
                w.writeheader()
                w.writerows(results)

        print(f"\n  applied (untracked):    {n_applied}", file=sys.stderr)
        print(f"  failures:               {n_failed}", file=sys.stderr)
        print(f"  CSV report: {args.out}", file=sys.stderr)
        print(f"  Audit log:  {args.log_file}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
