#!/usr/bin/env python3
"""One-shot: apply Claude's review decisions for the Stiga skipped rows.

Reads workdir/sku-matcher/matches_stiga_skipped.jsonl (one ambiguous row per line, each with its
top-N candidates inline), looks up the decision in DECISIONS below, and
either appends a row to lookups/matches_stiga.csv (rank-N candidate) or to
workdir/sku-matcher/matches_stiga_unmatched.jsonl. Re-writes the skipped file empty when done.

Run from the repo root:

    PYTHONPATH=. pyenv exec python scripts/sku_matcher/_apply_stiga_review_decisions.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts.sku_matcher.io_utils import append_jsonl, append_match, load_state, save_state


SKIPPED = "workdir/sku-matcher/matches_stiga_skipped.jsonl"
MATCHES_OUT = "lookups/matches_stiga.csv"
UNMATCHED_OUT = "workdir/sku-matcher/matches_stiga_unmatched.jsonl"
STATE_FILE = "workdir/sku-matcher/stiga_state.json"
SOURCE_A = "workdir/sku-matcher/stiga_prices_normalized.csv"


# Decisions: sku_a → either int rank (1-indexed) or "u" for unmatched.
# Built by reading workdir/sku-matcher/matches_stiga_skipped.jsonl and judging each row's top
# candidates against the Stiga product description.
DECISIONS: dict = {
    "2T0250481/ST1M": "u",
    "2T2210481/ST1": "u",
    "2T2805481/ST1": "u",
    "2T0670481/ST1": "u",
    "2T1275481/ST1": "u",
    "279000004/ST1": "u",
    "2T0660481/ST2": "u",
    "2T0665481/ST2": "u",
    "2T0070481/ST2": 1,
    "2T2100481/ST1": 1,
    "2T2620481/ST2": 1,
    "2T0810481/ST1": "u",
    "2T0830481/ST1": "u",
    "2T0990481/ST1": "u",
    "2T0970381/ST1": "u",
    "2T1315381/ST1": "u",
    "2T1430381/ST2": "u",
    "2T1535381/ST2": "u",
    "2T0510481/ST3": 2,
    "2T1215481/ST1": 1,
    "2T1835381/ST1": "u",
    "2T1950381/ST1": 1,
    "2T2010483/M25": 1,
    "2T0620483/M25": 1,
    "2T1210483/M25": 1,
    "2T2110447/AT2": 1,
    "2T0830447/AT1": "u",
    "2T1210447/AT2": "u",
    "2F6130535/ST2": "u",
    "2F6230845/ST1": 2,
    "2F6430831/ST2": 1,
    "2D6210021/ST2": 2,
    "2L0482008/UKS": "u",
    "298473278/ST1": "u",
    "2L0431048/ST1": "u",
    "2L0482348/ST1": "u",
    "294556838/ST1": "u",
    "2L0486548/ST2": "u",
    "298472048/ST1": 1,
    "291502048/ST2": "u",
    "294512048/ST1": "u",
    "294513048/ST1": "u",
    "294563838/ST1": "u",
    "291302063/M21": 3,
    "2L0482008/M24": "u",
    "299439073/M22": "u",
    "299489073/M22": 1,
    "294519037/AT1": 1,
    "294569037/AT20": 1,
    "2L0432847/AT1": "u",
    "294502837/AT9": "u",
    "294513837/AT9": "u",
    "294563837/AT1": "u",
    "271404201/UKS": "u",
    "257022001/UKS": "u",
    "271432101/ST1": "u",
    "257122001/ST1": "u",
    "271090021/ST1": "u",
    "273504501/ST1": "u",
    "273102501/ST1": "u",
    "273404501/ST1": "u",
    "273302501/ST1": "u",
    "278722208/UKS": "u",
    "PROMO_BC700EKIT_U": "u",
    "PROMO_BC700EBKIT_U": "u",
    "PROMO_CS700EKIT_U": "u",
    "271012008/ST1": "u",
    "271014008/ST1": "u",
    "277040008/ST1": "u",
    "287120102/ST2": 2,
    "287220002/ST1": 2,
    "283220008/ST1": 1,
    "281210003/21": 1,
    "240271002/ST2": "u",
    "240271012/ST2": "u",
    "240381402/ST1": 1,
    "240421602/ST1": 1,
    "240461802/ST1": 1,
    "240521802/ST1": 1,
    "255127092/ST2": 1,
    "252422008/ST2": "u",
    "287130152/ST2": "u",
    "232511810/15": "u",
    "271504203/MUK": "u",
    "271504293/MUK": "u",
    "287120123/M16": "u",
    "287120153/M16": "u",
    "219602532/S17": 1,
    "290602020/16": 1,
    "219802532/S17": 1,
    "290802020/16": 1,
    "2S2726611/ST1": "u",
    "2S2767615/ST1": "u",
    "212751142/14": "u",
    "213851142/ST1": 1,
    "219510032/10": "u",
    "210310022/10": "u",
    "290950050/10": 3,
    "290950020/10": 1,
    "290950010/10": "u",
    "290950030/10": 3,
    "290950070/10": "u",
    "290950040/10": 1,
}


def main() -> int:
    if not Path(SKIPPED).exists():
        print(f"❌ {SKIPPED} not found", file=sys.stderr)
        return 1

    with Path(SKIPPED).open() as f:
        rows = [json.loads(line) for line in f if line.strip()]

    keys = {r["sku_a"] for r in rows}
    missing = keys - set(DECISIONS)
    extra = set(DECISIONS) - keys
    if missing:
        print(f"❌ {len(missing)} skipped row(s) have no decision: {sorted(missing)[:5]}", file=sys.stderr)
        return 2
    if extra:
        print(f"⚠️  {len(extra)} decision(s) have no matching skipped row "
              f"(probably already-applied or a typo): {sorted(extra)[:5]}", file=sys.stderr)

    n_decided = 0
    n_unmatched = 0
    for row in rows:
        sku_a = row["sku_a"]
        title_a = row["title_a"]
        decision = DECISIONS[sku_a]
        if decision == "u":
            append_jsonl(UNMATCHED_OUT,
                         {"sku_a": sku_a, "title_a": title_a,
                          "reason": "manual_review_no_plausible_match"})
            n_unmatched += 1
        else:
            rank = int(decision)
            candidates = row["candidates"]
            if not (1 <= rank <= len(candidates)):
                print(f"❌ {sku_a}: rank {rank} out of range "
                      f"(have {len(candidates)} candidates)", file=sys.stderr)
                return 3
            c = candidates[rank - 1]
            append_match(MATCHES_OUT, sku_a, title_a,
                         c["sku_b"], c["title_b"], c["score"], c["method"])
            n_decided += 1

    # Drain the skipped file — every row is now resolved.
    Path(SKIPPED).write_text("")

    # Refresh workdir/sku-matcher/state.json so matched_skus reflects every decided row plus the
    # already-matched ones; current_index = len(df_a) so the interactive TUI
    # treats matching as complete.
    import pandas as pd
    df_a = pd.read_csv(SOURCE_A, dtype=str, keep_default_na=False, na_values=[""])
    state = load_state(STATE_FILE)
    existing = set(state.get("matched_skus", []))
    existing |= {r["sku_a"] for r in rows}
    save_state(STATE_FILE, len(df_a), sorted(existing))

    print(f"\n✓ Decided (added to {MATCHES_OUT}):   {n_decided}")
    print(f"✓ Unmatched (added to {UNMATCHED_OUT}): {n_unmatched}")
    print(f"✓ Skipped file drained: {SKIPPED}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
