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

import argparse
import csv
import html
import io
import json
import os
import re
import sys
import time
from collections import Counter

import requests

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts.mirakl_client import MiraklClient          # noqa: E402
from scripts.shopify_client import ShopifyClient        # noqa: E402
from scripts.mirakl_operators import TESCO              # noqa: E402
from scripts.blog_publish import (                      # noqa: E402
    STAGED_UPLOADS, FILE_CREATE, FILE_POLL,
)

_OUT_DIR = os.path.join(_REPO_ROOT, "workdir", "mirakl-tesco")
_CSV_PATH = os.path.join(_OUT_DIR, "bq_active_to_tesco_products.csv")
_REPORT_PATH = os.path.join(_OUT_DIR, "bq_active_to_tesco_REPORT.md")
# Persistent map: source (non-jpg) Shopify image URL → re-hosted .jpg CDN URL.
# Makes re-runs cheap and avoids duplicate uploads to Shopify Files.
_JPG_CACHE_PATH = os.path.join(_OUT_DIR, "image_jpg_cache.json")

# Per-product overrides + constants live in config/tesco_overrides.json,
# maintained by the /tesco-shopify-shape skill. Loaded here so the generator
# and the skill share one source of truth (no hardcoded SKU lists in code).
_OVERRIDES_PATH = os.path.join(_REPO_ROOT, "config", "tesco_overrides.json")


def _load_overrides() -> dict:
    with open(_OVERRIDES_PATH) as fh:
        return json.load(fh)


_OV = _load_overrides()
_DEFAULTS = _OV.get("defaults", {})              # constant value-list fields
_BRAND_COLOUR = _OV.get("brand_colour", {})      # baseColour by brand (lowercased)
_SKU_COLOUR = _OV.get("sku_colour", {})          # baseColour by SKU (wins over brand)
_EXCLUDE_SKUS = _OV.get("exclude_skus", {})      # SKU → reason held back
_SKU_CATEGORY = _OV.get("sku_category", {})      # SKU → leaf-category gid override
# Shopify taxonomy nodes Tesco doesn't open to sellers (404 on /products/attributes).
_KNOWN_UNSELLABLE_GIDS = set(_OV.get("unsellable_gids", []))
# Extra attribute values: {attrCode: {SKU_or_'*': value}} (SKU wins over '*').
_ATTR_EXTRAS = {k: v for k, v in _OV.get("attribute_extras", {}).items()
                if not k.startswith("_") and isinstance(v, dict)}


def _extras_for(sku: str) -> dict:
    """Resolve attribute_extras for a SKU: per-SKU value wins over '*' default."""
    out = {}
    for attr, by_sku in _ATTR_EXTRAS.items():
        if sku in by_sku:
            out[attr] = by_sku[sku]
        elif "*" in by_sku:
            out[attr] = by_sku["*"]
    return out


# --- Shopify-source → Tesco-attribute mapping engine (rich optional fields) ---
_ATTR_MAPPINGS = {k: v for k, v in _OV.get("attribute_mappings", {}).items()
                  if not k.startswith("_") and isinstance(v, dict)}
_LI_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.IGNORECASE | re.DOTALL)


def _as_json(v):
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, json.JSONDecodeError):
        return None


def _num(v):
    """Format a number without a trailing .0 (Tesco spec fields are strings)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return str(int(f)) if f == int(f) else f"{f:g}"


def _src_value(source: str, sources: dict):
    kind, _, arg = (source or "").partition(":")
    if kind == "const":
        return arg
    if kind == "field":
        return sources.get(arg)
    if kind == "weight":
        return sources.get("weight")
    if kind == "metafield":
        m = sources.get("metafields", {}).get(arg)
        return m["value"] if m else None
    if kind == "display_attribute":
        return sources.get("display_attributes", {}).get(arg)
    return None


def _apply_transform(val, t: str | None):
    if val is None:
        return None
    if not t:
        return val if isinstance(val, str) else str(val)
    if t in ("dim_value", "weight_value"):
        d = val if isinstance(val, dict) else _as_json(val)
        return _num(d.get("value")) if isinstance(d, dict) else _num(val)
    if t in ("dim_unit", "weight_unit"):
        d = val if isinstance(val, dict) else _as_json(val)
        return (d.get("unit") if isinstance(d, dict) else None)  # raw Shopify unit; value-list-backed on Tesco — verify
    if t == "list_first":
        lst = _as_json(val)
        return str(lst[0]) if isinstance(lst, list) and lst else None
    if t == "list_join":
        lst = _as_json(val)
        return ", ".join(map(str, lst)) if isinstance(lst, list) else None
    if t == "strip_html":
        return _strip_html(str(val))
    if t.startswith("bullet:"):
        items = [_strip_html(m) for m in _LI_RE.findall(str(val))]
        i = int(t.split(":", 1)[1])
        return items[i - 1] if 0 < i <= len(items) else None
    return str(val)


def _apply_mappings(cat_gid: str, sources: dict) -> dict:
    """Resolve attribute_mappings for a product. '*' scope first, then the
    product's category (category-scoped wins). Empty/None results are skipped."""
    out = {}
    for scope in ("*", cat_gid):
        for attr, spec in _ATTR_MAPPINGS.get(scope, {}).items():
            if attr.startswith("_") or not isinstance(spec, dict):
                continue
            val = _apply_transform(_src_value(spec.get("source"), sources),
                                   spec.get("transform"))
            if val not in (None, "", []):
                out[attr] = val
    return out

# CSV column order — editable facets (baseColour) kept near the front.
_COL_ORDER = [
    "shopifyHierarchyId", "sku", "barcode", "brand", "baseColour",
    "description", "marketingText",
    "countryOfOriginName", "ageRestriction", "unitQuantity", "vatRate",
    "image1",
]

_TAG_RE = re.compile(r"<[^>]+>")

# Tesco accepts JPEG images only (confirmed in the seller portal 2026-06-09:
# non-JPEG images were rejected with "Supported image types are: JPEG"; and a
# .png URL serving jpeg bytes via &format=pjpg was still rejected — Tesco keys
# off the URL extension AND/OR bytes). So images that aren't already .jpg are
# converted to real JPEG locally (Pillow) and re-uploaded to Shopify Files,
# yielding a genuine .jpg URL that serves jpeg to Tesco's */* fetcher. The
# storefront's own product images are untouched (these are separate files).
_JPG_EXT_RE = re.compile(r"\.jpe?g$", re.IGNORECASE)


def _is_jpg_url(url: str) -> bool:
    """True if the URL's file extension is .jpg/.jpeg (string match only)."""
    if not url:
        return False
    path = url.split("?", 1)[0]            # drop query string (e.g. ?v=…)
    return bool(_JPG_EXT_RE.search(path))


def _load_jpg_cache() -> dict:
    if os.path.exists(_JPG_CACHE_PATH):
        try:
            with open(_JPG_CACHE_PATH) as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def _save_jpg_cache(cache: dict) -> None:
    with open(_JPG_CACHE_PATH, "w") as fh:
        json.dump(cache, fh, indent=2, sort_keys=True)


def _to_jpeg_bytes(raw: bytes) -> bytes:
    """Convert image bytes to JPEG (q88, progressive), flattening any alpha
    onto white (JPEG has no transparency)."""
    from PIL import Image
    im = Image.open(io.BytesIO(raw))
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    else:
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88, progressive=True, optimize=True)
    return buf.getvalue()


def _upload_jpg(client, filename: str, data: bytes) -> str:
    """Staged-upload JPEG bytes to Shopify Files; return the .jpg CDN URL."""
    staged = client.execute(STAGED_UPLOADS, {"input": [{
        "filename": filename, "mimeType": "image/jpeg",
        "resource": "FILE", "httpMethod": "POST",
    }]})
    target = staged["stagedUploadsCreate"]["stagedTargets"][0]
    form = {p["name"]: p["value"] for p in target["parameters"]}
    up = requests.post(target["url"], data=form,
                       files={"file": (filename, data, "image/jpeg")}, timeout=120)
    up.raise_for_status()
    created = client.execute(FILE_CREATE, {"files": [{
        "originalSource": target["resourceUrl"], "contentType": "IMAGE", "alt": "",
    }]})
    errs = created["fileCreate"]["userErrors"]
    if errs:
        raise RuntimeError(f"fileCreate: {errs}")
    node = created["fileCreate"]["files"][0]
    fid = node["id"]
    url = (node.get("image") or {}).get("url")
    for _ in range(30):
        if url:
            return url
        time.sleep(2)
        n = client.execute(FILE_POLL, {"id": fid})["node"] or {}
        if n.get("fileStatus") == "READY":
            url = (n.get("image") or {}).get("url")
        elif n.get("fileStatus") == "FAILED":
            raise RuntimeError("Shopify fileStatus FAILED")
    raise RuntimeError("timed out waiting for Shopify to process the jpg")


def _ensure_jpg(client, url: str, cache: dict) -> str | None:
    """Return a .jpg URL for the image: pass through native .jpg, else fetch,
    convert to JPEG, upload to Shopify, and cache the new URL. None on failure."""
    if _is_jpg_url(url):
        return url
    key = url.split("?", 1)[0]
    if key in cache:
        return cache[key]
    try:
        raw = requests.get(url, headers={"Accept": "*/*"}, timeout=60)
        raw.raise_for_status()
        data = _to_jpeg_bytes(raw.content)
        base = re.sub(r"[^A-Za-z0-9_-]", "-", key.split("/")[-1].rsplit(".", 1)[0])
        new_url = _upload_jpg(client, f"tesco-{base}.jpg", data)
    except Exception as e:  # noqa: BLE001
        print(f"    [rehost failed] {key.split('/')[-1]}: {e}", file=sys.stderr)
        return None
    cache[key] = new_url
    _save_jpg_cache(cache)
    return new_url


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
      vendor
      descriptionHtml
      category { id fullName }
      featuredImage { url }
      media(first:10){ edges{ node{ ... on MediaImage { image{ url } } } } }
      metafields(first:100){ edges{ node{ namespace key type value } } }
      variants(first:1){ edges{ node{
        inventoryItem{ measurement{ weight{ value unit } } }
      } } }
    } }
  }
}
"""


def shopify_sources(node: dict) -> dict:
    """Normalise a Shopify product node into a flat map of mappable sources the
    attribute-mapping layer can read: metafields keyed 'ns.key', the parsed
    custom.display_attributes (keyed by their code), variant weight, and the
    title/vendor fields."""
    mfs = {f"{e['node']['namespace']}.{e['node']['key']}":
           {"type": e["node"]["type"], "value": e["node"]["value"]}
           for e in node.get("metafields", {}).get("edges", [])}
    disp = {}
    da = mfs.get("custom.display_attributes")
    if da and da.get("value"):
        try:
            for a in json.loads(da["value"]):
                if a.get("code"):
                    disp[a["code"]] = a.get("value")
        except (json.JSONDecodeError, TypeError):
            pass
    weight = None
    vedges = node.get("variants", {}).get("edges", [])
    if vedges:
        weight = (((vedges[0]["node"].get("inventoryItem") or {})
                   .get("measurement") or {}).get("weight"))
    return {
        "metafields": mfs,
        "display_attributes": disp,
        "weight": weight,                  # {"value":..,"unit":..} or None
        "title": node.get("title"),
        "vendor": node.get("vendor"),
    }


def _shopify_lookup(client, ean, *skus):
    queries = ([f"barcode:{ean}"] if ean else []) + [f"sku:{s}" for s in skus if s]
    for q in queries:
        edges = client.execute(_SHOPIFY_Q, {"q": q})["products"]["edges"]
        if edges:
            n = edges[0]["node"]
            # Collect all images in order; non-jpg ones are converted +
            # re-hosted as .jpg later (build → _ensure_jpg).
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
                "sources": shopify_sources(n),
            }
    return None


def build(only_skus: set[str] | None = None, only_eans: set[str] | None = None):
    os.makedirs(_OUT_DIR, exist_ok=True)
    k = MiraklClient("KINGFISHER")
    fs = TESCO.field_schema
    cp = TESCO.compliance
    jpg_cache = _load_jpg_cache()

    offers = k.get("/offers", params={"max": 100}).get("offers", [])
    active = [o for o in offers if o.get("active") is True]
    if only_skus:
        active = [o for o in active if o.get("shop_sku") in only_skus]
    if only_eans:
        active = [o for o in active if _ean_of(o) in only_eans]
    registered = _tesco_registered_brands()
    print(f"Offers: {len(offers)} total, {len(active)} active"
          + (f" (filtered to {len(active)} by EAN)" if only_eans else "")
          + (f" (filtered to {sorted(only_skus)})" if only_skus else ""))

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

            cat_gid = _SKU_CATEGORY.get(sku) or sh.get("category_gid")
            cat_name = sh.get("category_name") or "(none)"
            if sku in _SKU_CATEGORY:
                cat_name = f"{cat_name} → leaf override {cat_gid.split('/')[-1]}"
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

            # Ensure every image is a real .jpg (Tesco accepts JPEG only):
            # native .jpg pass through; others are converted + re-hosted. Cap
            # at Tesco's 10 slots before converting so we don't upload extras.
            print(f"  {sku}: resolving {min(len(sh['images']), 10)} image(s) to jpg…")
            images = []
            for u in sh["images"][:10]:
                ju = _ensure_jpg(sc, u, jpg_cache)
                if ju:
                    images.append(ju)
            if not images:
                outliers.append((sku, brand, cat_name, "no usable image after jpg conversion"))
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
            row.update(_apply_mappings(cat_gid, sh.get("sources") or {}))  # rich optional fields
            row.update(_DEFAULTS)
            row.update(_extras_for(sku))   # category-specific attrs / per-SKU overrides win
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
    ap = argparse.ArgumentParser(description="B&Q active offers → Tesco products CSV")
    ap.add_argument("--skus", help="Comma-separated shop_skus to limit to")
    ap.add_argument("--eans", help="Comma-separated EANs/barcodes to limit to")
    args = ap.parse_args()
    only = {s.strip() for s in args.skus.split(",")} if args.skus else None
    eans = {e.strip() for e in args.eans.split(",")} if args.eans else None
    build(only_skus=only, only_eans=eans)
