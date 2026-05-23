#!/usr/bin/env python3
"""Tag the Stiga-updated Shopify products with ``Spring Sale``.

The 145 SKUs in ``workdir/shopify-ops/stiga_updated_skus_2026-05-16.csv`` got new prices
from Stiga's May 2026 sheet. The ``Spring SALE and Special OFFERS`` collection
in Shopify auto-includes any product with the tag ``Spring Sale``. This
script:

  1. Resolves each variant SKU → parent product GID + existing tags.
  2. Dedupes to unique products (a product with multiple updated variants is
     tagged once).
  3. Adds ``Spring Sale`` only to products that don't already have it (so
     re-runs are no-ops).

Dry-run by default. Pass ``--apply`` to write.

Run from the repo root:

    PYTHONPATH=. pyenv exec python scripts/sku_matcher/_apply_spring_sale_tag.py [--apply]
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
DEFAULT_INPUT = "workdir/shopify-ops/stiga_updated_skus_2026-05-16.csv"


_LOOKUP = """
query findBySku($q: String!) {
  productVariants(first: 5, query: $q) {
    nodes {
      sku
      product {
        id
        title
        handle
        tags
      }
    }
  }
}
"""

_TAGS_ADD = """
mutation addTag($id: ID!, $tags: [String!]!) {
  tagsAdd(id: $id, tags: $tags) {
    userErrors { field message }
    node { ... on Product { id tags } }
  }
}
"""


def _load_skus(input_path: str) -> list:
    rows = []
    with open(input_path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            sku = (r.get("shopify_sku") or "").strip()
            if sku:
                rows.append({
                    "sku": sku,
                    "source": r.get("source", ""),
                    "shopify_title": r.get("shopify_title", ""),
                })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="Actually call tagsAdd (default: dry-run)")
    ap.add_argument("--input", default=DEFAULT_INPUT,
                    help=f"CSV with a 'shopify_sku' column (default: {DEFAULT_INPUT})")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    suffix = "_apply" if "--apply" in sys.argv else ""
    ap.add_argument("--out", default=f"workdir/shopify-ops/spring_sale_tag_{today}{suffix}.csv")
    ap.add_argument("--log-file", default=f"workdir/shopify-ops/spring_sale_tag_{today}{suffix}.jsonl")
    args = ap.parse_args()

    rows = _load_skus(args.input)
    print(f"input SKUs: {len(rows)}", file=sys.stderr)
    print(f"mode: {'LIVE — TAGS WILL BE WRITTEN' if args.apply else 'DRY-RUN'}\n",
          file=sys.stderr)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)

    # Resolve SKU → product. Some SKUs return multiple variants (when more
    # than one product shares a SKU — Shopify allows this); we keep all matches
    # so the tag is added to every product carrying an updated SKU.
    sku_to_products: dict[str, list[dict]] = {}
    not_found: list[str] = []

    with ShopifyClient() as client, open(args.log_file, "w", encoding="utf-8") as log_fh:
        for row in rows:
            sku = row["sku"]
            data = client.execute(_LOOKUP, {"q": f"sku:{sku}"})
            nodes = data["productVariants"]["nodes"]
            # Filter to exact SKU match (sku: is a prefix/contains search).
            exact = [n for n in nodes if (n.get("sku") or "").strip() == sku]
            if not exact:
                not_found.append(sku)
                log_fh.write(json.dumps({"sku": sku, "status": "not_found"}) + "\n")
                continue
            products = [{
                "id": n["product"]["id"],
                "title": n["product"]["title"],
                "handle": n["product"]["handle"],
                "tags": n["product"]["tags"],
            } for n in exact]
            # Dedupe products if the SKU appears on multiple variants of the
            # same product.
            seen = set()
            unique = []
            for p in products:
                if p["id"] not in seen:
                    seen.add(p["id"])
                    unique.append(p)
            sku_to_products[sku] = unique

        # Flatten product list, dedupe by product id, capture which SKUs
        # contributed each one.
        product_index: dict[str, dict] = {}
        for sku, plist in sku_to_products.items():
            for p in plist:
                rec = product_index.setdefault(p["id"], {
                    "id": p["id"],
                    "title": p["title"],
                    "handle": p["handle"],
                    "tags": p["tags"],
                    "contributing_skus": [],
                })
                rec["contributing_skus"].append(sku)

        print(f"Resolved unique products: {len(product_index)}", file=sys.stderr)
        if not_found:
            print(f"⚠️  SKUs not found in Shopify: {len(not_found)}",
                  file=sys.stderr)
            for s in not_found[:10]:
                print(f"    {s}", file=sys.stderr)

        # Decide per product. Already-tagged products are recorded as no-op.
        n_to_tag = 0
        n_already = 0
        n_applied = 0
        n_failed = 0
        csv_rows = []

        for pid, p in product_index.items():
            existing_tags = {t.strip() for t in p["tags"]}
            already = TAG in existing_tags
            entry = {
                "product_id": pid,
                "product_title": p["title"],
                "handle": p["handle"],
                "existing_tags": ", ".join(sorted(existing_tags)),
                "contributing_skus": ", ".join(sorted(p["contributing_skus"])),
                "already_tagged": "yes" if already else "no",
                "action": "",
                "error": "",
            }
            if already:
                entry["action"] = "skip_already_tagged"
                n_already += 1
                csv_rows.append(entry)
                log_fh.write(json.dumps(entry) + "\n")
                continue

            n_to_tag += 1
            if not args.apply:
                entry["action"] = "would_tag"
                csv_rows.append(entry)
                log_fh.write(json.dumps(entry) + "\n")
                continue

            try:
                resp = client.execute(_TAGS_ADD, {"id": pid, "tags": [TAG]})
                ue = resp["tagsAdd"].get("userErrors") or []
                if ue:
                    entry["action"] = "apply_failed"
                    entry["error"] = "; ".join(f"{e.get('field')}: {e.get('message')}" for e in ue)
                    n_failed += 1
                else:
                    entry["action"] = "applied"
                    n_applied += 1
            except Exception as e:                                          # noqa: BLE001
                entry["action"] = "apply_failed"
                entry["error"] = f"unexpected: {type(e).__name__}: {e}"
                n_failed += 1
            csv_rows.append(entry)
            log_fh.write(json.dumps(entry) + "\n")

    # Flat CSV report.
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        if csv_rows:
            w = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
            w.writeheader()
            w.writerows(csv_rows)

    print(f"\n{'=' * 60}")
    print(f"  unique products resolved:     {len(product_index)}")
    print(f"  already had '{TAG}':            {n_already}")
    print(f"  would tag (dry-run):          {n_to_tag if not args.apply else 0}")
    print(f"  applied:                      {n_applied}")
    print(f"  apply failures:               {n_failed}")
    if not_found:
        print(f"  SKUs not found in Shopify:    {len(not_found)}")
    print(f"\n  CSV report: {args.out}")
    print(f"  Audit log:  {args.log_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
