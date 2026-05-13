"""
One-time Google Ads OAuth setup script.

Run once to obtain a refresh token for the MowDirect Google Ads account.
The refresh token is saved to .env and used by google_ads_client.py for all
future API calls.

Usage:
    python3 scripts/google_ads_auth.py

Requires in .env:
    GOOGLE_ADS_CLIENT_ID
    GOOGLE_ADS_CLIENT_SECRET

Writes to .env on success:
    GOOGLE_ADS_REFRESH_TOKEN
"""
import os
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from dotenv import load_dotenv, set_key

load_dotenv()

CLIENT_ID     = os.environ["GOOGLE_ADS_CLIENT_ID"]
CLIENT_SECRET = os.environ["GOOGLE_ADS_CLIENT_SECRET"]

AUTH_URL  = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE     = "https://www.googleapis.com/auth/adwords"

CALLBACK_PORT = 8765
REDIRECT_URI  = f"http://localhost:{CALLBACK_PORT}/callback"

ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")


class _CallbackHandler(BaseHTTPRequestHandler):
    """Minimal one-shot HTTP server to capture the OAuth redirect."""

    captured_code: str | None  = None
    captured_state: str | None = None
    captured_error: str | None = None

    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler convention)
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if "error" in params:
            _CallbackHandler.captured_error = params["error"][0]
            body = b"<h1>Authorisation failed</h1><p>Check the terminal.</p>"
        elif "code" in params:
            _CallbackHandler.captured_code  = params["code"][0]
            _CallbackHandler.captured_state = (params.get("state") or [""])[0]
            body = (
                b"<h1>Google Ads auth successful</h1>"
                b"<p>You can close this tab and return to the terminal.</p>"
            )
        else:
            body = b"<h1>No code or error in callback</h1>"

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args, **kwargs):  # silence default access logging
        pass


def build_consent_url(state: str) -> str:
    params = {
        "client_id":     CLIENT_ID,
        "redirect_uri":  REDIRECT_URI,
        "response_type": "code",
        "scope":         SCOPE,
        "access_type":   "offline",   # required to receive a refresh_token
        "prompt":        "consent",   # force re-consent so refresh_token is always returned
        "state":         state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(code: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        data={
            "code":          code,
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri":  REDIRECT_URI,
            "grant_type":    "authorization_code",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    state = secrets.token_urlsafe(16)
    consent_url = build_consent_url(state)

    server = HTTPServer(("localhost", CALLBACK_PORT), _CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    print("\n=== Google Ads One-Time Auth Setup ===\n")
    print("A browser tab will open. Sign in to the Google account that owns the")
    print("MowDirect Google Ads account and approve the requested scope.\n")
    print(f"If the browser does not open, visit this URL manually:\n{consent_url}\n")

    webbrowser.open(consent_url)

    print(f"Waiting for callback on {REDIRECT_URI} ...")
    while _CallbackHandler.captured_code is None and _CallbackHandler.captured_error is None:
        pass

    server.shutdown()

    if _CallbackHandler.captured_error:
        print(f"\nAuthorisation error: {_CallbackHandler.captured_error}")
        return

    if _CallbackHandler.captured_state != state:
        print("\nState mismatch — refusing to exchange code (possible CSRF).")
        return

    print("Got authorisation code. Exchanging for tokens ...")
    tokens = exchange_code(_CallbackHandler.captured_code)

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print(f"\nError: no refresh_token in response: {tokens}")
        print("Tip: revoke the app at https://myaccount.google.com/permissions and re-run.")
        return

    set_key(ENV_PATH, "GOOGLE_ADS_REFRESH_TOKEN", refresh_token)
    print("\nRefresh token saved to .env (GOOGLE_ADS_REFRESH_TOKEN).")
    print(f"Access token expires in: {tokens.get('expires_in')}s")
    print("Done. Next step: set GOOGLE_ADS_CUSTOMER_ID in .env (10-digit ID from")
    print("the top-right of ads.google.com, dashes stripped).")


if __name__ == "__main__":
    main()
