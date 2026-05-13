"""
SPECTRUM SBS catalogue — operator-agnostic source of truth for the 15 cordless SKUs.

Combines three sources into one canonical record per SKU:

  - Shopify GraphQL    → EAN (variant.barcode), price, stock, image URLs,
                         display_attributes (raw spec metafield)
  - Amazon SP-API      → title, bullets, body_copy (Amazon-approved live listings),
                         item / item_package dimensions and weight
  - amazon_sbs_push.LISTINGS dict → fallback copy for the 2 bare batteries which
                         are not listed on Amazon (no-bare-lithium policy)

The catalogue is operator-AGNOSTIC. Mapping to Mirakl operator-specific attribute
codes happens in mirakl_operators.py; this module knows nothing about Kingfisher,
Tesco, or The Range.

Usage:
    from scripts.sbs_catalogue import load_catalogue

    cat = load_catalogue()                 # 15 SKUs
    cat = load_catalogue(use_cache=True)   # read /tmp/sbs_catalogue.json if present
    cat = load_catalogue(refresh=True)     # force re-fetch and rewrite cache

CLI:
    python3 scripts/sbs_catalogue.py            # pretty-print all 15 SKUs
    python3 scripts/sbs_catalogue.py --json     # dump to stdout as JSON
    python3 scripts/sbs_catalogue.py --refresh  # force re-fetch
"""
import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, asdict, field
from typing import Any

# Ensure imports work whether invoked from repo root or scripts/
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from shopify_client import ShopifyClient
from amazon_client import AmazonClient, AmazonSPAPIError
from amazon_sbs_push import LISTINGS as _AMAZON_LISTINGS_FALLBACK

CACHE_PATH = "/tmp/sbs_catalogue.json"
SELLER_ID = os.environ.get("AMAZON_SELLER_ID", "")

# --------------------------------------------------------------------------
# Canonical SBS taxonomy
# --------------------------------------------------------------------------
# product_type values are operator-agnostic. mirakl_operators.py maps each
# product_type to an operator-specific category code + attribute set.

PRODUCT_TYPES = {
    "SBS460CLM":      "LAWN_MOWER_BARE",      # 46cm self-prop
    "SBS220CHM":      "LAWN_MOWER_BARE",      # 22cm handy
    "SBS460CLM-KIT":  "LAWN_MOWER_KIT",
    "SBS220CHM-KIT":  "LAWN_MOWER_KIT",
    "SBS480CBV":      "LEAF_BLOWER_BARE",
    "SBS480CBV-KIT":  "LEAF_BLOWER_KIT",
    "SBS560CHT":      "HEDGE_TRIMMER_BARE",   # 45cm regular
    "SBS240CPHT":     "HEDGE_TRIMMER_BARE",   # pole — B&Q files in same category as regular
    "SBS560CHT-KIT":  "HEDGE_TRIMMER_KIT",
    "SBS240CPHT-KIT": "HEDGE_TRIMMER_KIT",
    "SBS20CB":        "BATTERY_BARE",          # 2.0Ah
    "SBS40CB":        "BATTERY_BARE",          # 4.0Ah
    "SBSCBC":         "CHARGER_BARE",          # 0.5A standard
    "SBSCSC":         "CHARGER_BARE",          # 2A fast
    "SBSCDC":         "CHARGER_BARE",          # 2A dual-bay
}

# SKUs not on Amazon UK (bare batteries — no-bare-lithium policy on Amazon).
# These take their copy from amazon_sbs_push.LISTINGS instead of a live SP-API call.
NOT_ON_AMAZON = {"SBS20CB", "SBS40CB"}

# Per-SKU dimension overrides. Keys: dim_l_cm, dim_w_cm, dim_h_cm, weight_kg.
# Use sparingly — only when source data is wrong/missing.
DIM_OVERRIDES: dict[str, dict[str, float]] = {
    # SBS240CPHT-KIT comes back from Amazon SP-API at 250cm package length —
    # almost certainly a typo (250 instead of 25). Bare SBS240CPHT ships at 81cm,
    # so the kit (same tool + battery + charger) should match. Decision 2026-05-07.
    "SBS240CPHT-KIT": {"dim_l_cm": 81.0},
}


@dataclass
class SBSProduct:
    """One SBS catalogue record. Operator-agnostic; flattened generic fields."""
    sku: str
    ean: str
    product_type: str          # one of the PRODUCT_TYPES values
    title: str
    body_copy: str
    bullets: list[str]
    image_url: str             # primary image
    image_urls: list[str]      # primary + additional
    weight_kg: float
    dim_l_cm: float
    dim_w_cm: float
    dim_h_cm: float
    price_gbp: float
    stock: int
    raw_specs: list[dict[str, str]] = field(default_factory=list)   # Shopify display_attributes verbatim
    sources: dict[str, str] = field(default_factory=dict)            # provenance per field group


# --------------------------------------------------------------------------
# Shopify loader
# --------------------------------------------------------------------------

_SHOPIFY_QUERY = """
query SBSProducts($cursor: String) {
  products(first: 50, after: $cursor, query: "sku:SBS*") {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        title
        descriptionHtml
        featuredImage { url }
        images(first: 10) { edges { node { url } } }
        metafield(namespace: "custom", key: "display_attributes") { value }
        variants(first: 10) {
          edges {
            node {
              sku
              barcode
              price
              inventoryQuantity
            }
          }
        }
      }
    }
  }
}
"""


def _parse_shopify_dim(s: str) -> tuple[float | None, float | None, float | None]:
    """Parse '8cm × 9cm × 15cm' → (8.0, 9.0, 15.0). Returns (None,None,None) on no match."""
    if not s:
        return None, None, None
    m = re.search(
        r"([0-9]+(?:\.[0-9]+)?)\s*cm\s*[x×]\s*"
        r"([0-9]+(?:\.[0-9]+)?)\s*cm\s*[x×]\s*"
        r"([0-9]+(?:\.[0-9]+)?)\s*cm",
        s, re.I,
    )
    if not m:
        return None, None, None
    return float(m.group(1)), float(m.group(2)), float(m.group(3))


def _parse_shopify_weight(s: str) -> float | None:
    """Parse '900g' / '3.62kg' / '25.6 kg' → kilograms. Returns None on no match."""
    if not s:
        return None
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(kg|g|kgs?)\b", s, re.I)
    if not m:
        return None
    v = float(m.group(1))
    unit = m.group(2).lower()
    return v / 1000.0 if unit.startswith("g") and not unit.startswith("kg") else v


def fetch_shopify_data() -> dict[str, dict[str, Any]]:
    """Pull per-SKU Shopify data for SBS variants. Returns {sku: data}."""
    out: dict[str, dict[str, Any]] = {}
    with ShopifyClient() as c:
        cursor = None
        for _ in range(10):
            data = c.execute(_SHOPIFY_QUERY, {"cursor": cursor})
            for e in data["products"]["edges"]:
                p = e["node"]
                images = [x["node"]["url"] for x in p.get("images", {}).get("edges", [])]
                feat = (p.get("featuredImage") or {}).get("url") or (images[0] if images else "")
                raw_specs = []
                mf = p.get("metafield")
                if mf and mf.get("value"):
                    try:
                        raw_specs = json.loads(mf["value"])
                    except json.JSONDecodeError:
                        pass

                # Per-spec parsed weight + dims from display_attributes (item-level for batteries/chargers)
                by_code = {a.get("code", ""): a.get("value", "") for a in raw_specs}
                shopify_wt_kg = _parse_shopify_weight(by_code.get("product_weight", ""))
                shopify_l, shopify_w, shopify_h = _parse_shopify_dim(by_code.get("dimensions", ""))

                for ve in p["variants"]["edges"]:
                    v = ve["node"]
                    sku = (v.get("sku") or "").strip()
                    if not sku.upper().startswith("SBS"):
                        continue
                    out[sku] = {
                        "title_shopify": p["title"],
                        "ean": (v.get("barcode") or "").strip(),
                        "price_gbp": float(v.get("price") or 0),
                        "stock": int(v.get("inventoryQuantity") or 0),
                        "image_url": feat,
                        "image_urls": images,
                        "raw_specs": raw_specs,
                        "shopify_weight_kg": shopify_wt_kg,
                        "shopify_dim_l_cm": shopify_l,
                        "shopify_dim_w_cm": shopify_w,
                        "shopify_dim_h_cm": shopify_h,
                        "description_html": p.get("descriptionHtml") or "",
                    }
            if not data["products"]["pageInfo"]["hasNextPage"]:
                break
            cursor = data["products"]["pageInfo"]["endCursor"]
    return out


# --------------------------------------------------------------------------
# Amazon SP-API loader
# --------------------------------------------------------------------------

def _amazon_attr_str(attrs: dict, key: str) -> str:
    """Return the first .value for an SP-API attribute, or ''."""
    arr = attrs.get(key) or []
    if not arr:
        return ""
    return (arr[0].get("value") or "").strip()


def _amazon_attr_dim(attrs: dict, key: str) -> dict | None:
    """Return the first dimension dict (length/width/height with unit+value) or None."""
    arr = attrs.get(key) or []
    return arr[0] if arr else None


def _to_cm(d: dict | None) -> float | None:
    if not d:
        return None
    v = d.get("value")
    u = (d.get("unit") or "").lower()
    if v is None:
        return None
    if u in ("centimeters", "centimetres", "cm"):
        return float(v)
    if u in ("millimeters", "millimetres", "mm"):
        return float(v) / 10.0
    if u in ("inches", "inch", "in"):
        return float(v) * 2.54
    if u in ("meters", "metres", "m"):
        return float(v) * 100.0
    return float(v)


def _to_kg(d: dict | None) -> float | None:
    if not d:
        return None
    v = d.get("value")
    u = (d.get("unit") or "").lower()
    if v is None:
        return None
    if u in ("kilograms", "kilogrammes", "kg"):
        return float(v)
    if u in ("grams", "gramme", "g"):
        return float(v) / 1000.0
    if u in ("pounds", "pound", "lb", "lbs"):
        return float(v) * 0.453592
    return float(v)


def fetch_amazon_data(skus: list[str]) -> dict[str, dict[str, Any]]:
    """Pull title/bullets/description/dims/weight from Amazon SP-API per SKU."""
    if not SELLER_ID:
        raise EnvironmentError("AMAZON_SELLER_ID must be set in environment or .env")
    c = AmazonClient()
    out: dict[str, dict[str, Any]] = {}
    for sku in skus:
        try:
            r = c.get(
                f"/listings/2021-08-01/items/{SELLER_ID}/{sku}",
                params={"marketplaceIds": c.marketplace_id, "includedData": "summaries,attributes"},
            )
        except AmazonSPAPIError as e:
            # Log and skip — caller decides whether to fall back
            print(f"  [WARN] Amazon getListingsItem({sku}): {e.status} — falling back",
                  file=sys.stderr)
            continue

        attrs = r.get("attributes", {}) or {}
        bullets = []
        for i in range(1, 6):
            bp = (attrs.get("bullet_point") or [])
            if i - 1 < len(bp):
                v = (bp[i - 1].get("value") or "").strip()
                if v:
                    bullets.append(v)

        title = _amazon_attr_str(attrs, "item_name")
        body  = _amazon_attr_str(attrs, "product_description")

        # Prefer item_dimensions if present, else item_package_dimensions
        item_d = _amazon_attr_dim(attrs, "item_dimensions")
        pkg_d  = _amazon_attr_dim(attrs, "item_package_dimensions")
        chosen_d = item_d or pkg_d
        dim_source = "item" if item_d else "package" if pkg_d else None

        item_w_d = _amazon_attr_dim(attrs, "item_weight")
        pkg_w_d  = _amazon_attr_dim(attrs, "item_package_weight")
        chosen_w = item_w_d or pkg_w_d
        wt_source = "item" if item_w_d else "package" if pkg_w_d else None

        out[sku] = {
            "title_amazon":     title,
            "body_copy_amazon": body,
            "bullets_amazon":   bullets,
            "dim_l_cm":         _to_cm((chosen_d or {}).get("length")),
            "dim_w_cm":         _to_cm((chosen_d or {}).get("width")),
            "dim_h_cm":         _to_cm((chosen_d or {}).get("height")),
            "dim_source":       dim_source,
            "weight_kg":        _to_kg(chosen_w),
            "weight_source":    wt_source,
        }
    return out


# --------------------------------------------------------------------------
# Catalogue assembly
# --------------------------------------------------------------------------

def _assemble(sku: str,
              shopify: dict[str, Any],
              amazon: dict[str, Any] | None) -> SBSProduct:
    """Combine sources for one SKU into the canonical SBSProduct."""
    sources: dict[str, str] = {}

    # ---- Title / body / bullets ----
    if amazon and amazon.get("title_amazon"):
        title = amazon["title_amazon"]
        body  = amazon["body_copy_amazon"]
        bullets = amazon["bullets_amazon"]
        sources["title"] = "amazon_sp_api"
        sources["body_copy"] = "amazon_sp_api"
        sources["bullets"] = "amazon_sp_api"
    elif sku in _AMAZON_LISTINGS_FALLBACK:
        fb = _AMAZON_LISTINGS_FALLBACK[sku]
        title = fb["title"]
        body  = fb["description"]
        bullets = fb["bullets"]
        sources["title"] = "amazon_sbs_push.LISTINGS"
        sources["body_copy"] = "amazon_sbs_push.LISTINGS"
        sources["bullets"] = "amazon_sbs_push.LISTINGS"
    else:
        # Last-ditch: Shopify product title + description
        title = shopify.get("title_shopify", "")
        body  = re.sub(r"<[^>]+>", "", shopify.get("description_html") or "")
        bullets = []
        sources["title"] = "shopify"
        sources["body_copy"] = "shopify_descriptionHtml_stripped"
        sources["bullets"] = "none"

    # ---- Dims + weight ----
    # Prefer Shopify display_attributes for batteries/chargers (item dims),
    # Amazon for tools/kits (package dims).
    pt = PRODUCT_TYPES[sku]
    if pt in ("BATTERY_BARE", "CHARGER_BARE") and shopify.get("shopify_dim_l_cm") is not None:
        L = shopify["shopify_dim_l_cm"]
        W = shopify["shopify_dim_w_cm"]
        H = shopify["shopify_dim_h_cm"]
        sources["dimensions"] = "shopify_display_attributes"
    elif amazon and amazon.get("dim_l_cm") is not None:
        L = amazon["dim_l_cm"]
        W = amazon["dim_w_cm"]
        H = amazon["dim_h_cm"]
        sources["dimensions"] = f"amazon_{amazon.get('dim_source')}"
    else:
        L = W = H = 0.0
        sources["dimensions"] = "missing"

    # For KIT product types, prefer Amazon item_weight — Shopify display_attributes
    # often inherits the bare-tool weight (the kit metafield was cloned from the
    # bare product but never re-weighed), so it understates the kit by the battery
    # + charger weight. Decision 2026-05-07.
    if pt.endswith("_KIT") and amazon and amazon.get("weight_kg") is not None:
        weight_kg = amazon["weight_kg"]
        sources["weight"] = f"amazon_{amazon.get('weight_source')}"
    elif shopify.get("shopify_weight_kg") is not None:
        weight_kg = shopify["shopify_weight_kg"]
        sources["weight"] = "shopify_display_attributes"
    elif amazon and amazon.get("weight_kg") is not None:
        weight_kg = amazon["weight_kg"]
        sources["weight"] = f"amazon_{amazon.get('weight_source')}"
    else:
        weight_kg = 0.0
        sources["weight"] = "missing"

    # Per-SKU overrides (e.g. SBS240CPHT-KIT length correction)
    overrides = DIM_OVERRIDES.get(sku, {})
    if "dim_l_cm" in overrides:
        L = overrides["dim_l_cm"]
        sources["dim_l_cm_override"] = "manual_DIM_OVERRIDES"
    if "dim_w_cm" in overrides:
        W = overrides["dim_w_cm"]
        sources["dim_w_cm_override"] = "manual_DIM_OVERRIDES"
    if "dim_h_cm" in overrides:
        H = overrides["dim_h_cm"]
        sources["dim_h_cm_override"] = "manual_DIM_OVERRIDES"
    if "weight_kg" in overrides:
        weight_kg = overrides["weight_kg"]
        sources["weight_override"] = "manual_DIM_OVERRIDES"

    return SBSProduct(
        sku=sku,
        ean=shopify.get("ean", ""),
        product_type=pt,
        title=title,
        body_copy=body,
        bullets=bullets,
        image_url=shopify.get("image_url", ""),
        image_urls=shopify.get("image_urls", []),
        weight_kg=round(float(weight_kg), 2),
        dim_l_cm=round(float(L), 2),
        dim_w_cm=round(float(W), 2),
        dim_h_cm=round(float(H), 2),
        price_gbp=float(shopify.get("price_gbp") or 0),
        stock=int(shopify.get("stock") or 0),
        raw_specs=shopify.get("raw_specs", []),
        sources=sources,
    )


def load_catalogue(*, refresh: bool = False, use_cache: bool = False) -> dict[str, SBSProduct]:
    """
    Load the canonical 15-SKU SBS catalogue.

    refresh=True   forces a re-fetch and overwrites the cache.
    use_cache=True reads /tmp/sbs_catalogue.json if present (no API calls).
    Default behaviour: re-fetch from APIs every call (no cache).
    """
    if use_cache and not refresh and os.path.exists(CACHE_PATH):
        with open(CACHE_PATH) as f:
            blob = json.load(f)
        return {k: SBSProduct(**v) for k, v in blob.items()}

    print("Fetching from Shopify…", file=sys.stderr)
    shopify_data = fetch_shopify_data()

    amazon_skus = [sku for sku in PRODUCT_TYPES if sku not in NOT_ON_AMAZON]
    print(f"Fetching from Amazon SP-API ({len(amazon_skus)} SKUs)…", file=sys.stderr)
    amazon_data = fetch_amazon_data(amazon_skus)

    catalogue: dict[str, SBSProduct] = {}
    for sku in PRODUCT_TYPES:
        sh = shopify_data.get(sku)
        if not sh:
            print(f"  [WARN] {sku} not found in Shopify — skipping", file=sys.stderr)
            continue
        am = amazon_data.get(sku)
        catalogue[sku] = _assemble(sku, sh, am)

    # Cache
    blob = {sku: asdict(p) for sku, p in catalogue.items()}
    with open(CACHE_PATH, "w") as f:
        json.dump(blob, f, indent=2)
    print(f"Cached to {CACHE_PATH}", file=sys.stderr)

    return catalogue


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _cli() -> int:
    parser = argparse.ArgumentParser(description="SBS catalogue loader")
    parser.add_argument("--json", action="store_true", help="Dump catalogue as JSON")
    parser.add_argument("--refresh", action="store_true", help="Force re-fetch (skip cache)")
    parser.add_argument("--cache", action="store_true", help="Read cache if present")
    parser.add_argument("--sku", help="Show only this SKU")
    args = parser.parse_args()

    cat = load_catalogue(refresh=args.refresh, use_cache=args.cache)

    if args.sku:
        if args.sku not in cat:
            print(f"Unknown SKU: {args.sku}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(asdict(cat[args.sku]), indent=2))
        else:
            _pretty(cat[args.sku])
        return 0

    if args.json:
        print(json.dumps({k: asdict(v) for k, v in cat.items()}, indent=2))
        return 0

    # Pretty table
    print(f"\n{'SKU':22s} {'Type':22s} {'EAN':16s} {'kg':>6s} {'L':>6s} {'W':>6s} {'H':>6s} {'£':>8s} stock")
    print("-" * 110)
    for sku, p in cat.items():
        print(f"{p.sku:22s} {p.product_type:22s} {p.ean:16s} "
              f"{p.weight_kg:>6.2f} {p.dim_l_cm:>6.1f} {p.dim_w_cm:>6.1f} {p.dim_h_cm:>6.1f} "
              f"{p.price_gbp:>8.2f} {p.stock}")
    print(f"\nTotal: {len(cat)} SKUs")
    print(f"\nSource provenance (sample — first SKU):")
    if cat:
        first = next(iter(cat.values()))
        for k, v in first.sources.items():
            print(f"  {k}: {v}")
    return 0


def _pretty(p: SBSProduct) -> None:
    print(f"SKU:          {p.sku}")
    print(f"EAN:          {p.ean}")
    print(f"Product type: {p.product_type}")
    print(f"Title:        {p.title}")
    print(f"Body copy:    {p.body_copy[:200]}…")
    print(f"Bullets ({len(p.bullets)}):")
    for b in p.bullets:
        print(f"  - {b[:120]}…")
    print(f"Weight:       {p.weight_kg} kg ({p.sources.get('weight')})")
    print(f"Dims (LxWxH): {p.dim_l_cm} x {p.dim_w_cm} x {p.dim_h_cm} cm ({p.sources.get('dimensions')})")
    print(f"Price:        £{p.price_gbp:.2f}")
    print(f"Stock:        {p.stock}")
    print(f"Image:        {p.image_url}")


if __name__ == "__main__":
    sys.exit(_cli())
