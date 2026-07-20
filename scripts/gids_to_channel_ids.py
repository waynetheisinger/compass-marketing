"""
One-off: convert Shopify product gids in raw.txt to channel identifiers of the
form `shopify_gb_<productNumericId>_<variantNumericId>`.

Reads gids from raw.txt (one per line), looks up each product's first variant via
a single batched GraphQL query, and overwrites raw.txt with the converted ids in
the same order. Run from the repo root:

    .venv/bin/python scripts/gids_to_channel_ids.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.shopify_client import ShopifyClient

RAW = "raw.txt"
MARKET = "gb"  # fixed market segment (Great Britain), not derived from the API


def numeric_id(gid: str) -> str:
    """gid://shopify/Product/123 -> '123'."""
    return gid.rsplit("/", 1)[-1]


def main() -> None:
    with open(RAW) as f:
        gids = [line.strip() for line in f if line.strip()]

    # Batch all products into one query, aliased p0..pN.
    nodes = " ".join(
        f'p{i}: product(id: "{g}") {{ id variants(first: 1) {{ nodes {{ id }} }} }}'
        for i, g in enumerate(gids)
    )
    with ShopifyClient() as client:
        data = client.execute("{ " + nodes + " }")

    out: list[str] = []
    skipped = 0
    for i, gid in enumerate(gids):
        product = data.get(f"p{i}")
        if not product:
            print(f"WARNING: {gid} did not resolve — skipping", file=sys.stderr)
            skipped += 1
            continue
        variants = product["variants"]["nodes"]
        if not variants:
            print(f"WARNING: {gid} has no variants — skipping", file=sys.stderr)
            skipped += 1
            continue
        pid = numeric_id(product["id"])
        vid = numeric_id(variants[0]["id"])
        out.append(f"shopify_{MARKET}_{pid}_{vid}")

    with open(RAW, "w") as f:
        f.write("\n".join(out) + "\n")

    print(f"{len(out)} converted, {skipped} skipped -> {RAW}")


if __name__ == "__main__":
    main()
