"""
Scan all FBA-registered SKUs on Amazon UK for the dual-channel fulfillment bug.

The bug: a listing has BOTH `AMAZON_EU` (FBA) and `DEFAULT` (MFN) rows in
`attributes.fulfillment_availability`, with a non-zero `quantity` on the DEFAULT
row. When FBA stock hits zero, Amazon falls back to the DEFAULT quantity and
routes orders as MFN — silent overselling.

Method:
  1. Enumerate all SKUs via FBA Inventory API summaries (paginated).
  2. For each SKU, GET the Listings API item with attributes+fulfillmentAvailability.
  3. Flag any listing whose `attributes.fulfillment_availability` contains both
     channels and a non-zero DEFAULT quantity.

Output: a sorted report of risky SKUs + their DEFAULT.quantity.
"""
import os
import sys
import time
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from amazon_client import AmazonClient, AmazonSPAPIError

MARKETPLACE = "A1F83G8C2ARO7P"
SELLER_ID   = os.environ["AMAZON_SELLER_ID"]


def list_all_fba_skus(client: AmazonClient) -> list[dict]:
    """Page through /fba/inventory/v1/summaries and return [{sku, asin, fnSku, name, total}]."""
    out: list[dict] = []
    next_token: str | None = None
    page = 0
    while True:
        params = {
            "granularityType": "Marketplace",
            "granularityId":   MARKETPLACE,
            "marketplaceIds":  MARKETPLACE,
            "details":         "true",
        }
        if next_token:
            params["nextToken"] = next_token
        resp = client.get("/fba/inventory/v1/summaries", params=params)
        page += 1
        payload    = resp.get("payload", {}) or {}
        summaries  = payload.get("inventorySummaries", []) or []
        for s in summaries:
            out.append({
                "sku":   s.get("sellerSku"),
                "asin":  s.get("asin"),
                "fnSku": s.get("fnSku"),
                "name":  s.get("productName"),
                "total": s.get("totalQuantity", 0),
            })
        # nextToken may be at top-level or inside pagination
        next_token = (resp.get("pagination") or {}).get("nextToken") or resp.get("nextToken")
        print(f"  page {page}: +{len(summaries)} (cumulative {len(out)})")
        if not next_token:
            break
        time.sleep(0.4)
    return out


def get_fulfillment_attr(client: AmazonClient, sku: str) -> tuple[list, list, list]:
    """Return (attribute_rows, top_level_rows, issues)."""
    resp = client.get(
        f"/listings/2021-08-01/items/{SELLER_ID}/{sku}",
        params={
            "marketplaceIds": MARKETPLACE,
            "includedData":   "attributes,fulfillmentAvailability,issues,summaries",
        },
    )
    attr_rows = (resp.get("attributes") or {}).get("fulfillment_availability", []) or []
    top_rows  = resp.get("fulfillmentAvailability", []) or []
    issues    = resp.get("issues") or []
    return attr_rows, top_rows, issues


def classify(attr_rows: list[dict]) -> tuple[str, int | None]:
    channels = {r.get("fulfillment_channel_code"): r for r in attr_rows}
    has_eu      = "AMAZON_EU" in channels
    has_default = "DEFAULT" in channels
    default_qty = channels.get("DEFAULT", {}).get("quantity") if has_default else None

    if has_eu and has_default:
        if default_qty and default_qty > 0:
            return "DUAL_RISKY", default_qty
        return "DUAL_ZEROED", default_qty
    if has_eu:
        return "FBA_ONLY", None
    if has_default:
        return "MFN_ONLY", default_qty
    return "NEITHER", None


def main():
    client = AmazonClient()

    print("=== Enumerating FBA-registered SKUs ===")
    skus = list_all_fba_skus(client)
    print(f"\nTotal FBA-registered SKUs: {len(skus)}\n")
    if not skus:
        return

    print("=== Scanning each listing's fulfillment_availability ===")
    rows: list[dict] = []
    for i, item in enumerate(skus, 1):
        sku = item["sku"]
        try:
            attr_rows, top_rows, issues = get_fulfillment_attr(client, sku)
        except AmazonSPAPIError as e:
            print(f"  [{i:3}/{len(skus)}] {sku}: ERR {e.status}")
            rows.append({**item, "verdict": "ERROR", "default_qty": None, "attr_rows": [], "top_rows": []})
            time.sleep(0.3)
            continue

        verdict, default_qty = classify(attr_rows)
        rows.append({
            **item,
            "verdict":     verdict,
            "default_qty": default_qty,
            "attr_rows":   attr_rows,
            "top_rows":    top_rows,
            "issues":      len(issues),
        })
        marker = "⚠️ " if verdict == "DUAL_RISKY" else "  "
        print(f"  {marker}[{i:3}/{len(skus)}] {sku:30}  fba_total={item['total']:>4}  "
              f"verdict={verdict:12}  default_qty={default_qty}")
        time.sleep(0.25)  # pacing — Listings GET is 5 req/sec

    print("\n=== Summary by verdict ===")
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    for v, n in sorted(counts.items()):
        print(f"  {v:12} {n}")

    risky = [r for r in rows if r["verdict"] == "DUAL_RISKY"]
    if risky:
        print(f"\n=== ⚠️  DUAL_RISKY listings ({len(risky)}) — DEFAULT.quantity > 0 alongside AMAZON_EU ===")
        for r in sorted(risky, key=lambda x: -(x["default_qty"] or 0)):
            print(f"\n  SKU: {r['sku']}  ASIN: {r['asin']}  fnSku: {r['fnSku']}")
            print(f"    name:        {(r['name'] or '')[:90]}")
            print(f"    fba_total:   {r['total']}")
            print(f"    default_qty: {r['default_qty']}")
            print(f"    attr rows:   {json.dumps(r['attr_rows'])}")
        print("\n  Fix per SKU:")
        for r in risky:
            print(f"    PYTHONPATH=. .venv/bin/python scripts/amazon_disable_mfn.py "
                  f"--sku {r['sku']} --mode fba-only --apply")
    else:
        print("\n  No DUAL_RISKY listings found. ✓")

    zeroed = [r for r in rows if r["verdict"] == "DUAL_ZEROED"]
    if zeroed:
        print(f"\n=== DUAL_ZEROED listings ({len(zeroed)}) — DEFAULT row exists but qty=0/None ===")
        for r in zeroed:
            print(f"  {r['sku']:30}  default_qty={r['default_qty']}  fba_total={r['total']}")


if __name__ == "__main__":
    main()
