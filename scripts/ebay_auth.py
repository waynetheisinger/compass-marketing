"""
One-time eBay OAuth setup script.

Run this once to obtain a refresh token for the MowDirect eBay seller account.
The refresh token is saved to .env and used by ebay_client.py for all future API calls.

Usage:
    python3 scripts/ebay_auth.py               # full OAuth + signing key setup
    python3 scripts/ebay_auth.py --signing-key  # generate/replace signing key only

Requires in .env:
    EBAY_CLIENT_ID
    EBAY_CLIENT_SECRET
    EBAY_RUNAME
    EBAY_ENVIRONMENT=production
"""
import argparse
import os
import base64
import webbrowser
import requests
from urllib.parse import urlencode, urlparse, parse_qs
from dotenv import load_dotenv, set_key

load_dotenv()

CLIENT_ID     = os.environ["EBAY_CLIENT_ID"]
CLIENT_SECRET = os.environ["EBAY_CLIENT_SECRET"]
RUNAME        = os.environ["EBAY_RUNAME"]
ENV           = os.environ.get("EBAY_ENVIRONMENT", "production")

SCOPES = " ".join([
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment.readonly",
    "https://api.ebay.com/oauth/api_scope/sell.finances",
])

AUTH_BASE  = "https://auth.ebay.com/oauth2/authorize"
TOKEN_URL  = "https://api.ebay.com/identity/v1/oauth2/token"
API_BASE   = "https://api.ebay.com"

if ENV == "sandbox":
    AUTH_BASE = "https://auth.sandbox.ebay.com/oauth2/authorize"
    TOKEN_URL = "https://api.sandbox.ebay.com/identity/v1/oauth2/token"
    API_BASE  = "https://api.sandbox.ebay.com"

ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")


# ---------------------------------------------------------------------------
# OAuth flow (user token)
# ---------------------------------------------------------------------------

def build_consent_url() -> str:
    params = {
        "client_id":     CLIENT_ID,
        "redirect_uri":  RUNAME,
        "response_type": "code",
        "scope":         SCOPES,
    }
    return f"{AUTH_BASE}?{urlencode(params)}"


def exchange_code(code: str) -> dict:
    credentials = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    resp = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type":  "application/x-www-form-urlencoded",
        },
        data={
            "grant_type":   "authorization_code",
            "code":         code,
            "redirect_uri": RUNAME,
        },
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Signing key generation (application token)
# ---------------------------------------------------------------------------

def get_app_token() -> str:
    """Get an application access token via client credentials grant."""
    credentials = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    resp = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type":  "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "client_credentials",
            "scope":      "https://api.ebay.com/oauth/api_scope",
        },
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def generate_signing_key(app_token: str) -> dict:
    """Create an ED25519 digital signing key pair via eBay Key Management API."""
    key_mgmt_base = API_BASE.replace("api.ebay.com", "apiz.ebay.com")
    resp = requests.post(
        f"{key_mgmt_base}/developer/key_management/v1/signing_key",
        headers={
            "Authorization": f"Bearer {app_token}",
            "Content-Type":  "application/json",
        },
        json={"signingKeyCipher": "ED25519"},
    )
    resp.raise_for_status()
    return resp.json()


def save_signing_key(key_data: dict) -> None:
    """Save JWE and private key to .env."""
    jwe         = key_data["jwe"]
    private_key = key_data["privateKey"].replace("\n", "\\n")  # single-line for .env
    set_key(ENV_PATH, "EBAY_SIGNING_KEY_JWE", jwe)
    set_key(ENV_PATH, "EBAY_SIGNING_PRIVATE_KEY", private_key)
    print("Signing key saved to .env (EBAY_SIGNING_KEY_JWE + EBAY_SIGNING_PRIVATE_KEY)")


def setup_signing_key() -> None:
    print("\n=== Generating eBay Digital Signing Key ===\n")
    print("Getting application token...")
    app_token = get_app_token()
    print("Generating ED25519 signing key pair...")
    key_data = generate_signing_key(app_token)
    save_signing_key(key_data)
    print(f"Key ID: {key_data.get('signingKeyId', 'n/a')}")
    print("Done.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--signing-key", action="store_true",
                        help="Generate/replace the digital signing key only (skip OAuth flow)")
    args = parser.parse_args()

    if args.signing_key:
        setup_signing_key()
        return

    # Full OAuth flow
    consent_url = build_consent_url()

    print("\n=== eBay One-Time Auth Setup ===\n")
    print("Open this URL in your browser, log in as the MowDirect eBay seller and approve the app.")
    print("After approving, your browser will show a connection error — that's expected.")
    print("Copy the FULL URL from your browser's address bar and paste it below.\n")
    print(f"URL:\n{consent_url}\n")

    webbrowser.open(consent_url)

    redirected_url = input("Paste the full redirect URL here: ").strip()

    parsed = urlparse(redirected_url)
    params = parse_qs(parsed.query)

    if "code" not in params:
        print(f"\nError: no 'code' found in URL. Got params: {list(params.keys())}")
        return

    code = params["code"][0]
    print("\nGot authorization code. Exchanging for tokens...")

    tokens = exchange_code(code)

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print(f"\nError: no refresh_token in response: {tokens}")
        return

    set_key(ENV_PATH, "EBAY_REFRESH_TOKEN", refresh_token)
    print(f"\nRefresh token saved to .env")
    print(f"Access token expires in: {tokens.get('expires_in')}s")
    print(f"Refresh token expiry:    {tokens.get('refresh_token_expires_in')}s "
          f"({tokens.get('refresh_token_expires_in', 0) // 86400} days)")

    # Also generate signing key as part of full setup
    print()
    setup_signing_key()


if __name__ == "__main__":
    main()
