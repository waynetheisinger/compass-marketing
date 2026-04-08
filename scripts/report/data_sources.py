"""
Data fetching layer for the monthly spend report.

Each fetch_* function returns (data, note):
  - (list[dict], None)          on success
  - (None, "NOT CONNECTED …")   if credentials are missing or API is unavailable

Callers must handle None data gracefully — the excel_writer renders these as
"NOT CONNECTED" rows in the spreadsheet rather than crashing.

All date arguments are UTC datetime objects. Callers should pass midnight on
the first and last day of the target month.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# eBay fees (eBay Finances API)
# ---------------------------------------------------------------------------

def fetch_ebay_fees(start: datetime, end: datetime) -> tuple[list[dict] | None, str | None]:
    """
    Fetch all eBay Finance transactions for the period.

    Returns raw transaction dicts. The excel_writer groups these by
    transactionType (FINAL_VALUE_FEE, AD_FEE, SHIPPING_LABEL, etc.).
    """
    required = ["EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "EBAY_REFRESH_TOKEN"]
    if missing := [v for v in required if not os.environ.get(v)]:
        return None, f"NOT CONNECTED — add to .env: {', '.join(missing)}"

    try:
        from scripts.ebay_finances_client import EBayFinancesClient
        client = EBayFinancesClient()
        txns = client.get_transactions(start, end)
        return txns, None
    except Exception as exc:
        return None, f"ERROR — {exc}"


# ---------------------------------------------------------------------------
# BaseLinker orders (used for ManoMano, OnBuy; Amazon fallback)
# ---------------------------------------------------------------------------

_BASELINKER_SOURCES = {
    "amazon":   "Amazon",   # fallback only — SP-API is authoritative
    "ebay":     "eBay",     # cross-reference only
    "bandquk":  "B&Q UK",   # B&Q orders also appear here (Mirakl is authoritative)
}


def fetch_baselinker_orders(
    start: datetime,
    end: datetime,
    sources: list[str] | None = None,
) -> tuple[dict[str, list] | None, str | None]:
    """
    Fetch orders from BaseLinker for each marketplace source.

    Args:
        start, end: UTC datetimes for the reporting period.
        sources:    List of source keys to fetch (default: all known sources).

    Returns:
        Dict keyed by source name, value is list of order dicts — e.g.
        {"manomano": [...], "onbuy": [...], "amazon": [...]}
    """
    if not os.environ.get("BASELINKER_API_TOKEN"):
        return None, "NOT CONNECTED — add BASELINKER_API_TOKEN to .env"

    if sources is None:
        sources = list(_BASELINKER_SOURCES.keys())

    try:
        from scripts.baselinker_client import BaseLinkerClient
        client = BaseLinkerClient()
        result: dict[str, list] = {}

        date_from = int(start.replace(tzinfo=timezone.utc).timestamp())
        date_to   = int(end.replace(tzinfo=timezone.utc).timestamp())

        for source in sources:
            orders: list[dict] = []
            page_date_from = date_from

            while True:
                resp = client.call("getOrders", {
                    "date_from":              page_date_from,
                    "date_to":                date_to,
                    "filter_order_source":    source,
                    "include_commission_data": True,
                    "get_unconfirmed_orders":  True,
                })
                batch = resp.get("orders", [])
                orders.extend(batch)

                if len(batch) < 100:
                    break

                # Advance cursor: last order's date_add + 1 second
                last_ts = max(o.get("date_add", page_date_from) for o in batch)
                if last_ts <= page_date_from:
                    break
                page_date_from = last_ts + 1

            result[source] = orders

        return result, None

    except Exception as exc:
        return None, f"ERROR — {exc}"


# ---------------------------------------------------------------------------
# Mirakl / B&Q (commission per order + invoices)
# ---------------------------------------------------------------------------

def fetch_mirakl_orders(
    start: datetime,
    end: datetime,
    instance: str = "KINGFISHER",
) -> tuple[list[dict] | None, str | None]:
    """Fetch B&Q orders from Mirakl with commission amounts."""
    prefix = f"MIRAKL_{instance.upper()}"
    required = [f"{prefix}_BASE_URL", f"{prefix}_API_KEY"]
    if missing := [v for v in required if not os.environ.get(v)]:
        return None, f"NOT CONNECTED — add to .env: {', '.join(missing)}"

    try:
        from scripts.mirakl_client import MiraklClient
        client = MiraklClient(instance)

        start_str = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_str   = end.strftime("%Y-%m-%dT%H:%M:%SZ")

        orders: list[dict] = []
        page_token: str | None = None

        while True:
            params: dict = {
                "start_update_date": start_str,
                "end_update_date":   end_str,
                "max":               100,
            }
            if page_token:
                params["page_token"] = page_token

            data = client.get("/orders", params=params)
            batch = data.get("orders", [])
            orders.extend(batch)

            page_token = data.get("next_page_token")
            if not page_token or not batch:
                break

        return orders, None

    except Exception as exc:
        return None, f"ERROR — {exc}"


def fetch_mirakl_invoices(
    start: datetime,
    end: datetime,
    instance: str = "KINGFISHER",
) -> tuple[list[dict] | None, str | None]:
    """Fetch Mirakl billing invoices (platform charges beyond per-order commission)."""
    prefix = f"MIRAKL_{instance.upper()}"
    if not (os.environ.get(f"{prefix}_BASE_URL") and os.environ.get(f"{prefix}_API_KEY")):
        return None, f"NOT CONNECTED — {prefix}_BASE_URL / API_KEY missing"

    try:
        from scripts.mirakl_client import MiraklClient
        client = MiraklClient(instance)

        params = {
            "start_date": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_date":   end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        data = client.get("/invoices", params=params)
        return data.get("invoices", []), None

    except Exception as exc:
        return None, f"ERROR — {exc}"


# ---------------------------------------------------------------------------
# Shopify payment fees
# ---------------------------------------------------------------------------

def fetch_shopify_fees(
    start: datetime,
    end: datetime,
) -> tuple[list[dict] | None, str | None]:
    """
    Fetch Shopify orders and extract payment processing fees.

    Returns list of dicts: {order_id, created_at, gross, fee_amount, gateway}
    """
    required = ["SHOPIFY_STORE_DOMAIN", "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET"]
    if missing := [v for v in required if not os.environ.get(v)]:
        return None, f"NOT CONNECTED — add to .env: {', '.join(missing)}"

    try:
        from scripts.shopify_client import ShopifyClient

        query = """
        query($cursor: String) {
          orders(first: 100, after: $cursor,
                 query: "channel:web created_at:>=%s created_at:<=%s") {
            pageInfo { hasNextPage endCursor }
            edges {
              node {
                id
                name
                createdAt
                totalPriceSet { shopMoney { amount } }
                transactions {
                  fees {
                    type
                    amount { amount }
                  }
                }
              }
            }
          }
        }
        """ % (
            start.strftime("%Y-%m-%dT%H:%M:%S"),
            end.strftime("%Y-%m-%dT%H:%M:%S"),
        )

        rows: list[dict] = []
        cursor = None
        with ShopifyClient() as client:
            while True:
                data = client.execute(query, {"cursor": cursor})
                orders_data = data.get("data", {}).get("orders", {})
                for edge in orders_data.get("edges", []):
                    node = edge["node"]
                    fee_total = sum(
                        float(fee["amount"]["amount"])
                        for txn in node.get("transactions", [])
                        for fee in txn.get("fees", [])
                    )
                    rows.append({
                        "order_id":   node["id"],
                        "name":       node["name"],
                        "created_at": node["createdAt"],
                        "gross":      float(node["totalPriceSet"]["shopMoney"]["amount"]),
                        "fee_amount": fee_total,
                    })

                page_info = orders_data.get("pageInfo", {})
                if not page_info.get("hasNextPage"):
                    break
                cursor = page_info["endCursor"]

        return rows, None

    except Exception as exc:
        return None, f"ERROR — {exc}"


# ---------------------------------------------------------------------------
# Google Ads spend (Phase 2)
# ---------------------------------------------------------------------------

def fetch_google_ads_spend(
    start: datetime,
    end: datetime,
) -> tuple[list[dict] | None, str | None]:
    """
    Fetch Google Ads campaign spend via GAQL.

    Returns list of dicts: {campaign_id, campaign_name, campaign_type,
                             spend_gbp, impressions, clicks}
    """
    required = [
        "GOOGLE_ADS_CLIENT_ID",
        "GOOGLE_ADS_CLIENT_SECRET",
        "GOOGLE_ADS_DEVELOPER_TOKEN",
        "GOOGLE_ADS_CUSTOMER_ID",
        "GOOGLE_ADS_REFRESH_TOKEN",
    ]
    if missing := [v for v in required if not os.environ.get(v)]:
        return None, f"NOT CONNECTED — add to .env: {', '.join(missing)}"

    try:
        from scripts.google_ads_client import GoogleAdsClient
        client = GoogleAdsClient()
        rows = client.get_campaign_spend(start.date(), end.date())
        return rows, None
    except Exception as exc:
        return None, f"ERROR — {exc}"


# ---------------------------------------------------------------------------
# Amazon SP-API fees (Phase 2)
# ---------------------------------------------------------------------------

def fetch_amazon_fees(
    start: datetime,
    end: datetime,
) -> tuple[list[dict] | None, str | None]:
    """
    Fetch Amazon settlement fee line items via SP-API.

    Falls back to BaseLinker commission data if SP-API credentials are absent.
    Returns (rows, note) where note is None on success or a coverage warning.
    """
    sp_required = [
        "AMAZON_SP_CLIENT_ID",
        "AMAZON_SP_CLIENT_SECRET",
        "AMAZON_SP_REFRESH_TOKEN",
        "AMAZON_SELLER_ID",
    ]
    if missing := [v for v in sp_required if not os.environ.get(v)]:
        # Try BaseLinker fallback for referral fees only
        bl_data, bl_note = fetch_baselinker_orders(start, end, sources=["amazon"])
        if bl_note:
            return None, (
                f"NOT CONNECTED — SP-API missing: {', '.join(missing)}. "
                f"BaseLinker fallback also failed: {bl_note}"
            )
        orders = (bl_data or {}).get("amazon", [])
        rows = [
            {
                "fee_type":  "Referral fees (BaseLinker estimate)",
                "amount":    abs(float(o.get("commission_amount", 0))),
                "order_id":  o.get("external_order_id", ""),
                "posted_at": o.get("date_add", ""),
                "source":    "BaseLinker (fallback)",
            }
            for o in orders
        ]
        note = (
            "PARTIAL — BaseLinker estimate for referral fees only. "
            f"Connect SP-API for FBA storage, fulfilment, and inbound costs: "
            f"add {', '.join(missing)} to .env"
        )
        return rows, note

    try:
        from scripts.amazon_client import AmazonClient
        client = AmazonClient()
        rows = client.get_settlement_fees(start, end)
        return rows, None
    except Exception as exc:
        return None, f"ERROR — {exc}"


# ---------------------------------------------------------------------------
# Amazon Advertising spend (Phase 2)
# ---------------------------------------------------------------------------

def fetch_amazon_ads_spend(
    start: datetime,
    end: datetime,
) -> tuple[list[dict] | None, str | None]:
    """Fetch Amazon Sponsored Products spend via Amazon Advertising API."""
    required = [
        "AMAZON_ADS_CLIENT_ID",
        "AMAZON_ADS_CLIENT_SECRET",
        "AMAZON_ADS_REFRESH_TOKEN",
        "AMAZON_ADS_PROFILE_ID",
    ]
    if missing := [v for v in required if not os.environ.get(v)]:
        return None, f"NOT CONNECTED — add to .env: {', '.join(missing)}"

    try:
        from scripts.amazon_ads_client import AmazonAdsClient
        client = AmazonAdsClient()
        rows = client.get_campaign_spend(start.date(), end.date())
        return rows, None
    except Exception as exc:
        return None, f"ERROR — {exc}"
