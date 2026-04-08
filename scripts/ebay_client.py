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
    EBAY_REFRESH_TOKEN          # generated once via scripts/ebay_auth.py
    EBAY_ENVIRONMENT            # production or sandbox
    EBAY_SIGNING_KEY_JWE        # generated once via scripts/ebay_auth.py --signing-key
    EBAY_SIGNING_PRIVATE_KEY    # generated once via scripts/ebay_auth.py --signing-key

Token refresh is handled automatically — access tokens last 2 hours and are
refreshed silently on expiry. The refresh token lasts ~18 months.

Digital signatures are added automatically to paths that require them
(currently: /sell/finances/). Signing keys are long-lived and only need
to be regenerated if revoked.

API docs: https://developer.ebay.com/docs
"""
import os
import time
import base64
import requests
from urllib.parse import urlparse, urlencode
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
# The Finances API uses apiz.ebay.com (not api.ebay.com)
_APIZ_BASE = (
    "https://apiz.sandbox.ebay.com"
    if _ENV == "sandbox"
    else "https://apiz.ebay.com"
)

_SCOPES = " ".join([
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment.readonly",
    "https://api.ebay.com/oauth/api_scope/sell.finances",
])

# Digital signature config (required for Finances API)
_SIGNING_KEY_JWE     = os.environ.get("EBAY_SIGNING_KEY_JWE", "")
_SIGNING_PRIVATE_KEY = os.environ.get("EBAY_SIGNING_PRIVATE_KEY", "").replace("\\n", "\n")

# Paths that require eBay digital signatures and use apiz.ebay.com
_SIGNED_PATH_PREFIXES = ("/sell/finances/",)
_APIZ_PATH_PREFIXES   = ("/sell/finances/",)


def _base_for(path: str) -> str:
    """Return the correct API base URL for the given path."""
    return _APIZ_BASE if any(path.startswith(p) for p in _APIZ_PATH_PREFIXES) else _API_BASE


def _requires_signature(path: str) -> bool:
    return any(path.startswith(p) for p in _SIGNED_PATH_PREFIXES)


def _signature_headers(method: str, full_url: str, body: bytes | None = None) -> dict:
    """
    Compute eBay HTTP Message Signature headers for the given request.

    Implements eBay's Digital Signatures spec (matches official eBay Python SDK):
    https://developer.ebay.com/develop/guides/digital-signatures-for-apis
    https://github.com/eBay/digital-signature-sdk-python
    """
    if not _SIGNING_KEY_JWE or not _SIGNING_PRIVATE_KEY:
        return {}

    import hashlib
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    parsed    = urlparse(full_url)
    path      = parsed.path
    authority = parsed.netloc
    created   = int(time.time())

    # Build component list: include content-digest only when there is a request body.
    # eBay's validator rejects content-digest in Signature-Input for requests with no body.
    _BASE_COMPONENTS = ["x-ebay-signature-key", "@method", "@path", "@authority"]
    lines = []
    digest_header: str | None = None
    if body:
        digest_b64    = base64.b64encode(hashlib.sha256(body).digest()).decode()
        digest_header = f"sha-256=:{digest_b64}:"
        _BASE_COMPONENTS = ["content-digest"] + _BASE_COMPONENTS
        lines.append(f'"content-digest": {digest_header}\n')

    _comp_str  = " ".join(f'"{c}"' for c in _BASE_COMPONENTS)
    sig_params = f"({_comp_str});created={created}"

    # Build signature base (RFC 9421 §2.5):
    # - each component line ends with \n
    # - @signature-params line has NO trailing \n
    lines.extend([
        f'"x-ebay-signature-key": {_SIGNING_KEY_JWE}\n',
        f'"@method": {method.upper()}\n',
        f'"@path": {path}\n',
        f'"@authority": {authority}\n',
        f'"@signature-params": {sig_params}',  # no trailing \n
    ])
    sig_base = "".join(lines)

    pem = _SIGNING_PRIVATE_KEY
    if not pem.startswith("-----BEGIN"):
        pem = f"-----BEGIN PRIVATE KEY-----\n{pem}\n-----END PRIVATE KEY-----\n"
    private_key = load_pem_private_key(pem.encode(), password=None)
    sig_bytes   = private_key.sign(sig_base.encode())
    sig_b64     = base64.b64encode(sig_bytes).decode()

    headers = {
        "x-ebay-signature-key": _SIGNING_KEY_JWE,
        "Signature-Input":      f"sig1={sig_params}",
        "Signature":            f"sig1=:{sig_b64}:",
    }
    if digest_header:
        headers["Content-Digest"] = digest_header
    return headers


class EBayClient:
    """
    eBay REST API client with automatic token refresh and digital signatures.

    Access tokens expire every 2 hours and are refreshed silently.
    Digital signatures are added automatically for paths that require them.
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
        url     = f"{_base_for(path)}{path}"
        headers = self._headers()
        if _requires_signature(path):
            # Prepare the request first so we sign the exact URL that gets sent
            prepared = requests.Request("GET", url, params=params).prepare()
            headers.update(_signature_headers("GET", prepared.url))
            prepared.headers.update(headers)
            resp = requests.Session().send(prepared)
        else:
            resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, payload: dict | None = None) -> dict:
        import json as _json
        url     = f"{_base_for(path)}{path}"
        headers = self._headers()
        body    = _json.dumps(payload).encode() if payload else None
        if _requires_signature(path):
            headers.update(_signature_headers("POST", url, body=body))
        resp = requests.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()

    def put(self, path: str, payload: dict | None = None) -> dict:
        import json as _json
        url     = f"{_base_for(path)}{path}"
        headers = self._headers()
        body    = _json.dumps(payload).encode() if payload else None
        if _requires_signature(path):
            headers.update(_signature_headers("PUT", url, body=body))
        resp = requests.put(url, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()

    def delete(self, path: str) -> None:
        url     = f"{_base_for(path)}{path}"
        headers = self._headers()
        if _requires_signature(path):
            headers.update(_signature_headers("DELETE", url))
        resp = requests.delete(url, headers=headers)
        resp.raise_for_status()
