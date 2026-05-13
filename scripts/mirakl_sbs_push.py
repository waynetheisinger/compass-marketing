"""
Operator-agnostic Mirakl SBS pusher — pushes the SPECTRUM SBS catalogue to any
Mirakl marketplace (Kingfisher/B&Q, Tesco, The Range).

Usage:
    # Dry run — generate the CSVs but do not submit
    python3 scripts/mirakl_sbs_push.py --operator KINGFISHER --dry-run

    # Single SKU canary push (recommended for first-time category)
    python3 scripts/mirakl_sbs_push.py --operator KINGFISHER --skus SBS460CLM

    # Full 15-SKU push
    python3 scripts/mirakl_sbs_push.py --operator KINGFISHER --skus all

    # Skip the offers file (only catalogue update)
    python3 scripts/mirakl_sbs_push.py --operator KINGFISHER --skus all --no-offers

    # Use cached catalogue (don't re-fetch from Shopify/Amazon)
    python3 scripts/mirakl_sbs_push.py --operator KINGFISHER --skus all --use-cache

What it does:
  1. Loads SBS catalogue (Shopify + Amazon SP-API merge)
  2. Resolves operator config (default KINGFISHER)
  3. Builds /products/imports CSV (semicolon-delimited)
  4. Submits via the Mirakl API (or saves only if --dry-run)
  5. Polls /products/imports/{id} until COMPLETE
  6. Reports any transformation_error_report rows (the per-line errors that
     tell us which attributes are missing / malformed — primary feedback loop
     for the iterative attribute schema discovery)
  7. If --no-offers not set, repeats steps 3-6 for /offers/imports

Output files (always written, even on dry-run):
    /tmp/sbs_<operator>_products.csv
    /tmp/sbs_<operator>_offers.csv

Imports already submitted (e.g. SBS240CPHT and SBS40CB submitted manually via
the Base portal on 2026-05-07) are upserts — same shop_sku → Mirakl updates the
existing row rather than duplicating.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import time
from typing import Any

# Ensure imports work whether invoked from repo root or scripts/
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from sbs_catalogue import load_catalogue, SBSProduct
from mirakl_operators import OPERATORS, OperatorConfig, build_product_row, build_offer_row
from mirakl_client import MiraklClient


# ---------------------------------------------------------------------------
# CSV building
# ---------------------------------------------------------------------------

# Column order for products file. Mandatory columns always come first; the rest
# are unioned across all SKUs. Mirakl is lenient about extra columns but strict
# about column names matching the operator's expected attribute codes.
_PRODUCT_PRIORITY_COLS = [
    "category", "shop_sku", "name", "ean", "image_main_1", "Body Copy",
    "Acquisition brand", "Core_Pack quantity", "Core_Pack type", "Guarantee",
    "reach_verified", "contains_wood", "fsc_pecl_certified", "Core_Product type",
    "Product_length", "Product_weight", "Product_width", "Product_height",
    "Cordless", "Batteries_supplied", "Battery_chemistry", "WEEE_regulated",
    "Tech_Rechargeable", "USB_Type_C_charger_included", "USB_power_delivery",
    "Maximum_charging_wattage", "Minimum_charging_wattage",
    "Power_voltage_supply",
]

_OFFER_PRIORITY_COLS = [
    "shop-sku", "product-id", "product-id-type", "description",
    "internal-description", "price", "quantity", "min-quantity-alert",
    "state-code", "available-start-date", "available-end-date",
    "discount-price", "discount-start-date", "discount-end-date",
    "leadtime-to-ship", "update-delete", "logistic-class",
]


def _ordered_columns(rows: list[dict[str, str]], priority: list[str]) -> list[str]:
    """Return column order: priority columns first, then any extras seen in rows."""
    seen = set()
    cols: list[str] = []
    for c in priority:
        if any(c in r for r in rows):
            cols.append(c)
            seen.add(c)
    for r in rows:
        for k in r:
            if k not in seen:
                cols.append(k)
                seen.add(k)
    return cols


def to_csv(rows: list[dict[str, str]], priority: list[str], delimiter: str = ";") -> str:
    """Render rows to a Mirakl-friendly semicolon-delimited CSV string."""
    cols = _ordered_columns(rows, priority)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, delimiter=delimiter,
                            quoting=csv.QUOTE_ALL, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({c: r.get(c, "") for c in cols})
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Mirakl submit + poll
# ---------------------------------------------------------------------------

def submit_products(client: MiraklClient, csv_text: str) -> int:
    """Submit a products CSV to /products/imports. Returns the import_id."""
    files = {"file": ("products.csv", csv_text.encode("utf-8"), "text/csv")}
    base = client._base_url
    resp = client._session.post(
        f"{base}/products/imports",
        files=files,
    )
    resp.raise_for_status()
    body = resp.json()
    iid = body.get("import_id")
    if not iid:
        raise RuntimeError(f"Unexpected /products/imports response: {body}")
    return iid


def submit_offers(client: MiraklClient, csv_text: str) -> int:
    """Submit an offers CSV to /offers/imports. Returns the import_id."""
    files = {"file": ("offers.csv", csv_text.encode("utf-8"), "text/csv")}
    base = client._base_url
    resp = client._session.post(
        f"{base}/offers/imports",
        files=files,
    )
    resp.raise_for_status()
    body = resp.json()
    iid = body.get("import_id")
    if not iid:
        raise RuntimeError(f"Unexpected /offers/imports response: {body}")
    return iid


def poll_until_complete(client: MiraklClient, kind: str, import_id: int,
                        timeout_seconds: int = 120, poll_seconds: int = 5) -> dict:
    """
    Poll an import until either:
      (a) status is terminal (COMPLETE / FAILED / CANCELLED), or
      (b) the *transformation* phase is done — i.e. transform_lines_read > 0
          and read == in_success + in_error.

    Some Mirakl operators (Kingfisher) leave imports in SENT for hours/days
    while their catalogue team processes them. The transformation feedback
    (which fields failed validation) is available within seconds of
    submission, and that's what we need for iterative schema discovery.
    """
    base = client._base_url
    sess = client._session
    deadline = time.time() + timeout_seconds
    last = None
    while time.time() < deadline:
        path = f"/{kind}/imports/{import_id}"
        r = sess.get(f"{base}{path}")
        r.raise_for_status()
        d = r.json()
        last = d
        status = d.get("import_status")
        read    = d.get("transform_lines_read") or 0
        ok      = d.get("transform_lines_in_success") or 0
        err     = d.get("transform_lines_in_error") or 0
        print(f"  {kind} import {import_id}: status={status} "
              f"read={read} ok={ok} err={err}")
        if status in ("COMPLETE", "FAILED", "CANCELLED"):
            return d
        # Transformation phase done — no need to wait for catalogue integration
        if read > 0 and read == ok + err:
            print(f"  → transformation phase complete; not waiting for catalogue integration")
            return d
        time.sleep(poll_seconds)
    print(f"  [timeout {timeout_seconds}s] returning last poll result")
    return last or {}


def fetch_transformation_errors(client: MiraklClient, kind: str, import_id: int) -> str | None:
    """Fetch the transformation_error_report for a products/offers import."""
    base = client._base_url
    paths = [
        f"/{kind}/imports/{import_id}/transformation_error_report",
        f"/{kind}/imports/{import_id}/transformation-error-report",
        f"/{kind}/imports/{import_id}/error_report",
    ]
    for p in paths:
        r = client._session.get(f"{base}{p}")
        if r.status_code == 200 and r.content:
            return r.content.decode("utf-8", errors="replace")
    return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run(operator_name: str,
        skus: list[str] | None,
        dry_run: bool,
        push_offers: bool,
        use_cache: bool,
        refresh_cache: bool) -> int:
    op = OPERATORS.get(operator_name)
    if not op or not op.channel:
        print(f"ERROR: operator '{operator_name}' is not configured "
              f"(channel='{op.channel if op else ''}'). "
              f"Populate mirakl_operators.{operator_name}.", file=sys.stderr)
        return 2

    print(f"Operator: {op.name}  (channel: {op.channel})")
    print(f"Dry run: {dry_run}  Offers: {push_offers}\n")

    # ---- 1. Load catalogue ----
    cat = load_catalogue(refresh=refresh_cache, use_cache=use_cache)
    if skus and skus != ["all"]:
        wanted = set(s.strip() for s in skus)
        unknown = wanted - set(cat)
        if unknown:
            print(f"ERROR: unknown SKUs: {sorted(unknown)}", file=sys.stderr)
            return 2
        cat = {k: v for k, v in cat.items() if k in wanted}

    print(f"Catalogue: {len(cat)} SKU(s)")
    for sku, p in cat.items():
        print(f"  {sku:22s} {p.product_type:22s} EAN={p.ean}  £{p.price_gbp:.2f}  stock={p.stock}")

    # ---- 2. Build product rows ----
    print(f"\nBuilding product rows…")
    product_rows = []
    for sku, p in cat.items():
        try:
            row = build_product_row(op, p)
        except ValueError as e:
            print(f"  [SKIP {sku}] {e}", file=sys.stderr)
            continue
        product_rows.append(row)

    products_csv = to_csv(product_rows, _PRODUCT_PRIORITY_COLS)
    products_path = f"/tmp/sbs_{op.name.lower()}_products.csv"
    with open(products_path, "w") as f:
        f.write(products_csv)
    print(f"  {len(product_rows)} rows → {products_path}")
    print(f"  Columns ({len(_ordered_columns(product_rows, _PRODUCT_PRIORITY_COLS))}): "
          f"{_ordered_columns(product_rows, _PRODUCT_PRIORITY_COLS)}")

    # ---- 3. Build offer rows ----
    offer_rows = []
    if push_offers:
        print(f"\nBuilding offer rows…")
        for sku, p in cat.items():
            offer_rows.append(build_offer_row(op, p))
        offers_csv = to_csv(offer_rows, _OFFER_PRIORITY_COLS)
        offers_path = f"/tmp/sbs_{op.name.lower()}_offers.csv"
        with open(offers_path, "w") as f:
            f.write(offers_csv)
        print(f"  {len(offer_rows)} rows → {offers_path}")

    if dry_run:
        print(f"\n[DRY RUN] No submission. Inspect the CSVs above.")
        return 0

    # ---- 4. Submit products ----
    print(f"\nSubmitting products to {op.name}…")
    client = MiraklClient(op.name)
    pid = submit_products(client, products_csv)
    print(f"  products import_id: {pid}")

    pdetail = poll_until_complete(client, "products", pid)
    if pdetail.get("transform_lines_in_error", 0) > 0:
        print(f"\n⚠️  Products import has {pdetail['transform_lines_in_error']} transformation errors")
        report = fetch_transformation_errors(client, "products", pid)
        if report:
            err_path = f"/tmp/sbs_{op.name.lower()}_products_errors_{pid}.xml"
            with open(err_path, "w") as f:
                f.write(report)
            print(f"  Error report saved → {err_path}")
            print(f"  First 2000 chars:\n{report[:2000]}")

    if not push_offers:
        print(f"\nDone (products only).")
        return 0

    # ---- 5. Submit offers ----
    print(f"\nSubmitting offers to {op.name}…")
    oid = submit_offers(client, offers_csv)
    print(f"  offers import_id: {oid}")

    odetail = poll_until_complete(client, "offers", oid)
    if odetail.get("transform_lines_in_error", 0) > 0:
        print(f"\n⚠️  Offers import has {odetail['transform_lines_in_error']} transformation errors")
        report = fetch_transformation_errors(client, "offers", oid)
        if report:
            err_path = f"/tmp/sbs_{op.name.lower()}_offers_errors_{oid}.xml"
            with open(err_path, "w") as f:
                f.write(report)
            print(f"  Error report saved → {err_path}")
            print(f"  First 2000 chars:\n{report[:2000]}")

    print(f"\nDone.")
    return 0


def _cli() -> int:
    ap = argparse.ArgumentParser(description="Operator-agnostic Mirakl SBS pusher")
    ap.add_argument("--operator", default="KINGFISHER",
                    help="Operator key from mirakl_operators.OPERATORS (default: KINGFISHER)")
    ap.add_argument("--skus", default="all",
                    help="Comma-separated SKUs, or 'all' (default: all)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Build CSVs to /tmp but do not submit")
    ap.add_argument("--no-offers", action="store_true",
                    help="Skip the /offers/imports submission")
    ap.add_argument("--use-cache", action="store_true",
                    help="Read /tmp/sbs_catalogue.json instead of re-fetching")
    ap.add_argument("--refresh", action="store_true",
                    help="Force re-fetch catalogue and overwrite cache")
    args = ap.parse_args()

    skus = None if args.skus == "all" else [s.strip() for s in args.skus.split(",")]
    skus = skus or ["all"]

    return run(
        operator_name=args.operator.upper(),
        skus=skus,
        dry_run=args.dry_run,
        push_offers=not args.no_offers,
        use_cache=args.use_cache,
        refresh_cache=args.refresh,
    )


if __name__ == "__main__":
    sys.exit(_cli())
