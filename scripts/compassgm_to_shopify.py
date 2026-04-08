"""
Push scraped compassgm.co.uk product data to MowDirect Shopify store.

Reads a JSON file produced by compassgm_scraper.py (with copy already rewritten),
then for each product:
  - Queries Shopify by SKU to detect create vs update
  - Creates or updates the product with all fields and metafields
  - Attaches images from the supplier URLs

Usage:
    python scripts/compassgm_to_shopify.py scraped_products/batch_rewritten.json

    # Dry run — show what would be sent without writing to Shopify
    python scripts/compassgm_to_shopify.py scraped_products/batch_rewritten.json --dry-run
"""

import json
import sys
import argparse
from pathlib import Path
from scripts.shopify_client import ShopifyClient


# ---------------------------------------------------------------------------
# GraphQL — lookup
# ---------------------------------------------------------------------------

FIND_BY_SKU = """
query findBySku($sku: String!) {
  productVariants(first: 1, query: $sku) {
    edges {
      node {
        id
        product {
          id
        }
      }
    }
  }
}
"""

# ---------------------------------------------------------------------------
# GraphQL — create
# ---------------------------------------------------------------------------

# In Shopify API 2024-04+, variants are no longer part of ProductInput.
# Create the product first, then update the auto-created default variant.
PRODUCT_CREATE = """
mutation productCreate($input: ProductInput!, $media: [CreateMediaInput!]) {
  productCreate(input: $input, media: $media) {
    product {
      id
      title
      handle
      variants(first: 1) {
        edges { node { id } }
      }
    }
    userErrors { field message }
  }
}
"""

# ---------------------------------------------------------------------------
# GraphQL — update
# ---------------------------------------------------------------------------

PRODUCT_UPDATE = """
mutation productUpdate($input: ProductInput!) {
  productUpdate(input: $input) {
    product {
      id
      title
      handle
    }
    userErrors { field message }
  }
}
"""

# Set SKU, price, barcode on a variant (used after create and on update)
VARIANT_UPDATE = """
mutation variantsBulkUpdate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) {
    productVariants { id sku price barcode }
    userErrors { field message }
  }
}
"""

# Attach additional images to an existing product
PRODUCT_CREATE_MEDIA = """
mutation productCreateMedia($productId: ID!, $media: [CreateMediaInput!]!) {
  productCreateMedia(productId: $productId, media: $media) {
    media { ... on MediaImage { id } }
    mediaUserErrors { field message }
  }
}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _metafield_inputs(metafields: dict) -> list[dict]:
    """Convert the metafields dict to Shopify MetafieldInput list."""
    type_map = {
        "custom.display_attributes": ("custom",      "display_attributes", "json"),
        "custom.feature_bullets":    ("custom",      "feature_bullets",    "multi_line_text_field"),
        "custom.bullet_two":         ("custom",      "bullet_two",         "single_line_text_field"),
        "custom.bullet_three":       ("custom",      "bullet_three",       "single_line_text_field"),
        "custom.delivery_time":      ("custom",      "delivery_time",      "single_line_text_field"),
        "filter.brand":              ("filter",      "brand",              "single_line_text_field"),
        # filter.condition omitted — metafield definition has an owner subtype constraint
    }
    result = []
    for key, value in metafields.items():
        if key not in type_map:
            continue
        if not value:
            continue
        namespace, mf_key, mf_type = type_map[key]
        # display_attributes must be stored as a JSON string
        if mf_type == "json" and not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        result.append({
            "namespace": namespace,
            "key":       mf_key,
            "type":      mf_type,
            "value":     value,
        })
    return result


def _media_inputs(image_urls: list[str]) -> list[dict]:
    return [
        {"originalSource": url, "mediaContentType": "IMAGE"}
        for url in image_urls
    ]


def _build_product_input(product: dict, product_id: str | None = None) -> dict:
    """Build the ProductInput dict for create or update (no variants — set separately)."""
    inp: dict = {
        "title":           product["title"],
        "descriptionHtml": product["description_html"],
        "vendor":          product["vendor"],
        "productType":     product["product_type"],
        "status":          product["status"].upper(),
        "metafields":      _metafield_inputs(product.get("metafields", {})),
    }
    if product_id:
        inp["id"] = product_id
    return inp



# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def find_existing(client: ShopifyClient, sku: str) -> tuple[str | None, str | None]:
    """Return (product_gid, variant_gid) if a variant with this SKU exists, else (None, None)."""
    data = client.execute(FIND_BY_SKU, {"sku": f"sku:{sku}"})
    edges = data.get("productVariants", {}).get("edges", [])
    if edges:
        node = edges[0]["node"]
        return node["product"]["id"], node["id"]
    return None, None


def _update_variant(client: ShopifyClient, product: dict, product_id: str, variant_id: str) -> None:
    variants = [{
        "id":            variant_id,
        "price":         product["price"],
        "barcode":       product["barcode"],
        "inventoryItem": {"sku": product["sku"]},
    }]
    data   = client.execute(VARIANT_UPDATE, {"productId": product_id, "variants": variants})
    errors = data["productVariantsBulkUpdate"].get("userErrors", [])
    for e in errors:
        print(f"  VARIANT ERROR ({e['field']}): {e['message']}", file=sys.stderr)


def create_product(client: ShopifyClient, product: dict, dry_run: bool = False) -> str | None:
    """Create a new Shopify product. Returns the new product GID."""
    inp   = _build_product_input(product)
    media = _media_inputs(product.get("images", []))

    if dry_run:
        print(f"  [DRY RUN] Would CREATE: {product['sku']} — {product['title']}")
        print(f"            Images: {len(media)}, Metafields: {len(inp['metafields'])}")
        return None

    data   = client.execute(PRODUCT_CREATE, {"input": inp, "media": media})
    result = data["productCreate"]
    errors = result.get("userErrors", [])
    if errors:
        for e in errors:
            print(f"  ERROR ({e['field']}): {e['message']}", file=sys.stderr)
        return None

    p          = result["product"]
    variant_id = p["variants"]["edges"][0]["node"]["id"]
    print(f"  CREATED  {p['handle']}  →  {p['id']}")

    _update_variant(client, product, p["id"], variant_id)
    print(f"  VARIANT  SKU={product['sku']}  price={product['price']}  barcode={product['barcode']}")
    return p["id"]


def update_product(client: ShopifyClient, product: dict, product_id: str,
                   variant_id: str, dry_run: bool = False) -> None:
    """Update an existing Shopify product."""
    inp = _build_product_input(product, product_id=product_id)

    if dry_run:
        print(f"  [DRY RUN] Would UPDATE: {product['sku']} — {product['title']}  ({product_id})")
        print(f"            Metafields: {len(inp['metafields'])}")
        return

    data   = client.execute(PRODUCT_UPDATE, {"input": inp})
    result = data["productUpdate"]
    errors = result.get("userErrors", [])
    if errors:
        for e in errors:
            print(f"  ERROR ({e['field']}): {e['message']}", file=sys.stderr)
        return

    p = result["product"]
    print(f"  UPDATED  {p['handle']}  →  {p['id']}")

    _update_variant(client, product, product_id, variant_id)
    print(f"  VARIANT  SKU={product['sku']}  price={product['price']}")

    # Add any new images (update doesn't accept media param)
    if product.get("images"):
        media = _media_inputs(product["images"])
        client.execute(PRODUCT_CREATE_MEDIA, {"productId": product_id, "media": media})
        print(f"  IMAGES   attached {len(media)} image(s)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process(products: list[dict], dry_run: bool = False) -> None:
    with ShopifyClient() as client:
        for product in products:
            sku = product.get("sku", "UNKNOWN")
            print(f"\n{'─'*60}")
            print(f"Processing: {sku} — {product.get('title', '')}")

            if "error" in product:
                print(f"  SKIP — scrape error: {product['error']}", file=sys.stderr)
                continue

            product_id, variant_id = find_existing(client, sku)

            if product_id:
                update_product(client, product, product_id, variant_id, dry_run=dry_run)
            else:
                create_product(client, product, dry_run=dry_run)

    print(f"\n{'─'*60}")
    print("Done.")


def main():
    parser = argparse.ArgumentParser(description="Push scraped products to Shopify")
    parser.add_argument("input_file", help="JSON file from compassgm_scraper.py (after copy rewrite)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to Shopify")
    args = parser.parse_args()

    raw = Path(args.input_file).read_text(encoding="utf-8")
    data = json.loads(raw)
    # Handle both single product (dict) and batch (list)
    products = data if isinstance(data, list) else [data]

    print(f"Loaded {len(products)} product(s) from {args.input_file}")
    if args.dry_run:
        print("── DRY RUN MODE ─────────────────────────────────────────")

    process(products, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
