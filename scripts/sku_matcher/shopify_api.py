"""Thin GraphQL wrapper over scripts.shopify_client for the SKU updater.

This module deliberately mirrors the surface the rest of the updater code
expects (`ShopifyProduct`, `ShopifyVariant`, `find_products_by_sku`,
`update_variant_sku`, `update_multiple_variant_skus`) but does all the work
through `scripts.shopify_client.ShopifyClient` — the project's canonical
Shopify auth + GraphQL entrypoint (client-credentials grant, `2026-01`).

The REST PUT /variants/{id}.json call this used to make is deprecated
(removed in API version 2025-07); the equivalent today is the
`productVariantsBulkUpdate` mutation with `inventoryItem.sku`.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

from scripts.shopify_client import ShopifyClient


@dataclass
class ShopifyVariant:
    """A Shopify product variant (IDs are GraphQL GIDs)."""
    id: str           # gid://shopify/ProductVariant/...
    product_id: str   # gid://shopify/Product/...
    title: str
    sku: str
    position: int

    def __repr__(self):
        return f"Variant(id={self.id}, title='{self.title}', sku='{self.sku}')"


@dataclass
class ShopifyProduct:
    """A Shopify product plus its variants."""
    id: str           # gid://shopify/Product/...
    title: str
    handle: str
    variants: List[ShopifyVariant]

    def __repr__(self):
        return f"Product(id={self.id}, title='{self.title}', variants={len(self.variants)})"


class ShopifyAPIError(Exception):
    """Raised for any Shopify error surfaced by the GraphQL layer."""


# ---------------------------------------------------------------------------
# GraphQL documents
# ---------------------------------------------------------------------------

_FIND_VARIANTS_BY_SKU = """
query findVariantsBySku($q: String!) {
  productVariants(first: 25, query: $q) {
    edges {
      node {
        id
        sku
        title
        position
        product {
          id
          title
          handle
          variants(first: 100) {
            edges {
              node {
                id
                sku
                title
                position
              }
            }
          }
        }
      }
    }
  }
}
"""

_BULK_UPDATE_VARIANTS = """
mutation variantsBulkUpdate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) {
    productVariants { id sku }
    userErrors { field message }
  }
}
"""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class ShopifyAPI:
    """Updater-facing wrapper. Holds a `ShopifyClient` and adapts it to the
    interface the updater script expects."""

    def __init__(self, client: Optional[ShopifyClient] = None):
        """Pass an existing ShopifyClient or let one be created on first use.

        The client manages its own token refresh and session — we just
        forward GraphQL calls to it. Credentials come from `.env`
        (SHOPIFY_STORE_DOMAIN / SHOPIFY_CLIENT_ID / SHOPIFY_CLIENT_SECRET).
        """
        self._client = client or ShopifyClient()
        self._owns_client = client is None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __enter__(self):
        if self._owns_client:
            self._client.__enter__()
        return self

    def __exit__(self, *exc):
        if self._owns_client:
            self._client.__exit__(*exc)

    def validate_credentials(self) -> bool:
        """One-shot connectivity check — fetches `{ shop { name } }`.

        Raises `ShopifyAPIError` if the call fails (bad credentials, network,
        permissions). Returns True on success so the updater's existing
        startup banner reads naturally.
        """
        try:
            data = self._client.execute("{ shop { name } }")
        except Exception as e:
            raise ShopifyAPIError(f"Credential validation failed: {e}") from e
        if not data.get("shop", {}).get("name"):
            raise ShopifyAPIError("Credential validation returned empty shop data")
        return True

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def find_products_by_sku(self, sku: str) -> List[ShopifyProduct]:
        """Return every product that has a variant matching `sku` exactly.

        Uses the `productVariants` connection's `query` filter so this is a
        single GraphQL call — no catalogue-wide pagination. The same product
        is only returned once even if it has multiple variants sharing the
        SKU (rare, but possible).
        """
        # `sku:VALUE` is Shopify's search syntax for an exact-match SKU filter.
        try:
            data = self._client.execute(_FIND_VARIANTS_BY_SKU, {"q": f"sku:{sku}"})
        except Exception as e:
            raise ShopifyAPIError(f"productVariants query failed: {e}") from e

        edges = data.get("productVariants", {}).get("edges", []) or []
        if not edges:
            return []

        # De-dupe by product GID — many variants of one product all share the
        # same product node.
        seen: dict[str, ShopifyProduct] = {}
        for edge in edges:
            node = edge["node"]
            product_node = node.get("product") or {}
            product_gid = product_node.get("id")
            if not product_gid or product_gid in seen:
                continue

            variant_edges = (
                product_node.get("variants", {}).get("edges", []) or []
            )
            variants = [
                ShopifyVariant(
                    id=v["node"]["id"],
                    product_id=product_gid,
                    title=v["node"].get("title") or "Default Title",
                    sku=v["node"].get("sku") or "",
                    position=v["node"].get("position") or 0,
                )
                for v in variant_edges
            ]

            seen[product_gid] = ShopifyProduct(
                id=product_gid,
                title=product_node.get("title") or "",
                handle=product_node.get("handle") or "",
                variants=variants,
            )

        return list(seen.values())

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def update_variant_sku(
        self,
        product_id: str,
        variant_id: str,
        new_sku: str,
    ) -> Tuple[bool, Optional[str]]:
        """Rewrite a single variant's SKU via `productVariantsBulkUpdate`.

        Note: Shopify's mutation puts `sku` under `inventoryItem`, not on the
        variant root (where it used to live on the REST API).
        """
        return self._update_one_product(
            product_id,
            [{"id": variant_id, "inventoryItem": {"sku": new_sku}}],
        )[0]

    def update_multiple_variant_skus(
        self,
        product_id: str,
        updates: List[Tuple[str, str]],
    ) -> List[Tuple[str, bool, Optional[str]]]:
        """Rewrite several variants of the **same** product in one mutation.

        Args:
            product_id: Parent product GID. All variant updates must belong
                to this product — `productVariantsBulkUpdate` is scoped to
                one product per call.
            updates: List of `(variant_gid, new_sku)` tuples.

        Returns one `(variant_id, success, error_message)` tuple per input,
        in the same order. If the mutation fails wholesale, every entry is
        marked failed with the same error.
        """
        if not updates:
            return []

        variants_payload = [
            {"id": vid, "inventoryItem": {"sku": new_sku}}
            for vid, new_sku in updates
        ]

        success, error = self._update_one_product(product_id, variants_payload)[0]
        if success:
            return [(vid, True, None) for vid, _ in updates]

        # The mutation is all-or-nothing at the API level — Shopify can return
        # per-variant userErrors, but if any fail none are written. Propagate
        # the same error to every row so the caller's logging is consistent.
        return [(vid, False, error) for vid, _ in updates]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _update_one_product(
        self,
        product_id: str,
        variants_payload: List[dict],
    ) -> List[Tuple[bool, Optional[str]]]:
        """Run `productVariantsBulkUpdate` and condense user errors.

        Returns a single-element list `[(success, error)]` — the wrapper
        funnels both single- and multi-variant paths through here.
        """
        try:
            data = self._client.execute(
                _BULK_UPDATE_VARIANTS,
                {"productId": product_id, "variants": variants_payload},
            )
        except Exception as e:
            return [(False, str(e))]

        result = data.get("productVariantsBulkUpdate") or {}
        errors = result.get("userErrors") or []
        if errors:
            joined = "; ".join(
                f"{'.'.join(e.get('field') or []) or '(no field)'}: {e.get('message', '')}"
                for e in errors
            )
            return [(False, joined)]

        return [(True, None)]
