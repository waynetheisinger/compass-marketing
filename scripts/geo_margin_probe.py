"""Ad-hoc: pull price / cost / stock for the geolocation.txt product range.

IDs in reports/geolocation.txt are of the form
  shopify_gb_{productId}_{variantId}
We query each ProductVariant for price, unit cost and inventory quantity,
then compute gross margin so we can size a target ROAS.
"""
from __future__ import annotations

import re
import sys

from scripts.shopify_client import ShopifyClient

IDS_FILE = "reports/geolocation.txt"

Q = """
query($ids: [ID!]!) {
  nodes(ids: $ids) {
    ... on ProductVariant {
      id
      title
      sku
      price
      inventoryQuantity
      product { title }
      inventoryItem { unitCost { amount } }
    }
  }
}
"""


def parse_variant_ids(path: str) -> list[str]:
    gids = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            # last numeric group is the variant id
            m = re.search(r"_(\d+)_(\d+)$", line)
            if not m:
                continue
            variant_id = m.group(2)
            gids.append(f"gid://shopify/ProductVariant/{variant_id}")
    return gids


def main() -> None:
    gids = parse_variant_ids(IDS_FILE)
    print(f"parsed {len(gids)} variant ids", file=sys.stderr)
    rows = []
    with ShopifyClient() as client:
        # batch in chunks of 50
        for i in range(0, len(gids), 50):
            chunk = gids[i : i + 50]
            data = client.execute(Q, {"ids": chunk})
            for node in data["nodes"]:
                if not node:
                    continue
                price = float(node["price"]) if node["price"] else 0.0
                cost = None
                ii = node.get("inventoryItem") or {}
                uc = ii.get("unitCost")
                if uc and uc.get("amount"):
                    cost = float(uc["amount"])
                qty = node.get("inventoryQuantity")
                rows.append(
                    {
                        "product": (node.get("product") or {}).get("title", ""),
                        "sku": node.get("sku") or "",
                        "price": price,
                        "cost": cost,
                        "qty": qty,
                    }
                )

    # print a tidy table + aggregates
    print(f"{'SKU':<22} {'price':>9} {'cost':>9} {'margin%':>8} {'qty':>6}  product")
    total_rev_potential = 0.0
    total_gp_potential = 0.0
    margins = []
    for r in sorted(rows, key=lambda x: -(x["price"] or 0)):
        if r["cost"] is not None and r["price"]:
            margin = (r["price"] - r["cost"]) / r["price"] * 100
            margins.append((margin, r))
        else:
            margin = None
        mstr = f"{margin:7.1f}" if margin is not None else "    n/a"
        cstr = f"{r['cost']:9.2f}" if r["cost"] is not None else "      n/a"
        qty = r["qty"] if r["qty"] is not None else "?"
        print(
            f"{r['sku']:<22} {r['price']:9.2f} {cstr} {mstr} {str(qty):>6}  {r['product'][:40]}"
        )

    print("\n--- aggregates (variants with known cost) ---")
    if margins:
        avg_margin = sum(m for m, _ in margins) / len(margins)
        # stock-weighted / price-weighted margin (weight by price as an AOV proxy)
        wsum = sum(r["price"] for _, r in margins)
        wmargin = sum(m * r["price"] for m, r in margins) / wsum if wsum else 0
        print(f"variants with cost: {len(margins)} / {len(rows)}")
        print(f"simple avg margin:  {avg_margin:.1f}%")
        print(f"price-weighted margin: {wmargin:.1f}%")
        print(f"min margin: {min(m for m, _ in margins):.1f}%  max margin: {max(m for m, _ in margins):.1f}%")
    stock_known = [r for r in rows if r["qty"] is not None]
    if stock_known:
        print(f"total units in stock (known): {sum(r['qty'] for r in stock_known)}")


if __name__ == "__main__":
    main()
