"""
BaseLinker (Base.com) API client for MowDirect automation scripts.

Usage:
    from scripts.baselinker_client import BaseLinkerClient

    client = BaseLinkerClient()
    orders = client.call("getOrders", {"date_from": 1234567890})

Credentials are read from .env (BASELINKER_API_TOKEN).
API docs: https://api.baselinker.com/
"""
import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

_ENDPOINT = "https://api.baselinker.com/connector.php"


class BaseLinkerClient:
    """
    Thin wrapper around the BaseLinker RPC API.

    All calls go to a single endpoint with method + parameters.
    Raises on HTTP errors and on API-level error responses.
    """

    def __init__(self):
        token = os.environ.get("BASELINKER_API_TOKEN")
        if not token:
            raise EnvironmentError("BASELINKER_API_TOKEN not set in environment or .env")
        self._headers = {"X-BLToken": token}

    def call(self, method: str, parameters: dict | None = None) -> dict:
        """
        Call a BaseLinker API method.

        Args:
            method:     API method name, e.g. "getOrders"
            parameters: Dict of method parameters (optional)

        Returns:
            The response dict (status == "SUCCESS" guaranteed, or raises).
        """
        payload = {
            "method": method,
            "parameters": json.dumps(parameters or {}),
        }
        resp = requests.post(_ENDPOINT, data=payload, headers=self._headers)
        resp.raise_for_status()

        data = resp.json()
        if data.get("status") != "SUCCESS":
            raise RuntimeError(
                f"BaseLinker error calling {method!r}: "
                f"{data.get('error_code')} — {data.get('error_message')}"
            )
        return data
