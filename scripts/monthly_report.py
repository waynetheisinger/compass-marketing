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

# Wayne Theisinger commission — flat 4% of total net (ex-VAT) revenue
# across all channels, shown as a single line on the Summary tab.
WAYNE_COMMISSION_RATE = 0.04

# Ad platforms suppressed entirely when NOT CONNECTED rather than flagged.
# Spend on these is negligible, so a "NOT CONNECTED" badge causes more concern
# than the omission. They appear normally (with real spend) once credentials
# are wired up — this only hides the not-connected state.
_SUPPRESS_AD_PLATFORM_IF_UNCONNECTED = {"Amazon Sponsored Products"}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate monthly marketing & cost report for funders.",
    )
    parser.add_argument(
        "--month",
        metavar="YYYY-MM",
        help="Reporting month, e.g. 2026-03 (whole calendar month).",
    )
    parser.add_argument(
        "--start",
        metavar="YYYY-MM-DD",
        help="Custom range start date (alternative to --month). "
             "Pair with --end, or omit --end to run through to now.",
    )
    parser.add_argument(
        "--end",
        metavar="YYYY-MM-DD",
        help="Custom range end date (with --start). Defaults to now if omitted.",
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
    parser.add_argument(
        "--wayne-commission",
        type=float,
        default=None,
        metavar="GBP",
        help=(
            "Override the auto-computed Wayne Theisinger commission with a "
            "fixed £ value — typically the audited month-end figure that "
            "accounts for true returns and refunds beyond what the "
            "marketplaces capture. Without this flag the line defaults to "
            f"{WAYNE_COMMISSION_RATE:.0%} of Net Revenue across all channels."
        ),
    )
    return parser.parse_args(argv)


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    """Return (start, end) as UTC datetimes for the given month."""
    last_day = monthrange(year, month)[1]
    start = datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.utc)
    end   = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)
    return start, end


def _range_label(start: datetime, end: datetime) -> str:
    """Human label for a custom date range, e.g. '21–26 May 2026' or
    '30 Apr – 26 May 2026'."""
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.day}–{end.day} {start:%b %Y}"
    if start.year == end.year:
        return f"{start.day} {start:%b} – {end.day} {end:%b %Y}"
    return f"{start.day} {start:%b %Y} – {end.day} {end:%b %Y}"


def _format_settlement_date(posted_at: str) -> str:
    """Format an Amazon PostedDate (ISO) as 'D Mon YYYY' for the 'correct to' note."""
    try:
        dt = datetime.fromisoformat(str(posted_at).replace("Z", "+00:00"))
        return f"{dt.day} {dt:%b %Y}"
    except (ValueError, AttributeError):
        return str(posted_at)[:10]


# ---------------------------------------------------------------------------
# Live data assembly
# ---------------------------------------------------------------------------

def _assemble_live(
    start: datetime,
    end: datetime,
    wayne_commission_override: float | None = None,
) -> dict:
    """
    Fetch data from all APIs and build the report_data dict for excel_writer.
    Missing credentials → None data with a note string.

    `wayne_commission_override`: if provided, used as the Wayne commission £
    value (a manual audited figure); otherwise the line defaults to
    `WAYNE_COMMISSION_RATE × total net revenue`.
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
    # Amazon settles fees in arrears; the Finances query is clamped to the last
    # posted settlement (in the client), so even an interim report gets a real,
    # reconciled figure (fees + Principal revenue on the same basis). The Amazon
    # row is annotated with the settlement cut-off date ("correct to: …") below.
    amazon_rows, amazon_revenue, amazon_note = ds.fetch_amazon_fees(start, end)

    print("Fetching Amazon FBA returns / removals (slow — async reports) …")
    fba_returns_data, fba_returns_note = ds.fetch_amazon_fba_returns(start, end)

    print("Fetching Amazon cancelled orders …")
    amazon_cancelled, amazon_cancel_note = ds.fetch_amazon_cancelled_orders(start, end)

    print("Fetching Mirakl cancelled orders …")
    mirakl_cancelled, mirakl_cancel_note = ds.fetch_mirakl_cancelled_orders(start, end)

    print("Fetching Google Ads spend …")
    google_rows, google_note = ds.fetch_google_ads_spend(start, end)

    print("Fetching Amazon Ads spend …")
    amazon_ads_rows, amazon_ads_note = ds.fetch_amazon_ads_spend(start, end)

    # ── eBay ───────────────────────────────────────────────────────────────
    # Order proceeds are inc-VAT; refunds also inc-VAT. Net revenue is what
    # remained after refunds, then ÷1.20 to match the ex-VAT basis used by
    # every other channel. Marketplace fees + ad spend come from the new
    # rich aggregate shape (post-2026 eBay Finances API layout).
    ebay_aggregated = tr.aggregate_ebay_transactions(ebay_txns or [])
    ebay_fee_rows   = [r for r in tr.ebay_fee_rows(ebay_aggregated) if not r["is_ad_spend"]]
    ebay_ad_amount  = ebay_aggregated["ad_spend"]
    ebay_net        = (ebay_aggregated["order_proceeds"] - ebay_aggregated["refunds"]) / 1.20
    # eBay marketplace fees are charged inc-VAT (verified: the fixed 0.35%
    # regulatory operating fee bills at 0.42% = ×1.2). That VAT is reclaimable,
    # so strip it to the ex-VAT cost basis — consistent with Amazon and B&Q.
    ebay_fee_rows   = [{**r, "amount": round(r["amount"] / 1.20, 2)} for r in ebay_fee_rows]

    ebay_channel = {
        "name":       "eBay",
        "source":     "eBay Finances API",
        "net":        round(ebay_net, 2),
        "total_fees": sum(r["amount"] for r in ebay_fee_rows),
        "fee_rows":   ebay_fee_rows,
        "note":       ebay_note,
    }

    # ── BaseLinker (Amazon fallback only) ──────────────────────────────────
    bl_summary = tr.aggregate_baselinker_orders(bl_data or {})

    # ── B&Q ────────────────────────────────────────────────────────────────
    bq_agg      = tr.aggregate_mirakl_orders(mirakl_orders or [])
    bq_inv_agg  = tr.aggregate_mirakl_invoices(mirakl_invoices or [])
    # B&Q commission is reported inc-VAT (total_commission = commission_fee +
    # commission_taxes, verified at 20%). Strip the reclaimable VAT to ex-VAT,
    # consistent with Amazon and eBay. Platform-invoice charges are passed
    # through as-is pending VAT verification.
    bq_fee_rows = [{"label": "Commission", "amount": round(bq_agg["commission"] / 1.20, 2)}]
    bq_fee_rows += [{"label": k, "amount": v} for k, v in bq_inv_agg.items()]
    bq_channel  = {
        "name":       "B&Q (Mirakl)",
        "source":     "Mirakl API",
        "net":        bq_agg["net"],
        "total_fees": sum(r["amount"] for r in bq_fee_rows),
        "fee_rows":   bq_fee_rows,
        "note":       mirakl_note,
    }

    # ── Shopify ────────────────────────────────────────────────────────────
    shopify_agg = tr.aggregate_shopify_fees(shopify_rows or [])
    shopify_channel = {
        "name":       "Shopify Direct",
        "source":     "Shopify GraphQL",
        "net":        shopify_agg["net"],
        "total_fees": shopify_agg["fee_amount"],
        "fee_rows":   [{"label": "Payment processing fees", "amount": shopify_agg["fee_amount"]}],
        "note":       shopify_note,
    }

    # ── Amazon ─────────────────────────────────────────────────────────────
    # `aggregate_amazon_fees` still splits commission vs FBA so we keep
    # the marketplace-fee tab clean; FBA aggregates are no longer reported.
    amazon_commission, _amazon_fba_unused = tr.aggregate_amazon_fees(amazon_rows or [])
    # Amazon charges 20% UK VAT on its seller fees (verified per-order: referral
    # is 15% of the gross sale price, then ×1.2 VAT → the amount SP-API reports).
    # That VAT is reclaimable input VAT, so it isn't a real cost — strip it to put
    # Amazon fees on the same ex-VAT basis as net revenue and the other channels.
    amazon_fee_rows = [{"label": k, "amount": round(v / 1.20, 2)}
                       for k, v in amazon_commission.items()]

    # Net revenue: prefer SP-API Finances "Principal" (authoritative, and on the
    # same settlement basis as the fees, so referral commission reconciles to the
    # expected % of sales). Fall back to the BaseLinker estimate only when SP-API
    # revenue isn't available (SP-API absent or error) — BaseLinker materially
    # under-captures Amazon sales.
    bl_amazon  = (bl_data or {}).get("amazon", [])
    bl_az_summ = tr.aggregate_baselinker_orders({"amazon": bl_amazon}).get("amazon", {})
    if amazon_revenue is not None:
        # Amazon's Principal is already EX-VAT (VAT is reported separately as a
        # "Tax" charge), so — unlike the gross-÷1.20 channels — we take it as net
        # directly. Dividing again would double-count VAT.
        amazon_net    = round(amazon_revenue, 2)
        amazon_source = "SP-API Finances (Principal)"
    else:
        amazon_net    = bl_az_summ.get("net", 0)
        amazon_source = "BaseLinker (estimate)"

    # Settlement cut-off: the most recent PostedDate actually received. For an
    # interim month this is mid-month — surface it next to the Amazon line so
    # the figure isn't mistaken for a full-month total.
    posted_dates = [r.get("posted_at") for r in (amazon_rows or []) if r.get("posted_at")]
    if posted_dates:
        amazon_source = f"{amazon_source} · correct to: {_format_settlement_date(max(posted_dates))}"

    amazon_channel = {
        "name":       "Amazon",
        "source":     amazon_source,
        "net":        amazon_net,
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
        # Suppress negligible-spend platforms when they're not connected, so the
        # report doesn't carry an alarming NOT CONNECTED line for a channel that
        # barely moves the figures.
        if (platform in _SUPPRESS_AD_PLATFORM_IF_UNCONNECTED
                and note_ and not note_.startswith("PARTIAL")):
            continue
        spend = sum(float(r_.get("spend_gbp", r_.get("spend", 0))) for r_ in (rows_ or []))
        ad_platform_summary.append({"platform": platform, "spend": spend, "note": note_})
        ad_notes[platform] = note_

    not_connected_ads = [
        {"platform": p, "note": n}
        for p, n in ad_notes.items()
        if n and not n.startswith("PARTIAL")
    ]

    # ── Cancellations ───────────────────────────────────────────────────────
    cancellations = {
        "Shopify Direct": tr.aggregate_shopify_cancellations(shopify_rows or []),
        "Amazon":         tr.aggregate_amazon_cancellations(amazon_cancelled or [])
                          if amazon_cancelled is not None else None,
        "B&Q (Mirakl)":   tr.aggregate_mirakl_cancellations(mirakl_cancelled or [])
                          if mirakl_cancelled is not None else None,
    }
    cancellation_notes = {
        "Shopify Direct": shopify_note,
        "Amazon":         amazon_cancel_note,
        "B&Q (Mirakl)":   mirakl_cancel_note,
    }

    # ── FBA returns / removals ──────────────────────────────────────────────
    fba_returns_payload: dict = {}
    if fba_returns_data:
        fba_returns_payload = {
            "customer_returns_summary":   tr.aggregate_customer_returns(
                fba_returns_data.get("customer_returns") or []),
            "removal_shipments_summary":  tr.aggregate_removal_shipments(
                fba_returns_data.get("removal_shipments") or []),
            "inventory_snapshot_summary": tr.aggregate_inventory_snapshot(
                fba_returns_data.get("inventory_snapshot") or []),
            "removal_fee_totals":         tr.extract_removal_fees(amazon_rows or []),
        }

    # ── Summary ─────────────────────────────────────────────────────────────
    # FBA cost-of-sales no longer rolls into the headline deductions; the
    # Summary tab carries a "Commission paid to Wayne Theisinger" line.
    # Default is WAYNE_COMMISSION_RATE × total net revenue; can be overridden
    # via --wayne-commission for the post-audit run. All revenue figures
    # here are net (ex-VAT).
    net_by_channel = {ch["name"]: ch["net"] for ch in channels}
    all_fee_rows   = [r_ for ch in channels for r_ in ch["fee_rows"]]
    auto_commission = round(sum(net_by_channel.values()) * WAYNE_COMMISSION_RATE, 2)
    if wayne_commission_override is not None:
        wayne_commission = round(float(wayne_commission_override), 2)
        wayne_commission_note = "Invoiced Value"
        wayne_commission_overridden = True
    else:
        wayne_commission = auto_commission
        wayne_commission_note = (
            f"{WAYNE_COMMISSION_RATE:.0%} of Net Revenue across all channels "
            "(pre-audit estimate)."
        )
        wayne_commission_overridden = False
    summary = tr.build_summary(all_fee_rows, ad_rows, net_by_channel,
                               wayne_commission=wayne_commission,
                               wayne_commission_note=wayne_commission_note,
                               wayne_commission_overridden=wayne_commission_overridden)
    # Surface the commission rate so excel_writer can render the auto-commission
    # line as a live "=Net Revenue * rate" formula (auditable on the sheet).
    summary["commission_rate"] = WAYNE_COMMISSION_RATE

    return {
        "summary":                  summary,
        "channels":                 channels,
        "fba_returns":              fba_returns_payload,
        "fba_returns_note":         fba_returns_note,
        "cancellations":            cancellations,
        "cancellation_notes":       cancellation_notes,
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

def _assemble_mock(wayne_commission_override: float | None = None) -> dict:
    """
    Build report_data from the same mock constants used by mock_report.py.

    `wayne_commission_override` mirrors the live path so dry-runs can also
    preview the audited-override variant of the Summary tab.
    """
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
            # Mock CHANNELS' values are illustrative — treated as net for the
            # new ex-VAT layout. (No back-out is applied to keep the round
            # numbers readable in the mock workbook.)
            "net":        ch["net"],
            "total_fees": sum(r["amount"] for r in ch_fee_rows),
            "fee_rows":   ch_fee_rows,
            "note":       None,
        })

    amazon_ch    = next(c for c in CHANNELS if c["name"] == "Amazon")

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

    net_by_channel = {ch["name"]: ch["net"] for ch in channels}
    auto_commission = round(sum(net_by_channel.values()) * WAYNE_COMMISSION_RATE, 2)
    if wayne_commission_override is not None:
        wayne_commission = round(float(wayne_commission_override), 2)
        wayne_commission_note = "Invoiced Value"
        wayne_commission_overridden = True
    else:
        wayne_commission = auto_commission
        wayne_commission_note = (
            f"{WAYNE_COMMISSION_RATE:.0%} of Net Revenue across all channels "
            "(pre-audit estimate)."
        )
        wayne_commission_overridden = False
    summary = tr.build_summary(all_fee_rows, ad_rows, net_by_channel,
                               wayne_commission=wayne_commission,
                               wayne_commission_note=wayne_commission_note,
                               wayne_commission_overridden=wayne_commission_overridden)
    # Surface the commission rate so excel_writer can render the auto-commission
    # line as a live "=Net Revenue * rate" formula (auditable on the sheet).
    summary["commission_rate"] = WAYNE_COMMISSION_RATE

    ad_platform_summary = [
        {"platform": "Google Ads", "spend": sum(r["spend"] for r in google_rows), "note": None},
        {"platform": "eBay Promoted Listings", "spend": ebay_ad, "note": None},
        {"platform": "Amazon Sponsored Products",
         "spend": sum(r["spend"] for r in amazon_ads), "note": None},
    ]

    return {
        "summary":                   summary,
        "channels":                  channels,
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

    if args.start:
        # Custom date range (alternative to a whole month).
        try:
            start = datetime.fromisoformat(args.start).replace(
                hour=0, minute=0, second=0, tzinfo=timezone.utc)
            if args.end:
                end = datetime.fromisoformat(args.end).replace(
                    hour=23, minute=59, second=59, tzinfo=timezone.utc)
            else:
                end = datetime.now(timezone.utc)   # through to now
        except ValueError:
            print("ERROR: --start/--end must be YYYY-MM-DD", file=sys.stderr)
            sys.exit(1)
        if end <= start:
            print("ERROR: --end must be after --start", file=sys.stderr)
            sys.exit(1)
        month_label = _range_label(start, end)
        period_slug = f"{start:%Y-%m-%d}_to_{end:%Y-%m-%d}"
    elif args.month:
        try:
            year, month = map(int, args.month.split("-"))
        except ValueError:
            print(f"ERROR: --month must be YYYY-MM, got {args.month!r}", file=sys.stderr)
            sys.exit(1)
        start, end = _month_bounds(year, month)
        month_label = start.strftime("%B %Y")
        period_slug = args.month
    else:
        print("ERROR: provide --month YYYY-MM or --start YYYY-MM-DD", file=sys.stderr)
        sys.exit(1)

    print(f"\nMowDirect Cost Report — {month_label}")
    print(f"Period: {start:%Y-%m-%d %H:%M} → {end:%Y-%m-%d %H:%M} UTC")
    print("-" * 50)

    if args.dry_run:
        print("Mode: DRY RUN (mock data)")
        report_data = _assemble_mock(wayne_commission_override=args.wayne_commission)
    else:
        report_data = _assemble_live(start, end,
                                     wayne_commission_override=args.wayne_commission)

    from scripts.report.excel_writer import build_workbook
    wb = build_workbook(report_data, month_label)

    os.makedirs(args.output, exist_ok=True)
    suffix   = "_mock" if args.dry_run else ""
    filename = f"marketing_spend{suffix}_{period_slug}.xlsx"
    out_path = os.path.join(args.output, filename)
    wb.save(out_path)

    summary = report_data["summary"]
    print(f"\nDone → {out_path}")
    print(f"  Net revenue (ex-VAT):   £{summary['net']:>12,.2f}")
    print(f"  Marketplace fees:       £{summary['total_fees']:>12,.2f}")
    print(f"  Commission paid to WT:  £{summary['wayne_commission']:>12,.2f}")
    print(f"  Ad spend:               £{summary['total_ads']:>12,.2f}")
    print(f"  ─────────────────────────────────────")
    print(f"  Combined deductions:    £{summary['combined']:>12,.2f}")
    print(f"  Contribution:           £{summary['contribution']:>12,.2f}")


if __name__ == "__main__":
    main()
