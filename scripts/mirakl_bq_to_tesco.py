#!/usr/bin/env python3
"""
B&Q (Kingfisher) active offers → Tesco product CSV (generate-then-review).

Produces an editable Tesco products CSV from the products behind B&Q's *active*
offers. The CSV is the deliverable: review it, hand-edit values (e.g. per-product
baseColour, fix anything), then push it with scripts/mirakl_push_products.py.

Why generate-then-push: Tesco's import transformation report is the real
validator, so there's no need to pre-validate each row against the live
/products/attributes endpoint (which rate-limits hard). This script only calls
Shopify (for category, copy, images) — never Tesco's attribute endpoint.

Mechanism: Tesco's category system IS the Shopify product taxonomy, so each
product's Shopify category gid is used directly as Tesco's shopifyHierarchyId.
Copy + images come from Shopify (matched by EAN, then SKU). Copy is run through
the Tesco cross-retailer compliance scrub.

Run from the repo root. Output → workdir/mirakl-tesco/.
"""
from __future__ import annotations

import csv
import html
import os
import re
import sys
from collections import Counter

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts.mirakl_client import MiraklClient          # noqa: E402
from scripts.shopify_client import ShopifyClient        # noqa: E402
from scripts.mirakl_operators import TESCO              # noqa: E402

_OUT_DIR = os.path.join(_REPO_ROOT, "workdir", "mirakl-tesco")
_CSV_PATH = os.path.join(_OUT_DIR, "bq_active_to_tesco_products.csv")
_REPORT_PATH = os.path.join(_OUT_DIR, "bq_active_to_tesco_REPORT.md")

# Constant value-list fields for this catalogue (codes == labels on Tesco).
# baseColour is set per brand below (varies per product — hand-edit in the CSV).
_DEFAULTS = {
    "countryOfOriginName": "China",   # confirm per product on review
    "ageRestriction":      "No",
    "unitQuantity":        "Each",
    "vatRate":             "20",      # standard-rated; verified accepted 2026-06-09
}

# Per-brand baseColour starting value (hand-edit exceptions in the CSV).
# Spectrum livery is green (confirmed for cordless). Leave others blank so they
# surface for review rather than guessing wrong.
_BRAND_COLOUR = {"spectrum": "Green"}

# Per-SKU baseColour overrides (confirmed by Wayne, 2026-06-09).
_SKU_COLOUR = {
    "GNP560-WT": "Green",    # Compass
    "C-MTF66-MQ": "Red",     # Mountfield ride-on (kept)
}

# SKUs to exclude even though they'd otherwise qualify (Wayne's call).
_EXCLUDE_SKUS = {
    "2T2010483/M25": "Tractor — not selling on Tesco (2026-06-09)",
}

# Shopify taxonomy nodes that Tesco does NOT open to third-party sellers
# (verified via 404 on /products/attributes, 2026-06-09). Products landing here
# can't be listed as-is — they need recategorising in Shopify (or are genuinely
# out of scope). Kept out of the CSV; listed in the report.
_KNOWN_UNSELLABLE_GIDS = {
    "gid://shopify/TaxonomyCategory/vp-1-5-1",     # Portable Fuel Cans
    "gid://shopify/TaxonomyCategory/vp-1-7-5",     # Motor Vehicle Trailers
    "gid://shopify/TaxonomyCategory/vp-1-7-5-4",   # Utility & Cargo Trailers
    "gid://shopify/TaxonomyCategory/bi-11-12",     # Heavy Machinery > Tractors (mis-tag)
}

# CSV column order — editable facets (baseColour) kept near the front.
_COL_ORDER = [
    "shopifyHierarchyId", "sku", "barcode", "brand", "baseColour",
    "description", "marketingText",
    "countryOfOriginName", "ageRestriction", "unitQuantity", "vatRate",
    "image1",
]

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    if not s:
        return ""
    return " ".join(html.unescape(_TAG_RE.sub(" ", s)).split())


def _ean_of(offer: dict) -> str | None:
    for r in offer.get("product_references", []):
        if r.get("reference_type") == "EAN":
            return r.get("reference")
    return None


def _tesco_registered_brands() -> set[str]:
    t = MiraklClient("TESCO")
    vls = t.get("/values_lists", params={"code": "brand"})["values_lists"]
    vals = next(v for v in vls if v["code"] == "brand")["values"]
    return {v["label"].lower() for v in vals}


_SHOPIFY_Q = """
query($q:String!){
  products(first:1, query:$q){
    edges{ node{
      title
      descriptionHtml
      category { id fullName }
      featuredImage { url }
      media(first:10){ edges{ node{ ... on MediaImage { image{ url } } } } }
    } }
  }
}
"""


def _shopify_lookup(client, ean, *skus):
    queries = ([f"barcode:{ean}"] if ean else []) + [f"sku:{s}" for s in skus if s]
    for q in queries:
        edges = client.execute(_SHOPIFY_Q, {"q": q})["products"]["edges"]
        if edges:
            n = edges[0]["node"]
            imgs = []
            if n.get("featuredImage"):
                imgs.append(n["featuredImage"]["url"])
            for e in n.get("media", {}).get("edges", []):
                url = (e.get("node") or {}).get("image", {}).get("url")
                if url and url not in imgs:
                    imgs.append(url)
            return {
                "title": n.get("title"),
                "marketingText": _strip_html(n.get("descriptionHtml")),
                "category_gid": (n.get("category") or {}).get("id"),
                "category_name": (n.get("category") or {}).get("fullName"),
                "images": imgs,
                "matched_by": q.split(":")[0],
            }
    return None


def build():
    os.makedirs(_OUT_DIR, exist_ok=True)
    k = MiraklClient("KINGFISHER")
    fs = TESCO.field_schema
    cp = TESCO.compliance

    offers = k.get("/offers", params={"max": 100}).get("offers", [])
    active = [o for o in offers if o.get("active") is True]
    registered = _tesco_registered_brands()
    print(f"Offers: {len(offers)} total, {len(active)} active")

    rows, listable_recs, outliers, scrub_log = [], [], [], {}

    with ShopifyClient() as sc:
        for o in active:
            sku = o.get("shop_sku")
            brand = (o.get("product_brand") or "").strip()
            ean = _ean_of(o)

            if sku in _EXCLUDE_SKUS:
                outliers.append((sku, brand, "—", _EXCLUDE_SKUS[sku]))
                continue
            if brand.lower() not in registered:
                outliers.append((sku, brand, "—", "brand not registered on Tesco"))
                continue

            sh = _shopify_lookup(sc, ean, sku, o.get("product_sku"))
            if not sh:
                outliers.append((sku, brand, "—",
                                 "not found in Shopify by EAN/SKU — no category/images"))
                continue

            cat_gid = sh.get("category_gid")
            cat_name = sh.get("category_name") or "(none)"
            if not cat_gid:
                outliers.append((sku, brand, cat_name, "no Shopify category assigned"))
                continue
            if cat_gid in _KNOWN_UNSELLABLE_GIDS:
                outliers.append((sku, brand, cat_name,
                                 "Tesco category not sellable — recategorise in Shopify"))
                continue
            if not sh.get("images"):
                outliers.append((sku, brand, cat_name, "no image in Shopify"))
                continue

            title = sh.get("title") or o.get("product_title")
            body = sh.get("marketingText") or _strip_html(o.get("product_description"))
            name_clean = cp.clean_name(title)
            body_clean, body_hits = cp.sanitise_prose(body)
            _, name_hits = cp.scrub(title or "")
            hits = {**({"name": name_hits} if name_hits else {}),
                    **({"marketingText": body_hits} if body_hits else {})}
            if hits:
                scrub_log[sku] = hits

            images = sh["images"]
            row = {
                fs.category: cat_gid,
                fs.sku: sku,
                fs.ean: ean or "",
                "brand": brand,
                "baseColour": _SKU_COLOUR.get(sku, _BRAND_COLOUR.get(brand.lower(), "")),
                fs.name: name_clean,
                fs.body: body_clean,
                fs.image_main: images[0],
            }
            for col, url in zip(fs.extra_images, images[1:]):
                row[col] = url
            row.update(_DEFAULTS)
            rows.append(row)
            listable_recs.append({
                "sku": sku, "brand": brand, "cat": cat_name,
                "colour": row["baseColour"], "imgs": len(images),
                "matched_by": sh["matched_by"],
            })

    _write_csv(rows)
    _write_report(listable_recs, outliers, scrub_log)
    return rows, listable_recs, outliers


def _write_csv(rows):
    cols = list(_COL_ORDER)
    for r in rows:
        for c in r:
            if c not in cols:
                cols.append(c)
    with open(_CSV_PATH, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter=";", extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"  CSV → {_CSV_PATH}  ({len(rows)} rows, {len(cols)} columns)")


def _write_report(listable, outliers, scrub_log):
    blanks = [r["sku"] for r in listable if not r["colour"]]
    lines = [
        "# B&Q active offers → Tesco — products CSV\n",
        f"- Active offers: **{sum(1 for _ in listable) + len(outliers)}** "
        f"(after dropping inactive)",
        f"- **In CSV (ready to review & push): {len(listable)}**",
        f"- Held back (need a fix first): {len(outliers)}\n",
        "## Before you push\n",
        f"- `baseColour` is pre-filled **Green** for Spectrum. "
        f"{len(blanks)} non-Spectrum row(s) have it **blank** — fill before push: "
        f"{', '.join(blanks) or 'none'}.",
        "- A few categories want extra required fields (batteries: `batterySize`, "
        "recycling info; tyred items: tyre dims). Tesco's transformation report "
        "after push will name any still missing — fill those in the CSV and re-push.",
        "- `countryOfOriginName` defaulted to **China**, `vatRate` **20** — "
        "override per row if wrong.\n",
    ]

    if outliers:
        lines.append("## Held back\n")
        lines.append("| SKU | brand | Shopify category | reason |")
        lines.append("|---|---|---|---|")
        for sku, brand, cat, why in outliers:
            lines.append(f"| {sku} | {brand} | {cat} | {why} |")
        lines.append("")

    lines.append("## In CSV — by Tesco category\n")
    catc = Counter(r["cat"] for r in listable)
    lines.append("| Tesco category (from Shopify) | # |")
    lines.append("|---|---|")
    for cat, n in catc.most_common():
        lines.append(f"| {cat} | {n} |")
    lines.append("")

    if scrub_log:
        lines.append("## Cross-retailer scrub hits (review nothing meaningful lost)\n")
        for sku, hits in scrub_log.items():
            lines.append(f"- `{sku}`: {hits}")
        lines.append("")

    with open(_REPORT_PATH, "w") as f:
        f.write("\n".join(lines))
    print(f"  Report → {_REPORT_PATH}")
    print(f"\n  {len(listable)} in CSV, {len(outliers)} held back, "
          f"{len(blanks)} need baseColour.")


if __name__ == "__main__":
    build()
