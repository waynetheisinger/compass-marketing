#!/usr/bin/env python3
"""Dump every Shopify variant (SKU + product title) to a matcher-ready CSV.

Output is feed-compatible with `matcher.py`'s default `file_b` shape:

    sku,title
    SBS40CB,SPECTRUM BY SWIFT SBS40CB 4.0AH BATTERY
    ...

Run from the repo root — credentials come from `.env` via
`scripts.shopify_client.ShopifyClient`:

    PYTHONPATH=. pyenv exec python scripts/sku_matcher/export_shopify_catalogue.py \\
        --out shopify_catalogue.csv

Pagination follows the pattern in scripts/find_products_without_sku.py.
"""

import argparse
import csv
import sys

from scripts.shopify_client import ShopifyClient

# 250 is the Shopify maximum; `variants(first: 100)` is the maximum per
# product (well above any realistic variant count).
_QUERY = """
query catalogue($cursor: String, $q: String) {
  products(first: 250, after: $cursor, query: $q) {
    pageInfo { hasNextPage endCursor }
    nodes {
      title
      variants(first: 100) {
        nodes {
          sku
        }
      }
    }
  }
}
"""


def parse_args():
    p = argparse.ArgumentParser(
        description="Dump Shopify variants as a matcher-ready CSV.",
    )
    p.add_argument(
        "--out",
        default="shopify_catalogue.csv",
        help="Output CSV path (default: shopify_catalogue.csv)",
    )
    p.add_argument(
        "--query",
        default="",
        help='Optional Shopify search filter, e.g. \'vendor:Honda\' or \'status:active\'. '
             'Default: no filter (every product).',
    )
    p.add_argument(
        "--include-blank-sku",
        action="store_true",
        help="Include variants whose SKU is empty/null (default: drop them, "
             "since the matcher's join key is SKU).",
    )
    return p.parse_args()


def main():
    args = parse_args()

    rows_written = 0
    products_seen = 0
    blanks_dropped = 0

    with ShopifyClient() as client, open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["sku", "title"])

        cursor = None
        page = 0
        while True:
            page += 1
            data = client.execute(_QUERY, {"cursor": cursor, "q": args.query or None})
            products = data["products"]

            for product in products["nodes"]:
                products_seen += 1
                title = product["title"]
                for variant in product["variants"]["nodes"]:
                    sku = (variant.get("sku") or "").strip()
                    if not sku and not args.include_blank_sku:
                        blanks_dropped += 1
                        continue
                    writer.writerow([sku, title])
                    rows_written += 1

            page_info = products["pageInfo"]
            if not page_info["hasNextPage"]:
                break
            cursor = page_info["endCursor"]
            print(
                f"  page {page}: {products_seen} products, {rows_written} variant rows so far...",
                file=sys.stderr,
            )

    print(
        f"\n✓ Wrote {rows_written} variant rows from {products_seen} products to {args.out}",
        file=sys.stderr,
    )
    if blanks_dropped:
        print(
            f"  ({blanks_dropped} variants with blank SKU dropped — use --include-blank-sku to keep)",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
