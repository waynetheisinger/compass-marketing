"""
Brand Registry / A+ / Virtual Bundles eligibility health check for MowDirect (UK).

What this checks:
  1. Brand attribute on our SBS listings — confirms what brand name Amazon has on file
  2. A+ Content API access — direct probe of /aplus/2020-11-01/contentDocuments
  3. Catalog brand search — does Amazon's catalog return our SKUs when filtered by brand=SPECTRUM
  4. Listings restrictions — do we have any brand-locked restrictions on our own SKUs

What this CANNOT check directly:
  - Virtual Bundles eligibility — no SP-API endpoint exists; gated entirely on Brand Registry approval
  - Brand Registry approval status itself — Amazon does not expose this via SP-API
  Both are inferred from the brand recognition signals above.
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from amazon_client import AmazonClient, AmazonSPAPIError

MARKETPLACE = "A1F83G8C2ARO7P"
SELLER_ID   = os.environ["AMAZON_SELLER_ID"]
SBS_SKUS    = ["SBS460CLM", "SBS480CBV", "SBS560CHT", "SBS240CPHT",
               "SBS40CB", "SBS20CB", "SBSCDC", "SBSCSC", "SBSCBC"]


def section(title):
    print(f"\n{'='*70}\n{title}\n{'='*70}")


def check_brand_on_listings(client):
    section("1. Brand attribute on our 9 SBS listings")
    brands_seen = {}
    for sku in SBS_SKUS:
        try:
            r = client.get(
                f"/listings/2021-08-01/items/{SELLER_ID}/{sku}",
                params={"marketplaceIds": MARKETPLACE,
                        "includedData": "summaries,attributes"},
            )
            summary       = (r.get("summaries") or [{}])[0]
            summary_brand = summary.get("brand")
            asin          = summary.get("asin")
            attrs         = r.get("attributes", {})
            attr_brand    = (attrs.get("brand", [{}])[0] or {}).get("value")
            print(f"  {sku:12} ASIN={asin}  summary.brand={summary_brand!r}  attr.brand={attr_brand!r}")
            brands_seen[summary_brand] = brands_seen.get(summary_brand, 0) + 1
        except AmazonSPAPIError as e:
            print(f"  {sku:12} ERROR: {e.status} {e.body[:120]}")
    print(f"\n  Brand frequency: {brands_seen}")


def check_aplus(client):
    section("2. A+ Content API access (/aplus/2020-11-01/contentDocuments)")
    try:
        r = client.get("/aplus/2020-11-01/contentDocuments",
                       params={"marketplaceId": MARKETPLACE})
        records = r.get("contentMetadataRecords") or []
        print(f"  Status: ACCESS GRANTED ({len(records)} A+ document(s) on account)")
        for rec in records[:10]:
            meta = rec.get("contentMetadata", {})
            print(f"    - {meta.get('name', '(unnamed)')!r}  status={meta.get('status')}  "
                  f"badges={meta.get('badgeSet') or []}  contentRefKey={rec.get('contentReferenceKey')}")
        if len(records) > 10:
            print(f"    ... ({len(records)-10} more)")
    except AmazonSPAPIError as e:
        if e.status == 403:
            print(f"  Status: ACCESS DENIED (HTTP 403)")
            print(f"    Means EITHER: (a) Brand Registry not approved for SPECTRUM, OR")
            print(f"                  (b) Our SP-API app lacks the 'A+ Content' role (re-auth needed).")
        elif e.status == 401:
            print(f"  Status: AUTH FAILED (HTTP 401) — token issue, re-check LWA refresh token")
        else:
            print(f"  Status: HTTP {e.status} — {e.body[:300]}")


def check_brand_catalog(client):
    section("3. Catalog brand-name search for 'SPECTRUM'")
    try:
        r = client.get("/catalog/2022-04-01/items",
                       params={"marketplaceIds":  MARKETPLACE,
                               "brandNames":      "SPECTRUM",
                               "includedData":    "summaries",
                               "pageSize":        20})
        items = r.get("items") or []
        print(f"  Items returned with brandNames=SPECTRUM: {len(items)}")
        if items:
            for it in items[:10]:
                s = (it.get("summaries") or [{}])[0]
                print(f"    ASIN={it.get('asin')}  brand={s.get('brand')!r}  "
                      f"title={(s.get('itemName') or '')[:70]!r}")
        else:
            print("  No catalog hits — Amazon does not yet recognise SPECTRUM as a brand,")
            print("  or our listings are not yet indexed under that brand name.")
    except AmazonSPAPIError as e:
        print(f"  Catalog brand search failed: HTTP {e.status} — {e.body[:300]}")


def check_listings_restrictions(client):
    section("4. Listing restrictions on our own ASINs (brand-gating signal)")
    print("  (skipping — restrictions API is for new listings, not existing seller-owned ASINs)")


def main():
    client = AmazonClient()
    check_brand_on_listings(client)
    check_aplus(client)
    check_brand_catalog(client)
    check_listings_restrictions(client)

    section("INTERPRETATION GUIDE")
    print("""
  Brand Registry approved for SPECTRUM if ALL of these are true:
    - Listings consistently show brand='SPECTRUM' in summaries (test 1)
    - A+ Content API returns 200 (test 2)  — OR — a 403 here is ambiguous (could be SP-API role)
    - Catalog search by brandNames=SPECTRUM returns our ASINs (test 3)

  Virtual Bundles eligibility = same as Brand Registry approval (no dedicated check).
  A+ Content eligibility      = test 2 result.

  If A+ returns 403 but tests 1 & 3 look clean, the gap is likely the SP-API app role —
  reassign the 'A+ Content Management' role in the Solution Provider Portal and re-auth.
""")


if __name__ == "__main__":
    main()
