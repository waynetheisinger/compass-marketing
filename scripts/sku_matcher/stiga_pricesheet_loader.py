#!/usr/bin/env python3
"""Normalize Stiga's supplier pricesheet into inputs the sku_matcher pipeline expects.

The Stiga CSV has a leading blank row, a multi-line header (`Suggested\\nPromo Inc VAT`),
section dividers (e.g. `RIDE ON,,,,,,`), and currency-formatted prices (`£3,249.00`).
This script emits two clean CSVs:

  * --out-normalized  → rows with `New Promo Inc vat` populated; columns shaped
                        for `price_stock_sync.py` (Product Code, Product
                        Description, Sell Price inc VAT, RRP inc VAT,
                        Quantity in Stock [empty]).
  * --out-review      → rows where `New Promo Inc vat` is blank (e.g. VISTA
                        ROBOTS / ROBOTS sections). For team triage — probably
                        SKUs we don't stock.

Run from the repo root:

    PYTHONPATH=. pyenv exec python scripts/sku_matcher/stiga_pricesheet_loader.py \\
        workdir/sku-matcher/Stiga-Pricesheet-Sheet1.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

CURRENCY_RE = re.compile(r"[£,\s]")


def parse_currency(raw) -> Optional[float]:
    """Convert `'£3,249.00'` → 3249.0. Empty/NaN → None."""
    if raw is None or pd.isna(raw):
        return None
    s = str(raw).strip()
    if not s:
        return None
    cleaned = CURRENCY_RE.sub("", s)
    try:
        return float(cleaned)
    except ValueError:
        return None


def looks_like_sku(value: str) -> bool:
    """Real Stiga SKUs have no spaces and are short digit-heavy codes (often
    with a slash suffix like ``/ST1M``). Section dividers are human-readable
    text — e.g. ``RIDE ON``, ``Mountfield 20v Handheld``, ``VISTA ROBOTS -
    Ref Free & 10Yr data`` — and contain spaces.
    """
    if not value:
        return False
    if " " in value:
        return False
    return any(c.isdigit() for c in value)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="workdir/sku-matcher/Stiga-Pricesheet-Sheet1.csv")
    ap.add_argument("--out-normalized", default="workdir/sku-matcher/stiga_prices_normalized.csv")
    ap.add_argument("--out-review", default="workdir/sku-matcher/stiga_needs_review.csv")
    ap.add_argument("--skiprows", type=int, default=2,
                    help="Leading rows to skip before the header (default 2)")
    args = ap.parse_args()

    src = Path(args.source)
    if not src.exists():
        print(f"❌ source not found: {src}", file=sys.stderr)
        return 1

    df = pd.read_csv(src, skiprows=args.skiprows, header=0,
                     dtype=str, keep_default_na=False, na_values=[""])

    # Header for `Suggested Promo Inc VAT` contains an embedded newline in
    # the source. Normalize all column names: collapse whitespace and lower-
    # case-key the ones we want, while keeping the human-readable originals
    # in a mapping for clarity below.
    df.columns = [re.sub(r"\s+", " ", c).strip() for c in df.columns]

    expected = {
        "sku": "SKU",
        "desc": "Description",
        "rrp": "RRP inc VAT",
        "suggested": "Suggested Promo Inc VAT",
        "new_promo": "New Promo Inc vat",
    }
    missing = [v for v in expected.values() if v not in df.columns]
    if missing:
        print(f"❌ expected columns missing from source: {missing}\n"
              f"   found: {list(df.columns)}", file=sys.stderr)
        return 1

    # Strip whitespace on string cells.
    for c in df.select_dtypes(include="object").columns:
        df[c] = df[c].astype(str).str.strip()
        df[c] = df[c].replace({"nan": ""})

    # Walk rows, tracking the most-recent section divider for context in the
    # review file.
    section = ""
    normalized_rows = []
    review_rows = []
    skipped_blank = 0
    skipped_divider = 0

    for _, row in df.iterrows():
        sku_raw = row[expected["sku"]]
        desc_raw = row[expected["desc"]]
        rrp_raw = row[expected["rrp"]]
        suggested_raw = row[expected["suggested"]]
        new_promo_raw = row[expected["new_promo"]]

        # Wholly empty row.
        if not sku_raw and not desc_raw:
            skipped_blank += 1
            continue

        # Section divider: text in the SKU column, nothing on the right.
        # Stiga sometimes puts the section name in the SKU column
        # (`RIDE ON,,,,,,`) and sometimes elsewhere. Treat any row whose SKU
        # cell isn't SKU-shaped as a divider.
        if not looks_like_sku(sku_raw):
            section = sku_raw or section
            skipped_divider += 1
            continue

        rrp = parse_currency(rrp_raw)
        suggested = parse_currency(suggested_raw)
        new_promo = parse_currency(new_promo_raw)

        if new_promo is None:
            review_rows.append({
                "Product Code": sku_raw,
                "Product Description": desc_raw,
                "Section": section,
                "RRP inc VAT": f"{rrp:.2f}" if rrp is not None else "",
                "Suggested Promo Inc VAT": f"{suggested:.2f}" if suggested is not None else "",
                "Reason": "no new promo price provided",
            })
            continue

        normalized_rows.append({
            "Product Code": sku_raw,
            "Product Description": desc_raw,
            "Sell Price inc VAT": f"{new_promo:.2f}",
            "RRP inc VAT": f"{rrp:.2f}" if rrp is not None else "",
            "Quantity in Stock": "",
        })

    # Write outputs.
    out_norm = Path(args.out_normalized)
    out_rev = Path(args.out_review)

    with out_norm.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "Product Code", "Product Description",
            "Sell Price inc VAT", "RRP inc VAT", "Quantity in Stock",
        ])
        w.writeheader()
        w.writerows(normalized_rows)

    with out_rev.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "Product Code", "Product Description", "Section",
            "RRP inc VAT", "Suggested Promo Inc VAT", "Reason",
        ])
        w.writeheader()
        w.writerows(review_rows)

    print(f"✓ {len(normalized_rows)} rows → {out_norm}")
    print(f"✓ {len(review_rows)} rows → {out_rev}")
    print(f"  ({skipped_divider} section dividers skipped, "
          f"{skipped_blank} blank rows skipped)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
