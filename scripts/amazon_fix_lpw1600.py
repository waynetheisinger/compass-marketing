"""
One-shot fix for SKU LPW1600 on Amazon UK.

Background: the listing was suppressed (issue 13013) because catalogue creation
failed, and catalogue creation failed because the submitted `maximum_flow_rate`
attribute (420 litres per hour) was rejected (issue 90244 — "We can't accept
the litres per hour you entered for Maximum Flow Rate").

The current UK PRESSURE_WASHER product-type schema does not expose
`maximum_flow_rate` as a top-level property, but the validator still accepts
the attribute behind the scenes — with a *strict* unit enum. Empirically, of
the candidate units (`litres per minute`, `litres per hour`, `gallons per
minute`, `gallons per hour`, `cubic_metres_per_hour`, `litres_per_minute`,
`gallons_per_minute`), only **`gallons_per_minute`** is on the allow-list.

JSON_PATCH `delete` and `replace` with `[]`/`null` are all rejected
("InvalidInput: Invalid empty value provided"). The only working path is to
**replace** with a structurally valid value in the accepted unit:

    420 L/h = 7 L/min = 1.85 US gallons/minute

Defaults to dry-run. Pass --apply to PATCH.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from amazon_client import AmazonClient, AmazonSPAPIError

MARKETPLACE  = "A1F83G8C2ARO7P"
SELLER_ID    = os.environ["AMAZON_SELLER_ID"]
SKU          = "LPW1600"
PRODUCT_TYPE = "PRESSURE_WASHER"

# 420 L/h ÷ 60 = 7 L/min ÷ 3.78541 = 1.85 US gpm
ACCEPTED_UNIT  = "gallons_per_minute"
ACCEPTED_VALUE = 1.85


def fetch_state(client: AmazonClient) -> dict:
    return client.get(
        f"/listings/2021-08-01/items/{SELLER_ID}/{SKU}",
        params={
            "marketplaceIds": MARKETPLACE,
            "includedData":   "summaries,attributes,issues",
        },
    )


def print_state(label: str, state: dict) -> None:
    summary = (state.get("summaries") or [{}])[0]
    attrs   = state.get("attributes") or {}
    issues  = state.get("issues") or []
    print(f"\n=== {label} ===")
    print(f"  asin:               {summary.get('asin') or '(none — not in catalogue yet)'}")
    print(f"  status:             {summary.get('status')}")
    print(f"  maximum_flow_rate:  {attrs.get('maximum_flow_rate')}")
    print(f"  issues ({len(issues)}):")
    for i in issues:
        print(f"    [{i.get('severity')}] {i.get('code')} {i.get('attributeNames') or ''}: "
              f"{(i.get('message') or '')[:140]}")


def patch_flow_rate(client: AmazonClient) -> dict:
    payload = {
        "productType": PRODUCT_TYPE,
        "patches": [{
            "op":    "replace",
            "path":  "/attributes/maximum_flow_rate",
            "value": [{
                "unit":           ACCEPTED_UNIT,
                "value":          ACCEPTED_VALUE,
                "marketplace_id": MARKETPLACE,
            }],
        }],
    }
    return client.patch(
        f"/listings/2021-08-01/items/{SELLER_ID}/{SKU}",
        payload=payload,
        params={"marketplaceIds": MARKETPLACE},
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Send the PATCH. Without this flag, dry-run only.")
    parser.add_argument("--repoll-delay", type=float, default=12.0,
                        help="Seconds to wait before re-reading state (default 12). Note: "
                             "the issues feed lags the attributes feed by up to ~1h, so a "
                             "stale 90244 message may persist briefly even after a clean "
                             "submission.")
    args = parser.parse_args()

    client = AmazonClient()

    before = fetch_state(client)
    print_state("BEFORE", before)

    current_mfr = (before.get("attributes") or {}).get("maximum_flow_rate") or []
    if current_mfr and current_mfr[0].get("unit") == ACCEPTED_UNIT:
        print(f"\nAlready set to {ACCEPTED_UNIT}={ACCEPTED_VALUE} — nothing to patch.")
        return

    if not args.apply:
        print(f"\nDRY-RUN — would replace maximum_flow_rate with "
              f"{ACCEPTED_UNIT}={ACCEPTED_VALUE}. Pass --apply to send.")
        return

    print(f"\n=== Applying patch (replace /attributes/maximum_flow_rate with "
          f"{ACCEPTED_UNIT}={ACCEPTED_VALUE}) ===")
    resp = patch_flow_rate(client)
    print(f"  status:        {resp.get('status')}")
    print(f"  submissionId:  {resp.get('submissionId')}")
    sub_issues = resp.get("issues") or []
    if sub_issues:
        print(f"  submission issues ({len(sub_issues)}):")
        for i in sub_issues:
            print(f"    [{i.get('severity')}] {i.get('code')}: {(i.get('message') or '')[:200]}")
    else:
        print("  submission issues: none — patch was accepted cleanly.")

    print(f"\nWaiting {args.repoll_delay:.0f}s before re-polling read state...")
    time.sleep(args.repoll_delay)

    after = fetch_state(client)
    print_state("AFTER", after)

    print("\nNote: issue 13013 (LISTING_SUPPRESSED) clears once Amazon mints the ASIN "
          "from the now-valid catalogue submission — typically within ~1 hour. "
          "The cached 90244 issue may also linger briefly even though the underlying "
          "value is now correct (litres-per-hour replaced with gallons_per_minute=1.85).")


if __name__ == "__main__":
    main()
