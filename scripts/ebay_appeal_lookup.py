"""
Resolve the 16 eBay item IDs flagged in the eBay account-appeal rejection
(SR# 1-504274814808) to their underlying SKU + product so we can chase
matching supplier invoices.

For each eBay legacy item ID:
  1. Hit eBay Browse API /buy/browse/v1/item/get_item_by_legacy_id  →  title, brand, GTIN, condition
  2. Look up the GTIN in Shopify via productVariants(query: "barcode:...") →  SKU, product handle
  3. Fall back to fuzzy title search in Shopify if GTIN miss
  4. Cross-check the BaseLinker external storage for an eBay listing with
     matching listing_id, to confirm SKU lineage and surface vendor info.

Output: a markdown table to stdout + a CSV at reports/ebay_appeal_lookup.csv
"""
import csv
import os
import sys
import time
from pathlib import Path

# Allow running as:  python scripts/ebay_appeal_lookup.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ebay_client import EBayClient
from scripts.shopify_client import ShopifyClient
from scripts.baselinker_client import BaseLinkerClient


# -- The 16 item IDs eBay still wants invoices for (msg 8, 25 Apr 2026)
ITEM_IDS = [
    "168262107999",
    "168256892996",
    "168256892945",
    "168256892901",
    "168256892804",
    "168256766026",
    "168249700554",
    "168249700516",
    "168249700475",
    "168241547069",
    "168226047815",
    "168218408799",
    "168185899183",
    "168185897894",
    "168172699432",
    "168161205816",
]

# Items already mapped by Wayne in his 8 Apr forward (sanity check)
KNOWN_FROM_8_APR = {
    "168262107999": 'Spectrum DCT38M-RDM 38" Lawn Tractor with Manual Drive',
    "168256892945": "Spectrum TG46S 3-in-1 Self-Propelled Petrol Lawnmower",
    "168256892804": "Spectrum TG46SE 3-in-1 SP Petrol Lawnmower (Electric Start)",
    "168256766026": "Alpina AT3 98AST Side Discharge Lawn Tractor",
    "168249700516": "Spectrum DC24-4 Ultra-Compact Manual Ride-On Mower",
    "168249700475": "Mountfield MTF66MQ Compact Rear-Collect Ride-On Mower",
    "168226047815": "Spectrum TG51SE 3-in-1 SP Petrol Lawnmower (Electric Start)",
    "168218408799": "Spectrum 227kg Steel Tipping Trailer SP22124",
    "168185899183": 'Spectrum DCT38M-SD 38" Side Discharge & Mulch Lawn Tractor',
    "168172699432": "Feider HRTF220 Pro Two-Wheel Rear-Tine Cultivator",
    "168161205816": "Racing Front-Tine Tiller Petrol 139PTIL63-C",
}


def fetch_ebay(client: EBayClient, item_id: str) -> dict:
    """Pull title/brand/GTIN/condition for one legacy listing ID."""
    try:
        r = client.get(
            "/buy/browse/v1/item/get_item_by_legacy_id",
            params={"legacy_item_id": item_id},
        )
    except Exception as e:
        return {"item_id": item_id, "error": f"{type(e).__name__}: {str(e)[:200]}"}

    aspects = {a["name"]: a["value"] for a in r.get("localizedAspects", [])}
    return {
        "item_id":        item_id,
        "title":          r.get("title"),
        "brand":          r.get("brand"),
        "mpn":            aspects.get("MPN") or aspects.get("Manufacturer Part Number") or r.get("mpn"),
        "gtin":           r.get("gtin"),
        "condition":      r.get("condition"),
        "item_end_date":  r.get("itemEndDate"),
        "category_path":  r.get("categoryPath"),
        "browse_item_id": r.get("itemId"),
    }


def shopify_by_barcode(client: ShopifyClient, gtin: str) -> dict | None:
    """Look up a Shopify variant by barcode (= GTIN/EAN)."""
    if not gtin:
        return None
    q = """
    query($q: String!) {
      productVariants(first: 5, query: $q) {
        nodes {
          sku
          barcode
          displayName
          product { handle title vendor onlineStoreUrl }
        }
      }
    }
    """
    data = client.execute(q, {"q": f"barcode:{gtin}"})
    nodes = data.get("productVariants", {}).get("nodes", [])
    return nodes[0] if nodes else None


def shopify_by_title(client: ShopifyClient, title: str) -> dict | None:
    """Fuzzy title search. eBay titles often have extra punctuation."""
    if not title:
        return None
    # Strip noise, take first ~6 meaningful tokens for the search
    tokens = [t for t in title.replace('"', " ").replace("/", " ").split() if len(t) > 2][:6]
    q = " ".join(tokens)
    query = """
    query($q: String!) {
      products(first: 3, query: $q) {
        nodes {
          handle
          title
          vendor
          variants(first: 3) { nodes { sku barcode } }
        }
      }
    }
    """
    data = client.execute(query, {"q": q})
    nodes = data.get("products", {}).get("nodes", [])
    return nodes[0] if nodes else None


def baselinker_by_ebay_id(bl: BaseLinkerClient, ebay_item_id: str) -> dict | None:
    """
    Look for the listing in BaseLinker. BaseLinker stores marketplace listings
    under inventory product 'manual links'; the cleanest path is to search the
    eBay account integration's listings list. Without a documented endpoint
    for that, we instead probe the inventory products list for any product
    that has this eBay item ID in its identifiers.
    """
    # Inventory listings: BaseLinker exposes getInventoryAvailableTextFieldKeys,
    # getInventoryProductsList(filter_id) — the product list filter takes
    # arbitrary filter_id which is inventory-specific. We can't enumerate
    # 100k+ products cheaply. Instead, we attempt the orders API which
    # carries marketplace_order_external_id but not the listing id.
    # → Skip BaseLinker for now; it's a confirmation source, not the primary.
    return None


def main():
    print("Resolving 16 eBay listing IDs...\n")
    ebay   = EBayClient()
    bl     = BaseLinkerClient()
    rows   = []

    with ShopifyClient() as shop:
        for item_id in ITEM_IDS:
            time.sleep(0.2)  # gentle on Browse API
            row = fetch_ebay(ebay, item_id)
            row["known_8apr"] = KNOWN_FROM_8_APR.get(item_id, "")

            # Shopify GTIN lookup
            sh = shopify_by_barcode(shop, row.get("gtin"))
            match = "barcode"
            if not sh:
                sh = shopify_by_title(shop, row.get("title"))
                match = "title-fuzzy" if sh else "miss"

            if sh and "sku" in sh:                  # variant node
                row["shopify_sku"]    = sh.get("sku")
                row["shopify_handle"] = sh["product"]["handle"]
                row["shopify_vendor"] = sh["product"].get("vendor")
                row["shopify_title"]  = sh["product"]["title"]
            elif sh:                                 # product node
                v = (sh.get("variants", {}).get("nodes") or [{}])[0]
                row["shopify_sku"]    = v.get("sku")
                row["shopify_handle"] = sh.get("handle")
                row["shopify_vendor"] = sh.get("vendor")
                row["shopify_title"]  = sh.get("title")
            row["shopify_match"] = match

            rows.append(row)

            print(f"  {item_id:14}  {row.get('title') or '✗ ' + (row.get('error') or 'unknown')}")

    # Write CSV
    out = Path("reports/ebay_appeal_lookup.csv")
    out.parent.mkdir(exist_ok=True)
    fields = [
        "item_id", "title", "brand", "gtin", "mpn", "condition", "item_end_date",
        "shopify_sku", "shopify_handle", "shopify_vendor", "shopify_title",
        "shopify_match", "known_8apr", "category_path", "error",
    ]
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"\nWrote {out}")

    # Markdown table to stdout
    print("\n## eBay item IDs → SKU mapping\n")
    print("| eBay ID | Brand | Title | GTIN | Shopify SKU | Match |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        print(
            f"| {r['item_id']} "
            f"| {r.get('brand') or '—'} "
            f"| {(r.get('title') or '—')[:60]} "
            f"| {r.get('gtin') or '—'} "
            f"| {r.get('shopify_sku') or '—'} "
            f"| {r.get('shopify_match') or '—'} |"
        )


if __name__ == "__main__":
    main()
