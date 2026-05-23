"""
Compute MowDirect's effective commission rate on a Mirakl operator (default
KINGFISHER / B&Q) from real per-order data.

Mirakl returns per-line commission on GET /orders. We sum commission and line
revenue over a date window, then divide for the effective rate. Output:
  - headline effective rate (commission / revenue) for the window
  - breakdown by Mirakl category_code
  - optional CSV with one row per order_line for further analysis

Usage:
    python scripts/mirakl_commission_audit.py
    python scripts/mirakl_commission_audit.py --operator KINGFISHER --days 90
    python scripts/mirakl_commission_audit.py --start 2026-01-01 --end 2026-05-18
    python scripts/mirakl_commission_audit.py --csv reports/mirakl_commission_2026-05-18.csv

Notes
-----
Field names below match the Mirakl seller OR11 spec, but we read each line
defensively because some operator instances populate `total_commission` while
others populate `commission_fee + commission_taxes`. We capture both shapes and
report what we found.

Only settled states are counted by default (SHIPPED, RECEIVED, CLOSED) — rates
on cancelled / refused lines aren't representative.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from mirakl_client import MiraklClient


SETTLED_STATES = {"SHIPPED", "RECEIVED", "CLOSED", "TO_COLLECT"}
PAGE_SIZE = 100


def _d(x) -> Decimal:
    """Coerce to Decimal, treating None / '' as 0."""
    if x in (None, ""):
        return Decimal("0")
    return Decimal(str(x))


def _line_commission(line: dict) -> Decimal:
    """
    Mirakl OR11 returns commission in either of two shapes — try both.
      1. `total_commission` (commission incl. tax, single field)
      2. `commission_fee` + sum(commission_taxes[].amount) if commission_taxes present
    """
    if "total_commission" in line and line["total_commission"] not in (None, ""):
        return _d(line["total_commission"])
    fee = _d(line.get("commission_fee"))
    taxes = line.get("commission_taxes") or []
    if isinstance(taxes, list):
        fee += sum((_d(t.get("amount")) for t in taxes), Decimal("0"))
    return fee


def _line_revenue(line: dict) -> Decimal:
    """
    Mirakl OR11 line price is normally `price` (unit) × `quantity`, but some
    operators populate `total_price` directly. Prefer total_price when present.
    """
    if "total_price" in line and line["total_price"] not in (None, ""):
        return _d(line["total_price"])
    return _d(line.get("price")) * _d(line.get("quantity") or 1)


def fetch_orders(client: MiraklClient, start: datetime, end: datetime) -> list[dict]:
    """Page through GET /orders for the date window. Returns flat list of orders."""
    out: list[dict] = []
    offset = 0
    params_base = {
        "start_date": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_date":   end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "max":        PAGE_SIZE,
    }
    while True:
        params = {**params_base, "offset": offset}
        resp = client.get("/orders", params=params)
        batch = resp.get("orders", [])
        out.extend(batch)
        total = resp.get("total_count", len(out))
        offset += len(batch)
        if not batch or offset >= total:
            break
    return out


def summarise(orders: list[dict]) -> tuple[Decimal, Decimal, dict, list[dict]]:
    """
    Returns: (total_revenue, total_commission, by_category, line_rows)
      - by_category: {category_code: {"revenue": D, "commission": D, "lines": int}}
      - line_rows:   list of dicts for CSV
    """
    total_rev = Decimal("0")
    total_com = Decimal("0")
    by_cat: dict[str, dict] = defaultdict(lambda: {
        "revenue": Decimal("0"), "commission": Decimal("0"), "lines": 0,
    })
    rows: list[dict] = []

    for order in orders:
        state = order.get("order_state") or order.get("state")
        if state not in SETTLED_STATES:
            continue
        order_id = order.get("order_id") or order.get("commercial_id")
        created = order.get("created_date") or order.get("acceptance_decision_date") or ""
        for line in order.get("order_lines", []) or []:
            line_state = line.get("order_line_state")
            if line_state and line_state not in SETTLED_STATES:
                continue
            rev = _line_revenue(line)
            com = _line_commission(line)
            cat = line.get("category_code") or line.get("category_label") or "(unknown)"
            total_rev += rev
            total_com += com
            by_cat[cat]["revenue"]    += rev
            by_cat[cat]["commission"] += com
            by_cat[cat]["lines"]      += 1
            rows.append({
                "order_id":      order_id,
                "created":       created,
                "order_state":   state,
                "line_state":    line_state or "",
                "category_code": cat,
                "shop_sku":      line.get("shop_sku") or line.get("offer_sku") or "",
                "product_title": line.get("product_title") or "",
                "quantity":      line.get("quantity"),
                "unit_price":    line.get("price"),
                "line_revenue":  str(rev),
                "commission":    str(com),
                "effective_rate": f"{(com / rev * 100):.2f}" if rev > 0 else "",
            })
    return total_rev, total_com, by_cat, rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--operator", default="KINGFISHER",
                   help="Mirakl operator name (env prefix). Default: KINGFISHER")
    p.add_argument("--days", type=int, default=90,
                   help="Lookback window in days (ignored if --start/--end given). Default: 90")
    p.add_argument("--start", help="ISO date, e.g. 2026-01-01. Overrides --days.")
    p.add_argument("--end",   help="ISO date. Default: today.")
    p.add_argument("--csv",   help="Optional CSV path for per-line detail")
    args = p.parse_args(argv)

    end = datetime.fromisoformat(args.end) if args.end else datetime.now(timezone.utc)
    if args.start:
        start = datetime.fromisoformat(args.start)
    else:
        start = end - timedelta(days=args.days)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    print(f"Operator:   {args.operator}")
    print(f"Window:     {start.date()} → {end.date()}  ({(end-start).days} days)")
    print(f"Settled in: {sorted(SETTLED_STATES)}")
    print()

    client = MiraklClient(args.operator)
    orders = fetch_orders(client, start, end)
    print(f"Orders fetched:  {len(orders)}")

    rev, com, by_cat, rows = summarise(orders)
    print(f"Settled lines:   {sum(c['lines'] for c in by_cat.values())}")
    print(f"Total revenue:   £{rev:,.2f}")
    print(f"Total commission:£{com:,.2f}")
    if rev > 0:
        print(f"Effective rate:  {com / rev * 100:.2f}%")
    else:
        print("Effective rate:  n/a (no settled revenue in window)")

    if by_cat:
        print()
        print(f"{'category_code':<24} {'lines':>6} {'revenue':>14} {'commission':>14} {'rate':>8}")
        print("-" * 70)
        for cat, agg in sorted(by_cat.items(), key=lambda kv: -kv[1]["revenue"]):
            rate = (agg["commission"] / agg["revenue"] * 100) if agg["revenue"] > 0 else Decimal("0")
            print(f"{cat:<24} {agg['lines']:>6} £{agg['revenue']:>12,.2f} "
                  f"£{agg['commission']:>12,.2f} {rate:>6.2f}%")

    if args.csv and rows:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\nPer-line CSV: {args.csv}  ({len(rows)} rows)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
