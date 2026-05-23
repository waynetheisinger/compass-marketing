#!/usr/bin/env python3
"""Reverse-direction sanity check: Shopify-side coverage of the Stiga price sync.

Lists every Shopify variant in the Stiga / Mountfield / Atco brand family that
the Stiga price-sheet sync did NOT touch — i.e. its SKU never appeared as a
matched ``sku_b`` in ``lookups/matches_stiga.csv``. This catches:

  * Listings we should have matched but couldn't (matcher gap)
  * Listings outside Stiga's sheet (discontinued, regional, accessory, new product)
  * Listings whose price didn't move because they were matched to a different
    Shopify variant (SKU drift)

Loose brand filter — a variant qualifies if ANY of these signal Stiga family:

  * Title contains "Stiga", "Mountfield", or "Atco" (case-insensitive)
  * SKU starts with a brand-family prefix (STIG, MF-, AT-, STIGA, MOUNT, ATCO,
    or a Stiga-style numeric like 2T/29/2L/2D/2F/2S/2T)
  * Shopify ``vendor`` is Stiga, Mountfield, or Atco

Run from the repo root:

    PYTHONPATH=. pyenv exec python scripts/sku_matcher/stiga_brand_coverage_report.py
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

from scripts.shopify_client import ShopifyClient


BRAND_KEYWORDS = {
    "Stiga": re.compile(r"\bstiga\b", re.IGNORECASE),
    "Mountfield": re.compile(r"\bmountfield\b", re.IGNORECASE),
    "Atco": re.compile(r"\batco\b", re.IGNORECASE),
}

# SKU prefixes that consistently indicate a Stiga-family product. Numeric
# Stiga supplier codes start with one of these two-character pairs — see the
# Stiga pricesheet for the full distribution.
SKU_PREFIXES = {
    "Stiga": ("STIG", "STIGA"),
    "Mountfield": ("MF-", "MOUNT"),
    "Atco": ("AT-", "ATCO"),
}
_NUMERIC_STIGA_PREFIXES = ("2T", "2L", "2D", "2F", "2S", "29", "27", "28")


def _classify_brand(title: str, sku: str, vendor: str | None) -> str | None:
    """Return 'Stiga' / 'Mountfield' / 'Atco' or None.

    Precedence: vendor wins (most authoritative when populated), then title
    keyword, then SKU prefix. Branded SKU prefixes (STIG-/MF-/AT-) take
    priority over the numeric Stiga prefix, which is broad.
    """
    if vendor:
        v = vendor.strip().lower()
        if v == "stiga":
            return "Stiga"
        if v == "mountfield":
            return "Mountfield"
        if v == "atco":
            return "Atco"

    for brand, rx in BRAND_KEYWORDS.items():
        if rx.search(title):
            return brand

    sku_u = (sku or "").upper()
    for brand, prefixes in SKU_PREFIXES.items():
        if any(sku_u.startswith(p) for p in prefixes):
            return brand

    # Numeric Stiga prefix — only assign if no other brand signal. This is
    # broad, so it's the last fallback.
    if any(sku_u.startswith(p) for p in _NUMERIC_STIGA_PREFIXES):
        return "Stiga"

    return None


_QUERY = """
query coverage($cursor: String) {
  products(first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      title
      handle
      vendor
      variants(first: 100) {
        nodes {
          sku
          title
          price
          compareAtPrice
        }
      }
    }
  }
}
"""


def _load_matched_skus(matches_path: str) -> set:
    """Return the set of Shopify SKUs (sku_b) that appear in lookups/matches_stiga.csv."""
    if not Path(matches_path).exists():
        sys.exit(f"❌ matches file not found: {matches_path}")
    out = set()
    with open(matches_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sku_b = (row.get("sku_b") or "").strip()
            if sku_b:
                out.add(sku_b.upper())
    return out


def _load_stiga_supplier_codes(stiga_path: str) -> list:
    """Return list of (supplier_code, description) tuples from the normalized Stiga sheet.

    Used to flag Shopify variants whose SKU or title contains a Stiga supplier
    code we DID have a price for — strong signal of a matcher miss.
    """
    if not Path(stiga_path).exists():
        return []
    out = []
    with open(stiga_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = (row.get("Product Code") or "").strip()
            if code:
                out.append((code.upper(), row.get("Product Description", "")))
    return out


def _suspected_match_miss(shopify_sku: str, shopify_title: str,
                          stiga_codes: list) -> tuple[str, str] | None:
    """If the Shopify SKU or title contains a Stiga supplier code, return
    (supplier_code, supplier_description). Otherwise None.

    Match is by case-insensitive substring on the full supplier code (which
    typically includes a slash, e.g. ``2T0210483/M22``) — this is restrictive
    enough that false positives are unlikely.
    """
    sku_u = shopify_sku.upper()
    title_u = shopify_title.upper()
    for code, desc in stiga_codes:
        if code in sku_u or code in title_u:
            return code, desc
    return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--matches", default="lookups/matches_stiga.csv",
                    help="Matches CSV (sku_b column) to cross-reference")
    ap.add_argument("--stiga-sheet", default="workdir/sku-matcher/stiga_prices_normalized.csv",
                    help="Normalized Stiga pricesheet (for miss-suspect flagging)")
    ap.add_argument("--out", default="reports/stiga_brands_not_covered_2026-05-16.csv")
    args = ap.parse_args()

    matched_u = _load_matched_skus(args.matches)
    stiga_codes = _load_stiga_supplier_codes(args.stiga_sheet)
    print(f"matched Shopify SKUs in {args.matches}: {len(matched_u)}",
          file=sys.stderr)
    print(f"Stiga supplier codes (for miss flagging): {len(stiga_codes)}",
          file=sys.stderr)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)

    n_total = 0
    n_brand = 0
    n_not_covered = 0
    not_covered = []

    with ShopifyClient() as client:
        cursor = None
        page = 0
        while True:
            page += 1
            data = client.execute(_QUERY, {"cursor": cursor})
            products = data["products"]
            for product in products["nodes"]:
                p_title = product["title"]
                handle = product["handle"]
                vendor = product.get("vendor")
                for variant in product["variants"]["nodes"]:
                    n_total += 1
                    sku = (variant.get("sku") or "").strip()
                    if not sku:
                        continue
                    v_title = variant.get("title") or ""
                    full_title = p_title if v_title in ("", "Default Title") \
                        else f"{p_title} — {v_title}"
                    brand = _classify_brand(full_title, sku, vendor)
                    if brand is None:
                        continue
                    n_brand += 1
                    if sku.upper() in matched_u:
                        continue
                    n_not_covered += 1
                    miss = _suspected_match_miss(sku, full_title, stiga_codes)
                    not_covered.append({
                        "brand": brand,
                        "shopify_sku": sku,
                        "shopify_title": full_title,
                        "vendor": vendor or "",
                        "current_price": variant.get("price") or "",
                        "current_compare_at_price": variant.get("compareAtPrice") or "",
                        "product_handle": handle,
                        "suspected_match_miss": "yes" if miss else "no",
                        "stiga_supplier_code": miss[0] if miss else "",
                        "stiga_supplier_desc": miss[1] if miss else "",
                    })
            page_info = products["pageInfo"]
            if not page_info["hasNextPage"]:
                break
            cursor = page_info["endCursor"]
            print(f"  page {page}: scanned {n_total} variants, "
                  f"{n_brand} brand-tagged, {n_not_covered} not covered",
                  file=sys.stderr)

    # Sort: suspected misses first (so they're visible immediately), then by
    # brand, then by title.
    not_covered.sort(key=lambda r: (
        r["suspected_match_miss"] != "yes",
        r["brand"],
        r["shopify_title"].lower(),
    ))

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "suspected_match_miss", "stiga_supplier_code", "stiga_supplier_desc",
            "brand", "shopify_sku", "shopify_title", "vendor",
            "current_price", "current_compare_at_price", "product_handle",
        ])
        w.writeheader()
        w.writerows(not_covered)

    print(f"\n✓ Scanned {n_total} variants total", file=sys.stderr)
    print(f"  Brand-tagged (Stiga/Mountfield/Atco): {n_brand}", file=sys.stderr)
    print(f"  Matched (covered by sync):            {n_brand - n_not_covered}", file=sys.stderr)
    print(f"  Not covered:                          {n_not_covered}", file=sys.stderr)

    from collections import Counter
    by_brand = Counter(r["brand"] for r in not_covered)
    misses = sum(1 for r in not_covered if r["suspected_match_miss"] == "yes")
    print(f"    suspected matcher misses (Stiga code in SKU/title): {misses}", file=sys.stderr)
    print(f"    likely out-of-sheet (no Stiga code overlap):        {n_not_covered - misses}", file=sys.stderr)
    for brand, count in sorted(by_brand.items()):
        print(f"    {brand:12s} {count}", file=sys.stderr)
    print(f"\n  → {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
