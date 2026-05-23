#!/usr/bin/env python3
"""One-shot fixup for the 29 matcher-miss Shopify variants surfaced by
``stiga_brand_coverage_report.py``.

Bypasses ``lookups/matches_stiga.csv`` entirely — the matches file enforces a 1:1
sku_a→sku_b mapping, but several Stiga supplier codes legitimately map to
TWO Shopify variants (Mountfield + Stiga rebadges). Instead we look up each
target Shopify SKU directly via the API and call ``decide_price`` per row.

Dry-run by default. Pass ``--apply`` to actually write.

Run from the repo root:

    PYTHONPATH=. pyenv exec python scripts/sku_matcher/_apply_stiga_miss_fixups.py [--apply]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from scripts.shopify_client import ShopifyClient
from scripts.sku_matcher.shopify_api import ShopifyAPI, ShopifyAPIError
from scripts.sku_matcher.price_stock_sync import (
    Decision,
    decide_price,
    fmt_price,
    _to_float,
    _variant_for_sku,
)


REPORT = "reports/stiga_brands_not_covered_2026-05-16.csv"
STIGA_SHEET = "workdir/sku-matcher/stiga_prices_normalized.csv"


# Shopify SKUs explicitly skipped — same SKU as a real match (would clobber
# the legitimate listing) or a suspicious title/description mismatch.
SKIP_SKUS = {
    "MF-2T0210483/M22-1": "different product (MTF 72 H) despite same supplier SKU base",
    # MF-2T2630483/M22 — clash with regular 1638H; one variant gets picked by
    # find_products_by_sku and we don't want to nuke the shop-soiled discount.
    # We disambiguate via title: skip only the SHOP SOILED one.
    "STIG-255127002/ST1-1": "title says SBL 327 V but supplier code is for BL 530 V — drift",
    "STIG-252422008/ST2":   "title says SHT 670 but supplier code is for HT 725 — drift",
}

# When a SKU returns multiple products, prefer the one whose title does NOT
# match this prefix — keeps shop-soiled / second-hand listings untouched.
SKIP_TITLE_PREFIXES = (
    "SHOP SOILED",
    "**SHOP SOILED**",
)


def _load_stiga_prices() -> dict:
    """Return supplier_code (upper) → (new_price, rrp) from the normalized sheet."""
    out = {}
    with open(STIGA_SHEET, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            code = (r["Product Code"] or "").strip().upper()
            if not code:
                continue
            out[code] = (
                _to_float(r["Sell Price inc VAT"]),
                _to_float(r["RRP inc VAT"]),
            )
    return out


def _load_misses() -> list:
    rows = []
    with open(REPORT, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["suspected_match_miss"] != "yes":
                continue
            rows.append({
                "shopify_sku": r["shopify_sku"].strip(),
                "shopify_title": r["shopify_title"],
                "brand": r["brand"],
                "stiga_supplier_code": r["stiga_supplier_code"].strip().upper(),
                "stiga_supplier_desc": r["stiga_supplier_desc"],
            })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="Actually write the updates (default: dry-run)")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    suffix = "_apply" if "--apply" in sys.argv else ""
    ap.add_argument("--out", default=f"workdir/shopify-ops/stiga_miss_fixups_{today}{suffix}.csv")
    ap.add_argument("--log-file", default=f"workdir/shopify-ops/stiga_miss_fixups_{today}{suffix}.jsonl")
    args = ap.parse_args()

    prices = _load_stiga_prices()
    misses = _load_misses()
    print(f"misses to consider: {len(misses)}", file=sys.stderr)
    print(f"mode: {'LIVE — WRITES PRICES' if args.apply else 'DRY-RUN'}", file=sys.stderr)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)

    n_applied = 0
    n_would_apply = 0
    n_skipped = 0
    skip_reasons: dict[str, int] = {}

    csv_rows = []
    with ShopifyClient() as client:
        api = ShopifyAPI(client=client)

        # Currency assertion.
        shop = client.execute("{ shop { name currencyCode } }")
        if shop["shop"]["currencyCode"] != "GBP":
            sys.exit(f"❌ store currency is {shop['shop']['currencyCode']}, not GBP")
        print(f"  Shopify store: {shop['shop']['name']}\n", file=sys.stderr)

        with open(args.log_file, "w", encoding="utf-8") as log_fh:
            for m in misses:
                sku = m["shopify_sku"]
                code = m["stiga_supplier_code"]
                title = m["shopify_title"]
                entry = {
                    "shopify_sku": sku,
                    "shopify_title": title,
                    "brand": m["brand"],
                    "stiga_supplier_code": code,
                    "stiga_supplier_desc": m["stiga_supplier_desc"],
                    "dry_run": not args.apply,
                }

                # Explicit skip list.
                if sku in SKIP_SKUS:
                    reason = SKIP_SKUS[sku]
                    entry["action"] = "skip"
                    entry["reason"] = reason
                    skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                    n_skipped += 1
                    csv_rows.append(_flatten(entry))
                    log_fh.write(json.dumps(entry) + "\n")
                    continue

                new_price, rrp = prices.get(code, (None, None))
                if new_price is None:
                    entry["action"] = "skip"
                    entry["reason"] = "stiga_code_not_in_sheet"
                    skip_reasons["stiga_code_not_in_sheet"] = skip_reasons.get("stiga_code_not_in_sheet", 0) + 1
                    n_skipped += 1
                    csv_rows.append(_flatten(entry))
                    log_fh.write(json.dumps(entry) + "\n")
                    continue

                try:
                    products = api.find_products_by_sku(sku)
                except ShopifyAPIError as e:
                    entry["action"] = "skip"
                    entry["reason"] = f"shopify_lookup_failed: {e}"
                    n_skipped += 1
                    csv_rows.append(_flatten(entry))
                    log_fh.write(json.dumps(entry) + "\n")
                    continue

                if not products:
                    entry["action"] = "skip"
                    entry["reason"] = "shopify_not_found"
                    n_skipped += 1
                    csv_rows.append(_flatten(entry))
                    log_fh.write(json.dumps(entry) + "\n")
                    continue

                # Disambiguate when multiple products share the SKU. Prefer the
                # one whose title doesn't start with a skip prefix
                # (e.g. SHOP SOILED). If all match the skip prefix, skip.
                candidates = [p for p in products
                              if not any(p.title.startswith(pref) for pref in SKIP_TITLE_PREFIXES)]
                if not candidates:
                    entry["action"] = "skip"
                    entry["reason"] = "only_skip_prefix_variants_found"
                    skip_reasons["only_skip_prefix_variants_found"] = \
                        skip_reasons.get("only_skip_prefix_variants_found", 0) + 1
                    n_skipped += 1
                    csv_rows.append(_flatten(entry))
                    log_fh.write(json.dumps(entry) + "\n")
                    continue
                product = candidates[0]
                if len(candidates) > 1:
                    entry["multiple_candidates_warning"] = [
                        {"id": p.id, "title": p.title} for p in candidates
                    ]

                variant = _variant_for_sku(product, sku)
                if variant is None:
                    entry["action"] = "skip"
                    entry["reason"] = "variant_with_sku_not_in_product"
                    n_skipped += 1
                    csv_rows.append(_flatten(entry))
                    log_fh.write(json.dumps(entry) + "\n")
                    continue

                entry["shopify_product_id"] = product.id
                entry["shopify_variant_id"] = variant.id
                entry["product_title"] = product.title

                decision = decide_price(
                    workbook_inc_vat=new_price,
                    current_shopify_price=_to_float(variant.price),
                    current_compare_at=_to_float(variant.compare_at_price),
                    max_multiplier=2.0,
                    allow_downward=True,
                    workbook_rrp=rrp,
                )
                entry["decision"] = asdict(decision)

                if decision.action != "apply":
                    n_skipped += 1
                    skip_reasons[decision.reason] = skip_reasons.get(decision.reason, 0) + 1
                    csv_rows.append(_flatten(entry))
                    log_fh.write(json.dumps(entry) + "\n")
                    continue

                if not args.apply:
                    entry["action"] = "would_apply"
                    n_would_apply += 1
                    csv_rows.append(_flatten(entry))
                    log_fh.write(json.dumps(entry) + "\n")
                    continue

                fields = {"price": fmt_price(decision.detail["workbook"])}
                if decision.detail.get("set_compare_at"):
                    fields["compareAtPrice"] = fmt_price(decision.detail["new_compare_at"])
                elif decision.detail.get("bump_compare_at"):
                    fields["compareAtPrice"] = fmt_price(decision.detail["workbook"])

                try:
                    ok, err = api.update_variant_fields(product.id, variant.id, fields)
                except Exception as e:                                      # noqa: BLE001
                    ok, err = False, f"unexpected: {e}"

                if ok:
                    entry["action"] = "applied"
                    n_applied += 1
                else:
                    entry["action"] = "apply_failed"
                    entry["error"] = err
                    n_skipped += 1
                    skip_reasons["apply_failed"] = skip_reasons.get("apply_failed", 0) + 1
                csv_rows.append(_flatten(entry))
                log_fh.write(json.dumps(entry) + "\n")

    # Flat CSV report.
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        if csv_rows:
            w = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
            w.writeheader()
            w.writerows(csv_rows)

    print(f"\n{'=' * 60}")
    print(f"  applied:        {n_applied}")
    print(f"  would apply:    {n_would_apply}")
    print(f"  skipped:        {n_skipped}")
    for r, c in sorted(skip_reasons.items(), key=lambda kv: -kv[1]):
        print(f"    {r:50s} {c}")
    print(f"\n  Audit log:  {args.log_file}")
    print(f"  CSV report: {args.out}")
    return 0


def _flatten(entry: dict) -> dict:
    """Pull decision detail up into CSV-friendly flat columns."""
    d = entry.get("decision") or {}
    detail = d.get("detail") or {}
    return {
        "action": entry.get("action", ""),
        "brand": entry.get("brand", ""),
        "shopify_sku": entry.get("shopify_sku", ""),
        "product_title": entry.get("product_title", ""),
        "shopify_title": entry.get("shopify_title", ""),
        "stiga_supplier_code": entry.get("stiga_supplier_code", ""),
        "stiga_supplier_desc": entry.get("stiga_supplier_desc", ""),
        "current_price": detail.get("current", ""),
        "new_price": detail.get("workbook", ""),
        "delta": detail.get("delta", ""),
        "delta_pct": detail.get("delta_pct", ""),
        "old_compare_at": detail.get("old_compare_at", ""),
        "new_compare_at": detail.get("new_compare_at", ""),
        "decision_reason": d.get("reason", "") or entry.get("reason", ""),
        "dry_run": entry.get("dry_run", ""),
    }


if __name__ == "__main__":
    sys.exit(main())
