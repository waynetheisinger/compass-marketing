#!/usr/bin/env python3
"""Single-pass auto-matcher — writes the same files matcher.py would.

Loads the Matcher once over catalogue B (Shopify), iterates every unmatched
row in catalogue A, and for each row picks one of three buckets:

  * **decide** (auto-accept top candidate)
        top score ≥ ``--accept-score`` AND the gap to rank-2 ≥ ``--accept-gap``
        AND the top candidate's title shares a meaningful token with the source
        description (cheap sanity check — guards against confidently-wrong
        TF-IDF matches when the source row is dominated by stopwords).
  * **unmatch** (no candidate is plausible)
        top score < ``--reject-score`` AND no candidate in top-K is over the
        accept threshold.
  * **review** (ambiguous — written to the skipped JSONL)
        everything else. Drop into an interactive review step after.

Output files mirror ``matcher.py``'s shape:

  --out                       lookups/matches_stiga.csv
  <out_stem>_skipped.jsonl   ambiguous rows + their top candidates
  <out_stem>_unmatched.jsonl  rows with no plausible match
  --state-file                workdir/sku-matcher/state.json (matched_skus, current_index)

Re-running is safe: rows already present in --out or marked unmatched are
skipped. Pass ``--dry-run`` to log decisions without writing the files.

Run from the repo root:

    PYTHONPATH=. pyenv exec python scripts/sku_matcher/auto_match.py \\
        workdir/sku-matcher/stiga_prices_normalized.csv lookups/shopify_catalogue.csv \\
        --col-a-sku "Product Code" --col-a-title "Product Description" \\
        --col-b-sku sku --col-b-title title \\
        --out lookups/matches_stiga.csv --state-file workdir/sku-matcher/stiga_state.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

from tqdm import tqdm

from scripts.sku_matcher.io_utils import (
    append_jsonl,
    append_match,
    get_matched_skus,
    load_csv,
    load_state,
    save_state,
)
from scripts.sku_matcher.matching import Matcher
from scripts.sku_matcher.normalize import load_stopwords


_TOKEN_RE = re.compile(r"[a-z0-9]+")
_GENERIC_TOKENS = {
    # Words that are too common to count as a "shared meaningful token" — most
    # of the Shopify catalogue mentions these.
    "kit", "bare", "tool", "battery", "petrol", "lawnmower", "lawn", "mower",
    "ride", "rider", "tractor", "garden", "stiga", "mountfield", "atco",
    "honda", "self", "propelled", "the", "and", "for", "with", "set",
}


def _tokens(text: str) -> set:
    return {t for t in _TOKEN_RE.findall(text.lower())
            if t not in _GENERIC_TOKENS and len(t) >= 2}


def _shared_meaningful_token(title_a: str, title_b: str) -> bool:
    """At least one non-generic token in common (case-insensitive)."""
    return bool(_tokens(title_a) & _tokens(title_b))


def _matched_set(out_path: str, state_file: str) -> set:
    state = load_state(state_file)
    return ({s.upper() for s in state.get("matched_skus", [])}
            | {s.upper() for s in get_matched_skus(out_path)})


def _side_path(out: str, suffix: str) -> str:
    return out.replace(".csv", suffix)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("file_a")
    ap.add_argument("file_b")
    ap.add_argument("--col-a-sku", default="sku")
    ap.add_argument("--col-a-title", default="title")
    ap.add_argument("--col-b-sku", default="sku")
    ap.add_argument("--col-b-title", default="title")
    ap.add_argument("--out", default="lookups/matches.csv")
    ap.add_argument("--state-file", default="workdir/sku-matcher/state.json")
    ap.add_argument("--stopwords", default=None)
    ap.add_argument("--top", type=int, default=5,
                    help="Candidates kept per row in the JSONL output (default 5)")
    ap.add_argument("--k", type=int, default=50)
    ap.add_argument("--accept-score", type=float, default=90.0,
                    help="Min top-candidate score to auto-accept (default 90)")
    ap.add_argument("--accept-gap", type=float, default=5.0,
                    help="Min score gap between rank-1 and rank-2 to auto-accept (default 5)")
    ap.add_argument("--reject-score", type=float, default=45.0,
                    help="Below this for ALL candidates → unmatched (default 45)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Log decisions but don't write the output files")
    args = ap.parse_args()

    df_a = load_csv(args.file_a, args.col_a_sku, args.col_a_title)
    df_b = load_csv(args.file_b, args.col_b_sku, args.col_b_title)
    stopwords = load_stopwords(args.stopwords)

    print(f"Loaded A: {len(df_a)} rows  |  B: {len(df_b)} rows",
          file=sys.stderr)

    matcher = Matcher(
        df_b,
        stopwords=stopwords,
        k=args.k,
        use_claude=False,
        max_claude_calls=0,
        min_score_threshold=0.0,
    )

    matched_u = _matched_set(args.out, args.state_file)
    skipped_path = _side_path(args.out, "_skipped.jsonl")
    unmatched_path = _side_path(args.out, "_unmatched.jsonl")

    n_decided = 0
    n_unmatched = 0
    n_review = 0
    n_skip_already = 0

    for idx in tqdm(range(len(df_a)), desc="Auto-matching"):
        row = df_a.iloc[idx]
        sku_a = str(row["sku"])
        title_a = str(row["title"])
        if sku_a.upper() in matched_u:
            n_skip_already += 1
            continue

        results = matcher.match(sku_a, title_a)
        if not results:
            if not args.dry_run:
                append_jsonl(unmatched_path,
                             {"sku_a": sku_a, "title_a": title_a,
                              "reason": "no_candidates"})
            matched_u.add(sku_a.upper())
            n_unmatched += 1
            continue

        top = results[0]
        top_score = top["score"]
        second_score = results[1]["score"] if len(results) > 1 else 0.0
        gap = top_score - second_score
        shares_token = _shared_meaningful_token(title_a, top["title_b"])

        # 1) Confident auto-accept.
        if (top_score >= args.accept_score
                and gap >= args.accept_gap
                and shares_token):
            if not args.dry_run:
                append_match(args.out, sku_a, title_a,
                             top["sku_b"], top["title_b"],
                             top["score"], top["method"])
            matched_u.add(sku_a.upper())
            n_decided += 1
            continue

        # 2) Confident reject — nothing in top-K is plausible.
        max_score = max(r["score"] for r in results)
        if max_score < args.reject_score:
            if not args.dry_run:
                append_jsonl(unmatched_path,
                             {"sku_a": sku_a, "title_a": title_a,
                              "reason": "all_candidates_below_reject_score",
                              "top_score": round(max_score, 2)})
            matched_u.add(sku_a.upper())
            n_unmatched += 1
            continue

        # 3) Ambiguous — log to review (skipped JSONL) with top-N candidates
        #    inline so the reviewer can decide without re-running the matcher.
        candidates = [
            {
                "rank": i + 1,
                "sku_b": r["sku_b"],
                "title_b": r["title_b"],
                "score": round(r["score"], 2),
                "method": r["method"],
            }
            for i, r in enumerate(results[:args.top])
        ]
        if not args.dry_run:
            append_jsonl(skipped_path, {
                "sku_a": sku_a,
                "title_a": title_a,
                "top_score": round(top_score, 2),
                "gap": round(gap, 2),
                "shares_meaningful_token": shares_token,
                "candidates": candidates,
            })
        n_review += 1

    # Persist matched_skus into state so the interactive matcher resumes
    # cleanly past the auto-decided rows.
    if not args.dry_run:
        matched_orig = [s for s in df_a["sku"].astype(str)
                        if s.upper() in matched_u]
        save_state(args.state_file, len(df_a), matched_orig)

    print(f"\nAuto-match summary", file=sys.stderr)
    print(f"  decided (auto-accepted):  {n_decided}", file=sys.stderr)
    print(f"  unmatched (no plausible): {n_unmatched}", file=sys.stderr)
    print(f"  review (ambiguous):       {n_review}", file=sys.stderr)
    print(f"  already processed:        {n_skip_already}", file=sys.stderr)
    print(f"  → matches:   {args.out}", file=sys.stderr)
    print(f"  → unmatched: {unmatched_path}", file=sys.stderr)
    print(f"  → review:    {skipped_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
