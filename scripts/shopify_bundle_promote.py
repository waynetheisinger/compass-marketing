"""
Promote a "componentized product" (variant-level components only) to a fully-
registered Shopify Bundles-app bundle, so it appears in Admin → Bundles and
gains the editable component-list extension on the product edit page.

Background: products created via `productCreate` + `productVariantRelationshipBulkUpdate`
have working variant components (frontend cart transform works) but are not
registered with the Bundles app. `productBundleUpdate` is documented as updating
"a product bundle or componentized product" — passing the existing components
re-registers the product with the Bundles app without changing the data.

Usage:
    python3.11 scripts/shopify_bundle_promote.py --sku SBS460CLM-KIT
    python3.11 scripts/shopify_bundle_promote.py --all-spectrum-kits
"""
import os
import sys
import time
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shopify_client import ShopifyClient


SPECTRUM_KIT_SKUS = [
    "SBS460CLM-KIT",
    "SBS560CHT-KIT",
    "SBS220CHM-KIT",
    "SBS240CPHT-KIT",
    "SBS480CBV-KIT",
]


GET_BUNDLE_BY_SKU = """
query Q($q: String!) {
  products(first: 5, query: $q) {
    edges {
      node {
        id handle title
        hasVariantsThatRequiresComponents
        bundleComponents(first: 50) {
          edges {
            node {
              quantity
              componentProduct {
                id title
                options { id name values }
              }
              componentVariants(first: 5) { edges { node { id sku selectedOptions { name value } } } }
            }
          }
        }
      }
    }
  }
}
"""


PRODUCT_BUNDLE_UPDATE = """
mutation BundleUpdate($input: ProductBundleUpdateInput!) {
  productBundleUpdate(input: $input) {
    productBundleOperation { id status }
    userErrors { field message }
  }
}
"""


# Polling query for the async ProductBundleOperation.
GET_OPERATION = """
query Q($id: ID!) {
  productOperation(id: $id) {
    __typename
    ... on ProductBundleOperation {
      id
      status
      product { id handle title }
      userErrors { field message code }
    }
  }
}
"""


def fetch_bundle(client, sku):
    r = client.execute(GET_BUNDLE_BY_SKU, variables={"q": f"sku:{sku}"})
    edges = r["products"]["edges"]
    matches = [e["node"] for e in edges if any(
        sku.lower() in (v["node"]["sku"] or "").lower()
        for c in e["node"]["bundleComponents"]["edges"]
        for v in c["node"]["componentVariants"]["edges"]
    ) or sku.lower() in (e["node"]["title"] or "").lower()]
    # Just take the first hit — sku search is exact enough
    if not edges:
        raise SystemExit(f"No product found for sku={sku}")
    return edges[0]["node"]


def build_components_input(node):
    """Convert the existing bundleComponents into ProductBundleComponentInput list."""
    out = []
    for c in node["bundleComponents"]["edges"]:
        n = c["node"]
        comp = {
            "productId": n["componentProduct"]["id"],
            "quantity":  n["quantity"],
        }
        # optionSelections is required (even for single-variant default-title components).
        # Each entry needs the componentOptionId (the option's ID on the component product)
        # and the values selected. For single-variant products, that's typically the default
        # Title option with value "Default Title".
        variants    = n["componentVariants"]["edges"]
        chosen_sel  = variants[0]["node"]["selectedOptions"]   # name + value pairs
        comp_opts   = n["componentProduct"]["options"]         # id + name + values
        opt_id_by_name = {o["name"]: o["id"] for o in comp_opts}
        # `name` is required: it names the option on the parent bundle product.
        # Reusing the component's option name (e.g. "Title") lets Shopify auto-consolidate
        # multiple components' default-Title options into a single "Title" option on
        # the parent — same shape as the working SBS460CLM-BUNDLE.
        comp["optionSelections"] = [
            {"componentOptionId": opt_id_by_name[s["name"]],
             "name":              s["name"],
             "values":            [s["value"]]}
            for s in chosen_sel
        ]
        out.append(comp)
    return out


def promote(client, sku):
    print(f"\n=== {sku} ===")
    node = fetch_bundle(client, sku)
    print(f"  Product: {node['title'][:70]}")
    print(f"  ID: {node['id']}")
    print(f"  Existing components ({len(node['bundleComponents']['edges'])}):")
    for c in node["bundleComponents"]["edges"]:
        n = c["node"]
        skus = [v["node"]["sku"] for v in n["componentVariants"]["edges"]]
        print(f"    x{n['quantity']}  {n['componentProduct']['title'][:50]}  ({skus})")

    components = build_components_input(node)
    payload = {
        "input": {
            "productId":  node["id"],
            "components": components,
        }
    }

    r = client.execute(PRODUCT_BUNDLE_UPDATE, variables=payload)
    res = r["productBundleUpdate"]
    if res["userErrors"]:
        print(f"  ✗ userErrors: {res['userErrors']}")
        return None
    op = res["productBundleOperation"]
    op_id = op["id"]
    print(f"  → operation queued: {op_id}  status={op['status']}")

    # Poll
    for attempt in range(30):
        time.sleep(2)
        r2 = client.execute(GET_OPERATION, variables={"id": op_id})
        op2 = r2["productOperation"]
        status = op2["status"]
        errs   = op2.get("userErrors") or []
        print(f"    attempt {attempt+1}: status={status}  errors={len(errs)}")
        if status in ("COMPLETE", "FAILED"):
            if errs:
                for e in errs:
                    print(f"      ! {e.get('field')}: {e.get('message')}  code={e.get('code')}")
            return op2
    print("  ! Timed out waiting for operation to complete")
    return op2


def main():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--sku")
    g.add_argument("--all-spectrum-kits", action="store_true")
    g.add_argument("--remaining-spectrum-kits", action="store_true",
                   help="Skip SBS460CLM-KIT (assume it's been done as the sanity-check)")
    args = p.parse_args()

    if args.sku:
        skus = [args.sku]
    elif args.remaining_spectrum_kits:
        skus = [s for s in SPECTRUM_KIT_SKUS if s != "SBS460CLM-KIT"]
    else:
        skus = SPECTRUM_KIT_SKUS

    with ShopifyClient() as client:
        for sku in skus:
            promote(client, sku)


if __name__ == "__main__":
    main()
