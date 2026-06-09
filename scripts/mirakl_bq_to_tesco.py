#!/usr/bin/env python3
"""
B&Q (Kingfisher) active offers → Tesco product listings.

Takes the products behind B&Q's *active* offers and prepares them as Tesco
Mirakl product rows, reusing the proven Tesco operator config (field schema +
cross-retailer compliance scrub). Category, rich copy and images come from
Shopify — Tesco's category system IS the Shopify product taxonomy, so each
product's Shopify category gid is used directly as Tesco's shopifyHierarchyId
(no lossy B&Q→Tesco category guessing).

Pipeline:
  1. Pull Kingfisher /offers, keep active=True only.
  2. Drop brands not registered on Tesco (checked live against the brand list).
  3. Resolve each product in Shopify by EAN (barcode), then SKU fallback:
     title, descriptionHtml→marketingText, taxonomy category gid, images.
  4. Build a Tesco row via the TESCO operator config (compliance scrub applied).
  5. Validate each row against its category's LIVE required-attribute set;
     anything missing is recorded as a gap.
  6. Write a dry-run CSV + a gaps/scrub markdown report to workdir/mirakl-tesco/.

This NEVER submits. Review the CSV + gaps report, fill gaps, then submit via
the existing push path. Run from the repo root.
"""
from __future__ import annotations

import csv
import html
import os
import re
import sys
import time
from collections import Counter, defaultdict

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts.mirakl_client import MiraklClient          # noqa: E402
from scripts.shopify_client import ShopifyClient        # noqa: E402
from scripts.mirakl_operators import TESCO              # noqa: E402

_OUT_DIR = os.path.join(_REPO_ROOT, "workdir", "mirakl-tesco")

# Per-product value-list defaults for fields that are constant across this
# catalogue. baseColour is deliberately NOT defaulted — it varies per product
# and is surfaced as a gap where a category requires it.
_DEFAULTS = {
    "countryOfOriginName": "China",   # confirm per product on review
    "ageRestriction":      "No",
    "unitQuantity":        "Each",
    "vatRate":             "20",      # standard-rated; verified accepted 2026-06-09
}

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    if not s:
        return ""
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    return " ".join(s.split())


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


def _shopify_lookup(client, ean: str | None, *skus: str) -> dict | None:
    """Find a Shopify product by EAN (barcode) first, then by any SKU given."""
    queries = []
    if ean:
        queries.append(f"barcode:{ean}")
    for s in skus:
        if s:
            queries.append(f"sku:{s}")
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


import requests

# gid → set of required codes, or a sentinel string:
#   "UNSUPPORTED" — Tesco returns 404, category not enabled for selling
#   "UNKNOWN"     — persistent fetch failure (neither 404 nor recoverable 429)
_req_cache: dict[str, set[str] | str] = {}


def _required_attrs(tcli, gid: str):
    """Required-attribute codes for a category gid, or a sentinel.

    Tesco rate-limits /products/attributes hard (HTTP 429), so 429s are retried
    with exponential backoff. A 404 means the Shopify taxonomy node is not a
    sellable Tesco category ('UNSUPPORTED'). Never returns an empty set on
    failure — a transient error must not read as 'no requirements / ready'."""
    if gid in _req_cache:
        return _req_cache[gid]
    result = "UNKNOWN"
    for attempt in range(6):
        try:
            attrs = tcli.get("/products/attributes", params={"hierarchy": gid})["attributes"]
            result = {a["code"] for a in attrs if a.get("required")}
            break
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else None
            if code == 404:
                result = "UNSUPPORTED"
                break
            if code == 429:
                time.sleep(2 ** attempt)   # 1,2,4,8,16,32s
                continue
            time.sleep(2 ** attempt)
        except Exception:
            time.sleep(2 ** attempt)
    _req_cache[gid] = result
    return result


def build():
    os.makedirs(_OUT_DIR, exist_ok=True)
    k = MiraklClient("KINGFISHER")
    tcli = MiraklClient("TESCO")
    fs = TESCO.field_schema
    cp = TESCO.compliance

    offers = k.get("/offers", params={"max": 100}).get("offers", [])
    active = [o for o in offers if o.get("active") is True]
    registered = _tesco_registered_brands()

    print(f"Offers: {len(offers)} total, {len(active)} active")

    rows: list[dict] = []
    records: list[dict] = []          # parallel metadata for the report
    skipped: list[tuple] = []
    scrub_log: dict[str, dict] = {}

    with ShopifyClient() as sc:
        for o in active:
            sku = o.get("shop_sku")
            brand = (o.get("product_brand") or "").strip()
            ean = _ean_of(o)

            if brand.lower() not in registered:
                skipped.append((sku, brand, "brand-not-registered-on-tesco"))
                continue

            sh = _shopify_lookup(sc, ean, sku, o.get("product_sku"))
            title = (sh or {}).get("title") or o.get("product_title")
            body = (sh or {}).get("marketingText") or _strip_html(o.get("product_description"))
            cat_gid = (sh or {}).get("category_gid")
            images = (sh or {}).get("images") or []

            name_clean = cp.clean_name(title)
            body_clean, body_hits = cp.sanitise_prose(body)
            _, name_hits = cp.scrub(title or "")
            hits = {}
            if name_hits: hits["name"] = name_hits
            if body_hits: hits["marketingText"] = body_hits
            if hits:
                scrub_log[sku] = hits

            row = {
                fs.category:   cat_gid or "",
                fs.sku:        sku,
                fs.name:       name_clean,
                fs.ean:        ean or "",
                fs.body:       body_clean,
                "brand":       brand,
            }
            if images:
                row[fs.image_main] = images[0]
                for col, url in zip(fs.extra_images, images[1:]):
                    row[col] = url
            row.update(_DEFAULTS)

            # Gap analysis against the category's live required set.
            # Three states: unmapped (no category), schema-unknown (fetch
            # failed), or a concrete missing-attribute list.
            if not cat_gid:
                missing = ["(no Tesco category — unmapped)"]
            else:
                req = _required_attrs(tcli, cat_gid)
                if req == "UNSUPPORTED":
                    missing = ["(Tesco category not sellable — recategorise)"]
                elif req == "UNKNOWN":
                    missing = ["(category schema unavailable — re-check)"]
                else:
                    missing = sorted(c for c in req if not str(row.get(c, "")).strip())
            if not images:
                missing = missing + ["(no image)"]

            rows.append(row)
            records.append({
                "sku": sku, "brand": brand, "ean": ean,
                "bq_category": o.get("category_label"),
                "tesco_category": (sh or {}).get("category_name") or "(unmapped)",
                "cat_gid": cat_gid, "matched_by": (sh or {}).get("matched_by") or "NO-SHOPIFY-MATCH",
                "n_images": len(images), "missing": sorted(set(missing)),
            })

    _write_csv(rows)
    _write_report(records, skipped, scrub_log)
    return rows, records, skipped


def _write_csv(rows: list[dict]):
    cols = []
    for r in rows:
        for c in r:
            if c not in cols:
                cols.append(c)
    path = os.path.join(_OUT_DIR, "bq_active_to_tesco_products.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter=";", extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"  CSV → {path}  ({len(rows)} rows, {len(cols)} columns)")


def _write_report(records, skipped, scrub_log):
    path = os.path.join(_OUT_DIR, "bq_active_to_tesco_REPORT.md")
    ready = [r for r in records if not r["missing"]]
    gapped = [r for r in records if r["missing"]]
    lines = []
    lines.append("# B&Q active offers → Tesco — dry-run report\n")
    lines.append(f"- Active offers processed: **{len(records)}**")
    lines.append(f"- Ready to submit (no required-attr gaps): **{len(ready)}**")
    lines.append(f"- With gaps to fill: **{len(gapped)}**")
    lines.append(f"- Skipped: **{len(skipped)}**\n")

    if skipped:
        lines.append("## Skipped\n")
        for sku, brand, why in skipped:
            lines.append(f"- `{sku}` [{brand}] — {why}")
        lines.append("")

    nomatch = [r for r in records if r["matched_by"] == "NO-SHOPIFY-MATCH"]
    if nomatch:
        lines.append("## No Shopify match (no images / no category — used B&Q offer copy)\n")
        for r in nomatch:
            lines.append(f"- `{r['sku']}` [{r['brand']}] EAN={r['ean']} — B&Q cat: {r['bq_category']}")
        lines.append("")

    # Gap frequency
    gapfreq = Counter()
    for r in gapped:
        for g in r["missing"]:
            gapfreq[g] += 1
    if gapfreq:
        lines.append("## Required-attribute gaps (by frequency)\n")
        lines.append("| attribute | # products missing |")
        lines.append("|---|---|")
        for attr, n in gapfreq.most_common():
            lines.append(f"| `{attr}` | {n} |")
        lines.append("")

    # Category coverage
    lines.append("## Tesco category resolution\n")
    catc = Counter(r["tesco_category"] for r in records)
    lines.append("| Tesco category (from Shopify) | # |")
    lines.append("|---|---|")
    for cat, n in catc.most_common():
        lines.append(f"| {cat} | {n} |")
    lines.append("")

    lines.append("## Per-product detail\n")
    lines.append("| SKU | brand | matched | Tesco category | imgs | missing required |")
    lines.append("|---|---|---|---|---|---|")
    for r in sorted(records, key=lambda x: (bool(x["missing"]), x["sku"])):
        miss = ", ".join(f"`{m}`" for m in r["missing"]) or "✓ none"
        lines.append(f"| {r['sku']} | {r['brand']} | {r['matched_by']} | "
                     f"{r['tesco_category']} | {r['n_images']} | {miss} |")
    lines.append("")

    if scrub_log:
        lines.append("## Cross-retailer scrub hits (review nothing meaningful lost)\n")
        for sku, hits in scrub_log.items():
            lines.append(f"- `{sku}`: {hits}")
        lines.append("")

    with open(path, "w") as f:
        f.write("\n".join(lines))
    print(f"  Report → {path}")
    print(f"\n  {len(ready)} ready, {len(gapped)} with gaps, {len(skipped)} skipped.")


if __name__ == "__main__":
    build()
