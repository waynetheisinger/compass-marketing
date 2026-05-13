"""
Disable the merchant-fulfilled (DEFAULT) fallback on a dual-channel Amazon UK
listing so a sold-out FBA SKU stops generating MFN orders.

Background:
  Some FBA listings on Amazon UK have a stale `DEFAULT` (merchant-fulfilled)
  fulfillment-availability row carrying a non-zero quantity alongside the live
  `AMAZON_EU` (FBA) row. When FBA stock hits zero, Amazon falls back to the
  DEFAULT row's quantity and routes orders to the seller as MFN — silent
  overselling. This script clears the fallback by patching
  `/attributes/fulfillment_availability` on the listing.

Modes:
  --mode zero      → leaves both rows registered but sets DEFAULT.quantity=0.
                     (Mirrors what Amazon Seller Support typically advises:
                     "set fulfillment-channel AMAZON_EU, quantity 0".)
  --mode fba-only  → drops the DEFAULT row entirely; listing becomes FBA-only.

Default: dry-run. Prints the current state and the patch that would be sent.
Pass --apply to actually PATCH the listing.

Usage:
    python3 scripts/amazon_disable_mfn.py --sku ZX-ECG4-BV4L
    python3 scripts/amazon_disable_mfn.py --sku ZX-ECG4-BV4L --mode fba-only
    python3 scripts/amazon_disable_mfn.py --sku ZX-ECG4-BV4L --apply
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from amazon_client import AmazonClient, AmazonSPAPIError

MARKETPLACE = "A1F83G8C2ARO7P"  # Amazon.co.uk
SELLER_ID   = os.environ["AMAZON_SELLER_ID"]


def get_listing(client: AmazonClient, sku: str) -> dict:
    return client.get(
        f"/listings/2021-08-01/items/{SELLER_ID}/{sku}",
        params={
            "marketplaceIds":  MARKETPLACE,
            "includedData":    "summaries,attributes,fulfillmentAvailability",
        },
    )


def build_patch(mode: str) -> list[dict]:
    if mode == "zero":
        new_value = [
            {"fulfillment_channel_code": "AMAZON_EU"},
            {"fulfillment_channel_code": "DEFAULT", "quantity": 0},
        ]
    elif mode == "fba-only":
        new_value = [
            {"fulfillment_channel_code": "AMAZON_EU"},
        ]
    else:
        raise ValueError(f"unknown mode: {mode}")

    return [
        {
            "op":    "replace",
            "path":  "/attributes/fulfillment_availability",
            "value": new_value,
        },
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sku", required=True, help="Seller SKU to patch")
    parser.add_argument("--mode", choices=["zero", "fba-only"], default="zero",
                        help="zero: keep DEFAULT row at qty 0 (Amazon support's recipe); "
                             "fba-only: remove DEFAULT row entirely. Default: zero.")
    parser.add_argument("--apply", action="store_true",
                        help="Send the PATCH. Without this flag the script is a dry-run.")
    args = parser.parse_args()

    client = AmazonClient()

    print(f"=== {args.sku}  (marketplace {MARKETPLACE}, mode={args.mode}) ===\n")

    listing = get_listing(client, args.sku)

    summaries = listing.get("summaries") or []
    if not summaries:
        raise SystemExit(f"No summary returned for SKU {args.sku} — does it exist?")
    summary = summaries[0]
    product_type = summary["productType"]

    current_attr  = (listing.get("attributes") or {}).get("fulfillment_availability", [])
    current_top   = listing.get("fulfillmentAvailability", [])
    issues        = listing.get("issues") or []

    print(f"  ASIN:          {summary.get('asin')}")
    print(f"  productType:   {product_type}")
    print(f"  status:        {summary.get('status')}")
    print(f"  fnSku:         {summary.get('fnSku')}")
    print(f"  itemName:      {summary.get('itemName')[:80]}…")
    print(f"  issues:        {len(issues)}")
    print(f"\n  attributes.fulfillment_availability (current):")
    print("    " + json.dumps(current_attr,  indent=2).replace("\n", "\n    "))
    print(f"\n  top-level fulfillmentAvailability (current):")
    print("    " + json.dumps(current_top,   indent=2).replace("\n", "\n    "))

    patches = build_patch(args.mode)
    payload = {"productType": product_type, "patches": patches}

    print(f"\n  PATCH payload:")
    print("    " + json.dumps(payload, indent=2).replace("\n", "\n    "))

    if not args.apply:
        print("\n  DRY-RUN — pass --apply to send the PATCH.")
        return

    print("\n  Sending PATCH…")
    try:
        resp = client.patch(
            f"/listings/2021-08-01/items/{SELLER_ID}/{args.sku}",
            payload=payload,
            params={"marketplaceIds": MARKETPLACE},
        )
    except AmazonSPAPIError as e:
        print(f"  ✗ FAILED: {e}")
        sys.exit(1)

    status        = resp.get("status")
    submission_id = resp.get("submissionId")
    resp_issues   = resp.get("issues") or []
    print(f"  → status={status}  submissionId={submission_id}")
    if resp_issues:
        print(f"  ! {len(resp_issues)} issue(s):")
        for iss in resp_issues:
            sev  = iss.get("severity")
            code = iss.get("code")
            msg  = iss.get("message")
            attr = iss.get("attributeNames") or []
            print(f"      [{sev}] {code}: {msg}  attrs={attr}")
    else:
        print("  ✓ no issues. Allow up to ~30 minutes for offer to reflect.")


if __name__ == "__main__":
    main()
