"""
Clone an existing componentized product as a NEW Bundles-app-registered bundle.

Why: products created via `productCreate` + `productVariantRelationshipBulkUpdate` are
"componentized products" — frontend cart transformation works, but they are NOT
registered with the Shopify Bundles app and therefore don't appear in Admin → Bundles
or get the in-product component editor extension. The marker is set ONLY by the
`productBundleCreate` mutation; there's no retrofit path.

This script:
  1. Reads the source product (title, description, vendor, productType, tags,
     SEO, variant pricing, custom metafields, images, and components)
  2. Creates a NEW product via `productBundleCreate` (registers with Bundles app)
  3. Patches the new product with the cloned source data
  4. Sets the variant SKU to the override (e.g. SBS460CLM-KIT-2)
  5. Defaults to DRAFT status so it doesn't go live until you've verified

Usage:
    python3.11 scripts/shopify_bundle_clone.py --source-sku SBS460CLM-KIT --target-sku SBS460CLM-KIT-2
"""
import os
import sys
import time
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shopify_client import ShopifyClient


# Metafield namespaces we want to copy. Skip:
#   - shopify.* (category-derived; auto-managed by Shopify when category is set)
#   - mm-google-shopping.* (category-derived for Google Shopping feed; can re-add if desired)
COPY_METAFIELD_NAMESPACES = {"custom", "filter", "judgeme"}


GET_SOURCE = """
query Q($q: String!) {
  products(first: 1, query: $q) {
    edges { node {
      id handle title descriptionHtml vendor productType tags status
      seo { title description }
      bundleComponents(first: 50) {
        edges { node {
          quantity
          componentProduct { id title options { id name values } }
          componentVariants(first: 5) { edges { node { id sku selectedOptions { name value } } } }
        } }
      }
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


CREATE_BUNDLE = """
mutation Create($input: ProductBundleCreateInput!) {
  productBundleCreate(input: $input) {
    productBundleOperation { id status }
    userErrors { field message }
  }
}
"""


GET_OPERATION = """
query Q($id: ID!) {
  productOperation(id: $id) {
    __typename
    ... on ProductBundleOperation {
      id status
      product { id handle title }
      userErrors { field message code }
    }
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
    productVariants { id sku price compareAtPrice }
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


def fetch_source(client, source_sku):
    r = client.execute(GET_SOURCE, variables={"q": f"sku:{source_sku}"})
    edges = r["products"]["edges"]
    if not edges:
        raise SystemExit(f"No product found with sku={source_sku}")
    return edges[0]["node"]


def build_components_input(source):
    out = []
    for e in source["bundleComponents"]["edges"]:
        n           = e["node"]
        variants    = n["componentVariants"]["edges"]
        chosen_sel  = variants[0]["node"]["selectedOptions"]
        comp_opts   = n["componentProduct"]["options"]
        opt_id_by_n = {o["name"]: o["id"] for o in comp_opts}
        out.append({
            "productId": n["componentProduct"]["id"],
            "quantity":  n["quantity"],
            "optionSelections": [
                {"componentOptionId": opt_id_by_n[s["name"]],
                 "name":              s["name"],
                 "values":            [s["value"]]}
                for s in chosen_sel
            ],
        })
    return out


def poll_operation(client, op_id, timeout_attempts=30):
    for i in range(timeout_attempts):
        time.sleep(2)
        r = client.execute(GET_OPERATION, variables={"id": op_id})
        op = r["productOperation"]
        status = op["status"]
        errs = op.get("userErrors") or []
        print(f"    poll {i+1}: status={status}  errors={len(errs)}")
        if status in ("COMPLETE", "FAILED"):
            for e in errs:
                print(f"      ! {e.get('field')}: {e.get('message')}  code={e.get('code')}")
            return op
    print("    ! polling timed out")
    return None


def create_bundle(client, title, components):
    print(f"\n→ productBundleCreate(title={title!r}, components={len(components)})")
    r = client.execute(CREATE_BUNDLE, variables={
        "input": {"title": title, "components": components}
    })
    res = r["productBundleCreate"]
    if res["userErrors"]:
        raise SystemExit(f"productBundleCreate failed: {res['userErrors']}")
    op_id = res["productBundleOperation"]["id"]
    print(f"  operation: {op_id}")
    op = poll_operation(client, op_id)
    if not op or op["status"] != "COMPLETE":
        raise SystemExit("Bundle creation did not complete cleanly")
    return op["product"]


def patch_product(client, product_id, source, target_sku):
    # 1. Update top-level fields (description, vendor, productType, tags, seo, status)
    print(f"\n→ productUpdate(top-level fields, status=DRAFT)")
    r = client.execute(PRODUCT_UPDATE, variables={
        "input": {
            "id":              product_id,
            "descriptionHtml": source.get("descriptionHtml") or "",
            "vendor":          source.get("vendor"),
            "productType":     source.get("productType"),
            "tags":            source.get("tags") or [],
            "status":          "DRAFT",
            "seo": {
                "title":       (source.get("seo") or {}).get("title"),
                "description": (source.get("seo") or {}).get("description"),
            },
        }
    })
    if r["productUpdate"]["userErrors"]:
        print(f"  ! {r['productUpdate']['userErrors']}")
    else:
        print(f"  ok: {r['productUpdate']['product']['handle']}  status={r['productUpdate']['product']['status']}")

    # 2. Update variant: SKU, price, compareAtPrice, weight, barcode, taxable
    src_v = source["variants"]["edges"][0]["node"]
    new_p = client.execute("""
    query Q($id: ID!) {
      product(id: $id) { variants(first:1) { edges { node { id } } } }
    }""", variables={"id": product_id})
    new_variant_id = new_p["product"]["variants"]["edges"][0]["node"]["id"]
    weight = ((src_v.get("inventoryItem") or {}).get("measurement") or {}).get("weight") or {}

    print(f"\n→ productVariantsBulkUpdate(sku={target_sku}, price={src_v.get('price')})")
    r2 = client.execute(VARIANTS_UPDATE, variables={
        "productId": product_id,
        "variants": [{
            "id":              new_variant_id,
            "price":           src_v.get("price"),
            "compareAtPrice":  src_v.get("compareAtPrice"),
            "barcode":         src_v.get("barcode"),
            "taxable":         src_v.get("taxable"),
            "inventoryItem": {
                "sku":         target_sku,
                "tracked":     ((src_v.get("inventoryItem") or {}).get("tracked")),
                "measurement": {
                    "weight": {
                        "value": weight.get("value"),
                        "unit":  weight.get("unit"),
                    }
                } if weight else None,
            },
        }]
    })
    if r2["productVariantsBulkUpdate"]["userErrors"]:
        print(f"  ! {r2['productVariantsBulkUpdate']['userErrors']}")
    else:
        print(f"  ok: variant updated")

    # 3. Copy metafields (custom.*, filter.*, judgeme.*)
    mfs = []
    for e in source["metafields"]["edges"]:
        n = e["node"]
        if n["namespace"] not in COPY_METAFIELD_NAMESPACES:
            continue
        mfs.append({
            "ownerId":   product_id,
            "namespace": n["namespace"],
            "key":       n["key"],
            "type":      n["type"],
            "value":     n["value"],
        })
    if mfs:
        print(f"\n→ metafieldsSet({len(mfs)} metafields: {sorted({m['namespace'] for m in mfs})})")
        r3 = client.execute(SET_METAFIELDS, variables={"metafields": mfs})
        if r3["metafieldsSet"]["userErrors"]:
            print(f"  ! {r3['metafieldsSet']['userErrors']}")
        else:
            print(f"  ok: {len(r3['metafieldsSet']['metafields'])} metafields set")

    # 4. Copy images
    images = [e["node"] for e in source["media"]["edges"]
              if e["node"].get("mediaContentType") == "IMAGE" and e["node"].get("image")]
    if images:
        print(f"\n→ productCreateMedia({len(images)} images)")
        r4 = client.execute(CREATE_MEDIA, variables={
            "productId": product_id,
            "media": [{
                "mediaContentType": "IMAGE",
                "originalSource":   img["image"]["url"],
                "alt":              img["image"].get("altText") or "",
            } for img in images]
        })
        if r4["productCreateMedia"]["mediaUserErrors"]:
            print(f"  ! {r4['productCreateMedia']['mediaUserErrors']}")
        else:
            print(f"  ok: {len(r4['productCreateMedia']['media'])} media queued (process async)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source-sku", required=True)
    p.add_argument("--target-sku", required=True)
    args = p.parse_args()

    with ShopifyClient() as client:
        print(f"=== Cloning bundle: {args.source_sku} → {args.target_sku} ===")
        source = fetch_source(client, args.source_sku)
        print(f"Source product: {source['title']}")
        print(f"  components:   {len(source['bundleComponents']['edges'])}")
        print(f"  metafields:   {len(source['metafields']['edges'])}  (will copy: {COPY_METAFIELD_NAMESPACES})")
        print(f"  media:        {len(source['media']['edges'])}")

        components = build_components_input(source)
        new_product = create_bundle(client, source["title"], components)
        print(f"\n✓ Created: id={new_product['id']}  handle={new_product['handle']}")

        patch_product(client, new_product["id"], source, args.target_sku)

        print(f"\n=== Done ===")
        print(f"New product (DRAFT): https://admin.shopify.com → Products → {new_product['handle']}")
        print(f"Verify: does it appear in Admin → Bundles?")


if __name__ == "__main__":
    main()
