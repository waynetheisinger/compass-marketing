"""
Monthly marketing & cost report for MowDirect funders.

Pulls data from all connected APIs, assembles a 5-tab Excel spreadsheet,
and saves it to the reports/ directory.

Usage:
    python scripts/monthly_report.py --month 2026-03
    python scripts/monthly_report.py --month 2026-03 --output reports/
    python scripts/monthly_report.py --month 2026-03 --dry-run

The --dry-run flag uses the mock data from scripts/mock_report.py instead of
live API calls, producing a labelled mock spreadsheet for review.

Missing API credentials are handled gracefully — those channels appear as
"NOT CONNECTED" rows in the spreadsheet rather than causing the script to fail.
"""
from __future__ import annotations

import argparse
import os
import sys
from calendar import monthrange
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate monthly marketing & cost report for funders.",
    )
    parser.add_argument(
        "--month",
        required=True,
        metavar="YYYY-MM",
        help="Reporting month, e.g. 2026-03",
    )
    parser.add_argument(
        "--output",
        default="reports",
        metavar="DIR",
        help="Output directory (default: reports/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use mock data instead of live APIs (for format review)",
    )
    return parser.parse_args(argv)


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    """Return (start, end) as UTC datetimes for the given month."""
    last_day = monthrange(year, month)[1]
    start = datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.utc)
    end   = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)
    return start, end


# ---------------------------------------------------------------------------
# Live data assembly
# ---------------------------------------------------------------------------

def _assemble_live(start: datetime, end: datetime) -> dict:
    """
    Fetch data from all APIs and build the report_data dict for excel_writer.
    Missing credentials → None data with a note string.
    """
    from scripts.report import data_sources as ds
    from scripts.report import transforms as tr

    print("Fetching eBay fees …")
    ebay_txns, ebay_note = ds.fetch_ebay_fees(start, end)

    print("Fetching BaseLinker orders (Amazon fallback) …")
    bl_data, bl_note = ds.fetch_baselinker_orders(start, end,
                                                   sources=["amazon"])

    print("Fetching B&Q (Mirakl) orders …")
    mirakl_orders, mirakl_note = ds.fetch_mirakl_orders(start, end)

    print("Fetching B&Q (Mirakl) invoices …")
    mirakl_invoices, mirakl_inv_note = ds.fetch_mirakl_invoices(start, end)

    print("Fetching Shopify payment fees …")
    shopify_rows, shopify_note = ds.fetch_shopify_fees(start, end)

    print("Fetching Amazon fees …")
    amazon_rows, amazon_note = ds.fetch_amazon_fees(start, end)

    print("Fetching Google Ads spend …")
    google_rows, google_note = ds.fetch_google_ads_spend(start, end)

    print("Fetching Amazon Ads spend …")
    amazon_ads_rows, amazon_ads_note = ds.fetch_amazon_ads_spend(start, end)

    # ── eBay ───────────────────────────────────────────────────────────────
    ebay_aggregated = tr.aggregate_ebay_transactions(ebay_txns or [])
    ebay_fee_rows   = [r for r in tr.ebay_fee_rows(ebay_aggregated) if not r["is_ad_spend"]]
    ebay_ad_amount  = abs(ebay_aggregated.get("AD_FEE", 0.0))
    ebay_gross      = abs(ebay_aggregated.get("SALE", 0.0))

    ebay_channel = {
        "name":       "eBay",
        "source":     "eBay Finances API",
        "gross":      ebay_gross,
        "total_fees": sum(r["amount"] for r in ebay_fee_rows),
        "fee_rows":   ebay_fee_rows,
        "note":       ebay_note,
    }

    # ── BaseLinker (Amazon fallback only) ──────────────────────────────────
    bl_summary = tr.aggregate_baselinker_orders(bl_data or {})

    # ── B&Q ────────────────────────────────────────────────────────────────
    bq_agg      = tr.aggregate_mirakl_orders(mirakl_orders or [])
    bq_inv_agg  = tr.aggregate_mirakl_invoices(mirakl_invoices or [])
    bq_fee_rows = [{"label": "Commission", "amount": bq_agg["commission"]}]
    bq_fee_rows += [{"label": k, "amount": v} for k, v in bq_inv_agg.items()]
    bq_channel  = {
        "name":       "B&Q (Mirakl)",
        "source":     "Mirakl API",
        "gross":      bq_agg["gross"],
        "total_fees": sum(r["amount"] for r in bq_fee_rows),
        "fee_rows":   bq_fee_rows,
        "note":       mirakl_note,
    }

    # ── Shopify ────────────────────────────────────────────────────────────
    shopify_agg = tr.aggregate_shopify_fees(shopify_rows or [])
    shopify_channel = {
        "name":       "Shopify Direct",
        "source":     "Shopify GraphQL",
        "gross":      shopify_agg["gross"],
        "total_fees": shopify_agg["fee_amount"],
        "fee_rows":   [{"label": "Payment processing fees", "amount": shopify_agg["fee_amount"]}],
        "note":       shopify_note,
    }

    # ── Amazon ─────────────────────────────────────────────────────────────
    amazon_commission, amazon_fba = tr.aggregate_amazon_fees(amazon_rows or [])
    amazon_fee_rows = [{"label": k, "amount": v} for k, v in amazon_commission.items()]
    amazon_fba_rows = [{"label": k, "amount": v} for k, v in amazon_fba.items()]

    # Estimate gross from BaseLinker if SP-API not connected
    bl_amazon   = (bl_data or {}).get("amazon", [])
    bl_az_summ  = tr.aggregate_baselinker_orders({"amazon": bl_amazon}).get("amazon", {})
    amazon_gross_estimate = bl_az_summ.get("gross", 0)

    amazon_channel = {
        "name":       "Amazon",
        "source":     "SP-API Settlement" if not amazon_note else "BaseLinker (fallback)",
        "gross":      amazon_gross_estimate,
        "total_fees": sum(r["amount"] for r in amazon_fee_rows),
        "fee_rows":   amazon_fee_rows,
        "note":       amazon_note,
    }

    channels = [
        shopify_channel,
        ebay_channel,
        amazon_channel,
        bq_channel,
    ]

    # ── Ad spend ────────────────────────────────────────────────────────────
    ad_rows = tr.build_ad_spend_rows(
        google_rows,
        ebay_ad_amount if not ebay_note else None,
        amazon_ads_rows,
    )

    ad_platform_summary = []
    ad_notes: dict[str, str | None] = {}

    for platform, rows_, note_ in [
        ("Google Ads",       google_rows,      google_note),
        ("eBay Promoted Listings",
         [{"spend": ebay_ad_amount}] if not ebay_note else None, ebay_note),
        ("Amazon Sponsored Products", amazon_ads_rows, amazon_ads_note),
    ]:
        spend = sum(float(r_.get("spend_gbp", r_.get("spend", 0))) for r_ in (rows_ or []))
        ad_platform_summary.append({"platform": platform, "spend": spend, "note": note_})
        ad_notes[platform] = note_

    not_connected_ads = [
        {"platform": p, "note": n}
        for p, n in ad_notes.items()
        if n and not n.startswith("PARTIAL")
    ]

    # ── Summary ─────────────────────────────────────────────────────────────
    gross_by_channel = {ch["name"]: ch["gross"] for ch in channels}
    all_fee_rows     = [r_ for ch in channels for r_ in ch["fee_rows"]]
    summary          = tr.build_summary(all_fee_rows, amazon_fba_rows, ad_rows, gross_by_channel)

    return {
        "summary":                  summary,
        "channels":                 channels,
        "fba_rows":                 amazon_fba_rows,
        "amazon_fba_note":          amazon_note,
        "ad_spend_rows":            ad_rows,
        "ad_spend_not_connected":   not_connected_ads,
        "ad_spend_platform_summary": ad_platform_summary,
        "ad_spend_notes":           ad_notes,
        "ebay_raw_transactions":    ebay_txns or [],
        "amazon_raw_fees":          amazon_rows or [],
        "baselinker_raw_orders":    [
            o for orders in (bl_data or {}).values() for o in orders
        ],
        "google_ads_raw":           google_rows or [],
        "is_mock":                  False,
    }


# ---------------------------------------------------------------------------
# Dry-run (mock) assembly — delegates to mock_report constants
# ---------------------------------------------------------------------------

def _assemble_mock() -> dict:
    """Build report_data from the same mock constants used by mock_report.py."""
    # Import mock data directly from mock_report
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location(
        "mock_report",
        pathlib.Path(__file__).parent / "mock_report.py",
    )
    mock = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mock)

    CHANNELS = mock.CHANNELS
    GOOGLE_ADS = mock.GOOGLE_ADS

    channels = []
    for ch in CHANNELS:
        ch_fee_rows = [{"label": label, "amount": amount} for label, amount in ch["fees"]]
        channels.append({
            "name":       ch["name"],
            "source":     ch["source"],
            "gross":      ch["gross"],
            "total_fees": sum(r["amount"] for r in ch_fee_rows),
            "fee_rows":   ch_fee_rows,
            "note":       None,
        })

    amazon_ch    = next(c for c in CHANNELS if c["name"] == "Amazon")
    fba_rows     = [{"label": label, "amount": amount} for label, amount in amazon_ch["fba_costs"]]
    total_fba    = sum(r["amount"] for r in fba_rows)

    google_rows  = [
        {
            "platform":      "Google Ads",
            "campaign_name": name,
            "campaign_type": "PMax" if "Max" in name else "Shopping",
            "spend":         spend,
            "impressions":   impr,
            "clicks":        clicks,
        }
        for name, spend, impr, clicks in GOOGLE_ADS
    ]
    ebay_ad = sum(s for _, s, _, _ in CHANNELS[1]["ad_spend"])
    amazon_ads = [
        {"platform": "Amazon", "campaign_name": name, "campaign_type": "Sponsored Products",
         "spend": spend, "impressions": impr, "clicks": clicks}
        for name, spend, impr, clicks in amazon_ch["ad_spend"]
    ]

    from scripts.report import transforms as tr
    all_fee_rows = [r_ for ch in channels for r_ in ch["fee_rows"]]
    ad_rows = google_rows + [
        {"platform": "eBay", "campaign_name": "Promoted Listings",
         "campaign_type": "PROMOTED_LISTINGS", "spend": ebay_ad,
         "impressions": None, "clicks": None}
    ] + amazon_ads

    gross_by_channel = {ch["name"]: ch["gross"] for ch in channels}
    summary = tr.build_summary(all_fee_rows, fba_rows, ad_rows, gross_by_channel)

    ad_platform_summary = [
        {"platform": "Google Ads", "spend": sum(r["spend"] for r in google_rows), "note": None},
        {"platform": "eBay Promoted Listings", "spend": ebay_ad, "note": None},
        {"platform": "Amazon Sponsored Products",
         "spend": sum(r["spend"] for r in amazon_ads), "note": None},
    ]

    return {
        "summary":                   summary,
        "channels":                  channels,
        "fba_rows":                  fba_rows,
        "amazon_fba_note":           None,
        "ad_spend_rows":             ad_rows,
        "ad_spend_not_connected":    [],
        "ad_spend_platform_summary": ad_platform_summary,
        "ad_spend_notes":            {},
        "ebay_raw_transactions":     [],
        "amazon_raw_fees":           [],
        "baselinker_raw_orders":     [],
        "google_ads_raw":            [],
        "is_mock":                   True,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    try:
        year, month = map(int, args.month.split("-"))
    except ValueError:
        print(f"ERROR: --month must be YYYY-MM, got {args.month!r}", file=sys.stderr)
        sys.exit(1)

    start, end = _month_bounds(year, month)
    month_label = start.strftime("%B %Y")

    print(f"\nMowDirect Monthly Report — {month_label}")
    print(f"Period: {start:%Y-%m-%d} → {end:%Y-%m-%d}")
    print("-" * 50)

    if args.dry_run:
        print("Mode: DRY RUN (mock data)")
        report_data = _assemble_mock()
    else:
        report_data = _assemble_live(start, end)

    from scripts.report.excel_writer import build_workbook
    wb = build_workbook(report_data, month_label)

    os.makedirs(args.output, exist_ok=True)
    suffix   = "_mock" if args.dry_run else ""
    filename = f"marketing_spend{suffix}_{args.month}.xlsx"
    out_path = os.path.join(args.output, filename)
    wb.save(out_path)

    summary = report_data["summary"]
    print(f"\nDone → {out_path}")
    print(f"  Gross revenue:          £{summary['gross']:>12,.2f}")
    print(f"  Marketplace fees:       £{summary['total_fees']:>12,.2f}")
    print(f"  FBA cost of sales:      £{summary['total_fba']:>12,.2f}")
    print(f"  Ad spend:               £{summary['total_ads']:>12,.2f}")
    print(f"  ─────────────────────────────────────")
    print(f"  Combined deductions:    £{summary['combined']:>12,.2f}")
    print(f"  Net contribution:       £{summary['net']:>12,.2f}")


if __name__ == "__main__":
    main()
