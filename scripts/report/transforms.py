"""
Data transformation and aggregation helpers for the monthly spend report.

Converts raw API responses into the normalised dicts that excel_writer expects.
All monetary values are rounded to 2 decimal places (GBP).

VAT handling
------------
The funder report is presented **net of VAT** (ex-VAT). MowDirect's catalogue
is entirely standard-rated UK garden machinery (20% VAT). Revenue figures
fetched from the source APIs are inc-VAT by platform convention, so the
aggregators back out 20% via `_to_net()` before returning. Marketplace
fees and ad spend are NOT converted — those costs are charged ex-VAT to the
seller (input VAT is recoverable), so their raw values are already the right
cost basis for an ex-VAT P&L.
"""
from __future__ import annotations

from datetime import datetime


# Standard-rate UK VAT divisor. Apply to inc-VAT revenue figures to back
# out output VAT and present net revenue.
_VAT_DIVISOR = 1.20


def _to_net(value: float) -> float:
    """Divide a (presumed inc-VAT) revenue figure by 1.20 to get the net amount."""
    return float(value) / _VAT_DIVISOR


# ---------------------------------------------------------------------------
# eBay
# ---------------------------------------------------------------------------

# Human-readable labels for fee types we surface to funders.
EBAY_FEE_LABELS: dict[str, str] = {
    "FINAL_VALUE_FEE":                 "Final value fees",
    "FINAL_VALUE_FEE_FIXED_PER_ORDER": "Fixed per-order fee",
    "REGULATORY_OPERATING_FEE":        "Regulatory operating fee",
    "INTERNATIONAL_FEE":               "International fee",
    "INSERTION_FEE":                   "Insertion fees",
    "OTHER_FEES":                      "Other eBay charges",
    "SUBSCRIPTION_FEE":                "Store subscription",
    "SHIPPING_LABEL":                  "Shipping label costs",
    # Ad-spend types — surfaced on the Ad Spend tab via `EBAY_AD_FEE_TYPES`,
    # not as fees in the Marketplace tab.
    "AD_FEE":                          "Promoted Listings (Standard)",
    "PREMIUM_AD_FEES":                 "Promoted Listings (Priority)",
}

# `feeType` values inside NON_SALE_CHARGE that represent paid advertising
# (Promoted Listings). These go on the Ad Spend tab, not the fees table.
EBAY_AD_FEE_TYPES: set[str] = {"AD_FEE", "PREMIUM_AD_FEES"}


def aggregate_ebay_transactions(transactions: list[dict]) -> dict:
    """
    Walk eBay Finance transactions and produce a normalised report aggregate.

    eBay's Finances API moved away from per-fee transaction types: marketplace
    fees (FVF, regulatory, fixed-per-order, international) are now itemised
    inside each SALE transaction's `marketplaceFees[]` array, and `SALE.amount`
    is the *seller's net take* — not the order proceeds. Order proceeds are
    in `totalFeeBasisAmount`. Other fee categories (ad spend, insertion,
    misc) come through as separate NON_SALE_CHARGE transactions carrying a
    `feeType` field. The shape returned here makes those distinctions
    explicit so `monthly_report.py` can build a Marketplace-Fees tab that
    reconciles to the eBay seller-hub dashboard.

    Returns:
        {
            "order_proceeds":        float,                # inc-VAT, gross
            "marketplace_fees":      {feeType: amount},    # from SALE rows
            "marketplace_fees_total": float,
            "non_sale_charges":      {feeType: amount},    # NON_SALE_CHARGE
            "ad_spend":              float,                # AD_FEE+PREMIUM
            "ad_spend_by_type":      {feeType: amount},
            "other_charges":         float,                # non-ad NSC
            "refunds":               float,                # REFUND total
            "disputes":              float,                # DISPUTE total
            "credits":               float,                # CREDIT total (fee refunds)
            "transfers":             float,                # TRANSFER total
            "transaction_counts":    {transactionType: int},
        }
    """
    order_proceeds = 0.0
    mp_fees: dict[str, float] = {}
    nsc:     dict[str, float] = {}
    refunds = disputes = credits = transfers = 0.0
    counts: dict[str, int] = {}

    for txn in transactions:
        t_type = txn.get("transactionType", "UNKNOWN")
        counts[t_type] = counts.get(t_type, 0) + 1
        try:
            amt = float((txn.get("amount") or {}).get("value", 0) or 0)
        except (TypeError, ValueError):
            amt = 0.0

        if t_type == "SALE":
            try:
                order_proceeds += float(
                    (txn.get("totalFeeBasisAmount") or {}).get("value", 0) or 0
                )
            except (TypeError, ValueError):
                pass
            for line in txn.get("orderLineItems") or []:
                for mf in line.get("marketplaceFees") or []:
                    ft  = mf.get("feeType", "UNKNOWN")
                    try:
                        fee_amt = float((mf.get("amount") or {}).get("value", 0) or 0)
                    except (TypeError, ValueError):
                        fee_amt = 0.0
                    mp_fees[ft] = mp_fees.get(ft, 0.0) + fee_amt
        elif t_type == "NON_SALE_CHARGE":
            ft = txn.get("feeType") or "OTHER"
            nsc[ft] = nsc.get(ft, 0.0) + amt
        elif t_type == "REFUND":
            refunds += amt
        elif t_type == "DISPUTE":
            disputes += amt
        elif t_type == "CREDIT":
            credits += amt
        elif t_type == "TRANSFER":
            transfers += amt

    mp_fees_total = sum(mp_fees.values())
    ad_by_type    = {k: v for k, v in nsc.items() if k in EBAY_AD_FEE_TYPES}
    ad_spend      = sum(ad_by_type.values())
    other_charges = sum(v for k, v in nsc.items() if k not in EBAY_AD_FEE_TYPES)

    return {
        "order_proceeds":          round(order_proceeds, 2),
        "marketplace_fees":        {k: round(v, 2) for k, v in mp_fees.items()},
        "marketplace_fees_total":  round(mp_fees_total, 2),
        "non_sale_charges":        {k: round(v, 2) for k, v in nsc.items()},
        "ad_spend":                round(ad_spend, 2),
        "ad_spend_by_type":        {k: round(v, 2) for k, v in ad_by_type.items()},
        "other_charges":           round(other_charges, 2),
        "refunds":                 round(refunds, 2),
        "disputes":                round(disputes, 2),
        "credits":                 round(credits, 2),
        "transfers":               round(transfers, 2),
        "transaction_counts":      counts,
    }


def ebay_fee_rows(aggregated: dict) -> list[dict]:
    """
    Build the eBay fee-row list for the Marketplace Fees & Commissions tab.

    Each row: {fee_type, label, amount (positive = cost), is_ad_spend}.
    Ad-spend lines are excluded (they go on the Ad Spend tab). Credits net
    off the marketplace-fee total because they are typically FVF refunds
    issued by eBay when an order is refunded.
    """
    rows: list[dict] = []

    # Marketplace fees deducted at point of sale (FVF, regulatory, etc.)
    for ft, amt in sorted(aggregated.get("marketplace_fees", {}).items(),
                           key=lambda x: -x[1]):
        if amt == 0:
            continue
        rows.append({
            "fee_type":    ft,
            "label":       EBAY_FEE_LABELS.get(ft, ft.replace("_", " ").title()),
            "amount":      round(amt, 2),
            "is_ad_spend": False,
        })

    # Non-ad, non-sale charges (insertion fees, store subs, oddments).
    for ft, amt in sorted(aggregated.get("non_sale_charges", {}).items(),
                           key=lambda x: -x[1]):
        if amt == 0 or ft in EBAY_AD_FEE_TYPES:
            continue
        rows.append({
            "fee_type":    ft,
            "label":       EBAY_FEE_LABELS.get(ft, ft.replace("_", " ").title()),
            "amount":      round(amt, 2),
            "is_ad_spend": False,
        })

    # Disputes — money out to the seller (lost dispute or chargeback).
    if aggregated.get("disputes", 0) > 0:
        rows.append({
            "fee_type":    "DISPUTE",
            "label":       "Disputes / chargebacks",
            "amount":      round(aggregated["disputes"], 2),
            "is_ad_spend": False,
        })

    # Credits — eBay refunding fees to the seller; net off as a negative row.
    if aggregated.get("credits", 0) > 0:
        rows.append({
            "fee_type":    "CREDIT",
            "label":       "Fee credits (refunds from eBay)",
            "amount":      round(-aggregated["credits"], 2),
            "is_ad_spend": False,
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
        {source_key: {net, commission, order_count, source_label}}

    `net` is revenue with 20% VAT backed out from BaseLinker's inc-VAT
    `price_brutto` line totals.
    """
    source_labels = {
        "manomano": "ManoMano",
        "onbuy":    "OnBuy",
        "amazon":   "Amazon (BaseLinker fallback)",
        "ebay":     "eBay (BaseLinker cross-ref)",
    }
    result: dict[str, dict] = {}
    for source, orders in orders_by_source.items():
        gross_inc_vat = sum(
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
            "net":          round(_to_net(gross_inc_vat), 2),
            "commission":   round(commission, 2),
            "order_count":  len(orders),
        }
    return result


# ---------------------------------------------------------------------------
# Mirakl / B&Q
# ---------------------------------------------------------------------------

_MIRAKL_NON_REVENUE_STATES = {"CANCELED", "REFUSED", "REFUNDED"}


def aggregate_mirakl_orders(orders: list[dict]) -> dict:
    """
    Summarise Mirakl orders into {net, commission, order_count}.

    Excludes orders in CANCELED/REFUSED/REFUNDED states so the net figure
    reflects revenue actually earned. Cancellations are reported separately
    via aggregate_mirakl_cancellations. `net` is revenue ex-VAT.
    """
    gross_inc_vat = 0.0
    commission = 0.0
    counted = 0
    for order in orders:
        state = (order.get("order_state") or order.get("state") or "").upper()
        if state in _MIRAKL_NON_REVENUE_STATES:
            continue
        # Mirakl order total: price_amount (before commission)
        price = order.get("price", 0)
        gross_inc_vat += float(price if not isinstance(price, dict) else price.get("amount", 0) or 0)
        comm = order.get("total_commission", order.get("commission", 0))
        commission += float(comm if not isinstance(comm, dict) else comm.get("amount", 0) or 0)
        counted += 1
    return {
        "net":         round(_to_net(gross_inc_vat), 2),
        "commission":  round(commission, 2),
        "order_count": counted,
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
    """
    Sum Shopify order rows into {net, fee_amount, order_count}.

    Rows arrive with `net` already ex-VAT (the VAT back-out happens in
    `data_sources.fetch_shopify_fees`).
    """
    return {
        "net":         round(sum(r["net"]        for r in rows), 2),
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
        "FBARemovalFee":                  "FBA removal fees",
        "FBA Removal Fee":                "FBA removal fees",
        "FBADisposalFee":                 "FBA disposal fees",
        "FBA Disposal Fee":               "FBA disposal fees",
        "DisposalComplete":               "FBA disposal fees",
        "RemovalShipping":                "FBA removal shipping",
        "ReturnShipping":                 "Buyer return shipping",
    }
    return labels.get(fee_type, fee_type)


# ---------------------------------------------------------------------------
# Cancellations — Shopify, Amazon, Mirakl
# ---------------------------------------------------------------------------

# Per-platform reason → attribution bucket (customer / seller / other).
# Where the platform doesn't expose a clean reason, we fall back to "Unknown".
SHOPIFY_REASON_ATTRIBUTION: dict[str, str] = {
    "CUSTOMER":  "Customer",
    "DECLINED":  "Customer (payment declined)",
    "FRAUD":     "Seller (fraud)",
    "INVENTORY": "Seller (out of stock)",
    "OTHER":     "Unknown",
    "STAFF":     "Seller (staff cancelled)",
}


def aggregate_shopify_cancellations(rows: list[dict]) -> dict:
    """
    Bucket Shopify orders by cancellation status. Operates on the rows
    returned by fetch_shopify_fees (which already includes cancelled_at +
    cancel_reason).
    """
    cancelled = [r for r in rows if r.get("cancelled_at")]
    by_reason: dict[str, dict] = {}
    for r in cancelled:
        raw = (r.get("cancel_reason") or "OTHER").upper()
        bucket = SHOPIFY_REASON_ATTRIBUTION.get(raw, raw.title())
        b = by_reason.setdefault(bucket, {"orders": 0, "value": 0.0})
        b["orders"] += 1
        # `net` already ex-VAT in the row (back-out applied in data_sources)
        b["value"]  += float(r.get("net", 0) or 0)

    return {
        "total_orders": len(cancelled),
        "total_value":  round(sum(b["value"] for b in by_reason.values()), 2),
        "by_reason": [
            {"label": k, "orders": v["orders"], "value": round(v["value"], 2)}
            for k, v in sorted(by_reason.items(), key=lambda x: -x[1]["value"])
        ],
        "all_orders": len(rows),
    }


def aggregate_amazon_cancellations(orders: list[dict]) -> dict:
    """
    Bucket Amazon cancelled orders.

    SP-API's `IsBuyerRequestedCancellation` field exists in the schema but
    is null on virtually every order — Amazon doesn't expose attribution
    via the Orders API. We surface a single bucket with a note rather than
    inventing a customer/seller split that would be misleading.

    Many cancelled orders also have OrderTotal=null, so the £ figure is
    a partial estimate (only counts orders where Amazon kept the total).
    """
    gross_inc_vat = 0.0
    has_total     = 0
    for o in orders:
        amt_obj = o.get("OrderTotal")
        if amt_obj:
            try:
                gross_inc_vat += float(amt_obj.get("Amount", 0) or 0)
                has_total     += 1
            except (TypeError, ValueError):
                pass

    if not orders:
        return {"total_orders": 0, "total_value": 0.0, "by_reason": []}

    # SP-API OrderTotal is inc-VAT; back out 20% for the funder report's net basis.
    total_value = round(_to_net(gross_inc_vat), 2)

    return {
        "total_orders": len(orders),
        "total_value":  total_value,
        "by_reason": [{
            "label":  f"Cancelled (attribution not exposed by SP-API; "
                      f"£ figure covers {has_total} of {len(orders)} orders)",
            "orders": len(orders),
            "value":  total_value,
        }],
    }


def aggregate_mirakl_cancellations(orders: list[dict]) -> dict:
    """
    Bucket B&Q (Mirakl) cancelled orders by state — CANCELED (mostly customer)
    vs REFUSED (we declined to accept).

    Note: Mirakl zeroes the order total on cancellation, so `value` is
    structurally £0 for these rows. We still count and label them.
    """
    state_label = {
        "CANCELED": "Customer-cancelled",
        "REFUSED":  "Seller-refused",
    }
    buckets: dict[str, dict] = {}
    for o in orders:
        state = (o.get("order_state") or o.get("state") or "").upper()
        label = state_label.get(state, state.title() or "Unknown")
        b = buckets.setdefault(label, {"orders": 0, "value": 0.0})
        b["orders"] += 1
        # Mirakl returns 0 on cancellation, but include if non-zero just in case.
        # Back out VAT so the £ basis matches the net-revenue figures elsewhere.
        try:
            tp = o.get("total_price", 0)
            value = float(tp if not isinstance(tp, dict) else tp.get("amount", 0) or 0)
        except (TypeError, ValueError):
            value = 0.0
        b["value"] += _to_net(value)

    return {
        "total_orders": len(orders),
        "total_value":  round(sum(b["value"] for b in buckets.values()), 2),
        "by_reason": [
            {"label": k, "orders": v["orders"], "value": round(v["value"], 2)}
            for k, v in sorted(buckets.items(), key=lambda x: -x[1]["orders"])
        ],
    }


# ---------------------------------------------------------------------------
# Amazon FBA returns / removals / unfulfillable inventory
# ---------------------------------------------------------------------------

# Amazon disposition strings → display labels.
# SELLABLE = went back to active inventory; UNSELLABLE_* = stranded as unfulfillable.
RETURN_DISPOSITION_LABELS: dict[str, str] = {
    "SELLABLE":                       "Returned to sellable stock",
    # Amazon uses both UNSELLABLE_X and bare X variants depending on report version
    "UNSELLABLE_CUSTOMER_DAMAGED":    "Unsellable — customer damaged",
    "CUSTOMER_DAMAGED":               "Unsellable — customer damaged",
    "UNSELLABLE_DEFECTIVE":           "Unsellable — defective",
    "DEFECTIVE":                      "Unsellable — defective",
    "UNSELLABLE_CARRIER_DAMAGED":     "Unsellable — carrier damaged",
    "CARRIER_DAMAGED":                "Unsellable — carrier damaged",
    "UNSELLABLE_DAMAGED":             "Unsellable — damaged",
    "DAMAGED":                        "Unsellable — damaged",
    "UNSELLABLE_DISTRIBUTOR_DAMAGED": "Unsellable — fulfilment-centre damaged",
    "DISTRIBUTOR_DAMAGED":            "Unsellable — fulfilment-centre damaged",
    "UNSELLABLE_NO_INVENTORY_RECEIVED": "Unsellable — nothing received",
    "NO_INVENTORY_RECEIVED":          "Unsellable — nothing received",
    "UNSELLABLE_EXPIRED":             "Unsellable — expired",
    "EXPIRED":                        "Unsellable — expired",
}

# Fee types in Finances data that relate to FBA removals/disposals
REMOVAL_FEE_KEYWORDS: tuple[str, ...] = ("Removal", "Disposal", "ReturnShipping")


def aggregate_customer_returns(rows: list[dict]) -> dict:
    """
    Bucket customer-returns rows by detailed-disposition.

    Returns:
        {
            "by_disposition": [{disposition, label, units, lines}],
            "total_units":    int,
            "total_lines":    int,
            "sellable_units": int,
            "unsellable_units": int,
        }
    """
    buckets: dict[str, dict] = {}
    for row in rows:
        disp  = (row.get("detailed-disposition") or "").strip() or "UNKNOWN"
        try:
            qty = int(row.get("quantity") or 0)
        except ValueError:
            qty = 0
        b = buckets.setdefault(disp, {"units": 0, "lines": 0})
        b["units"] += qty
        b["lines"] += 1

    by_disposition = [
        {
            "disposition": disp,
            "label":       RETURN_DISPOSITION_LABELS.get(disp, disp.replace("_", " ").title()),
            "units":       data["units"],
            "lines":       data["lines"],
        }
        for disp, data in sorted(buckets.items(), key=lambda x: -x[1]["units"])
    ]
    total_units      = sum(b["units"] for b in by_disposition)
    sellable_units   = sum(b["units"] for b in by_disposition if b["disposition"] == "SELLABLE")
    unsellable_units = total_units - sellable_units

    return {
        "by_disposition":   by_disposition,
        "total_units":      total_units,
        "total_lines":      sum(b["lines"] for b in by_disposition),
        "sellable_units":   sellable_units,
        "unsellable_units": unsellable_units,
    }


def aggregate_removal_shipments(rows: list[dict]) -> dict:
    """
    Bucket removal-shipment rows by order-type (Return / Disposal / Liquidations).

    Returns:
        {
            "by_type": [{order_type, units, lines}],
            "total_units": int,
            "returned_units": int,    # shipped back to seller
            "disposed_units": int,    # destroyed by Amazon
            "liquidated_units": int,  # sold to liquidator
        }
    """
    buckets: dict[str, dict] = {}
    for row in rows:
        otype = (row.get("order-type") or row.get("removal-order-type") or "").strip() or "UNKNOWN"
        try:
            qty = int(row.get("shipped-quantity") or 0)
        except ValueError:
            qty = 0
        b = buckets.setdefault(otype, {"units": 0, "lines": 0})
        b["units"] += qty
        b["lines"] += 1

    by_type = [
        {"order_type": k, "units": v["units"], "lines": v["lines"]}
        for k, v in sorted(buckets.items(), key=lambda x: -x[1]["units"])
    ]

    def _sum(*types) -> int:
        return sum(v["units"] for k, v in buckets.items() if k in types)

    return {
        "by_type":          by_type,
        "total_units":      sum(v["units"] for v in buckets.values()),
        "returned_units":   _sum("Return"),
        "disposed_units":   _sum("Disposal"),
        "liquidated_units": _sum("Liquidations"),
    }


def aggregate_inventory_snapshot(summaries: list[dict]) -> dict:
    """
    Sum per-SKU FBA inventory into headline counts. We care about the
    "researching/unfulfillable" buckets — these are units Amazon is holding
    that we could request a removal for.

    The Inventory API returns inventoryDetails with reservedQuantity,
    researchingQuantity, unfulfillableQuantity, fulfillableQuantity, etc.
    """
    fulfillable     = 0
    unfulfillable   = 0
    inbound_working = 0
    inbound_shipped = 0
    inbound_receiving = 0
    researching     = 0
    sku_count       = 0

    for s in summaries:
        sku_count += 1
        details = s.get("inventoryDetails") or {}
        fulfillable     += int(details.get("fulfillableQuantity") or 0)
        unfulfillable   += int(details.get("unfulfillableQuantity", {}).get("totalUnfulfillableQuantity")
                               if isinstance(details.get("unfulfillableQuantity"), dict)
                               else (details.get("unfulfillableQuantity") or 0))
        researching_obj = details.get("researchingQuantity") or {}
        researching += int(researching_obj.get("totalResearchingQuantity") or 0) \
            if isinstance(researching_obj, dict) else int(researching_obj or 0)
        inbound_working   += int(details.get("inboundWorkingQuantity") or 0)
        inbound_shipped   += int(details.get("inboundShippedQuantity") or 0)
        inbound_receiving += int(details.get("inboundReceivingQuantity") or 0)

    return {
        "sku_count":         sku_count,
        "fulfillable":       fulfillable,
        "unfulfillable":     unfulfillable,
        "researching":       researching,
        "inbound_working":   inbound_working,
        "inbound_shipped":   inbound_shipped,
        "inbound_receiving": inbound_receiving,
        # convenience: units sitting at Amazon that could be picked up
        "available_to_pickup": unfulfillable + researching,
    }


def extract_removal_fees(amazon_fee_rows: list[dict]) -> dict:
    """
    Pull removal/disposal fee rows out of the Finances data we already
    fetched, so we can show the £ cost of returns activity alongside unit
    counts.

    Returns {label: total_amount} keyed by readable labels.
    """
    totals: dict[str, float] = {}
    for row in amazon_fee_rows:
        fee_type = row.get("fee_type", "") or ""
        if not any(kw in fee_type for kw in REMOVAL_FEE_KEYWORDS):
            continue
        amount = abs(float(row.get("amount", 0) or 0))
        label  = _normalise_amazon_fee_label(fee_type)
        totals[label] = round(totals.get(label, 0.0) + amount, 2)
    return totals


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
                "platform":          "Google Ads",
                "campaign_name":     r.get("campaign_name", ""),
                "campaign_type":     r.get("campaign_type", ""),
                "spend":             round(float(r.get("spend_gbp", 0)), 2),
                "impressions":       int(r.get("impressions", 0)),
                "clicks":            int(r.get("clicks", 0)),
                "conversions":       float(r.get("conversions", 0)),
                "conversions_value": round(float(r.get("conversions_value", 0)), 2),
            })

    if ebay_ad_amount is not None:
        rows.append({
            "platform":          "eBay",
            "campaign_name":     "Promoted Listings",
            "campaign_type":     "PROMOTED_LISTINGS",
            "spend":             round(ebay_ad_amount, 2),
            "impressions":       None,
            "clicks":            None,
            "conversions":       None,
            "conversions_value": None,
        })

    if amazon_ads_rows:
        for r in amazon_ads_rows:
            rows.append({
                "platform":          "Amazon",
                "campaign_name":     r.get("campaign_name", ""),
                "campaign_type":     r.get("campaign_type", "Sponsored Products"),
                "spend":             round(float(r.get("spend", 0)), 2),
                "impressions":       r.get("impressions"),
                "clicks":            r.get("clicks"),
                "conversions":       r.get("conversions"),
                "conversions_value": r.get("conversions_value"),
            })

    return rows


# ---------------------------------------------------------------------------
# Summary totals
# ---------------------------------------------------------------------------

def build_summary(
    channel_fee_rows: list[dict],
    ad_spend_rows: list[dict],
    net_by_channel: dict[str, float],
    wayne_commission: float = 0.0,
    wayne_commission_note: str | None = None,
    wayne_commission_overridden: bool = False,
) -> dict:
    """
    Compute top-level summary figures.

    All revenue values are ex-VAT (net). `wayne_commission` is shown on the
    Summary tab in place of the old FBA cost-of-sales aggregate;
    `wayne_commission_note` is the explanatory text rendered alongside it
    (e.g. "4% of Net Revenue across all channels" vs "Invoiced Value"); and
    `wayne_commission_overridden` signals that the figure came from a
    manual audit input — the % column for that row should render an
    em-dash rather than `audited / net`, so funders don't see e.g. 2.9%
    and ask why it isn't the headline 4%. FBA fees are still surfaced
    inside the per-channel commission/fee rows where relevant; they are
    deliberately excluded from the headline deductions.

    Returns dict with: net, total_fees, wayne_commission,
    wayne_commission_note, wayne_commission_overridden, total_ads,
    combined, contribution
    """
    net        = round(sum(net_by_channel.values()), 2)
    total_fees = round(sum(r["amount"] for r in channel_fee_rows), 2)
    total_ads  = round(sum(r["spend"]  for r in ad_spend_rows), 2)
    commission = round(float(wayne_commission), 2)
    combined   = round(total_fees + commission + total_ads, 2)

    return {
        "net":                          net,
        "total_fees":                   total_fees,
        "wayne_commission":             commission,
        "wayne_commission_note":        wayne_commission_note,
        "wayne_commission_overridden":  wayne_commission_overridden,
        "total_ads":                    total_ads,
        "combined":                     combined,
        "contribution":                 round(net - combined, 2),
    }
