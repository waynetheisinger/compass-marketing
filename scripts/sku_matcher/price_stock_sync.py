#!/usr/bin/env python3
"""Apply Andrew's workbook prices + check Shopify stock against expected.

For each row of Andrew's `workdir/sku-matcher/stockPricesAndSkus.csv`, find the matched Shopify
variant (via the lookups/matches.csv produced by `matcher.py`) and:

  1. **Price** — raise the Shopify variant's price to the workbook's
     `Sell Price inc VAT`, **strictly upward only**. Never lowers.
  2. **Stock** — compare Shopify's current stock to the workbook's
     `Quantity in Stock` with tolerance `±max(5 units, 10%)`. Logs every
     mismatch. **Never writes inventory.**

Dry-run by default. Pass `--apply` to actually write prices. All decisions
land in a JSONL audit log and a flat CSV report under `workdir/shopify-ops/`.

Run from the repo root — credentials come from `.env`:

    PYTHONPATH=. pyenv exec python scripts/sku_matcher/price_stock_sync.py \\
        lookups/matches.csv workdir/sku-matcher/stockPricesAndSkus.csv [--apply]

Re-running `--apply` immediately is a safe no-op: the upward-only check is
self-idempotent.
"""

import argparse
import json
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List

import pandas as pd
from tqdm import tqdm

from scripts.shopify_client import ShopifyClient
from scripts.sku_matcher.shopify_api import (
    ShopifyAPI,
    ShopifyAPIError,
    ShopifyProduct,
    ShopifyVariant,
)
from scripts.sku_matcher.shopify_io import (
    append_log,
    load_state,
    save_state,
)

# ---------------------------------------------------------------------------
# Dataclasses — pure data, no behaviour.
# ---------------------------------------------------------------------------

@dataclass
class WorkbookRow:
    product_code: str          # original case, post-strip
    description: str
    price_inc_vat: Optional[float]
    qty_in_stock: Optional[int]
    rrp_inc_vat: Optional[float] = None   # only populated when --rrp-col is set


@dataclass
class MatchRow:
    sku_a: str                 # original case from lookups/matches.csv
    title_a: str
    sku_b: str                 # Shopify SKU
    title_b: str
    score: float
    method: str


@dataclass
class Decision:
    action: str                # "apply", "ok", "skip", "mismatch"
    reason: str = ""           # short code, e.g. "not_upward", "shopify_price_missing"
    detail: dict = field(default_factory=dict)   # numbers for the log


# ---------------------------------------------------------------------------
# Decision rules — pure functions, REPL-testable without API contact.
# ---------------------------------------------------------------------------

def decide_price(
    workbook_inc_vat: Optional[float],
    current_shopify_price: Optional[float],
    current_compare_at: Optional[float],
    max_multiplier: float,
    absolute_floor: float = 1.0,
    allow_downward: bool = False,
    workbook_rrp: Optional[float] = None,
) -> Decision:
    """Decide whether to change the variant's price (and optionally compareAtPrice).

    Default behaviour is upward-only (Andrew's workflow); pass
    ``allow_downward=True`` for supplier promo updates that may also drop
    prices. The ``max_multiplier`` guard is enforced in both directions to
    catch decimal-place mistakes either way.

    ``workbook_rrp``: when provided, ``compareAtPrice`` is overwritten with it
    (the workbook is treated as authoritative for RRP). When omitted, falls
    back to bump-up-if-below-new-price behaviour so the storefront never shows
    "was £X / now £Y" with Y > X.
    """
    if workbook_inc_vat is None or workbook_inc_vat <= 0:
        return Decision("skip", "workbook_price_invalid",
                        {"workbook": workbook_inc_vat})
    if workbook_inc_vat < absolute_floor:
        return Decision("skip", "workbook_price_below_floor",
                        {"workbook": workbook_inc_vat, "floor": absolute_floor})
    if current_shopify_price is None or current_shopify_price <= 0:
        return Decision("skip", "shopify_price_missing",
                        {"current": current_shopify_price,
                         "workbook": workbook_inc_vat})

    # No-op short-circuit: nothing on price AND nothing on RRP.
    price_unchanged = abs(workbook_inc_vat - current_shopify_price) < 0.005
    rrp_unchanged = (
        workbook_rrp is None
        or (current_compare_at is not None
            and abs(workbook_rrp - current_compare_at) < 0.005)
    )
    if price_unchanged and rrp_unchanged:
        return Decision("skip", "no_change",
                        {"current": current_shopify_price,
                         "workbook": workbook_inc_vat,
                         "workbook_rrp": workbook_rrp,
                         "current_compare_at": current_compare_at})

    if not allow_downward and workbook_inc_vat < current_shopify_price:
        return Decision("skip", "not_upward",
                        {"current": current_shopify_price,
                         "workbook": workbook_inc_vat})

    ratio = workbook_inc_vat / current_shopify_price
    if ratio > max_multiplier:
        return Decision("skip", "suspicious_large_increase",
                        {"current": current_shopify_price,
                         "workbook": workbook_inc_vat,
                         "multiplier_cap": max_multiplier,
                         "actual_multiplier": round(ratio, 3)})
    if ratio < 1 / max_multiplier:
        return Decision("skip", "suspicious_large_decrease",
                        {"current": current_shopify_price,
                         "workbook": workbook_inc_vat,
                         "multiplier_cap": max_multiplier,
                         "actual_multiplier": round(ratio, 3)})

    delta = workbook_inc_vat - current_shopify_price
    detail = {
        "current": current_shopify_price,
        "workbook": workbook_inc_vat,
        "delta": round(delta, 2),
        "delta_pct": round(delta / current_shopify_price * 100, 1),
    }

    if workbook_rrp is not None:
        # Workbook is authoritative for RRP — overwrite compareAtPrice when it
        # disagrees with what's currently on the variant.
        if current_compare_at is None or abs(workbook_rrp - current_compare_at) >= 0.005:
            detail["set_compare_at"] = True
            detail["old_compare_at"] = current_compare_at
            detail["new_compare_at"] = workbook_rrp
    elif current_compare_at and workbook_inc_vat > current_compare_at:
        # Legacy bump-up-only: keep the strike-through coherent when the new
        # price has overtaken the existing "was" price.
        detail["bump_compare_at"] = True
        detail["old_compare_at"] = current_compare_at
        detail["new_compare_at"] = workbook_inc_vat
    return Decision("apply", "", detail)


def decide_stock(
    expected_qty: Optional[int],
    actual_qty: Optional[int],
    tolerance_floor: int,
    tolerance_pct: float,
) -> Decision:
    """Decide whether Shopify's stock disagrees with the workbook beyond tolerance.

    Tolerance is `max(tolerance_floor, expected * tolerance_pct/100)` — tight
    for low-stock SKUs, proportional for high-stock.
    """
    if expected_qty is None:
        return Decision("skip", "workbook_qty_missing")
    if actual_qty is None:
        return Decision("skip", "shopify_inventory_unavailable",
                        {"expected": expected_qty})

    tolerance = max(tolerance_floor, abs(expected_qty) * tolerance_pct / 100)
    diff = actual_qty - expected_qty
    if abs(diff) <= tolerance:
        return Decision("ok", "", {
            "expected": expected_qty,
            "actual": actual_qty,
            "diff": diff,
            "tolerance": round(tolerance, 2),
        })
    return Decision("mismatch", "outside_tolerance", {
        "expected": expected_qty,
        "actual": actual_qty,
        "diff": diff,
        "tolerance": round(tolerance, 2),
    })


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _to_float(x) -> Optional[float]:
    if x is None or x == "" or pd.isna(x):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _to_int(x) -> Optional[int]:
    f = _to_float(x)
    return None if f is None else int(round(f))


def load_workbook_csv(
    path: str,
    code_col: str,
    desc_col: str,
    price_col: str,
    qty_col: str,
    rrp_col: Optional[str] = None,
) -> Dict[str, WorkbookRow]:
    """Load a workbook CSV (Andrew's or a supplier's). Keyed by upper-case code.

    Strips whitespace, forces SKU to string, drops rows with empty SKU,
    detects duplicate codes and marks them all unusable. When ``rrp_col`` is
    given, that column is parsed into ``WorkbookRow.rrp_inc_vat``.
    """
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    required = [code_col, desc_col, price_col, qty_col]
    if rrp_col:
        required.append(rrp_col)
    for col in required:
        if col not in df.columns:
            raise ValueError(
                f"Workbook CSV is missing column {col!r}. "
                f"Found: {list(df.columns)}"
            )

    # Strip whitespace on everything.
    for c in df.select_dtypes(include="object").columns:
        df[c] = df[c].str.strip()

    df = df[df[code_col].notna() & (df[code_col] != "")]

    # Detect duplicates so we can refuse to act on any of them.
    counts = df[code_col].str.upper().value_counts()
    dupes = set(counts[counts > 1].index)
    if dupes:
        print(
            f"⚠️  {len(dupes)} duplicate product code(s) in workbook — all rows will be skipped: {sorted(dupes)}",
            file=sys.stderr,
        )

    out: Dict[str, WorkbookRow] = {}
    for _, row in df.iterrows():
        code = row[code_col]
        key = code.upper()
        if key in dupes:
            # Skip duplicates entirely — the row's price/qty are ambiguous.
            continue
        out[key] = WorkbookRow(
            product_code=code,
            description=row[desc_col] or "",
            price_inc_vat=_to_float(row[price_col]),
            qty_in_stock=_to_int(row[qty_col]),
            rrp_inc_vat=_to_float(row[rrp_col]) if rrp_col else None,
        )
    return out


def load_matches(path: str, min_score: float) -> Dict[str, MatchRow]:
    """Load lookups/matches.csv keyed by **upper-case** `sku_a` (Andrew's code).

    Bails hard if the same `sku_a` maps to multiple `sku_b` values — that's
    impossible from a clean matcher run and signals a corrupted file.
    """
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    needed = {"sku_a", "title_a", "sku_b", "title_b", "score", "method"}
    missing = needed - set(df.columns)
    if missing:
        raise ValueError(f"lookups/matches.csv is missing columns: {missing}")

    for c in df.select_dtypes(include="object").columns:
        df[c] = df[c].str.strip()
    df = df[df["sku_a"].notna() & (df["sku_a"] != "")
            & df["sku_b"].notna() & (df["sku_b"] != "")]
    df["score"] = pd.to_numeric(df["score"], errors="coerce").fillna(0.0)

    # Detect inconsistent mappings.
    grouped = df.groupby(df["sku_a"].str.upper())["sku_b"].nunique()
    inconsistent = grouped[grouped > 1]
    if not inconsistent.empty:
        raise RuntimeError(
            "lookups/matches.csv contains inconsistent mappings (same sku_a → multiple sku_b): "
            f"{list(inconsistent.index)[:10]}"
        )

    below = df[df["score"] < min_score]
    if not below.empty:
        print(
            f"  {len(below)} match(es) below --min-score {min_score} — will be logged as 'unmatched'.",
            file=sys.stderr,
        )

    out: Dict[str, MatchRow] = {}
    for _, row in df.iterrows():
        if row["score"] < min_score:
            continue
        out[row["sku_a"].upper()] = MatchRow(
            sku_a=row["sku_a"], title_a=row["title_a"],
            sku_b=row["sku_b"], title_b=row["title_b"],
            score=float(row["score"]), method=row["method"],
        )
    return out


# ---------------------------------------------------------------------------
# Shopify formatting
# ---------------------------------------------------------------------------

def fmt_price(p: float) -> str:
    """Shopify accepts price as a string. Always 2dp, no thousands sep."""
    return f"{p:.2f}"


def _variant_for_sku(product: ShopifyProduct, sku: str) -> Optional[ShopifyVariant]:
    """Return the variant whose SKU matches (case-insensitive)."""
    sku_u = sku.strip().upper()
    for v in product.variants:
        if (v.sku or "").strip().upper() == sku_u:
            return v
    return None


# ---------------------------------------------------------------------------
# Per-row processing
# ---------------------------------------------------------------------------

def process_row(
    api: ShopifyAPI,
    wb_row: WorkbookRow,
    match_row: MatchRow,
    args,
) -> dict:
    """Look up the Shopify variant, decide, optionally apply, return a log dict."""
    entry = {
        "product_code": wb_row.product_code,
        "shopify_sku": match_row.sku_b,
        "shopify_title": match_row.title_b,
        "match_score": match_row.score,
        "match_method": match_row.method,
        "dry_run": not args.apply,
    }

    # Lookup
    try:
        products = api.find_products_by_sku(match_row.sku_b)
    except ShopifyAPIError as e:
        entry["price_decision"] = asdict(Decision("skip", "shopify_lookup_failed",
                                                  {"error": str(e)}))
        entry["stock_decision"] = asdict(Decision("skip", "shopify_lookup_failed"))
        return entry

    if not products:
        entry["price_decision"] = asdict(Decision("skip", "shopify_not_found"))
        entry["stock_decision"] = asdict(Decision("skip", "shopify_not_found"))
        return entry

    if len(products) > 1:
        entry["multiple_products_warning"] = [
            {"id": p.id, "title": p.title} for p in products
        ]
    product = products[0]

    variant = _variant_for_sku(product, match_row.sku_b)
    if variant is None:
        entry["price_decision"] = asdict(Decision(
            "skip", "variant_with_sku_not_in_product",
            {"product_id": product.id, "looked_for": match_row.sku_b,
             "found_variants": [v.sku for v in product.variants]}))
        entry["stock_decision"] = asdict(Decision("skip", "variant_with_sku_not_in_product"))
        return entry

    entry["shopify_product_id"] = product.id
    entry["shopify_variant_id"] = variant.id

    # Sibling-variant visibility: log any siblings we're NOT touching.
    siblings = [
        {"sku": v.sku, "title": v.title, "price": v.price,
         "inventory_quantity": v.inventory_quantity}
        for v in product.variants if v.id != variant.id
    ]
    if siblings:
        entry["sibling_variants_unchanged"] = siblings

    # Decisions
    price_decision = decide_price(
        workbook_inc_vat=wb_row.price_inc_vat,
        current_shopify_price=_to_float(variant.price),
        current_compare_at=_to_float(variant.compare_at_price),
        max_multiplier=args.max_price_multiplier,
        allow_downward=args.allow_downward,
        workbook_rrp=wb_row.rrp_inc_vat,
    )
    stock_decision = decide_stock(
        expected_qty=wb_row.qty_in_stock,
        actual_qty=variant.inventory_quantity,
        tolerance_floor=args.stock_tolerance_floor,
        tolerance_pct=args.stock_tolerance_pct,
    )
    entry["price_decision"] = asdict(price_decision)
    entry["stock_decision"] = asdict(stock_decision)

    # Apply (or simulate)
    if price_decision.action == "apply" and args.apply:
        new_price = price_decision.detail["workbook"]
        fields = {"price": fmt_price(new_price)}
        # `set_compare_at` (RRP supplied by workbook) takes precedence over the
        # legacy `bump_compare_at` (raise existing strike-through when overtaken).
        if price_decision.detail.get("set_compare_at"):
            fields["compareAtPrice"] = fmt_price(price_decision.detail["new_compare_at"])
        elif price_decision.detail.get("bump_compare_at"):
            fields["compareAtPrice"] = fmt_price(new_price)
        try:
            ok, err = api.update_variant_fields(product.id, variant.id, fields)
        except Exception as e:                                     # noqa: BLE001
            ok, err = False, f"unexpected: {e}"
        if ok:
            entry["price_decision"]["action"] = "applied"
        else:
            entry["price_decision"]["action"] = "apply_failed"
            entry["price_decision"]["error"] = err

    return entry


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

_CSV_COLS = [
    "product_code", "shopify_sku", "shopify_title",
    "current_price", "workbook_price", "price_delta", "price_delta_pct",
    "price_action", "price_skip_reason",
    "old_compare_at", "new_compare_at",
    "expected_stock", "actual_stock", "stock_diff", "stock_tolerance",
    "stock_action", "stock_skip_reason",
    "score", "match_method",
]


def _flatten_for_csv(entry: dict) -> dict:
    pd_ = entry.get("price_decision", {}) or {}
    sd_ = entry.get("stock_decision", {}) or {}
    pd_detail = pd_.get("detail", {}) or {}
    sd_detail = sd_.get("detail", {}) or {}
    return {
        "product_code":     entry.get("product_code", ""),
        "shopify_sku":      entry.get("shopify_sku", ""),
        "shopify_title":    entry.get("shopify_title", ""),
        "current_price":    pd_detail.get("current", ""),
        "workbook_price":   pd_detail.get("workbook", ""),
        "price_delta":      pd_detail.get("delta", ""),
        "price_delta_pct":  pd_detail.get("delta_pct", ""),
        "price_action":     pd_.get("action", ""),
        "price_skip_reason": pd_.get("reason", ""),
        "old_compare_at":   pd_detail.get("old_compare_at", ""),
        "new_compare_at":   pd_detail.get("new_compare_at", ""),
        "expected_stock":   sd_detail.get("expected", ""),
        "actual_stock":     sd_detail.get("actual", ""),
        "stock_diff":       sd_detail.get("diff", ""),
        "stock_tolerance":  sd_detail.get("tolerance", ""),
        "stock_action":     sd_.get("action", ""),
        "stock_skip_reason": sd_.get("reason", ""),
        "score":            entry.get("match_score", ""),
        "match_method":     entry.get("match_method", ""),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    p = argparse.ArgumentParser(
        description=(
            "Apply Andrew's workbook prices + log stock mismatches "
            "(dry-run by default; --apply writes prices)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Workflow:\n"
            "  1. resave workdir/sku-matcher/stockPricesAndSkus.xls → workdir/sku-matcher/stockPricesAndSkus.csv\n"
            "  2. PYTHONPATH=. pyenv exec python scripts/sku_matcher/export_shopify_catalogue.py\n"
            "  3. PYTHONPATH=. pyenv exec python scripts/sku_matcher/matcher.py ... → lookups/matches.csv\n"
            "  4. (this script)  dry-run first, --apply once you're happy with the report\n"
        ),
    )
    p.add_argument("matches_file", help="Path to lookups/matches.csv (from matcher.py)")
    p.add_argument("workbook_csv", help="Path to Andrew's resaved CSV")
    p.add_argument("--apply", action="store_true",
                   help="Write price changes (default: dry-run)")
    p.add_argument("--sku", help="Process only this Product Code "
                                 "(case-insensitive). Use during initial testing.")
    p.add_argument("--min-score", type=float, default=70.0,
                   help="Ignore matches scoring below this (default: 70)")
    p.add_argument("--stock-tolerance-pct", type=float, default=10.0,
                   help="Stock tolerance as %% of expected (default: 10)")
    p.add_argument("--stock-tolerance-floor", type=int, default=5,
                   help="Stock tolerance floor in units (default: 5)")
    p.add_argument("--max-price-multiplier", type=float, default=2.0,
                   help="Skip rows where new_price > current × this (default: 2.0)")
    p.add_argument("--price-col", default="Sell Price inc VAT")
    p.add_argument("--qty-col", default="Quantity in Stock")
    p.add_argument("--code-col", default="Product Code")
    p.add_argument("--desc-col", default="Product Description")
    p.add_argument("--rrp-col", default=None,
                   help="Workbook column holding the RRP. When set, "
                        "compareAtPrice is overwritten with this value.")
    p.add_argument("--allow-downward", action="store_true",
                   help="Allow new price < current Shopify price. "
                        "Default off (upward-only, Andrew's workflow).")
    p.add_argument("--log-file",
                   default=f"workdir/shopify-ops/price_stock_sync_{today}.jsonl")
    p.add_argument("--out",
                   default=f"workdir/shopify-ops/price_stock_sync_{today}.csv")
    p.add_argument("--state-file",
                   default="price_stock_sync_workdir/sku-matcher/state.json")
    return p.parse_args()


def _print_banner(args, log_file: str):
    print("=" * 80)
    print("Price + Stock Sync from Andrew's Workbook")
    print("=" * 80)
    print(f"lookups/matches.csv:        {args.matches_file}")
    print(f"workbook CSV:       {args.workbook_csv}")
    print(f"price column:       {args.price_col!r}")
    print(f"qty column:         {args.qty_col!r}")
    print(f"rrp column:         {args.rrp_col!r}" if args.rrp_col else "rrp column:         (none — legacy bump-up-only)")
    print(f"direction:          {'BIDIRECTIONAL (--allow-downward)' if args.allow_downward else 'upward-only'}")
    print(f"min match score:    {args.min_score}")
    print(f"stock tolerance:    ±max({args.stock_tolerance_floor} units, {args.stock_tolerance_pct}%)")
    print(f"max price ×:        {args.max_price_multiplier}")
    print(f"mode:               {'LIVE — WRITES PRICES' if args.apply else 'DRY-RUN'}")
    print(f"audit log:          {log_file}")
    print(f"CSV report:         {args.out}")
    print("=" * 80)


def main():
    args = parse_args()

    if not Path(args.matches_file).exists():
        print(f"❌ lookups/matches.csv not found: {args.matches_file}", file=sys.stderr)
        sys.exit(1)
    if not Path(args.workbook_csv).exists():
        print(f"❌ workbook CSV not found: {args.workbook_csv}", file=sys.stderr)
        sys.exit(1)

    # Ensure output parent dirs exist.
    Path(args.log_file).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)

    _print_banner(args, args.log_file)

    # Load both files up front — fail fast on schema problems.
    print("\nLoading inputs...")
    workbook = load_workbook_csv(
        args.workbook_csv,
        code_col=args.code_col, desc_col=args.desc_col,
        price_col=args.price_col, qty_col=args.qty_col,
        rrp_col=args.rrp_col,
    )
    print(f"  workbook rows: {len(workbook)}")
    matches = load_matches(args.matches_file, min_score=args.min_score)
    print(f"  matches kept (score ≥ {args.min_score}): {len(matches)}")

    # Resume cursor (mirrors shopify_io.{load,save}_state shape).
    state = load_state(args.state_file)
    start_idx = state.get("current_index", 0)
    processed: set = set(state.get("updated_skus", []) + state.get("skipped_skus", []))

    # Filter / order keys.
    keys = sorted(workbook.keys())
    if args.sku:
        target = args.sku.strip().upper()
        keys = [k for k in keys if k == target]
        if not keys:
            print(f"❌ --sku {args.sku!r} not found in workbook (keys are upper-cased).")
            sys.exit(1)

    # Open CSV writer in append mode so resume keeps prior rows.
    import csv as _csv
    csv_exists = Path(args.out).exists() and start_idx > 0
    csv_fh = open(args.out, "a" if csv_exists else "w", newline="", encoding="utf-8")
    csv_writer = _csv.DictWriter(csv_fh, fieldnames=_CSV_COLS)
    if not csv_exists:
        csv_writer.writeheader()

    # Counters
    n_unmatched = 0
    n_applied = 0
    n_would_apply = 0
    n_compare_at_bumps = 0
    n_price_skipped: dict = {}
    n_stock_mismatch = 0
    n_stock_ok = 0
    n_stock_skipped: dict = {}

    config = {
        "matches_file": args.matches_file,
        "workbook_csv": args.workbook_csv,
        "min_score": args.min_score,
        "stock_tolerance_pct": args.stock_tolerance_pct,
        "stock_tolerance_floor": args.stock_tolerance_floor,
        "max_price_multiplier": args.max_price_multiplier,
        "allow_downward": args.allow_downward,
        "rrp_col": args.rrp_col,
        "apply": args.apply,
    }

    try:
        with ShopifyClient() as client:
            api = ShopifyAPI(client=client)

            # Currency assertion — cheap insurance against running this on the wrong store.
            shop = client.execute("{ shop { name currencyCode } }")
            cur = shop["shop"]["currencyCode"]
            if cur != "GBP":
                print(f"❌ Connected store currency is {cur}, not GBP. Aborting.",
                      file=sys.stderr)
                sys.exit(1)
            print(f"  Shopify store: {shop['shop']['name']} ({cur})\n")

            iterator = tqdm(
                keys[start_idx:],
                desc="Syncing",
                initial=start_idx,
                total=len(keys),
            )

            for offset, key in enumerate(iterator, start=start_idx):
                wb_row = workbook[key]

                # No-match guard.
                match_row = matches.get(key)
                if match_row is None:
                    n_unmatched += 1
                    entry = {
                        "product_code": wb_row.product_code,
                        "shopify_sku": "",
                        "match_score": None,
                        "match_method": "",
                        "price_decision": asdict(Decision("skip", "unmatched")),
                        "stock_decision": asdict(Decision("skip", "unmatched")),
                        "dry_run": not args.apply,
                    }
                    append_log(args.log_file, entry)
                    csv_writer.writerow(_flatten_for_csv(entry))
                    processed.add(key)
                    save_state(args.state_file, offset + 1,
                               list(processed), [], [], config)
                    continue

                # Skip-if-already-processed only matters during resume.
                if key in processed and not args.sku:
                    continue

                entry = process_row(api, wb_row, match_row, args)
                append_log(args.log_file, entry)
                csv_writer.writerow(_flatten_for_csv(entry))
                csv_fh.flush()
                processed.add(key)
                save_state(args.state_file, offset + 1,
                           list(processed), [], [], config)

                pa = entry["price_decision"]["action"]
                if pa == "applied":
                    n_applied += 1
                elif pa == "apply":
                    n_would_apply += 1
                elif pa in ("skip", "apply_failed"):
                    reason = entry["price_decision"].get("reason") or pa
                    n_price_skipped[reason] = n_price_skipped.get(reason, 0) + 1
                _pd_detail = entry.get("price_decision", {}).get("detail", {}) or {}
                if _pd_detail.get("bump_compare_at") or _pd_detail.get("set_compare_at"):
                    n_compare_at_bumps += 1

                sa = entry["stock_decision"]["action"]
                if sa == "ok":
                    n_stock_ok += 1
                elif sa == "mismatch":
                    n_stock_mismatch += 1
                elif sa == "skip":
                    reason = entry["stock_decision"].get("reason") or "skip"
                    n_stock_skipped[reason] = n_stock_skipped.get(reason, 0) + 1

    except KeyboardInterrupt:
        print("\n\nInterrupted! State saved.", file=sys.stderr)
        csv_fh.close()
        sys.exit(130)

    csv_fh.close()

    # Clean up state file once the run is complete.
    if not args.sku and len(processed) >= len(keys):
        Path(args.state_file).unlink(missing_ok=True)

    # Summary
    print(f"\n{'=' * 80}")
    print("Summary")
    print(f"{'=' * 80}")
    print(f"Workbook rows processed:   {len(keys)}")
    print(f"Unmatched (no row in lookups/matches.csv at score ≥ {args.min_score}):  {n_unmatched}")
    print()
    print("Price:")
    if args.apply:
        print(f"  applied:                   {n_applied}")
    else:
        print(f"  would apply (dry-run):     {n_would_apply}")
    if n_compare_at_bumps:
        print(f"    …of which compareAtPrice would also be raised: {n_compare_at_bumps}")
    if n_price_skipped:
        print("  skipped:")
        for reason, count in sorted(n_price_skipped.items(), key=lambda kv: -kv[1]):
            print(f"    {reason:35s} {count}")
    print()
    print("Stock:")
    print(f"  within tolerance (ok):     {n_stock_ok}")
    print(f"  mismatch (logged):         {n_stock_mismatch}")
    if n_stock_skipped:
        print("  skipped:")
        for reason, count in sorted(n_stock_skipped.items(), key=lambda kv: -kv[1]):
            print(f"    {reason:35s} {count}")
    print()
    print(f"Audit log:  {args.log_file}")
    print(f"CSV report: {args.out}")
    print("=" * 80)

    if not args.apply and n_would_apply:
        print(f"\nDry-run complete. Re-run with --apply to write {n_would_apply} price change(s).")


if __name__ == "__main__":
    main()
