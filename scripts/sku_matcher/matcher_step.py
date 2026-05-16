#!/usr/bin/env python3
"""Headless single-step driver for matcher.py — designed for Claude-as-TUI.

Each invocation does one action and prints a single JSON object to stdout.
Progress/info messages go to stderr. Errors print `{"error": "..."}` to
stdout and exit non-zero.

State (`state.json`) and output (`matches.csv` / `*_skipped.jsonl` /
`*_unmatched.jsonl`) are read/written in the same shape the interactive
`matcher.py` uses, so you can swap between the two drivers freely.

Subcommands
-----------

  status                              progress + counts
  next [--top N] [--claude]           top-N candidates for the next unmatched row
  peek --sku SKU [--top N] [--claude] same shape, but for a specific row (no advance)
  decide --sku SKU --pick <N|SKU>     record a chosen match (rank 1-indexed, or sku_b)
  skip   --sku SKU                    append to *_skipped.jsonl (revisit later)
  unmatch --sku SKU                   append to *_unmatched.jsonl + mark processed

Common args (always required, mirroring matcher.py):

  file_a, file_b, --col-a-sku, --col-a-title, --col-b-sku, --col-b-title,
  --out, --state-file, --stopwords

Run from the repo root with PYTHONPATH=. pyenv exec python — same as the
rest of the sku_matcher folder.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

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


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------

def _emit(obj: dict) -> None:
    """One JSON object to stdout, then bail. No trailing whitespace."""
    json.dump(obj, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    sys.stdout.flush()


def _die(msg: str, **detail) -> None:
    payload = {"error": msg}
    payload.update(detail)
    _emit(payload)
    sys.exit(1)


def _matched_set(args) -> set:
    """Union of state.json's matched_skus and what's already in matches.csv.

    Returned values are upper-cased for case-insensitive lookup.
    """
    state = load_state(args.state_file)
    from_state = {s.upper() for s in state.get("matched_skus", [])}
    from_csv = {s.upper() for s in get_matched_skus(args.out)}
    return from_state | from_csv


def _save_matched_state(args, matched_upper: set) -> None:
    """Persist matched_skus in state.json. Index is set to total so the
    interactive matcher resumes past the matched rows."""
    state = load_state(args.state_file)
    df_a = load_csv(args.file_a, args.col_a_sku, args.col_a_title)
    # Reuse original case from df_a where possible — state.json originally
    # held original case.
    matched_orig = [s for s in df_a["sku"].astype(str)
                    if s.upper() in matched_upper]
    save_state(args.state_file, state.get("current_index", 0), matched_orig)


def _load_a_and_matcher(args, use_claude_override: Optional[bool] = None) -> tuple:
    """Load df_a + build a Matcher over catalogue B. Cheap enough to call per
    invocation (TF-IDF over a few thousand rows is sub-second)."""
    df_a = load_csv(args.file_a, args.col_a_sku, args.col_a_title)
    df_b = load_csv(args.file_b, args.col_b_sku, args.col_b_title)
    stopwords = load_stopwords(args.stopwords)
    use_claude = bool(getattr(args, "claude", False))
    if use_claude_override is not None:
        use_claude = use_claude_override
    matcher = Matcher(
        df_b,
        stopwords=stopwords,
        k=getattr(args, "k", 50),
        use_claude=use_claude,
        max_claude_calls=getattr(args, "max_claude", 10),
        min_score_threshold=getattr(args, "min_score", 0.0),
    )
    return df_a, matcher


def _row_for_sku(df_a: pd.DataFrame, sku_a: str) -> Optional[dict]:
    """Find the row in df_a whose 'sku' matches (case-insensitive)."""
    matches = df_a[df_a["sku"].astype(str).str.upper() == sku_a.upper()]
    if matches.empty:
        return None
    row = matches.iloc[0]
    return {"index": int(row.name), "sku": str(row["sku"]),
            "title": str(row["title"])}


def _format_candidates(results: list, top: int) -> list:
    """Trim + reshape matcher results for the JSON output."""
    out = []
    for rank, r in enumerate(results[:top], start=1):
        out.append({
            "rank": rank,
            "sku_b": r["sku_b"],
            "title_b": r["title_b"],
            "score": round(r["score"], 2),
            "method": r["method"],
            "tfidf_score": round(r.get("tfidf_score", 0.0), 3),
            "fuzz_score": round(r.get("fuzz_score", 0.0), 1),
            "claude_score": round(r.get("claude_score", 0.0), 1),
        })
    return out


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_status(args) -> None:
    df_a = load_csv(args.file_a, args.col_a_sku, args.col_a_title)
    matched_u = _matched_set(args)
    a_keys_u = {str(s).upper() for s in df_a["sku"]}
    matched_in_a = matched_u & a_keys_u
    skipped_count = _count_jsonl(_side_path(args, "_skipped.jsonl"))
    unmatched_count = _count_jsonl(_side_path(args, "_unmatched.jsonl"))
    _emit({
        "command": "status",
        "file_a": args.file_a,
        "file_b": args.file_b,
        "out": args.out,
        "state_file": args.state_file,
        "total": len(df_a),
        "matched": len(matched_in_a),
        "remaining": len(df_a) - len(matched_in_a),
        "skipped_pending_review": skipped_count,
        "unmatched_logged": unmatched_count,
    })


def cmd_next(args) -> None:
    df_a, matcher = _load_a_and_matcher(args)
    matched_u = _matched_set(args)
    skipped_u = _read_jsonl_sku_set(_side_path(args, "_skipped.jsonl")) if args.skip_skipped else set()
    excluded = matched_u | skipped_u

    for idx in range(len(df_a)):
        row = df_a.iloc[idx]
        sku_a = str(row["sku"])
        if sku_a.upper() in excluded:
            continue
        title_a = str(row["title"])
        results = matcher.match(sku_a, title_a)
        _emit({
            "command": "next",
            "row_index": idx,
            "total": len(df_a),
            "remaining": len(df_a) - len(matched_u),
            "sku_a": sku_a,
            "title_a": title_a,
            "candidates": _format_candidates(results, args.top),
            "note": "no candidates" if not results else None,
        })
        return

    _emit({"command": "next", "done": True, "total": len(df_a),
           "matched": len(matched_u & {str(s).upper() for s in df_a["sku"]})})


def cmd_peek(args) -> None:
    df_a, matcher = _load_a_and_matcher(args)
    row = _row_for_sku(df_a, args.sku)
    if not row:
        _die(f"sku_a {args.sku!r} not found in file_a", file_a=args.file_a)
    results = matcher.match(row["sku"], row["title"])
    _emit({
        "command": "peek",
        "row_index": row["index"],
        "sku_a": row["sku"],
        "title_a": row["title"],
        "candidates": _format_candidates(results, args.top),
    })


def cmd_decide(args) -> None:
    df_a, matcher = _load_a_and_matcher(args, use_claude_override=False)
    row = _row_for_sku(df_a, args.sku)
    if not row:
        _die(f"sku_a {args.sku!r} not found in file_a")

    matched_u = _matched_set(args)
    already = row["sku"].upper() in matched_u
    if already and not args.force:
        _die(f"sku_a {args.sku!r} is already matched. Pass --force to overwrite.",
             sku_a=row["sku"])

    # Resolve --pick: either a rank (1-indexed int) or an explicit sku_b.
    results = matcher.match(row["sku"], row["title"])
    chosen = None
    pick = args.pick.strip()
    if pick.isdigit():
        rank = int(pick)
        if not (1 <= rank <= len(results)):
            _die(f"pick rank {rank} out of range (1..{len(results)})")
        chosen = results[rank - 1]
    else:
        # Look up by sku_b text — exact case-insensitive match against the
        # candidates first, then a fallback search in df_b (so the caller can
        # type a sku that's not in the current top-N).
        for r in results:
            if r["sku_b"].upper() == pick.upper():
                chosen = r
                break
        if chosen is None:
            # Last-resort lookup in catalogue B itself, so a caller who's
            # certain of the target can name it directly.
            df_b = load_csv(args.file_b, args.col_b_sku, args.col_b_title)
            hits = df_b[df_b["sku"].astype(str).str.upper() == pick.upper()]
            if hits.empty:
                _die(f"sku_b {pick!r} not found in candidates or file_b")
            row_b = hits.iloc[0]
            chosen = {
                "sku_b": str(row_b["sku"]),
                "title_b": str(row_b["title"]),
                "score": 0.0,
                "method": "manual",
            }

    # If already matched and --force, rewrite matches.csv with the old row replaced.
    if already:
        _rewrite_match(args.out, row["sku"], row["title"], chosen)
    else:
        append_match(args.out, row["sku"], row["title"],
                     chosen["sku_b"], chosen["title_b"],
                     chosen["score"], chosen["method"])

    matched_u.add(row["sku"].upper())
    _save_matched_state(args, matched_u)

    _emit({
        "command": "decide",
        "sku_a": row["sku"],
        "title_a": row["title"],
        "chosen": {
            "sku_b": chosen["sku_b"],
            "title_b": chosen["title_b"],
            "score": round(chosen["score"], 2),
            "method": chosen["method"],
        },
        "overwritten": already,
    })


def cmd_skip(args) -> None:
    df_a, _ = _load_a_and_matcher(args, use_claude_override=False)
    row = _row_for_sku(df_a, args.sku)
    if not row:
        _die(f"sku_a {args.sku!r} not found in file_a")
    append_jsonl(_side_path(args, "_skipped.jsonl"),
                 {"sku_a": row["sku"], "title_a": row["title"]})
    _emit({"command": "skip", "sku_a": row["sku"]})


def cmd_unmatch(args) -> None:
    df_a, _ = _load_a_and_matcher(args, use_claude_override=False)
    row = _row_for_sku(df_a, args.sku)
    if not row:
        _die(f"sku_a {args.sku!r} not found in file_a")
    append_jsonl(_side_path(args, "_unmatched.jsonl"),
                 {"sku_a": row["sku"], "title_a": row["title"]})

    matched_u = _matched_set(args)
    matched_u.add(row["sku"].upper())
    _save_matched_state(args, matched_u)

    _emit({"command": "unmatch", "sku_a": row["sku"]})


# ---------------------------------------------------------------------------
# Misc filesystem helpers
# ---------------------------------------------------------------------------

def _side_path(args, suffix: str) -> str:
    """Mirror matcher.py:87-88 — sibling files derived from --out."""
    return args.out.replace(".csv", suffix)


def _count_jsonl(path: str) -> int:
    p = Path(path)
    if not p.exists():
        return 0
    with p.open(encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def _read_jsonl_sku_set(path: str) -> set:
    p = Path(path)
    if not p.exists():
        return set()
    out = set()
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict) and "sku_a" in obj:
                    out.add(str(obj["sku_a"]).upper())
            except json.JSONDecodeError:
                continue
    return out


def _rewrite_match(out_path: str, sku_a: str, title_a: str, chosen: dict) -> None:
    """Replace the existing row for sku_a in matches.csv. Used when --force
    overrides a previous decision."""
    import csv as _csv
    p = Path(out_path)
    if not p.exists():
        # Nothing to rewrite — just append.
        append_match(out_path, sku_a, title_a, chosen["sku_b"],
                     chosen["title_b"], chosen["score"], chosen["method"])
        return

    target_u = sku_a.upper()
    rows_out = []
    with p.open(encoding="utf-8") as f:
        reader = _csv.reader(f)
        header = next(reader)
        rows_out.append(header)
        replaced = False
        for r in reader:
            if r and r[0].upper() == target_u:
                rows_out.append([
                    sku_a, title_a, chosen["sku_b"], chosen["title_b"],
                    f"{chosen['score']:.2f}", chosen["method"],
                ])
                replaced = True
            else:
                rows_out.append(r)
        if not replaced:
            rows_out.append([
                sku_a, title_a, chosen["sku_b"], chosen["title_b"],
                f"{chosen['score']:.2f}", chosen["method"],
            ])
    with p.open("w", newline="", encoding="utf-8") as f:
        _csv.writer(f).writerows(rows_out)


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------

def _add_common_args(p) -> None:
    p.add_argument("file_a", help="Catalogue A (e.g. Andrew's CSV)")
    p.add_argument("file_b", help="Catalogue B (Shopify export)")
    p.add_argument("--col-a-sku", default="sku")
    p.add_argument("--col-a-title", default="title")
    p.add_argument("--col-b-sku", default="sku")
    p.add_argument("--col-b-title", default="title")
    p.add_argument("--out", default="matches.csv")
    p.add_argument("--state-file", default="state.json")
    p.add_argument("--stopwords", default=None)


def _add_match_args(p) -> None:
    p.add_argument("--top", type=int, default=8,
                   help="Number of candidates to return (default: 8)")
    p.add_argument("--k", type=int, default=50,
                   help="TF-IDF candidate pool size (default: 50)")
    p.add_argument("--claude", action="store_true",
                   help="Enable Claude semantic tie-breaker")
    p.add_argument("--max-claude", type=int, default=10)


def parse_args():
    p = argparse.ArgumentParser(
        description="Headless step driver for matcher.py — Claude-as-TUI.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="Progress + counts")
    _add_common_args(s)

    s = sub.add_parser("next", help="Top-N candidates for the next unmatched row")
    _add_common_args(s); _add_match_args(s)
    s.add_argument("--skip-skipped", action="store_true",
                   help="Skip rows already in *_skipped.jsonl")

    s = sub.add_parser("peek", help="Top-N candidates for a specific row")
    _add_common_args(s); _add_match_args(s)
    s.add_argument("--sku", required=True)

    s = sub.add_parser("decide", help="Record a chosen match")
    _add_common_args(s); _add_match_args(s)
    s.add_argument("--sku", required=True, help="The sku_a being decided")
    s.add_argument("--pick", required=True,
                   help="Rank (1-indexed) from the current candidate list, OR an explicit sku_b")
    s.add_argument("--force", action="store_true",
                   help="Overwrite an existing match for this sku_a")

    s = sub.add_parser("skip", help="Defer this row for later")
    _add_common_args(s)
    s.add_argument("--sku", required=True)

    s = sub.add_parser("unmatch", help="Log this row as no-match and mark processed")
    _add_common_args(s)
    s.add_argument("--sku", required=True)

    return p.parse_args()


_DISPATCH = {
    "status": cmd_status,
    "next": cmd_next,
    "peek": cmd_peek,
    "decide": cmd_decide,
    "skip": cmd_skip,
    "unmatch": cmd_unmatch,
}


def main():
    args = parse_args()
    try:
        _DISPATCH[args.cmd](args)
    except Exception as e:                                          # noqa: BLE001
        _die(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
