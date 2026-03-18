"""
Mirakl API client for MowDirect automation scripts.

Supports multiple Mirakl marketplace instances (Kingfisher/B&Q, Tesco,
The Range, etc.) — each has its own base URL and API key.

Usage:
    from scripts.mirakl_client import MiraklClient

    # Named instance (reads from .env)
    client = MiraklClient("KINGFISHER")
    orders = client.get("/orders")

    # Or pass credentials directly
    client = MiraklClient(base_url="https://...", api_key="...")

Env var convention per instance:
    MIRAKL_{NAME}_BASE_URL=https://marketplace.example.com/api
    MIRAKL_{NAME}_API_KEY=...

API docs: https://developer.mirakl.com/content/product/mmp/rest/seller/openapi3
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv()


class MiraklClient:
    """
    Thin wrapper around the Mirakl Seller REST API.

    Each marketplace instance (Kingfisher, Tesco, The Range) is a separate
    client with its own base URL and API key.
    """

    def __init__(self, name: str | None = None, *, base_url: str | None = None, api_key: str | None = None):
        """
        Initialise for a named marketplace instance or with explicit credentials.

        Args:
            name:     Marketplace name, e.g. "KINGFISHER". Reads
                      MIRAKL_{NAME}_BASE_URL and MIRAKL_{NAME}_API_KEY from env.
            base_url: Override base URL (required if name not given).
            api_key:  Override API key (required if name not given).
        """
        if name:
            prefix = f"MIRAKL_{name.upper()}"
            base_url = base_url or os.environ.get(f"{prefix}_BASE_URL")
            api_key  = api_key  or os.environ.get(f"{prefix}_API_KEY")
            if not base_url or not api_key:
                raise EnvironmentError(
                    f"{prefix}_BASE_URL and {prefix}_API_KEY must be set in environment or .env"
                )
        elif not base_url or not api_key:
            raise ValueError("Provide either a marketplace name or explicit base_url and api_key")

        self._base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update({"Authorization": api_key})

    def get(self, endpoint: str, params: dict | None = None) -> dict:
        """GET request. Returns parsed JSON."""
        resp = self._session.get(f"{self._base_url}{endpoint}", params=params)
        resp.raise_for_status()
        return resp.json()

    def post(self, endpoint: str, payload: dict | None = None) -> dict:
        """POST request. Returns parsed JSON."""
        resp = self._session.post(f"{self._base_url}{endpoint}", json=payload)
        resp.raise_for_status()
        return resp.json()

    def put(self, endpoint: str, payload: dict | None = None) -> dict:
        """PUT request. Returns parsed JSON."""
        resp = self._session.put(f"{self._base_url}{endpoint}", json=payload)
        resp.raise_for_status()
        return resp.json()
