"""
Audit Google Ads spend over a date range to investigate Andrew's claim that
"ClickSlice have drawn £6k through the card this week."

Pulls:
  1. Daily total spend across the whole account (1 Apr → today)
     — so we can see April's tail vs May's run and identify what would have
       cleared on the Amex this week as a billing threshold event.
  2. Per-campaign spend for the 'this week' window (4–10 May, the Mon–Sun
     window Andrew was referencing on Sunday).
  3. Per-campaign spend for the 1–9 May window (matches Wayne's prior email).
  4. Invoice records (if any) — only populated for invoiced accounts, but
     worth checking in case MowDirect's ads account is invoiced not card.

Run:
    .venv/bin/python scripts/google_ads_card_audit.py
"""
from __future__ import annotations
from datetime import date, timedelta
from collections import defaultdict
from scripts.google_ads_client import GoogleAdsClient, GoogleAdsAPIError


client = GoogleAdsClient()


def daily_totals(start: date, end: date) -> list[tuple[str, float]]:
    """Total account spend per day, inclusive."""
    query = f"""
        SELECT segments.date, metrics.cost_micros
        FROM customer
        WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'
    """
    rows = client.search_stream(query)
    daily: dict[str, int] = defaultdict(int)
    for r in rows:
        d = r.get("segments", {}).get("date", "")
        c = int(r.get("metrics", {}).get("costMicros", 0) or 0)
        daily[d] += c
    return sorted(((d, c / 1_000_000) for d, c in daily.items()), key=lambda x: x[0])


def campaign_totals(start: date, end: date) -> list[dict]:
    """Per-campaign spend over the window."""
    return sorted(
        client.get_campaign_spend(start, end),
        key=lambda r: r["spend_gbp"],
        reverse=True,
    )


def try_invoices(year: int, month: int) -> list[dict] | str:
    """Invoiced accounts only. Card-billed accounts return an error here."""
    query = f"""
        SELECT
          invoice.id,
          invoice.type,
          invoice.billing_setup,
          invoice.payments_account_id,
          invoice.issue_date,
          invoice.total_amount_micros,
          invoice.currency_code
        FROM invoice
        WHERE invoice.issue_year = '{year}' AND invoice.issue_month = {month}
    """
    try:
        return client.search_stream(query)
    except GoogleAdsAPIError as e:
        return f"invoice query rejected ({e.status}): {e.body[:200]}"


def fmt_money(x: float) -> str:
    return f"£{x:,.2f}"


def main() -> None:
    today = date.today()
    apr_1 = date(2026, 4, 1)
    apr_30 = date(2026, 4, 30)
    may_1 = date(2026, 5, 1)
    may_9 = date(2026, 5, 9)
    week_start = date(2026, 5, 4)   # Mon
    week_end = date(2026, 5, 10)    # Sun (Andrew's "this week" on 10 May)

    print("=" * 72)
    print(f"GOOGLE ADS CARD AUDIT — run {today.isoformat()}")
    print("=" * 72)

    # 1) Daily totals — full picture from 1 Apr to today
    print("\n--- DAILY ACCOUNT SPEND (1 Apr → today) ---")
    rows = daily_totals(apr_1, today)
    running = 0.0
    for d, c in rows:
        running += c
        marker = ""
        if d == apr_30.isoformat():
            marker = "  ← end of April"
        if d == may_1.isoformat():
            marker = "  ← May begins"
        if d == week_start.isoformat():
            marker = "  ← 'this week' starts (Mon 4 May)"
        if d == week_end.isoformat():
            marker = "  ← 'this week' ends (Sun 10 May)"
        print(f"  {d}   {fmt_money(c):>12}   cum {fmt_money(running):>12}{marker}")
    print(f"\n  TOTAL since 1 Apr: {fmt_money(running)}")

    # 2) The window Andrew was talking about: Mon 4 May → Sun 10 May
    print("\n--- WINDOW: Mon 4 May → Sun 10 May (Andrew's 'this week') ---")
    cw_rows = campaign_totals(week_start, week_end)
    cw_total = sum(r["spend_gbp"] for r in cw_rows)
    cw_conv = sum(r["conversions_value"] for r in cw_rows)
    for r in cw_rows:
        if r["spend_gbp"] < 0.01:
            continue
        roas = (r["conversions_value"] / r["spend_gbp"]) if r["spend_gbp"] else 0
        print(f"  {fmt_money(r['spend_gbp']):>10}   ROAS {roas:>5.2f}x   "
              f"{r['campaign_type']:<25} {r['campaign_name']}")
    print(f"\n  TOTAL spend  4–10 May : {fmt_money(cw_total)}")
    print(f"  TOTAL conv value      : {fmt_money(cw_conv)}")
    print(f"  Account ROAS          : {cw_conv / cw_total:.2f}x"
          if cw_total else "  Account ROAS          : n/a")

    # 3) Wayne's email window: 1–9 May
    print("\n--- WINDOW: 1–9 May (matches Wayne's Sat 9 May email) ---")
    e_rows = campaign_totals(may_1, may_9)
    e_total = sum(r["spend_gbp"] for r in e_rows)
    e_conv = sum(r["conversions_value"] for r in e_rows)
    print(f"  Spend 1–9 May        : {fmt_money(e_total)}  "
          f"(email quoted £2,967.47)")
    print(f"  Conv value 1–9 May   : {fmt_money(e_conv)}  "
          f"(email quoted £23,287.46)")

    # 4) April tail — what would still have been clearing on the card in May
    print("\n--- APRIL TOTAL (for billing-cycle context) ---")
    apr_rows = campaign_totals(apr_1, apr_30)
    apr_total = sum(r["spend_gbp"] for r in apr_rows)
    print(f"  April total spend    : {fmt_money(apr_total)}")
    print(f"  April daily avg      : {fmt_money(apr_total / 30)}")

    # 5) Try invoice resource — confirms whether account is card or invoiced
    print("\n--- INVOICE RESOURCE PROBE (May 2026) ---")
    inv = try_invoices(2026, 5)
    if isinstance(inv, str):
        print(f"  {inv}")
        print("  → consistent with credit-card billing (no invoice resource)")
    else:
        print(f"  {len(inv)} invoice rows returned")
        for row in inv:
            print(f"    {row}")


if __name__ == "__main__":
    main()
