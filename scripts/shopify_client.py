"""
Shopify GraphQL client for MowDirect automation scripts.

Usage:
    from scripts.shopify_client import ShopifyClient

    with ShopifyClient() as client:
        result = client.execute("{ shop { name } }")
        print(result)

Credentials are read from .env (SHOPIFY_STORE_DOMAIN, SHOPIFY_CLIENT_ID,
SHOPIFY_CLIENT_SECRET, SHOPIFY_API_VERSION).
"""
import os
import time
import json
import requests
import shopify
from dotenv import load_dotenv

load_dotenv()

_STORE   = os.environ["SHOPIFY_STORE_DOMAIN"]
_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2026-01")
_ID      = os.environ["SHOPIFY_CLIENT_ID"]
_SECRET  = os.environ["SHOPIFY_CLIENT_SECRET"]


def _fetch_token() -> tuple[str, float]:
    """Exchange client credentials for an access token.
    Returns (token, expiry_timestamp).
    """
    resp = requests.post(
        f"https://{_STORE}/admin/oauth/access_token",
        json={"client_id": _ID, "client_secret": _SECRET, "grant_type": "client_credentials"},
    )
    resp.raise_for_status()
    data = resp.json()
    expires_in = data.get("expires_in", 86399)
    # Refresh a minute early to avoid edge-case expiry mid-request
    expiry = time.time() + expires_in - 60
    return data["access_token"], expiry


class ShopifyClient:
    """
    Context manager that manages token acquisition and session lifecycle.

    with ShopifyClient() as client:
        result = client.execute("{ shop { name } }")

    Token is refreshed automatically if it has expired.
    """

    def __init__(self):
        self._token: str | None = None
        self._expiry: float = 0

    def _ensure_token(self):
        if not self._token or time.time() >= self._expiry:
            self._token, self._expiry = _fetch_token()
            session = shopify.Session(_STORE, _VERSION, self._token)
            shopify.ShopifyResource.activate_session(session)

    def execute(self, query: str, variables: dict | None = None) -> dict:
        """Run a GraphQL query/mutation. Returns the parsed response dict."""
        self._ensure_token()
        raw = shopify.GraphQL().execute(query=query, variables=variables)
        result = json.loads(raw)
        if "errors" in result:
            raise RuntimeError(f"GraphQL errors: {result['errors']}")
        return result["data"]

    def __enter__(self):
        self._ensure_token()
        return self

    def __exit__(self, *_):
        shopify.ShopifyResource.clear_session()
