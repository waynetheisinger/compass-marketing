"""
eBay REST API client for MowDirect automation scripts.

Usage:
    from scripts.ebay_client import EBayClient

    client = EBayClient()
    orders = client.get("/sell/fulfillment/v1/order", params={"limit": 10})
    items  = client.get("/sell/inventory/v1/inventory_item")

Credentials are read from .env:
    EBAY_CLIENT_ID
    EBAY_CLIENT_SECRET
    EBAY_REFRESH_TOKEN     # generated once via scripts/ebay_auth.py
    EBAY_ENVIRONMENT       # production or sandbox

Token refresh is handled automatically — access tokens last 2 hours and are
refreshed silently on expiry. The refresh token lasts ~18 months.

API docs: https://developer.ebay.com/docs
"""
import os
import time
import base64
import requests
from dotenv import load_dotenv

load_dotenv()

_CLIENT_ID     = os.environ["EBAY_CLIENT_ID"]
_CLIENT_SECRET = os.environ["EBAY_CLIENT_SECRET"]
_REFRESH_TOKEN = os.environ["EBAY_REFRESH_TOKEN"]
_ENV           = os.environ.get("EBAY_ENVIRONMENT", "production")

_TOKEN_URL = (
    "https://api.sandbox.ebay.com/identity/v1/oauth2/token"
    if _ENV == "sandbox"
    else "https://api.ebay.com/identity/v1/oauth2/token"
)
_API_BASE = (
    "https://api.sandbox.ebay.com"
    if _ENV == "sandbox"
    else "https://api.ebay.com"
)

_SCOPES = " ".join([
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment.readonly",
])


class EBayClient:
    """
    eBay REST API client with automatic token refresh.

    Access tokens expire every 2 hours and are refreshed silently.
    All paths are relative to the API base, e.g. "/sell/fulfillment/v1/order".
    """

    def __init__(self):
        self._access_token: str | None = None
        self._token_expiry: float = 0

    def _ensure_token(self):
        if not self._access_token or time.time() >= self._token_expiry:
            self._access_token, self._token_expiry = self._refresh_access_token()

    def _refresh_access_token(self) -> tuple[str, float]:
        credentials = base64.b64encode(f"{_CLIENT_ID}:{_CLIENT_SECRET}".encode()).decode()
        resp = requests.post(
            _TOKEN_URL,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type":  "application/x-www-form-urlencoded",
            },
            data={
                "grant_type":    "refresh_token",
                "refresh_token": _REFRESH_TOKEN,
                "scope":         _SCOPES,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        expiry = time.time() + data["expires_in"] - 60  # refresh 1 min early
        return data["access_token"], expiry

    def _headers(self) -> dict:
        self._ensure_token()
        return {"Authorization": f"Bearer {self._access_token}"}

    def get(self, path: str, params: dict | None = None) -> dict:
        resp = requests.get(f"{_API_BASE}{path}", headers=self._headers(), params=params)
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, payload: dict | None = None) -> dict:
        resp = requests.post(f"{_API_BASE}{path}", headers=self._headers(), json=payload)
        resp.raise_for_status()
        return resp.json()

    def put(self, path: str, payload: dict | None = None) -> dict:
        resp = requests.put(f"{_API_BASE}{path}", headers=self._headers(), json=payload)
        resp.raise_for_status()
        return resp.json()

    def delete(self, path: str) -> None:
        resp = requests.delete(f"{_API_BASE}{path}", headers=self._headers())
        resp.raise_for_status()
