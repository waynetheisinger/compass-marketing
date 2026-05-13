"""
Normalize Amazon UK listings: every SKU that has AMAZON_EU registered should
have DEFAULT (MFN) quantity zeroed — Amazon silently rejects PATCHes that try
to remove the DEFAULT row entirely (replace to a 1-element array returns
ACCEPTED but never lands), so we keep the row and set quantity=0 instead.

MFN-only listings (DEFAULT-only, no AMAZON_EU) are left untouched.

Defaults to dry-run. Pass --apply to PATCH.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from amazon_client import AmazonClient, AmazonSPAPIError

MARKETPLACE = "A1F83G8C2ARO7P"
SELLER_ID   = os.environ["AMAZON_SELLER_ID"]


def enumerate_all_listings(client: AmazonClient) -> list[dict]:
    """Paginate /listings/2021-08-01/items/{sellerId} returning summary+attrs."""
    out: list[dict] = []
    next_token: str | None = None
    page = 0
    while True:
        params = {
            "marketplaceIds": MARKETPLACE,
            "includedData":   "summaries,attributes",
            "pageSize":       20,
        }
        if next_token:
            params["pageToken"] = next_token
        resp = client.get(f"/listings/2021-08-01/items/{SELLER_ID}", params=params)
        page += 1
        items = resp.get("items") or []
        for it in items:
            s = (it.get("summaries") or [{}])[0]
            attr_rows = (it.get("attributes") or {}).get("fulfillment_availability", []) or []
            out.append({
                "sku":          it.get("sku"),
                "asin":         s.get("asin"),
                "name":         (s.get("itemName") or "")[:90],
                "status":       s.get("status"),
                "productType":  s.get("productType"),
                "attr_rows":    attr_rows,
            })
        next_token = (resp.get("pagination") or {}).get("nextToken")
        print(f"  page {page}: +{len(items)} (cumulative {len(out)})")
        if not next_token:
            break
        time.sleep(0.3)
    return out


def needs_fix(attr_rows: list[dict]) -> bool:
    """Risky if AMAZON_EU + DEFAULT both present AND DEFAULT.quantity > 0."""
    channels = {r.get("fulfillment_channel_code"): r for r in attr_rows}
    if "AMAZON_EU" not in channels or "DEFAULT" not in channels:
        return False
    return (channels["DEFAULT"].get("quantity") or 0) > 0


def patch_zero_default(client: AmazonClient, sku: str, product_type: str) -> dict:
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Send PATCH requests. Without this flag, dry-run only.")
    args = parser.parse_args()

    client = AmazonClient()

    print("=== Enumerating all listings ===")
    listings = enumerate_all_listings(client)
    print(f"\nTotal listings: {len(listings)}\n")

    targets = [l for l in listings if needs_fix(l["attr_rows"])]
    untouched_mfn = [l for l in listings if "DEFAULT" in {r.get("fulfillment_channel_code") for r in l["attr_rows"]}
                     and "AMAZON_EU" not in {r.get("fulfillment_channel_code") for r in l["attr_rows"]}]
    clean_fba = [l for l in listings if "AMAZON_EU" in {r.get("fulfillment_channel_code") for r in l["attr_rows"]}
                 and "DEFAULT" not in {r.get("fulfillment_channel_code") for r in l["attr_rows"]}]
    dual_zeroed = [l for l in listings
                   if {r.get("fulfillment_channel_code") for r in l["attr_rows"]} >= {"AMAZON_EU", "DEFAULT"}
                   and not needs_fix(l["attr_rows"])]

    print(f"Classification:")
    print(f"  Already FBA-only:               {len(clean_fba)}")
    print(f"  Dual-channel, DEFAULT zeroed:   {len(dual_zeroed)}")
    print(f"  MFN-only (will skip):           {len(untouched_mfn)}")
    print(f"  Dual-channel risky (FIX):       {len(targets)}")

    if not targets:
        print("\nNo risky dual-channel listings found. Nothing to do.")
        return

    print(f"\n=== {len(targets)} listings will be patched to DEFAULT.quantity=0 ===")
    for t in targets:
        default_qty = next((r.get("quantity") for r in t["attr_rows"] if r.get("fulfillment_channel_code") == "DEFAULT"), None)
        print(f"  {t['sku']:32}  {t['asin']}  default_qty={default_qty:>5}  {t['name'][:60]}")

    if not args.apply:
        print("\nDRY-RUN — pass --apply to send PATCH requests.")
        return

    print(f"\n=== Applying patches ===")
    results = []
    for i, t in enumerate(targets, 1):
        sku = t["sku"]
        try:
            resp = patch_zero_default(client, sku, t["productType"])
            status = resp.get("status")
            sub_id = resp.get("submissionId")
            issues = resp.get("issues") or []
            issue_summary = ""
            if issues:
                worst = max((i.get("severity") for i in issues), default="")
                issue_summary = f"  ({len(issues)} issues, worst={worst})"
            print(f"  [{i:2}/{len(targets)}] {sku:32}  status={status}  sub={sub_id}{issue_summary}")
            results.append({"sku": sku, "status": status, "submissionId": sub_id, "issues": issues})
            for iss in issues:
                print(f"      [{iss.get('severity')}] {iss.get('code')}: {iss.get('message')}")
        except AmazonSPAPIError as e:
            print(f"  [{i:2}/{len(targets)}] {sku:32}  ERROR: {e}")
            results.append({"sku": sku, "error": str(e)})
        time.sleep(0.5)

    print(f"\n=== Summary ===")
    accepted = sum(1 for r in results if r.get("status") == "ACCEPTED")
    errors = sum(1 for r in results if "error" in r)
    print(f"  Accepted: {accepted}/{len(targets)}")
    print(f"  Errors:   {errors}/{len(targets)}")
    print("\nAllow up to ~30 min for changes to reflect on the read API.")


if __name__ == "__main__":
    main()
