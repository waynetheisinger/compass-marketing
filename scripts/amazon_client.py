"""
Amazon SP-API client for MowDirect automation scripts.

Usage:
    from scripts.amazon_client import AmazonClient

    client = AmazonClient()
    data = client.get("/sellers/v1/marketplaceParticipations")
    orders = client.get(
        "/orders/v0/orders",
        params={"MarketplaceIds": client.marketplace_id, "CreatedAfter": "2026-04-01T00:00:00Z"},
    )

Credentials are read from .env:
    AMAZON_LWA_CLIENT_ID
    AMAZON_LWA_CLIENT_SECRET
    AMAZON_REFRESH_TOKEN      # from self-authorization in Solution Provider Portal
    AMAZON_MARKETPLACE_ID     # UK: A1F83G8C2ARO7P
    AMAZON_ENDPOINT           # EU: https://sellingpartnerapi-eu.amazon.com
    AMAZON_REGION             # EU: eu-west-1 (reserved for future use — SP-API no
                              # longer requires AWS SigV4 signing as of 2023)

Token refresh is handled automatically — LWA access tokens last 1 hour and are
refreshed silently on expiry. The refresh token is long-lived (does not expire
as long as the app stays authorised in Seller Central).

API docs: https://developer-docs.amazon.com/sp-api/
"""
import csv
import gzip
import io
import os
import time
from datetime import datetime
import requests
from dotenv import load_dotenv

load_dotenv()

_CLIENT_ID       = os.environ["AMAZON_LWA_CLIENT_ID"]
_CLIENT_SECRET   = os.environ["AMAZON_LWA_CLIENT_SECRET"]
_REFRESH_TOKEN   = os.environ["AMAZON_REFRESH_TOKEN"]
_MARKETPLACE_ID  = os.environ["AMAZON_MARKETPLACE_ID"]
_ENDPOINT        = os.environ["AMAZON_ENDPOINT"].rstrip("/")

_LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"


class AmazonSPAPIError(Exception):
    """Raised when SP-API returns a non-2xx response."""

    def __init__(self, status: int, body: str, request_id: str | None = None):
        self.status     = status
        self.body       = body
        self.request_id = request_id
        super().__init__(f"SP-API {status} (x-amzn-RequestId={request_id}): {body}")


class AmazonClient:
    """
    Amazon Selling Partner API client with automatic LWA token refresh.

    All paths are relative to the regional endpoint, e.g. "/sellers/v1/marketplaceParticipations".
    The `marketplace_id` attribute is a convenience for callers that need to pass it as a query param.
    """

    marketplace_id = _MARKETPLACE_ID

    def __init__(self):
        self._access_token: str | None = None
        self._token_expiry: float      = 0

    def _ensure_token(self):
        if not self._access_token or time.time() >= self._token_expiry:
            self._access_token, self._token_expiry = self._refresh_access_token()

    def _refresh_access_token(self) -> tuple[str, float]:
        resp = requests.post(
            _LWA_TOKEN_URL,
            data={
                "grant_type":    "refresh_token",
                "refresh_token": _REFRESH_TOKEN,
                "client_id":     _CLIENT_ID,
                "client_secret": _CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        data   = resp.json()
        expiry = time.time() + data["expires_in"] - 60  # refresh 1 min early
        return data["access_token"], expiry

    def _headers(self) -> dict:
        self._ensure_token()
        return {
            "x-amz-access-token": self._access_token,
            "Content-Type":       "application/json",
            "Accept":             "application/json",
        }

    def _raise_for_status(self, resp: requests.Response):
        if resp.status_code >= 400:
            raise AmazonSPAPIError(
                status=resp.status_code,
                body=resp.text,
                request_id=resp.headers.get("x-amzn-RequestId"),
            )

    def get(self, path: str, params: dict | None = None) -> dict:
        resp = requests.get(f"{_ENDPOINT}{path}", headers=self._headers(), params=params)
        self._raise_for_status(resp)
        return resp.json()

    def post(self, path: str, payload: dict | None = None, params: dict | None = None) -> dict:
        resp = requests.post(f"{_ENDPOINT}{path}", headers=self._headers(), params=params, json=payload)
        self._raise_for_status(resp)
        return resp.json() if resp.content else {}

    def put(self, path: str, payload: dict | None = None, params: dict | None = None) -> dict:
        resp = requests.put(f"{_ENDPOINT}{path}", headers=self._headers(), params=params, json=payload)
        self._raise_for_status(resp)
        return resp.json() if resp.content else {}

    def patch(self, path: str, payload: dict | None = None, params: dict | None = None) -> dict:
        resp = requests.patch(f"{_ENDPOINT}{path}", headers=self._headers(), params=params, json=payload)
        self._raise_for_status(resp)
        return resp.json() if resp.content else {}

    def delete(self, path: str, params: dict | None = None) -> None:
        resp = requests.delete(f"{_ENDPOINT}{path}", headers=self._headers(), params=params)
        self._raise_for_status(resp)

    # -----------------------------------------------------------------
    # Reports API (async: request → poll → download)
    # -----------------------------------------------------------------

    def request_report(
        self,
        report_type: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> str:
        """
        Request a report and return the reportId. For date-ranged reports
        (e.g. customer returns) pass start/end; for snapshot reports
        (e.g. AFN inventory) leave them None.
        """
        body: dict = {
            "reportType":     report_type,
            "marketplaceIds": [_MARKETPLACE_ID],
        }
        if start is not None:
            body["dataStartTime"] = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        if end is not None:
            body["dataEndTime"]   = end.strftime("%Y-%m-%dT%H:%M:%SZ")

        resp = self.post("/reports/2021-06-30/reports", payload=body)
        return resp["reportId"]

    def wait_for_report(self, report_id: str, timeout: int = 600,
                        poll_every: float = 10.0) -> str | None:
        """
        Poll the report until it reaches a terminal state. Returns the
        reportDocumentId on DONE, or None on CANCELLED (Amazon cancels
        reports that have zero rows for the requested period — treated as
        "no data" rather than an error). Raises on FATAL or timeout.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            info = self.get(f"/reports/2021-06-30/reports/{report_id}")
            status = info.get("processingStatus")
            if status == "DONE":
                doc_id = info.get("reportDocumentId")
                if not doc_id:
                    raise AmazonSPAPIError(0, f"Report {report_id} DONE but no documentId")
                return doc_id
            if status == "CANCELLED":
                return None
            if status == "FATAL":
                raise AmazonSPAPIError(0, f"Report {report_id} ended FATAL: {info}")
            time.sleep(poll_every)
        raise AmazonSPAPIError(0, f"Report {report_id} timed out after {timeout}s")

    def download_report_document(self, document_id: str) -> str:
        """
        Fetch the presigned document URL, decompress GZIP if needed, and
        return the text payload (TSV/CSV/JSON depending on report type).
        """
        info = self.get(f"/reports/2021-06-30/documents/{document_id}")
        url        = info["url"]
        compression = info.get("compressionAlgorithm")

        resp = requests.get(url)
        resp.raise_for_status()
        body = resp.content
        if compression == "GZIP":
            body = gzip.decompress(body)
        # SP-API report files are typically Latin-1 / Windows-1252; UTF-8 fallback handles both
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError:
            return body.decode("latin-1")

    def fetch_report_rows(
        self,
        report_type: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict]:
        """
        End-to-end helper: request → wait → download → parse TSV → list of dicts.
        Each dict's keys are the TSV header columns verbatim (kebab-case from Amazon).
        """
        report_id = self.request_report(report_type, start, end)
        doc_id    = self.wait_for_report(report_id)
        if doc_id is None:
            return []  # CANCELLED — no rows for the period
        text      = self.download_report_document(doc_id)
        if not text.strip():
            return []
        reader = csv.DictReader(io.StringIO(text), delimiter="\t")
        return [dict(row) for row in reader]

    # -----------------------------------------------------------------
    # FBA Returns / Removals high-level helpers
    # -----------------------------------------------------------------

    def get_fba_customer_returns(self, start: datetime, end: datetime) -> list[dict]:
        """
        Customer returns received by Amazon for the period, with disposition.

        Each row (TSV columns from Amazon, lower-kebab):
          return-date, order-id, sku, asin, fnsku, product-name, quantity,
          fulfillment-center-id, detailed-disposition, reason, status,
          license-plate-number, customer-comments
        """
        return self.fetch_report_rows(
            "GET_FBA_FULFILLMENT_CUSTOMER_RETURNS_DATA", start, end,
        )

    def get_fba_removal_shipments(self, start: datetime, end: datetime) -> list[dict]:
        """
        Removal shipments processed by Amazon (Return-to-seller or Disposal)
        for the period.

        Each row:
          request-date, order-id, order-type (Return|Disposal|Liquidations),
          shipment-date, sku, fnsku, disposition (Sellable|Unsellable),
          shipped-quantity, carrier, tracking-number, ...
        """
        return self.fetch_report_rows(
            "GET_FBA_FULFILLMENT_REMOVAL_SHIPMENT_DETAIL_DATA", start, end,
        )

    def get_fba_inventory_summary(self) -> list[dict]:
        """
        Current FBA inventory snapshot via the synchronous Inventory API.
        Includes per-SKU fulfillable / unfulfillable / inbound counts, so the
        report can show units 'available to be picked up' (unfulfillable).
        """
        rows: list[dict] = []
        next_token: str | None = None
        while True:
            params: dict = {
                "details":         "true",
                "granularityType": "Marketplace",
                "granularityId":   _MARKETPLACE_ID,
                "marketplaceIds":  _MARKETPLACE_ID,
            }
            if next_token:
                params["nextToken"] = next_token
            resp = self.get("/fba/inventory/v1/summaries", params=params)
            payload   = resp.get("payload", {}) or {}
            rows.extend(payload.get("inventorySummaries", []) or [])
            next_token = (resp.get("pagination") or {}).get("nextToken")
            if not next_token:
                break
            time.sleep(0.5)
        return rows

    def get_cancelled_orders(self, start: datetime, end: datetime) -> list[dict]:
        """
        Fetch orders with OrderStatus=Canceled created between start and end.
        Includes IsBuyerRequestedCancellation for attribution.

        Each row (passed through from SP-API): AmazonOrderId, OrderTotal,
        OrderStatus, IsBuyerRequestedCancellation, CancelReason (sometimes),
        PurchaseDate, MarketplaceId, etc.
        """
        path = "/orders/v0/orders"
        params: dict = {
            "MarketplaceIds":  _MARKETPLACE_ID,
            "CreatedAfter":    start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "CreatedBefore":   end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "OrderStatuses":   "Canceled",
            "MaxResultsPerPage": 100,
        }
        rows: list[dict] = []
        next_token: str | None = None

        while True:
            page = self.get(path,
                            params={"MarketplaceIds": _MARKETPLACE_ID, "NextToken": next_token}
                            if next_token else params)
            payload = page.get("payload", {}) or {}
            rows.extend(payload.get("Orders", []) or [])
            next_token = payload.get("NextToken")
            if not next_token:
                break
            time.sleep(1.0)  # Orders API rate limit

        return rows

    def get_settlement_fees(self, start: datetime, end: datetime) -> list[dict]:
        """
        List Amazon Finances events posted between start and end and return
        flat fee rows for the monthly report.

        Each row: {fee_type, amount, order_id, posted_at, source}.
        Amounts are signed as Amazon reports them (fees are typically negative);
        the downstream aggregator takes abs().

        Pages through Finances API NextToken with a 2.5s sleep — the
        ListFinancialEvents endpoint is rate-limited to ~0.5 req/s.
        """
        path = "/finances/v0/financialEvents"
        params: dict = {
            "PostedAfter":        start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "PostedBefore":       end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "MaxResultsPerPage":  100,
        }
        rows: list[dict] = []
        next_token: str | None = None

        while True:
            page = self.get(path, params={"NextToken": next_token} if next_token else params)
            payload = page.get("payload", {}) or {}
            events  = payload.get("FinancialEvents", {}) or {}
            rows.extend(_flatten_finance_events(events))
            next_token = payload.get("NextToken")
            if not next_token:
                break
            time.sleep(2.5)

        return rows


def _money(amount_obj: dict | None) -> float:
    if not amount_obj:
        return 0.0
    try:
        return float(amount_obj.get("CurrencyAmount", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _flatten_finance_events(events: dict) -> list[dict]:
    rows: list[dict] = []

    for ev in events.get("ShipmentEventList", []) or []:
        order_id = ev.get("AmazonOrderId", "")
        posted   = ev.get("PostedDate", "")
        for item in ev.get("ShipmentItemList", []) or []:
            for fee in item.get("ItemFeeList", []) or []:
                rows.append({
                    "fee_type":  fee.get("FeeType", ""),
                    "amount":    _money(fee.get("FeeAmount")),
                    "order_id":  order_id,
                    "posted_at": posted,
                    "source":    "SP-API Finances (Shipment)",
                })

    for ev in events.get("RefundEventList", []) or []:
        order_id = ev.get("AmazonOrderId", "")
        posted   = ev.get("PostedDate", "")
        for item in ev.get("ShipmentItemAdjustmentList", []) or []:
            for fee in item.get("ItemFeeAdjustmentList", []) or []:
                rows.append({
                    "fee_type":  fee.get("FeeType", ""),
                    "amount":    _money(fee.get("FeeAmount")),
                    "order_id":  order_id,
                    "posted_at": posted,
                    "source":    "SP-API Finances (Refund)",
                })

    for ev in events.get("ServiceFeeEventList", []) or []:
        for fee in ev.get("FeeList", []) or []:
            rows.append({
                "fee_type":  fee.get("FeeType", ""),
                "amount":    _money(fee.get("FeeAmount")),
                "order_id":  ev.get("AmazonOrderId", ""),
                "posted_at": ev.get("PostedDate", ""),
                "source":    "SP-API Finances (Service)",
            })

    return rows


if __name__ == "__main__":
    # Smoke test — hits /sellers/v1/marketplaceParticipations, which needs no special role
    # and confirms LWA auth + endpoint routing end-to-end.
    import json
    client = AmazonClient()
    result = client.get("/sellers/v1/marketplaceParticipations")
    print(json.dumps(result, indent=2))
