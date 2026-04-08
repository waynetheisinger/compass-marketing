"""
List all Shopify product IDs (and titles) where every variant has an empty SKU.

Usage:
    python -m scripts.find_products_without_sku
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.shopify_client import ShopifyClient

QUERY = """
query getProducts($cursor: String) {
  products(first: 250, after: $cursor) {
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      id
      title
      variants(first: 100) {
        nodes {
          sku
        }
      }
    }
  }
}
"""

def main():
    no_sku = []
    cursor = None
    page = 0

    with ShopifyClient() as client:
        while True:
            page += 1
            data = client.execute(QUERY, {"cursor": cursor})
            products = data["products"]

            for product in products["nodes"]:
                skus = [v["sku"] for v in product["variants"]["nodes"]]
                if all(not s for s in skus):
                    no_sku.append((product["id"], product["title"]))

            page_info = products["pageInfo"]
            if not page_info["hasNextPage"]:
                break
            cursor = page_info["endCursor"]
            print(f"  Fetched page {page}, continuing...", file=sys.stderr)

    print(f"\nProducts with no SKU on any variant: {len(no_sku)}\n")
    for gid, title in sorted(no_sku):
        numeric_id = gid.split("/")[-1]
        print(f"  {numeric_id}  {title}")

if __name__ == "__main__":
    main()
