"""
Parse Google Ads' Billing Activity report — the export contains the actual
card transactions (threshold charges, monthly charges, declines) that the
GAQL API does NOT expose. Source file is UTF-16 with tab delimiters.

Run:
    .venv/bin/python scripts/parse_billing_activity.py
"""
from __future__ import annotations
import csv
import io
from collections import defaultdict
from datetime import datetime

CSV_PATH = "/Users/waynetheisinger/compass/marketingPlan/Billing activity report.csv"


def load_rows() -> list[dict]:
    """Decode UTF-16, drop the two-line preamble, parse as tab-separated."""
    with open(CSV_PATH, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-16")
    lines = text.splitlines()
    # Line 1: "Billing activity report"
    # Line 2: window string e.g. "March 1, 2026 - May 11, 2026"
    # Line 3: header row
    body = "\n".join(lines[2:])
    reader = csv.DictReader(io.StringIO(body), delimiter="\t")
    rows = []
    for r in reader:
        # Normalise column names (csv may leave stray whitespace)
        rows.append({k.strip(): (v or "").strip() for k, v in r.items()})
    return rows


def parse_money(s: str) -> float:
    """'£500.00' -> 500.0; '-£500.00' -> -500.0; '--' / '' -> 0."""
    if not s or s in {"--", "-"}:
        return 0.0
    sign = -1.0 if s.startswith("-") else 1.0
    cleaned = (
        s.replace("-", "")
         .replace("£", "")
         .replace(",", "")
         .replace('"', "")
         .strip()
    )
    try:
        return sign * float(cleaned)
    except ValueError:
        return 0.0


def parse_date(s: str) -> datetime | None:
    s = s.strip().strip('"')
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def main() -> None:
    rows = load_rows()
    print(f"Loaded {len(rows)} rows. Columns: {list(rows[0].keys())}\n")

    # Tally by Type
    by_type: dict[str, float] = defaultdict(float)
    for r in rows:
        cost = parse_money(r.get("Costs", ""))
        credit = parse_money(r.get("Credits", ""))
        by_type[r["Type"]] += cost + credit  # credits are already negative

    print("=== TOTALS BY TYPE (March 1 → May 11, 2026) ===")
    for t, v in sorted(by_type.items(), key=lambda x: x[0]):
        print(f"  {t:<20} {v:>12,.2f}")

    # Pull every Payments row — these ARE the card transactions
    print("\n=== EVERY PAYMENT EVENT (card transactions) ===")
    print(f"  {'Date':<14} {'Amount':>10}   Description")
    pay_rows = [r for r in rows if r["Type"] == "Payments"]
    successful_total = 0.0
    declined_total = 0.0
    by_month: dict[str, float] = defaultdict(float)
    for r in pay_rows:
        d = parse_date(r["Date"])
        cr = parse_money(r["Credits"])
        desc = r["Description"]
        is_decline = "declined" in desc.lower()
        flag = "DECLINED" if is_decline else "        "
        print(f"  {r['Date']:<14} {cr:>10,.2f}   {flag}  {desc[:90]}")
        if is_decline:
            # The amount on a decline is in the description, not in Credits;
            # the Credits column is 0.00 for a decline. So just count attempts.
            declined_total += 1
        else:
            successful_total += cr
            if d:
                by_month[d.strftime("%Y-%m")] += cr

    print(f"\n  Successful card charges total : £{abs(successful_total):,.2f}")
    print(f"  Declined attempts             : {int(declined_total)}")

    print("\n=== CARD CHARGES BY MONTH ===")
    for m in sorted(by_month):
        print(f"  {m}   £{abs(by_month[m]):>10,.2f}")

    # Specific: what hit the card in the 'last 9 days' window (May 3 - May 11)
    print("\n=== CARD CHARGES BY WINDOW ===")
    windows = [
        ("Last 9 days (May 3 – May 11)", datetime(2026, 5, 3), datetime(2026, 5, 11)),
        ("Andrew's 'this week' (May 4 – May 10)", datetime(2026, 5, 4), datetime(2026, 5, 10)),
        ("All of May to date", datetime(2026, 5, 1), datetime(2026, 5, 11)),
        ("April", datetime(2026, 4, 1), datetime(2026, 4, 30)),
        ("March", datetime(2026, 3, 1), datetime(2026, 3, 31)),
    ]
    for label, start, end in windows:
        total = 0.0
        n = 0
        for r in pay_rows:
            if "declined" in r["Description"].lower():
                continue
            d = parse_date(r["Date"])
            if d and start <= d <= end:
                total += parse_money(r["Credits"])
                n += 1
        print(f"  {label:<45}  £{abs(total):>10,.2f}  ({n} charges)")


if __name__ == "__main__":
    main()
