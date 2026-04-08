"""
eBay Finances API client for MowDirect automation scripts.

Wraps the eBay Finances API to retrieve fee transactions, ad spend, and
payout data. Uses the existing EBayClient for auth — no new credentials needed,
but the refresh token must include the sell.finances.readonly scope (re-run
ebay_auth.py after adding that scope to ebay_client.py).

Usage:
    from scripts.ebay_finances_client import EBayFinancesClient
    from datetime import datetime

    client = EBayFinancesClient()
    transactions = client.get_transactions(
        datetime(2026, 3, 1), datetime(2026, 3, 31, 23, 59, 59)
    )
    summary = client.get_fee_summary(datetime(2026, 3, 1), datetime(2026, 3, 31, 23, 59, 59))

Transaction types returned by the API:
    SALE, REFUND, CREDIT, DISPUTE, NON_SALE_CHARGE,
    FINAL_VALUE_FEE, AD_FEE, SHIPPING_LABEL, SUBSCRIPTION_FEE,
    TRANSFER, ADJUSTMENT, WITHDRAWAL, DEPOSIT

API docs: https://developer.ebay.com/api-docs/sell/finances/resources/transaction/methods/getTransactions
"""
from __future__ import annotations

from datetime import datetime, timezone

from scripts.ebay_client import EBayClient

_PATH = "/sell/finances/v1/transaction"
_PAGE_LIMIT = 200


class EBayFinancesClient:
    """
    eBay Finances API wrapper.

    Handles pagination automatically. All monetary amounts are returned in
    the currency of the seller account (GBP for MowDirect).
    """

    def __init__(self):
        self._client = EBayClient()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_transactions(
        self,
        start: datetime,
        end: datetime,
        transaction_type: str | None = None,
    ) -> list[dict]:
        """
        Fetch all finance transactions between start and end (inclusive).

        Args:
            start:            Start of period (UTC datetime).
            end:              End of period (UTC datetime).
            transaction_type: Optional filter, e.g. "FINAL_VALUE_FEE",
                              "AD_FEE", "SHIPPING_LABEL". None = all types.

        Returns:
            List of raw transaction dicts from the eBay API.
        """
        params: dict = {
            "filter": self._date_filter(start, end),
            "limit":  _PAGE_LIMIT,
            "offset": 0,
        }
        if transaction_type:
            params["filter"] += f",transactionType:{{{transaction_type}}}"

        results: list[dict] = []
        while True:
            data = self._client.get(_PATH, params=params)
            batch = data.get("transactions", [])
            results.extend(batch)
            total = data.get("total", 0)
            params["offset"] += len(batch)
            if params["offset"] >= total or not batch:
                break

        return results

    def get_fee_summary(self, start: datetime, end: datetime) -> dict[str, float]:
        """
        Fetch all transactions and return amounts grouped by transaction type.

        Returns dict like:
            {
                "FINAL_VALUE_FEE": -1234.56,
                "AD_FEE":           -78.90,
                "SHIPPING_LABEL":   -45.00,
                "SUBSCRIPTION_FEE": -49.00,
                ...
            }
        Amounts are negative for charges, positive for credits/refunds —
        matching the raw eBay API convention.
        """
        transactions = self.get_transactions(start, end)
        summary: dict[str, float] = {}
        for txn in transactions:
            t_type = txn.get("transactionType", "UNKNOWN")
            amount = self._parse_amount(txn)
            summary[t_type] = round(summary.get(t_type, 0.0) + amount, 2)
        return summary

    def get_ad_spend(self, start: datetime, end: datetime) -> float:
        """
        Return total eBay Promoted Listings spend (AD_FEE transactions).
        Returns a positive float (spend) even though stored as negative in API.
        """
        txns = self.get_transactions(start, end, transaction_type="AD_FEE")
        return round(abs(sum(self._parse_amount(t) for t in txns)), 2)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _date_filter(start: datetime, end: datetime) -> str:
        """
        Build the eBay filter string for a date range.
        eBay expects ISO 8601 UTC: transactionDate:[2026-03-01T00:00:00.000Z..]
        """
        def fmt(dt: datetime) -> str:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        return f"transactionDate:[{fmt(start)}..{fmt(end)}]"

    @staticmethod
    def _parse_amount(txn: dict) -> float:
        """Extract the GBP amount from a transaction dict."""
        try:
            return float(txn["amount"]["value"])
        except (KeyError, TypeError, ValueError):
            return 0.0
