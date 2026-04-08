"""
Data transformation and aggregation helpers for the monthly spend report.

Converts raw API responses into the normalised dicts that excel_writer expects.
All monetary values are rounded to 2 decimal places (GBP).
"""
from __future__ import annotations

from datetime import datetime


# ---------------------------------------------------------------------------
# eBay
# ---------------------------------------------------------------------------

# Maps eBay transactionType → human-readable label used in the report
EBAY_FEE_LABELS: dict[str, str] = {
    "FINAL_VALUE_FEE":  "Referral / final value fees",
    "AD_FEE":           "Promoted Listings spend",
    "SHIPPING_LABEL":   "Shipping label costs",
    "SUBSCRIPTION_FEE": "Store subscription",
    "REFUND":           "Refunds issued (credit)",
    "DISPUTE":          "Dispute / chargeback",
    "ADJUSTMENT":       "Adjustments",
    "NON_SALE_CHARGE":  "Other charges",
}

# These types are ad spend (Tab 4) rather than fees (Tab 2)
EBAY_AD_TYPES: set[str] = {"AD_FEE"}


def aggregate_ebay_transactions(transactions: list[dict]) -> dict[str, float]:
    """
    Sum eBay Finance transactions by type.

    Returns {transaction_type: total_amount} with negative values for charges
    and positive for credits, matching the eBay API convention.
    """
    totals: dict[str, float] = {}
    for txn in transactions:
        t_type = txn.get("transactionType", "UNKNOWN")
        try:
            amount = float(txn["amount"]["value"])
        except (KeyError, TypeError, ValueError):
            amount = 0.0
        totals[t_type] = round(totals.get(t_type, 0.0) + amount, 2)
    return totals


def ebay_fee_rows(aggregated: dict[str, float]) -> list[dict]:
    """
    Convert aggregated eBay transactions into fee rows for Tab 2.

    Returns list of:
        {fee_type, label, amount (positive = cost), is_ad_spend}
    Excludes SALE and DEPOSIT types (not fees).
    """
    skip_types = {"SALE", "DEPOSIT", "WITHDRAWAL", "TRANSFER"}
    rows = []
    for t_type, amount in sorted(aggregated.items()):
        if t_type in skip_types:
            continue
        rows.append({
            "fee_type":   t_type,
            "label":      EBAY_FEE_LABELS.get(t_type, t_type.replace("_", " ").title()),
            "amount":     round(abs(amount), 2),
            "is_ad_spend": t_type in EBAY_AD_TYPES,
        })
    return rows


# ---------------------------------------------------------------------------
# BaseLinker orders → commission rows
# ---------------------------------------------------------------------------

def aggregate_baselinker_orders(
    orders_by_source: dict[str, list[dict]],
) -> dict[str, dict]:
    """
    Summarise BaseLinker orders by source into:
        {source_key: {gross, commission, order_count, source_label}}
    """
    source_labels = {
        "manomano": "ManoMano",
        "onbuy":    "OnBuy",
        "amazon":   "Amazon (BaseLinker fallback)",
        "ebay":     "eBay (BaseLinker cross-ref)",
    }
    result: dict[str, dict] = {}
    for source, orders in orders_by_source.items():
        gross = sum(
            sum(
                float(p.get("price_brutto", 0) or 0) * int(p.get("quantity", 1) or 1)
                for p in o.get("products", [])
            )
            for o in orders
        )
        commission = sum(
            abs(float(
                (o.get("commission") or {}).get("gross", 0) or 0
            ))
            for o in orders
        )
        result[source] = {
            "source_label": source_labels.get(source, source),
            "gross":        round(gross, 2),
            "commission":   round(commission, 2),
            "order_count":  len(orders),
        }
    return result


# ---------------------------------------------------------------------------
# Mirakl / B&Q
# ---------------------------------------------------------------------------

def aggregate_mirakl_orders(orders: list[dict]) -> dict:
    """
    Summarise Mirakl orders into {gross, commission, order_count}.
    """
    gross = 0.0
    commission = 0.0
    for order in orders:
        # Mirakl order total: price_amount (before commission)
        price = order.get("price", 0)
        gross += float(price if not isinstance(price, dict) else price.get("amount", 0) or 0)
        comm = order.get("total_commission", order.get("commission", 0))
        commission += float(comm if not isinstance(comm, dict) else comm.get("amount", 0) or 0)
    return {
        "gross":       round(gross, 2),
        "commission":  round(commission, 2),
        "order_count": len(orders),
    }


def aggregate_mirakl_invoices(invoices: list[dict]) -> dict[str, float]:
    """
    Sum Mirakl invoice lines by type.
    Returns {invoice_type_label: total_amount}.
    """
    totals: dict[str, float] = {}
    for inv in invoices:
        label = inv.get("type_label") or inv.get("type") or "Platform charge"
        amt = inv.get("amount", 0)
        amount = abs(float(amt if not isinstance(amt, dict) else amt.get("amount", 0) or 0))
        totals[label] = round(totals.get(label, 0.0) + amount, 2)
    return totals


# ---------------------------------------------------------------------------
# Shopify
# ---------------------------------------------------------------------------

def aggregate_shopify_fees(rows: list[dict]) -> dict:
    """Sum Shopify order fees into {gross, fee_amount, order_count}."""
    return {
        "gross":       round(sum(r["gross"]      for r in rows), 2),
        "fee_amount":  round(sum(r["fee_amount"] for r in rows), 2),
        "order_count": len(rows),
    }


# ---------------------------------------------------------------------------
# Amazon settlement lines
# ---------------------------------------------------------------------------

# Fee types that belong on the FBA Cost of Sales tab, not Marketplace Fees
FBA_FEE_TYPES: set[str] = {
    "FBA Fulfillment Fee",
    "FBAPerUnitFulfillmentFee",
    "FBAPerOrderFulfillmentFee",
    "FBA Storage Fee",
    "FBAStorageFee",
    "FBA Inbound Transportation Fee",
    "FBAInboundTransportationFee",
    "FBA Prep Service Fee",
    "FBAPrepServiceFee",
    "FBA Long-Term Storage Fee",
    "FBALongTermStorageFee",
    "FBA Removal Fee",
    "FBARemovalFee",
}

COMMISSION_FEE_TYPES: set[str] = {
    "Referral Fee",
    "ReferralFee",
    "VariableClosingFee",
    "Variable Closing Fee",
}


def aggregate_amazon_fees(
    rows: list[dict],
) -> tuple[dict[str, float], dict[str, float]]:
    """
    Split Amazon settlement rows into commission fees and FBA costs.

    Returns:
        commission_totals: {fee_type_label: amount}  — for Marketplace Fees tab
        fba_totals:        {fee_type_label: amount}  — for FBA Cost of Sales tab
    """
    commission_totals: dict[str, float] = {}
    fba_totals: dict[str, float] = {}

    for row in rows:
        fee_type = row.get("fee_type", "")
        amount   = abs(float(row.get("amount", 0) or 0))
        label    = _normalise_amazon_fee_label(fee_type)

        if fee_type in FBA_FEE_TYPES or any(f in fee_type for f in ("FBA", "Storage", "Inbound", "Prep")):
            fba_totals[label] = round(fba_totals.get(label, 0.0) + amount, 2)
        else:
            commission_totals[label] = round(commission_totals.get(label, 0.0) + amount, 2)

    return commission_totals, fba_totals


def _normalise_amazon_fee_label(fee_type: str) -> str:
    """Convert CamelCase or snake_case Amazon fee types to readable labels."""
    labels = {
        "ReferralFee":                    "Referral fees",
        "Referral Fee":                   "Referral fees",
        "FBAPerUnitFulfillmentFee":       "FBA fulfilment fees (per unit)",
        "FBAPerOrderFulfillmentFee":      "FBA fulfilment fees (per order)",
        "FBA Fulfillment Fee":            "FBA fulfilment fees",
        "FBAStorageFee":                  "FBA storage fees",
        "FBA Storage Fee":                "FBA storage fees",
        "FBAInboundTransportationFee":    "FBA inbound shipping",
        "FBA Inbound Transportation Fee": "FBA inbound shipping",
        "FBAPrepServiceFee":              "FBA prep & labelling",
        "FBA Prep Service Fee":           "FBA prep & labelling",
        "FBALongTermStorageFee":          "FBA long-term storage fees",
        "FBA Long-Term Storage Fee":      "FBA long-term storage fees",
        "FBARemovalFee":                  "FBA removal / disposal fees",
        "FBA Removal Fee":                "FBA removal / disposal fees",
    }
    return labels.get(fee_type, fee_type)


# ---------------------------------------------------------------------------
# Ad spend rows
# ---------------------------------------------------------------------------

def build_ad_spend_rows(
    google_rows: list[dict] | None,
    ebay_ad_amount: float | None,
    amazon_ads_rows: list[dict] | None,
) -> list[dict]:
    """
    Merge ad spend from all sources into a flat list for Tab 4.

    Each row: {platform, campaign_name, spend, impressions, clicks}
    """
    rows: list[dict] = []

    if google_rows:
        for r in google_rows:
            rows.append({
                "platform":      "Google Ads",
                "campaign_name": r.get("campaign_name", ""),
                "campaign_type": r.get("campaign_type", ""),
                "spend":         round(float(r.get("spend_gbp", 0)), 2),
                "impressions":   int(r.get("impressions", 0)),
                "clicks":        int(r.get("clicks", 0)),
            })

    if ebay_ad_amount is not None:
        rows.append({
            "platform":      "eBay",
            "campaign_name": "Promoted Listings",
            "campaign_type": "PROMOTED_LISTINGS",
            "spend":         round(ebay_ad_amount, 2),
            "impressions":   None,
            "clicks":        None,
        })

    if amazon_ads_rows:
        for r in amazon_ads_rows:
            rows.append({
                "platform":      "Amazon",
                "campaign_name": r.get("campaign_name", ""),
                "campaign_type": r.get("campaign_type", "Sponsored Products"),
                "spend":         round(float(r.get("spend", 0)), 2),
                "impressions":   r.get("impressions"),
                "clicks":        r.get("clicks"),
            })

    return rows


# ---------------------------------------------------------------------------
# Summary totals
# ---------------------------------------------------------------------------

def build_summary(
    channel_fee_rows: list[dict],
    fba_rows: list[dict],
    ad_spend_rows: list[dict],
    gross_by_channel: dict[str, float],
) -> dict:
    """
    Compute top-level summary figures.

    Returns dict with: gross, total_fees, total_fba, total_ads, combined, net
    """
    gross      = round(sum(gross_by_channel.values()), 2)
    total_fees = round(sum(r["amount"] for r in channel_fee_rows), 2)
    total_fba  = round(sum(r["amount"] for r in fba_rows), 2)
    total_ads  = round(sum(r["spend"]  for r in ad_spend_rows), 2)
    combined   = round(total_fees + total_fba + total_ads, 2)

    return {
        "gross":      gross,
        "total_fees": total_fees,
        "total_fba":  total_fba,
        "total_ads":  total_ads,
        "combined":   combined,
        "net":        round(gross - combined, 2),
    }
