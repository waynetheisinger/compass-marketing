"""
Targeted MTD pull for May 1–11 2026 — Amazon marketplace fees, Amazon FBA
fulfilment/storage, and B&Q (Mirakl) commissions.

Reuses the existing report helpers; the goal is a one-shot console output
that Wayne can copy directly into the workbook (or into the meeting deck).
"""
from __future__ import annotations
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
load_dotenv()

from scripts.report import data_sources as ds
from scripts.report import transforms as tr


START = datetime(2026, 5, 1,  0, 0, 0,  tzinfo=timezone.utc)
# Clamp end to "now - 5 min" so SP-API doesn't reject the future bound
END   = datetime.now(timezone.utc) - timedelta(minutes=5)


def main() -> None:
    print(f"Window: {START:%Y-%m-%d %H:%M} → {END:%Y-%m-%d %H:%M} UTC\n")

    # ── B&Q (Mirakl) ────────────────────────────────────────────────────────
    print("=" * 60)
    print("B&Q (Mirakl) — commissions + invoices")
    print("=" * 60)
    bq_orders, bq_note = ds.fetch_mirakl_orders(START, END)
    if bq_note:
        print(f"  Note: {bq_note}")
    bq_agg = tr.aggregate_mirakl_orders(bq_orders or [])
    print(f"  Orders pulled        : {len(bq_orders or [])}")
    print(f"  Net (B&Q sales, ex-VAT): £{bq_agg.get('net', 0):>10,.2f}")
    print(f"  Commission (referral): £{bq_agg.get('commission', 0):>10,.2f}")

    bq_inv, bq_inv_note = ds.fetch_mirakl_invoices(START, END)
    if bq_inv_note:
        print(f"  Invoices note: {bq_inv_note}")
    bq_inv_agg = tr.aggregate_mirakl_invoices(bq_inv or [])
    if bq_inv_agg:
        print("  Platform invoice charges:")
        for k, v in bq_inv_agg.items():
            print(f"    {k:<30}  £{v:>10,.2f}")
    else:
        print("  Platform invoice charges: none in window")

    print(f"\n  B&Q total fees & commission : £"
          f"{bq_agg.get('commission', 0) + sum(bq_inv_agg.values()):>10,.2f}\n")

    # ── Amazon (SP-API Finances) ────────────────────────────────────────────
    print("=" * 60)
    print("Amazon — marketplace fees + FBA cost of sales")
    print("=" * 60)
    print("  (SP-API Finances paginates with 2.5s gap; this leg can take a minute)")
    amazon_rows, amazon_note = ds.fetch_amazon_fees(START, END)
    if amazon_note:
        print(f"  Note: {amazon_note}")

    if not amazon_rows:
        print("  No fee rows returned.")
        return

    # The transform splits commission from FBA cost-of-sale rows by fee_type
    commission, fba = tr.aggregate_amazon_fees(amazon_rows)

    print(f"\n  Total fee line items: {len(amazon_rows)}")

    print("\n  Marketplace fees (commission / referral):")
    com_total = 0.0
    for k, v in sorted(commission.items(), key=lambda x: -x[1]):
        print(f"    {k:<35}  £{v:>10,.2f}")
        com_total += v
    print(f"    {'─' * 35}")
    print(f"    {'TOTAL marketplace fees':<35}  £{com_total:>10,.2f}")

    print("\n  FBA cost of sales (fulfilment / storage / removal):")
    fba_total = 0.0
    for k, v in sorted(fba.items(), key=lambda x: -x[1]):
        print(f"    {k:<35}  £{v:>10,.2f}")
        fba_total += v
    print(f"    {'─' * 35}")
    print(f"    {'TOTAL FBA cost of sales':<35}  £{fba_total:>10,.2f}")

    print("\n" + "=" * 60)
    print("SUMMARY  (paste these into the workbook / deck)")
    print("=" * 60)
    print(f"  Amazon marketplace fees (May 1–11) : £{com_total:>10,.2f}")
    print(f"  Amazon FBA cost of sales (May 1–11): £{fba_total:>10,.2f}")
    print(f"  B&Q (Mirakl) commission + invoices : £"
          f"{bq_agg.get('commission', 0) + sum(bq_inv_agg.values()):>10,.2f}")


if __name__ == "__main__":
    main()
