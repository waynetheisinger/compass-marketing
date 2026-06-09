#!/usr/bin/env python3
"""
Push a (hand-edited) products or offers CSV file to a Mirakl operator.

The companion to scripts/mirakl_bq_to_tesco.py: generate a CSV, review and
hand-edit it (baseColour, fixes, etc.), then submit it here. Reuses the
submission + transformation-report helpers from mirakl_sbs_push.

Run from the repo root.

    # See what would be sent (no submission)
    python scripts/mirakl_push_products.py --operator TESCO \
        --file workdir/mirakl-tesco/bq_active_to_tesco_products.csv --dry-run

    # Submit the products CSV and print Tesco's transformation report
    python scripts/mirakl_push_products.py --operator TESCO \
        --file workdir/mirakl-tesco/bq_active_to_tesco_products.csv

    # Submit an offers CSV
    python scripts/mirakl_push_products.py --operator TESCO --kind offers \
        --file workdir/mirakl-tesco/bq_active_to_tesco_offers.csv
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts.mirakl_client import MiraklClient          # noqa: E402
from scripts.mirakl_operators import OPERATORS          # noqa: E402
from scripts.mirakl_sbs_push import (                   # noqa: E402
    submit_products, submit_offers, poll_until_complete, fetch_transformation_errors,
)


def _read(path: str, delimiter: str = ";"):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    rows = list(csv.reader(text.splitlines(), delimiter=delimiter))
    if not rows:
        raise SystemExit(f"{path} is empty")
    return rows[0], rows[1:], text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Push a CSV file to a Mirakl operator")
    ap.add_argument("--operator", "-o", required=True, help="Operator key (e.g. TESCO)")
    ap.add_argument("--file", "-f", required=True, help="Path to the CSV to submit")
    ap.add_argument("--kind", choices=["products", "offers"], default="products")
    ap.add_argument("--dry-run", action="store_true",
                    help="Validate + summarise the file; do not submit")
    ap.add_argument("--delimiter", default=";")
    ap.add_argument("--timeout", type=int, default=180,
                    help="Seconds to poll for the transformation phase (default 180)")
    args = ap.parse_args(argv)

    name = args.operator.upper()
    op = OPERATORS.get(name)
    if not op:
        print(f"ERROR: unknown operator '{name}'", file=sys.stderr)
        return 2
    if not os.path.exists(args.file):
        print(f"ERROR: file not found: {args.file}", file=sys.stderr)
        return 2

    header, body, text = _read(args.file, args.delimiter)
    print(f"Operator: {name}  ({op.channel or '(channel unset)'})")
    print(f"File:     {args.file}")
    print(f"Kind:     {args.kind}")
    print(f"Rows:     {len(body)}   Columns: {len(header)}")
    print(f"Columns:  {header}")

    # Flag obviously-empty required-ish cells so a half-edited file is caught
    # before submission (e.g. blank baseColour the generator left for review).
    blanks = {}
    for col_i, col in enumerate(header):
        empty = sum(1 for r in body if col_i >= len(r) or not r[col_i].strip())
        if empty:
            blanks[col] = empty
    if blanks:
        print("\nColumns with blank cells (fill before push if Tesco requires them):")
        for col, n in sorted(blanks.items(), key=lambda x: -x[1]):
            print(f"  {col}: {n} blank")

    if args.dry_run:
        print("\n[DRY RUN] Not submitting.")
        return 0

    if not op.channel and args.kind == "offers":
        print("ERROR: offers require a channel code; set MIRAKL_"
              f"{name}_CHANNEL.", file=sys.stderr)
        return 2

    client = MiraklClient(name)
    print(f"\nSubmitting {args.kind}…")
    iid = (submit_products if args.kind == "products" else submit_offers)(client, text)
    print(f"  import_id: {iid}")
    poll_until_complete(client, args.kind, iid, timeout_seconds=args.timeout)
    report = fetch_transformation_errors(client, args.kind, iid)
    if report:
        print("\nTransformation report:")
        print(report[:4000])
    else:
        print("\nNo transformation error report (clean, or none returned).")
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
