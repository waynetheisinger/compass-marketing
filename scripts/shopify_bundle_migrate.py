"""
Migrate 5 broken componentized bundles → 5 new UI-created Bundles-app bundles.

Setup (already done by user, in Shopify Admin):
  - Created 5 new skeleton bundle products via Admin → Bundles (so they're
    properly registered with the Bundles app)
  - Each new skeleton has the correct components attached, SKU `<old>-NEW`,
    and a price set
  - The user has changed the charger in 2 kits (SBS240CPHT-KIT, SBS480CBV-KIT):
    SBSCSC (Fast 2A) → SBSCBC (Standard 0.5A). Sales copy must reflect this.

What this script does:
  1. Pulls full data from each OLD broken kit (description, vendor, productType,
     tags, SEO, metafields, media, variant weight/barcode/taxable)
  2. Patches each NEW skeleton with that data
  3. Where the charger has been swapped, rewrites the bullet line in the
     description to reference the new charger
  4. Renames OLD variant SKU → `<sku>-OLD` and sets OLD status to DRAFT
  5. Renames NEW variant SKU (drops `-NEW` suffix) and sets NEW status to ACTIVE

What this does NOT change on the new product:
  - price / compareAtPrice (user set these deliberately on the skeletons)
  - components (user picked the new charger; we don't touch components)
  - title / handle (user named the skeletons; handles will keep `-1` suffix)
"""
import os
import sys
import time
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shopify_client import ShopifyClient


# (new_product_id, old_sku) — pairs derived from the user's brief
PAIRS = [
    ("15859855950197", "SBS460CLM-KIT"),
    ("15859855130997", "SBS560CHT-KIT"),
    ("15859855098229", "SBS220CHM-KIT"),
    ("15859854967157", "SBS240CPHT-KIT"),
    ("15859854836085", "SBS480CBV-KIT"),
]

# Charger swaps (old SBSCSC fast 2A → new SBSCBC standard 0.5A) for two kits.
# Bullet line in the description that references the charger:
CHARGER_SWAP_OLD = "<strong>1× Spectrum SBSCSC</strong> — 40V 2A fast charger"
CHARGER_SWAP_NEW = "<strong>1× Spectrum SBSCBC</strong> — 40V Standard Battery Charger (0.5A)"
KITS_WITH_CHARGER_SWAP = {"SBS240CPHT-KIT", "SBS480CBV-KIT"}

# Metafield namespaces to copy (skip shopify.* and mm-google-shopping.* which are
# category-derived/auto-managed).
COPY_METAFIELD_NAMESPACES = {"custom", "filter", "judgeme"}


GET_OLD = """
query Q($q: String!) {
  products(first: 1, query: $q) {
    edges { node {
      id handle title descriptionHtml vendor productType tags status
      seo { title description }
      variants(first: 5) {
        edges { node {
          id sku price compareAtPrice barcode taxable
          inventoryItem {
            tracked
            measurement { weight { value unit } }
          }
        } }
      }
      metafields(first: 250) { edges { node { namespace key type value } } }
      media(first: 50) { edges { node { mediaContentType
        ... on MediaImage { id image { url altText } }
      } } }
    } }
  }
}
"""


GET_NEW = """
query Q($id: ID!) {
  product(id: $id) {
    id handle title status
    variants(first: 5) { edges { node { id sku } } }
  }
}
"""


PRODUCT_UPDATE = """
mutation Upd($input: ProductUpdateInput!) {
  productUpdate(product: $input) {
    product { id handle status }
    userErrors { field message }
  }
}
"""


VARIANTS_UPDATE = """
mutation V($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) {
    productVariants { id sku }
    userErrors { field message }
  }
}
"""


SET_METAFIELDS = """
mutation MF($metafields: [MetafieldsSetInput!]!) {
  metafieldsSet(metafields: $metafields) {
    metafields { id namespace key }
    userErrors { field message }
  }
}
"""


CREATE_MEDIA = """
mutation Media($productId: ID!, $media: [CreateMediaInput!]!) {
  productCreateMedia(productId: $productId, media: $media) {
    media { ... on MediaImage { id image { url } } status }
    mediaUserErrors { field message }
  }
}
"""


def fetch_old(client, sku):
    r = client.execute(GET_OLD, variables={"q": f"sku:{sku}"})
    edges = r["products"]["edges"]
    if not edges:
        raise SystemExit(f"OLD not found: sku={sku}")
    return edges[0]["node"]


def fetch_new(client, pid):
    gid = f"gid://shopify/Product/{pid}"
    r = client.execute(GET_NEW, variables={"id": gid})
    if not r["product"]:
        raise SystemExit(f"NEW not found: id={pid}")
    return r["product"]


def amend_description(html: str, sku: str) -> str:
    if sku not in KITS_WITH_CHARGER_SWAP:
        return html
    if CHARGER_SWAP_OLD not in html:
        print(f"  ! WARN: charger swap line not found in {sku} description — leaving description as-is")
        return html
    return html.replace(CHARGER_SWAP_OLD, CHARGER_SWAP_NEW)


def patch_data(client, new_product, old, dry_run=False):
    """Copy data from old into new. Doesn't touch SKU or status."""
    new_id  = new_product["id"]
    old_sku = old["variants"]["edges"][0]["node"]["sku"]
    description_html = amend_description(old.get("descriptionHtml") or "", old_sku)
    if old_sku in KITS_WITH_CHARGER_SWAP and CHARGER_SWAP_OLD in (old.get("descriptionHtml") or ""):
        print(f"  ✓ amended charger reference in description ({old_sku})")

    # 1. productUpdate: description, vendor, productType, tags, seo
    print(f"  → productUpdate: description ({len(description_html)} chr), vendor={old.get('vendor')!r}, "
          f"productType={old.get('productType')!r}, tags={len(old.get('tags') or [])}")
    if not dry_run:
        r = client.execute(PRODUCT_UPDATE, variables={
            "input": {
                "id":              new_id,
                "descriptionHtml": description_html,
                "vendor":          old.get("vendor"),
                "productType":     old.get("productType"),
                "tags":            old.get("tags") or [],
                "seo": {
                    "title":       (old.get("seo") or {}).get("title"),
                    "description": (old.get("seo") or {}).get("description"),
                },
            }
        })
        errs = r["productUpdate"]["userErrors"]
        if errs:
            print(f"    ! errors: {errs}")
        else:
            print(f"    ok: handle={r['productUpdate']['product']['handle']}")

    # 2. variant: weight, barcode, taxable, tracked (but NOT price/compareAtPrice/sku)
    new_variant_id = new_product["variants"]["edges"][0]["node"]["id"]
    src_v = old["variants"]["edges"][0]["node"]
    weight = ((src_v.get("inventoryItem") or {}).get("measurement") or {}).get("weight") or {}
    iv = src_v.get("inventoryItem") or {}
    inv_input = {
        "tracked": iv.get("tracked"),
    }
    if weight:
        inv_input["measurement"] = {"weight": {"value": weight.get("value"), "unit": weight.get("unit")}}
    print(f"  → productVariantsBulkUpdate: barcode={src_v.get('barcode')!r}, "
          f"taxable={src_v.get('taxable')}, weight={weight}")
    if not dry_run:
        r2 = client.execute(VARIANTS_UPDATE, variables={
            "productId": new_id,
            "variants": [{
                "id":            new_variant_id,
                "barcode":       src_v.get("barcode"),
                "taxable":       src_v.get("taxable"),
                "inventoryItem": inv_input,
            }]
        })
        errs = r2["productVariantsBulkUpdate"]["userErrors"]
        if errs:
            print(f"    ! errors: {errs}")
        else:
            print(f"    ok")

    # 3. metafields (custom.*, filter.*, judgeme.*)
    mfs = []
    for e in old["metafields"]["edges"]:
        n = e["node"]
        if n["namespace"] not in COPY_METAFIELD_NAMESPACES:
            continue
        mfs.append({
            "ownerId":   new_id,
            "namespace": n["namespace"],
            "key":       n["key"],
            "type":      n["type"],
            "value":     n["value"],
        })
    print(f"  → metafieldsSet: {len(mfs)} metafields ({sorted({m['namespace'] for m in mfs})})")
    if mfs and not dry_run:
        # Shopify caps metafieldsSet at 25 per call.
        for i in range(0, len(mfs), 25):
            chunk = mfs[i:i+25]
            r3 = client.execute(SET_METAFIELDS, variables={"metafields": chunk})
            errs = r3["metafieldsSet"]["userErrors"]
            if errs:
                print(f"    ! errors (chunk {i//25 + 1}): {errs}")
            else:
                print(f"    ok: chunk {i//25 + 1} — {len(r3['metafieldsSet']['metafields'])} set")

    # 4. media (images)
    images = [e["node"] for e in old["media"]["edges"]
              if e["node"].get("mediaContentType") == "IMAGE" and e["node"].get("image")]
    print(f"  → productCreateMedia: {len(images)} images")
    if images and not dry_run:
        r4 = client.execute(CREATE_MEDIA, variables={
            "productId": new_id,
            "media": [{
                "mediaContentType": "IMAGE",
                "originalSource":   img["image"]["url"],
                "alt":              img["image"].get("altText") or "",
            } for img in images]
        })
        errs = r4["productCreateMedia"]["mediaUserErrors"]
        if errs:
            print(f"    ! errors: {errs}")
        else:
            print(f"    ok: {len(r4['productCreateMedia']['media'])} queued (async processing)")


def rename_sku_and_set_status(client, product_id, variant_id, new_sku, status, dry_run=False):
    """Single round-trip: rename SKU + set product status."""
    # productUpdate doesn't accept variant updates, so do them separately.
    print(f"  → variant SKU → {new_sku!r}")
    if not dry_run:
        r1 = client.execute(VARIANTS_UPDATE, variables={
            "productId": product_id,
            "variants":  [{"id": variant_id, "inventoryItem": {"sku": new_sku}}],
        })
        errs = r1["productVariantsBulkUpdate"]["userErrors"]
        if errs:
            print(f"    ! errors: {errs}")
        else:
            print(f"    ok")

    print(f"  → status → {status}")
    if not dry_run:
        r2 = client.execute(PRODUCT_UPDATE, variables={
            "input": {"id": product_id, "status": status},
        })
        errs = r2["productUpdate"]["userErrors"]
        if errs:
            print(f"    ! errors: {errs}")
        else:
            print(f"    ok: status={r2['productUpdate']['product']['status']}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="Print actions without executing")
    p.add_argument("--phase", choices=["patch", "flip", "all"], default="all",
                   help="patch: copy data only; flip: rename SKUs + status only; all: do both")
    p.add_argument("--only", help="comma-separated old SKUs to process (default: all)")
    args = p.parse_args()

    pairs = PAIRS
    if args.only:
        only = set(s.strip() for s in args.only.split(","))
        pairs = [(nid, sku) for nid, sku in PAIRS if sku in only]
        if not pairs:
            raise SystemExit(f"--only matched nothing. Known SKUs: {[s for _, s in PAIRS]}")

    with ShopifyClient() as client:
        for new_id, old_sku in pairs:
            print(f"\n{'='*70}")
            print(f"=== {old_sku} → new id={new_id} ===")
            print('=' * 70)

            old = fetch_old(client, old_sku)
            new = fetch_new(client, new_id)
            print(f"  OLD: id={old['id']}  status={old['status']}  variant_sku={old['variants']['edges'][0]['node']['sku']}")
            print(f"  NEW: id={new['id']}  status={new['status']}  variant_sku={new['variants']['edges'][0]['node']['sku']}")

            if args.phase in ("patch", "all"):
                print(f"\n  ─── PHASE 1: copy data ───")
                patch_data(client, new, old, dry_run=args.dry_run)

            if args.phase in ("flip", "all"):
                print(f"\n  ─── PHASE 2: rename + flip status ───")
                # Step 1: old → -OLD suffix + DRAFT (frees up the canonical SKU)
                old_var_id = old["variants"]["edges"][0]["node"]["id"]
                rename_sku_and_set_status(
                    client, old["id"], old_var_id,
                    new_sku=f"{old_sku}-OLD", status="DRAFT", dry_run=args.dry_run,
                )
                # Step 2: new → drop -NEW suffix + ACTIVE
                new_var_id = new["variants"]["edges"][0]["node"]["id"]
                rename_sku_and_set_status(
                    client, new["id"], new_var_id,
                    new_sku=old_sku, status="ACTIVE", dry_run=args.dry_run,
                )

        print(f"\n{'='*70}\n=== Done ===\n{'='*70}")


if __name__ == "__main__":
    main()
