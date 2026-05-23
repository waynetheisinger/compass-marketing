#!/usr/bin/env python3
"""Reverse the untrack pass: set inventoryItem.tracked=true on every variant
listed in ``workdir/shopify-ops/spring_sale_untrack_2026-05-16_apply.csv``.

The source CSV was written by ``_untrack_zero_stock_spring_sale.py --apply``
and contains every (product_id, variant_id) we just modified, so the rollback
targets exactly the same set — no scanning, no inference.

Dry-run by default. Pass ``--apply`` to write.

Run from the repo root:

    PYTHONPATH=. pyenv exec python scripts/sku_matcher/_rollback_untrack_spring_sale.py [--apply]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from scripts.shopify_client import ShopifyClient


SOURCE = "workdir/shopify-ops/spring_sale_untrack_2026-05-16_apply.csv"


_UPDATE = """
mutation retrack($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
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
                    help="Actually re-enable tracking (default: dry-run)")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    suffix = "_apply" if "--apply" in sys.argv else ""
    ap.add_argument("--out", default=f"workdir/shopify-ops/spring_sale_rollback_{today}{suffix}.csv")
    ap.add_argument("--log-file", default=f"workdir/shopify-ops/spring_sale_rollback_{today}{suffix}.jsonl")
    args = ap.parse_args()

    if not Path(SOURCE).exists():
        sys.exit(f"❌ source not found: {SOURCE}")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)

    by_product: dict[str, list[dict]] = {}
    with open(SOURCE, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["action"] != "untracked":
                continue
            by_product.setdefault(r["product_id"], []).append({
                "variant_id": r["variant_id"],
                "sku": r["shopify_sku"],
                "product_title": r["product_title"],
            })

    n_total = sum(len(v) for v in by_product.values())
    print(f"mode: {'LIVE — RE-ENABLE TRACKING' if args.apply else 'DRY-RUN'}",
          file=sys.stderr)
    print(f"variants to re-track: {n_total} across {len(by_product)} products",
          file=sys.stderr)

    if not args.apply:
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["product_id", "variant_id", "shopify_sku", "product_title", "action"])
            for pid, vs in by_product.items():
                for v in vs:
                    w.writerow([pid, v["variant_id"], v["sku"], v["product_title"], "would_retrack"])
        print(f"  CSV report: {args.out}", file=sys.stderr)
        return 0

    n_applied = 0
    n_failed = 0
    results = []
    with ShopifyClient() as client, open(args.log_file, "w", encoding="utf-8") as log_fh:
        for pid, vs in by_product.items():
            payload = [{"id": v["variant_id"],
                        "inventoryItem": {"tracked": True}} for v in vs]
            try:
                resp = client.execute(_UPDATE, {"productId": pid, "variants": payload})
                ue = resp["productVariantsBulkUpdate"].get("userErrors") or []
                if ue:
                    err = "; ".join(f"{e.get('field')}: {e.get('message')}" for e in ue)
                    for v in vs:
                        row = {"action": "apply_failed", "product_id": pid,
                               "variant_id": v["variant_id"], "shopify_sku": v["sku"],
                               "product_title": v["product_title"], "error": err}
                        results.append(row)
                        log_fh.write(json.dumps(row) + "\n")
                        n_failed += 1
                else:
                    for v in vs:
                        row = {"action": "retracked", "product_id": pid,
                               "variant_id": v["variant_id"], "shopify_sku": v["sku"],
                               "product_title": v["product_title"], "error": ""}
                        results.append(row)
                        log_fh.write(json.dumps(row) + "\n")
                        n_applied += 1
            except Exception as e:                                          # noqa: BLE001
                for v in vs:
                    row = {"action": "apply_failed", "product_id": pid,
                           "variant_id": v["variant_id"], "shopify_sku": v["sku"],
                           "product_title": v["product_title"],
                           "error": f"unexpected: {type(e).__name__}: {e}"}
                    results.append(row)
                    log_fh.write(json.dumps(row) + "\n")
                    n_failed += 1

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        if results:
            w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            w.writeheader()
            w.writerows(results)

    print(f"\n  retracked:  {n_applied}", file=sys.stderr)
    print(f"  failures:   {n_failed}", file=sys.stderr)
    print(f"  CSV report: {args.out}", file=sys.stderr)
    print(f"  Audit log:  {args.log_file}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
