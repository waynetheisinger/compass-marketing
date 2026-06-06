#!/usr/bin/env python3
"""Dry-run reconciliation: NumaSuite canonical SKUs → Shopify variant SKUs.

EAN/barcode-first (drift-proof) with title-similarity fallback. No Shopify
mutations. Produces:

  reports/numasuite_dry_run/proposed_rewrites.csv
  reports/numasuite_dry_run/review_queue.csv
  reports/numasuite_dry_run/summary.txt

Invocation:

    PYTHONPATH=. .venv/bin/python -m scripts.sku_matcher.numasuite_dry_run \
        --numasuite workdir/sku-matcher/numasuite_export.csv \
        --shopify  lookups/shopify_catalogue.csv

The NumaSuite CSV is expected to have SKU, EAN/barcode, and title columns.
Column names are auto-detected (case/whitespace-insensitive) but can be
overridden with --ns-sku / --ns-ean / --ns-title.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

from scripts.sku_matcher.matching import Matcher


REVIEW_SCORE_THRESHOLD = 85.0


def detect_column(df: pd.DataFrame, candidates: list[str], explicit: str | None, label: str) -> str:
    if explicit:
        if explicit not in df.columns:
            raise SystemExit(f"--{label} {explicit!r} not found in NumaSuite CSV columns: {list(df.columns)}")
        return explicit
    lookup = {c.strip().lower(): c for c in df.columns}
    for cand in candidates:
        if cand in lookup:
            return lookup[cand]
    raise SystemExit(
        f"Could not auto-detect {label} column. Tried {candidates}. "
        f"Got columns: {list(df.columns)}. Pass --{label} explicitly."
    )


def normalize_barcode(value) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none", "null"}:
        return ""
    if s.endswith(".0"):
        s = s[:-2]
    return s


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--numasuite", required=True, help="NumaSuite CSV export (SKU + EAN + title).")
    p.add_argument("--shopify", default="lookups/shopify_catalogue.csv",
                   help="Shopify catalogue CSV (default: lookups/shopify_catalogue.csv).")
    p.add_argument("--out-dir", default="reports/numasuite_dry_run", help="Output directory for the three report files.")
    p.add_argument("--ns-sku", help="Override SKU column name in NumaSuite CSV.")
    p.add_argument("--ns-ean", help="Override EAN/barcode column name in NumaSuite CSV.")
    p.add_argument("--ns-title", help="Override title column name in NumaSuite CSV.")
    p.add_argument("--review-threshold", type=float, default=REVIEW_SCORE_THRESHOLD,
                   help=f"Title-match score below which a row goes to review queue (default: {REVIEW_SCORE_THRESHOLD}).")
    p.add_argument("--k", type=int, default=50, help="Candidates per title-match query (default 50).")
    return p.parse_args()


def main():
    args = parse_args()

    ns_path = Path(args.numasuite)
    if not ns_path.exists():
        raise SystemExit(f"NumaSuite CSV not found: {ns_path}")
    sp_path = Path(args.shopify)
    if not sp_path.exists():
        raise SystemExit(f"Shopify catalogue CSV not found: {sp_path}")

    df_ns = pd.read_csv(ns_path, dtype=str).fillna("")
    df_sp = pd.read_csv(sp_path, dtype=str).fillna("")

    ns_sku_col = detect_column(df_ns, ["sku", "product sku", "stock code", "code"], args.ns_sku, "ns-sku")
    ns_ean_col = detect_column(df_ns, ["ean", "barcode", "gtin", "upc"], args.ns_ean, "ns-ean")
    ns_title_col = detect_column(df_ns, ["title", "name", "product name", "description"], args.ns_title, "ns-title")

    if not {"sku", "title", "barcode"}.issubset(df_sp.columns):
        raise SystemExit(
            f"Shopify catalogue missing required columns. Need sku,title,barcode. "
            f"Got: {list(df_sp.columns)}. Re-run export_shopify_catalogue.py."
        )

    df_ns = df_ns.rename(columns={ns_sku_col: "sku", ns_ean_col: "ean", ns_title_col: "title"})
    df_ns["sku"] = df_ns["sku"].astype(str).str.strip()
    df_ns["ean"] = df_ns["ean"].map(normalize_barcode)
    df_ns["title"] = df_ns["title"].astype(str).str.strip()

    df_sp["sku"] = df_sp["sku"].astype(str).str.strip()
    df_sp["barcode"] = df_sp["barcode"].map(normalize_barcode)
    df_sp["title"] = df_sp["title"].astype(str).str.strip()

    # ----- Stage 0: verbatim SKU match (no-op rows) -----
    sp_skus_upper = {row["sku"].upper(): row for _, row in df_sp.iterrows() if row["sku"]}

    # ----- Stage 1: EAN-first join -----
    sp_by_barcode: dict[str, list[dict]] = defaultdict(list)
    for _, row in df_sp.iterrows():
        bc = row["barcode"]
        if bc:
            sp_by_barcode[bc].append({"sku": row["sku"], "title": row["title"]})

    matched_rows = []
    fallback_rows = []
    multi_match_rows = []
    verbatim_rows = []

    for _, ns in df_ns.iterrows():
        ns_sku, ns_ean, ns_title = ns["sku"], ns["ean"], ns["title"]
        if not ns_sku:
            continue
        # Stage 0: SKU verbatim
        sp_hit = sp_skus_upper.get(ns_sku.upper())
        if sp_hit is not None:
            verbatim_rows.append({
                "numasuite_sku": ns_sku,
                "current_shopify_sku": sp_hit["sku"],
                "proposed_sku": ns_sku,
                "match_method": "sku-verbatim",
                "confidence": 100.0,
                "numasuite_title": ns_title,
                "shopify_title": sp_hit["title"],
                "numasuite_ean": ns_ean,
                "shopify_barcode": sp_hit["barcode"],
                "needs_review": "no-op",
                "review_reason": "already aligned",
            })
            continue
        if ns_ean and ns_ean in sp_by_barcode:
            hits = sp_by_barcode[ns_ean]
            if len(hits) == 1:
                hit = hits[0]
                matched_rows.append({
                    "numasuite_sku": ns_sku,
                    "current_shopify_sku": hit["sku"],
                    "proposed_sku": ns_sku,
                    "match_method": "ean",
                    "confidence": 100.0,
                    "numasuite_title": ns_title,
                    "shopify_title": hit["title"],
                    "numasuite_ean": ns_ean,
                    "shopify_barcode": ns_ean,
                    "needs_review": "no" if hit["sku"] != ns_sku else "no-op",
                    "review_reason": "" if hit["sku"] != ns_sku else "already aligned",
                })
            else:
                multi_match_rows.append({
                    "numasuite_sku": ns_sku,
                    "current_shopify_sku": "|".join(h["sku"] for h in hits),
                    "proposed_sku": ns_sku,
                    "match_method": "ean-conflict",
                    "confidence": 0.0,
                    "numasuite_title": ns_title,
                    "shopify_title": "|".join(h["title"] for h in hits),
                    "numasuite_ean": ns_ean,
                    "shopify_barcode": ns_ean,
                    "needs_review": "yes",
                    "review_reason": f"multiple Shopify variants share barcode {ns_ean}",
                })
        else:
            fallback_rows.append(ns)

    # ----- Stage 2: Title-similarity fallback for the unmatched rest -----
    title_match_rows = []
    if fallback_rows:
        df_b = df_sp[["sku", "title"]].copy()
        matcher = Matcher(df_b=df_b, k=args.k, use_claude=False)

        for ns in fallback_rows:
            ns_sku, ns_ean, ns_title = ns["sku"], ns["ean"], ns["title"]
            results = matcher.match(ns_sku, ns_title)
            if not results:
                title_match_rows.append({
                    "numasuite_sku": ns_sku,
                    "current_shopify_sku": "",
                    "proposed_sku": ns_sku,
                    "match_method": "no-candidates",
                    "confidence": 0.0,
                    "numasuite_title": ns_title,
                    "shopify_title": "",
                    "numasuite_ean": ns_ean,
                    "shopify_barcode": "",
                    "needs_review": "yes",
                    "review_reason": "no title candidates",
                })
                continue
            top = results[0]
            score = float(top["score"])
            sp_bc = ""
            sp_hits = df_sp[df_sp["sku"] == top["sku_b"]]
            if not sp_hits.empty:
                sp_bc = sp_hits.iloc[0]["barcode"]
            review = score < args.review_threshold or not ns_ean
            reasons = []
            if not ns_ean:
                reasons.append("no NumaSuite EAN")
            if score < args.review_threshold:
                reasons.append(f"title score {score:.1f} < {args.review_threshold}")
            title_match_rows.append({
                "numasuite_sku": ns_sku,
                "current_shopify_sku": top["sku_b"],
                "proposed_sku": ns_sku,
                "match_method": top["method"],
                "confidence": round(score, 2),
                "numasuite_title": ns_title,
                "shopify_title": top["title_b"],
                "numasuite_ean": ns_ean,
                "shopify_barcode": sp_bc,
                "needs_review": "yes" if review else "no",
                "review_reason": "; ".join(reasons),
            })

    all_rows = verbatim_rows + matched_rows + multi_match_rows + title_match_rows

    # ----- Outputs -----
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cols = ["numasuite_sku", "current_shopify_sku", "proposed_sku", "match_method",
            "confidence", "numasuite_title", "shopify_title", "numasuite_ean",
            "shopify_barcode", "needs_review", "review_reason"]

    proposed_path = out_dir / "proposed_rewrites.csv"
    review_path = out_dir / "review_queue.csv"
    summary_path = out_dir / "summary.txt"

    with proposed_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(all_rows)

    review_rows = [r for r in all_rows if r["needs_review"] == "yes"]
    with review_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(review_rows)

    method_counts = Counter(r["match_method"] for r in all_rows)
    aligned_with_rewrite = sum(
        1 for r in all_rows
        if r["match_method"] == "ean" and r["needs_review"] == "no"
        and r["current_shopify_sku"] != r["proposed_sku"]
    )

    lines = [
        "NumaSuite → Shopify dry-run reconciliation",
        "=" * 50,
        f"NumaSuite rows:                  {len(df_ns)}",
        f"  NumaSuite rows with EAN:       {(df_ns['ean']!='').sum()}",
        f"Shopify variants:                {len(df_sp)}",
        f"  with barcode:                  {(df_sp['barcode']!='').sum()}",
        "",
        f"SKU verbatim (no-op):            {len(verbatim_rows)}",
        f"EAN-matched (1:1, rewrite):      {len(matched_rows)}",
        f"  including no-op:               {sum(1 for r in matched_rows if r['needs_review']=='no-op')}",
        f"EAN conflicts (review):          {len(multi_match_rows)}",
        f"Title-fallback rows:             {len(title_match_rows)}",
        "",
        "Match method breakdown:",
    ]
    for m, n in sorted(method_counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {m}: {n}")
    lines += [
        "",
        f"Review queue size:               {len(review_rows)}",
        "",
        f"Proposed rewrites: {proposed_path}",
        f"Review queue:      {review_path}",
        "",
        "No Shopify mutations were performed.",
    ]
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
