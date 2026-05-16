#!/usr/bin/env bash
# Wrapper around matcher_step.py with the Andrew-workbook arguments baked
# in. Used by Claude-as-TUI during interactive matching sessions.
#
# Usage:
#   bash scripts/sku_matcher/andrew_match.sh <subcommand> [extra args]
#
# Examples:
#   bash scripts/sku_matcher/andrew_match.sh status
#   bash scripts/sku_matcher/andrew_match.sh next --top 5
#   bash scripts/sku_matcher/andrew_match.sh decide --sku TG48-PRO --pick 1
#   bash scripts/sku_matcher/andrew_match.sh peek --sku SBS40CB
#   bash scripts/sku_matcher/andrew_match.sh skip --sku HRM2500\ LIVE
#   bash scripts/sku_matcher/andrew_match.sh unmatch --sku F220
#
# To start fresh: rm matches.csv state.json matches_skipped.jsonl matches_unmatched.jsonl
set -euo pipefail

# Resolve repo root from this script's location so the wrapper works regardless
# of the caller's cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

if [[ $# -lt 1 ]]; then
    echo "usage: $0 <status|next|peek|decide|skip|unmatch> [extra args]" >&2
    exit 1
fi

SUBCMD="$1"; shift

PYTHONPATH=. pyenv exec python scripts/sku_matcher/matcher_step.py "${SUBCMD}" \
    stockPricesAndSkus.csv shopify_catalogue.csv \
    --col-a-sku "Product Code" --col-a-title "Product Description" \
    --col-b-sku sku --col-b-title title \
    --out matches.csv --state-file state.json \
    "$@"
