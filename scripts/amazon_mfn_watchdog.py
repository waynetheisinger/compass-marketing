"""
Amazon UK MFN-fallback watchdog.

Scans every listing's `attributes.fulfillment_availability`. If any FBA-registered
SKU (AMAZON_EU) has a DEFAULT row with quantity > 0, that's a phantom MFN
fallback (Amazon will route orders as merchant-fulfilled when FBA stock hits
zero). The watchdog:

  1. Patches the listing to set DEFAULT.quantity=0
     (replace op — keep both rows. Shrinking the array is silently rejected.
      See memory: amazon_fulfillment_availability_patch_quirk.md)
  2. Fires a macOS Notification Center alert naming the affected SKUs
  3. Appends to logs/amazon_mfn_watchdog.log

Designed to be run by launchd every 15 minutes. Exits 0 on healthy runs so
launchd doesn't restart it.
"""
import logging
import os
import sys
import subprocess
import time
from logging.handlers import RotatingFileHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from amazon_client import AmazonClient, AmazonSPAPIError

MARKETPLACE = "A1F83G8C2ARO7P"
SELLER_ID   = os.environ["AMAZON_SELLER_ID"]
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR  = os.path.join(PROJECT_ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, "amazon_mfn_watchdog.log")

os.makedirs(LOG_DIR, exist_ok=True)
_logger = logging.getLogger("mfn_watchdog")
_logger.setLevel(logging.INFO)
_handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=4)
_handler.setFormatter(logging.Formatter("%(asctime)sZ %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"))
_handler.converter = time.gmtime
_logger.addHandler(_handler)


def log(line: str) -> None:
    _logger.info(line)


def notify(title: str, message: str) -> None:
    safe_title   = title.replace('"', "'")
    safe_message = message.replace('"', "'")
    subprocess.run(
        ["osascript", "-e",
         f'display notification "{safe_message}" with title "{safe_title}" sound name "Basso"'],
        check=False,
    )


def enumerate_all_listings(client: AmazonClient) -> list[dict]:
    out: list[dict] = []
    next_token: str | None = None
    while True:
        params = {
            "marketplaceIds": MARKETPLACE,
            "includedData":   "summaries,attributes",
            "pageSize":       20,
        }
        if next_token:
            params["pageToken"] = next_token
        resp = client.get(f"/listings/2021-08-01/items/{SELLER_ID}", params=params)
        for it in resp.get("items") or []:
            s = (it.get("summaries") or [{}])[0]
            attr_rows = (it.get("attributes") or {}).get("fulfillment_availability", []) or []
            out.append({
                "sku":         it.get("sku"),
                "asin":        s.get("asin"),
                "name":        (s.get("itemName") or "")[:60],
                "productType": s.get("productType"),
                "attr_rows":   attr_rows,
            })
        next_token = (resp.get("pagination") or {}).get("nextToken")
        if not next_token:
            break
        time.sleep(0.3)
    return out


def is_risky(attr_rows: list[dict]) -> tuple[bool, int | None]:
    by_channel = {r.get("fulfillment_channel_code"): r for r in attr_rows}
    if "AMAZON_EU" not in by_channel or "DEFAULT" not in by_channel:
        return False, None
    qty = by_channel["DEFAULT"].get("quantity") or 0
    return qty > 0, qty


def zero_default(client: AmazonClient, sku: str, product_type: str) -> dict:
    payload = {
        "productType": product_type,
        "patches": [{
            "op":    "replace",
            "path":  "/attributes/fulfillment_availability",
            "value": [
                {"fulfillment_channel_code": "AMAZON_EU"},
                {"fulfillment_channel_code": "DEFAULT", "quantity": 0},
            ],
        }],
    }
    return client.patch(
        f"/listings/2021-08-01/items/{SELLER_ID}/{sku}",
        payload=payload,
        params={"marketplaceIds": MARKETPLACE},
    )


def main() -> int:
    try:
        client = AmazonClient()
        listings = enumerate_all_listings(client)
    except Exception as e:
        log(f"ERROR enumerate failed: {e}")
        notify("Amazon MFN watchdog — error", f"Listing enumerate failed: {str(e)[:120]}")
        return 0  # don't let launchd restart-loop us

    risky: list[dict] = []
    for l in listings:
        flagged, qty = is_risky(l["attr_rows"])
        if flagged:
            risky.append({**l, "default_qty": qty})

    if not risky:
        log(f"OK scanned={len(listings)} risky=0")
        return 0

    log(f"ALERT scanned={len(listings)} risky={len(risky)} skus={[r['sku'] for r in risky]}")

    fixed: list[str] = []
    failed: list[tuple[str, str]] = []
    for r in risky:
        try:
            resp = zero_default(client, r["sku"], r["productType"])
            sub_id = resp.get("submissionId")
            log(f"  PATCH {r['sku']} qty_was={r['default_qty']} sub={sub_id} status={resp.get('status')}")
            fixed.append(f"{r['sku']} ({r['default_qty']})")
        except AmazonSPAPIError as e:
            log(f"  PATCH {r['sku']} FAILED: {e}")
            failed.append((r["sku"], str(e)[:80]))
        time.sleep(0.5)

    parts = []
    if fixed:
        parts.append(f"Zeroed: {', '.join(fixed)}")
    if failed:
        parts.append(f"FAILED: {', '.join(s for s,_ in failed)}")
    notify("Amazon MFN watchdog — phantom inventory caught", " | ".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
