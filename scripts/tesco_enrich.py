#!/usr/bin/env python3
"""
Single-product Tesco enrichment — the Tesco analogue of /enrich-bq.

Unlike the bulk /tesco-csv-batch (which reuses Shopify copy verbatim, name-
scrubbed), this is for ONE product done carefully: the prose is REWRITTEN into a
distinct voice so a Tesco listing does not duplicate mowdirect.co.uk or leak the
MowDirect identity. It emits both a push-ready 1-row CSV and a portal-fill
markdown doc (Tesco portal labels → values) for manual entry/review.

The deterministic plumbing (category from Shopify taxonomy, image re-host to
JPEG, optional-field mapping, name scrub) is reused from mirakl_bq_to_tesco; the
voice rewrite is the skill's (Claude's) job per .claude/skills/enrich-bq/
BRAND_VOICE.md (the shared voice bible).

Subcommands:
  gather --sku <SKU>            build the base row + emit a context JSON with the
                                raw Shopify source copy for Claude to rewrite
  apply  --sku <SKU> --row-json overlay Claude's rewritten copy + chosen attrs,
                                scrub, write the CSV + portal-fill doc

Run from the repo root. Outputs → workdir/mirakl-tesco/enrich/.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts.mirakl_client import MiraklClient            # noqa: E402
from scripts.shopify_client import ShopifyClient          # noqa: E402
import scripts.mirakl_bq_to_tesco as gen                  # noqa: E402

_ENRICH_DIR = os.path.join(_REPO_ROOT, "workdir", "mirakl-tesco", "enrich")
# Prose fields the rewrite owns (everything else is factual / auto-filled).
_PROSE_FIELDS = ["description", "marketingText",
                 "productFeatures1", "productFeatures2", "productFeatures3",
                 "whatIsInBox"]


def _base_row(sku: str) -> dict:
    """Generate the base Tesco row for one Shopify SKU (reuses the full pipeline)
    and return it as a dict (the row written to the shared working CSV)."""
    gen.build(shopify_skus=[sku])
    with open(gen._CSV_PATH, newline="") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    match = [r for r in rows if r.get("sku") == sku]
    return match[0] if match else (rows[0] if rows else {})


def _raw_source(sku: str) -> dict:
    """Pull the raw Shopify copy (pre-scrub) so Claude rewrites from the original,
    not from the already-reused text."""
    with ShopifyClient() as sc:
        edges = sc.execute(gen._SHOPIFY_Q, {"q": f"sku:{sku}"})["products"]["edges"]
        if not edges:
            edges = sc.execute(gen._SHOPIFY_Q, {"q": f"barcode:{sku}"})["products"]["edges"]
    if not edges:
        return {}
    n = edges[0]["node"]
    src = gen.shopify_sources(n)
    fb = src["metafields"].get("custom.feature_bullets", {}).get("value", "")
    bullets = [gen._strip_html(m) for m in gen._LI_RE.findall(fb or "")]
    return {
        "title": n.get("title"),
        "body_text": gen._strip_html(n.get("descriptionHtml")),
        "bullets": bullets,
    }


def cmd_gather(args) -> None:
    os.makedirs(_ENRICH_DIR, exist_ok=True)
    sku = args.sku
    base = _base_row(sku)
    if not base:
        print(json.dumps({"error": f"{sku}: no Tesco-listable base row (held back or "
                          "not in Shopify) — see the batch report"}, indent=2))
        return
    ctx = {
        "sku": sku,
        "category": base.get("shopifyHierarchyId"),
        "brand": base.get("brand"),
        "base_row": base,                       # already category/image/attr-resolved
        "source": _raw_source(sku),             # raw Shopify copy to rewrite FROM
        "rewrite_fields": _PROSE_FIELDS,
        "voice_doc": ".claude/skills/enrich-bq/BRAND_VOICE.md",
        "instructions": (
            "Rewrite the prose fields into a distinct voice per BRAND_VOICE.md: "
            "different lead/structure/verbs from mowdirect.co.uk, same product "
            "facts. NEVER name MowDirect, Compass, Tesco, any retailer, price, or "
            "delivery. Then call: tesco_enrich.py apply --sku %s --row-json <file>"
            % sku),
    }
    path = os.path.join(_ENRICH_DIR, f"{sku}_context.json")
    with open(path, "w") as f:
        json.dump(ctx, f, indent=2, ensure_ascii=False)
    print(json.dumps({"context": path, "category": ctx["category"],
                      "rewrite_fields": _PROSE_FIELDS}, indent=2))


def _portal_labels(gid: str) -> dict:
    """code → portal label for a category (for the copy-paste doc)."""
    try:
        attrs = MiraklClient("TESCO").get("/products/attributes",
                                          params={"hierarchy": gid})["attributes"]
        return {a["code"]: a.get("label", a["code"]) for a in attrs}
    except Exception:
        return {}


def cmd_apply(args) -> None:
    os.makedirs(_ENRICH_DIR, exist_ok=True)
    sku = args.sku
    with open(args.row_json) as f:
        rewrite = json.load(f)

    base = _base_row(sku)
    if not base:
        print(f"ERROR: no base row for {sku}", file=sys.stderr)
        raise SystemExit(2)

    cp = gen.TESCO.compliance
    row = dict(base)
    scrub_hits = {}
    # Overlay rewritten prose (scrub as a backstop — the rewrite shouldn't name
    # retailers, but never trust prose into a marketplace unscrubbed).
    for fld in _PROSE_FIELDS:
        if fld in rewrite and rewrite[fld] not in (None, ""):
            clean, hits = cp.sanitise_prose(str(rewrite[fld]))
            row[fld] = clean
            if hits:
                scrub_hits[fld] = hits
    # Optional extra attribute values supplied by Claude.
    for k, v in (rewrite.get("attrs") or {}).items():
        if v not in (None, ""):
            row[k] = str(v)

    # Write the 1-row CSV (push-ready).
    csv_path = os.path.join(_ENRICH_DIR, f"{sku}.csv")
    cols = list(gen._COL_ORDER) + [c for c in row if c not in gen._COL_ORDER]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter=";", extrasaction="ignore")
        w.writeheader(); w.writerow(row)

    # Write the portal-fill markdown doc (Tesco labels → values).
    labels = _portal_labels(row.get("shopifyHierarchyId", ""))
    doc = [f"# Tesco portal fill — {sku}\n",
           f"Category: `{row.get('shopifyHierarchyId')}`\n",
           "Copy each value into the matching field in the Tesco seller portal.\n"]
    for code, val in row.items():
        if val in (None, ""):
            continue
        label = labels.get(code, code)
        doc.append(f"**{label}**  (`{code}`)\n\n{val}\n")
    doc_path = os.path.join(_ENRICH_DIR, f"{sku}_portal.md")
    with open(doc_path, "w") as f:
        f.write("\n".join(doc))

    print(json.dumps({"csv": csv_path, "portal_doc": doc_path,
                      "scrub_hits": scrub_hits,
                      "note": "Review the portal doc; push the CSV with "
                              "mirakl_push_products.py --operator TESCO --file " + csv_path},
                     indent=2))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Single-product Tesco enrichment")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("gather"); p.add_argument("--sku", required=True)
    p.set_defaults(fn=cmd_gather)
    p = sub.add_parser("apply"); p.add_argument("--sku", required=True)
    p.add_argument("--row-json", required=True); p.set_defaults(fn=cmd_apply)
    args = ap.parse_args(argv)
    args.fn(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
