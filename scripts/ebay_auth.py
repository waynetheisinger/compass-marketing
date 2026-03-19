"""
One-time eBay OAuth setup script.

Run this once to obtain a refresh token for the MowDirect eBay seller account.
The refresh token is saved to .env and used by ebay_client.py for all future API calls.

Usage:
    python3 scripts/ebay_auth.py

Requires in .env:
    EBAY_CLIENT_ID
    EBAY_CLIENT_SECRET
    EBAY_RUNAME
    EBAY_ENVIRONMENT=production
"""
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
])

AUTH_BASE  = "https://auth.ebay.com/oauth2/authorize"
TOKEN_URL  = "https://api.ebay.com/identity/v1/oauth2/token"

if ENV == "sandbox":
    AUTH_BASE = "https://auth.sandbox.ebay.com/oauth2/authorize"
    TOKEN_URL = "https://api.sandbox.ebay.com/identity/v1/oauth2/token"


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


def main():
    consent_url = build_consent_url()

    print("\n=== eBay One-Time Auth Setup ===\n")
    print("Opening your browser. Log in as the MowDirect eBay seller and approve the app.")
    print("After approving, your browser will show a connection error — that's expected.")
    print("Copy the FULL URL from your browser's address bar and paste it below.\n")

    webbrowser.open(consent_url)

    redirected_url = input("Paste the full redirect URL here: ").strip()

    # Extract code from URL
    parsed = urlparse(redirected_url)
    params = parse_qs(parsed.query)

    if "code" not in params:
        print(f"\nError: no 'code' found in URL. Got params: {list(params.keys())}")
        return

    code = params["code"][0]
    print(f"\nGot authorization code. Exchanging for tokens...")

    tokens = exchange_code(code)

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print(f"\nError: no refresh_token in response: {tokens}")
        return

    # Save to .env
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    set_key(env_path, "EBAY_REFRESH_TOKEN", refresh_token)

    print(f"\nSuccess! Refresh token saved to .env")
    print(f"Access token expires in: {tokens.get('expires_in')}s")
    print(f"Refresh token expiry:    {tokens.get('refresh_token_expires_in')}s "
          f"({tokens.get('refresh_token_expires_in', 0) // 86400} days)")


if __name__ == "__main__":
    main()
