#!/usr/bin/env python3
"""Cleanup pass: remove the ``Spring Sale`` tag from products we tagged in
error.

Background: an earlier run tagged 147 products based on the Stiga price-sheet
sync. Intent was actually "products in Andrew's stockPricesAndSkus workbook".
This script reverses the wrong tags while preserving any that are correct.

For each product in ``workdir/shopify-ops/spring_sale_tag_2026-05-16_apply.csv`` whose
``action == 'applied'``:
  1. Fetch the product's variants from Shopify.
  2. If ANY variant SKU appears as ``sku_b`` in ``lookups/matches.csv`` (Andrew's
     workbook map), keep the tag. The product genuinely belongs in
     stockPricesAndSkus, so the tag was correct (even if added for the wrong
     reason).
  3. Otherwise call ``tagsRemove`` for the ``Spring Sale`` tag on this product.

Dry-run by default. Pass ``--apply`` to write.

Run from the repo root:

    PYTHONPATH=. pyenv exec python scripts/sku_matcher/_spring_sale_cleanup_untag.py [--apply]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from scripts.shopify_client import ShopifyClient


TAG = "Spring Sale"
PRIOR_TAG_APPLY = "workdir/shopify-ops/spring_sale_tag_2026-05-16_apply.csv"
MATCHES = "lookups/matches.csv"


_GET_VARIANTS = """
query getVariants($id: ID!) {
  product(id: $id) {
    id
    title
    tags
    variants(first: 100) {
      nodes { id sku }
    }
  }
}
"""

_TAGS_REMOVE = """
mutation removeTag($id: ID!, $tags: [String!]!) {
  tagsRemove(id: $id, tags: $tags) {
    userErrors { field message }
    node { ... on Product { id tags } }
  }
}
"""


def _load_andrew_sku_set() -> set:
    """Set of sku_b (upper-cased Shopify SKUs) that appear in lookups/matches.csv."""
    if not Path(MATCHES).exists():
        sys.exit(f"❌ matches file not found: {MATCHES}")
    out = set()
    with open(MATCHES, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            sku_b = (r.get("sku_b") or "").strip()
            if sku_b:
                out.add(sku_b.upper())
    return out


def _load_prior_tag_targets() -> list:
    """List of {product_id, product_title, handle} for products we tagged."""
    if not Path(PRIOR_TAG_APPLY).exists():
        sys.exit(f"❌ prior tag apply CSV not found: {PRIOR_TAG_APPLY}")
    out = []
    with open(PRIOR_TAG_APPLY, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("action") != "applied":
                continue
            out.append({
                "product_id": r["product_id"],
                "product_title": r["product_title"],
                "handle": r["handle"],
                "contributing_skus": r.get("contributing_skus", ""),
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="Actually call tagsRemove (default: dry-run)")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    suffix = "_apply" if "--apply" in sys.argv else ""
    ap.add_argument("--out", default=f"workdir/shopify-ops/spring_sale_cleanup_{today}{suffix}.csv")
    ap.add_argument("--log-file", default=f"workdir/shopify-ops/spring_sale_cleanup_{today}{suffix}.jsonl")
    args = ap.parse_args()

    andrew_skus = _load_andrew_sku_set()
    targets = _load_prior_tag_targets()
    print(f"Andrew sku_b set (from lookups/matches.csv): {len(andrew_skus)}", file=sys.stderr)
    print(f"Previously-tagged products to recheck: {len(targets)}", file=sys.stderr)
    print(f"mode: {'LIVE — TAGS WILL BE REMOVED' if args.apply else 'DRY-RUN'}\n",
          file=sys.stderr)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)

    n_keep = 0
    n_remove = 0
    n_no_tag_present = 0  # tag already gone (e.g. someone removed manually)
    n_failed = 0
    csv_rows = []

    with ShopifyClient() as client, open(args.log_file, "w", encoding="utf-8") as log_fh:
        for t in targets:
            pid = t["product_id"]
            data = client.execute(_GET_VARIANTS, {"id": pid})
            product = data.get("product")
            if product is None:
                row = {"action": "skip", "reason": "product_not_found",
                       "product_id": pid, "product_title": t["product_title"],
                       "matched_andrew_skus": "",
                       "all_variant_skus": ""}
                csv_rows.append(row)
                log_fh.write(json.dumps(row) + "\n")
                continue

            variant_skus = [(v.get("sku") or "").strip()
                            for v in product["variants"]["nodes"]
                            if (v.get("sku") or "").strip()]
            overlap = [s for s in variant_skus if s.upper() in andrew_skus]
            tags = set(t_.strip() for t_ in product["tags"])

            row = {
                "product_id": pid,
                "product_title": product["title"],
                "all_variant_skus": ", ".join(variant_skus),
                "matched_andrew_skus": ", ".join(overlap),
                "contributing_skus": t["contributing_skus"],
                "tags_before": ", ".join(sorted(tags)),
                "action": "",
                "error": "",
            }

            if overlap:
                # Product genuinely belongs in stockPricesAndSkus — keep.
                row["action"] = "keep_tagged"
                n_keep += 1
                csv_rows.append(row)
                log_fh.write(json.dumps(row) + "\n")
                continue

            if TAG not in tags:
                # Already untagged somehow — record but do nothing.
                row["action"] = "tag_already_absent"
                n_no_tag_present += 1
                csv_rows.append(row)
                log_fh.write(json.dumps(row) + "\n")
                continue

            if not args.apply:
                row["action"] = "would_untag"
                n_remove += 1
                csv_rows.append(row)
                log_fh.write(json.dumps(row) + "\n")
                continue

            try:
                resp = client.execute(_TAGS_REMOVE, {"id": pid, "tags": [TAG]})
                ue = resp["tagsRemove"].get("userErrors") or []
                if ue:
                    row["action"] = "apply_failed"
                    row["error"] = "; ".join(f"{e.get('field')}: {e.get('message')}" for e in ue)
                    n_failed += 1
                else:
                    row["action"] = "untagged"
                    n_remove += 1
            except Exception as e:                                          # noqa: BLE001
                row["action"] = "apply_failed"
                row["error"] = f"unexpected: {type(e).__name__}: {e}"
                n_failed += 1
            csv_rows.append(row)
            log_fh.write(json.dumps(row) + "\n")

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        if csv_rows:
            w = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
            w.writeheader()
            w.writerows(csv_rows)

    print(f"\n{'=' * 60}")
    print(f"  kept tagged (variant in lookups/matches.csv):  {n_keep}")
    print(f"  would untag / untagged:                {n_remove}")
    print(f"  tag already absent:                    {n_no_tag_present}")
    print(f"  failures:                              {n_failed}")
    print(f"\n  CSV report: {args.out}")
    print(f"  Audit log:  {args.log_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
