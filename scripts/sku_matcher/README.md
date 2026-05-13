# sku_matcher — operator notes (for Claude)

Wayne does not invoke these scripts directly. Claude (= me) drives them on his behalf. This file is the dense cheat-sheet I need when picking the work back up.

Context: [[sku-mismatch-problem]]. Shopify SKUs are drifted from the canonical SKUs that NumaSuite + marketplaces use; until they match, the NumaSuite→Shopify stock sync (CLAUDE.md Priority 1) cannot ship.

Two-stage pipeline:

```
shopify_export.csv ──╮
                     ├─► matcher.py (TUI) ──► matches.csv ──► shopify_updater.py ──► Shopify writes
canonical_skus.csv ──╯
```

## Invocation

Project Python is **pyenv 3.12.0** (`.python-version`). Every dep is already installed there. Never use bare `python3.11` — that resolves to `/usr/local/opt/python@3.11`, which has nothing.

```bash
# Stage 1
PYTHONPATH=. pyenv exec python scripts/sku_matcher/matcher.py \
    A.csv B.csv \
    --col-a-sku sku --col-a-title title \
    --col-b-sku sku --col-b-title title \
    --out matches.csv [--claude] [--min-score 70] [--k 50] [--redo]

# Stage 2 — credentials come from .env via scripts/shopify_client.py.
PYTHONPATH=. pyenv exec python scripts/sku_matcher/shopify_updater.py matches.csv \
    --shopify-sku-col sku_b --target-sku-col sku_a [--dry-run] [--min-score 85]

# Utility — drop Shopify-export rows that only have a Handle column populated.
PYTHONPATH=. pyenv exec python scripts/sku_matcher/filter_handle_only.py in.csv out.csv
```

`--claude` needs `ANTHROPIC_API_KEY` in env. The Shopify side needs `SHOPIFY_STORE_DOMAIN`, `SHOPIFY_CLIENT_ID`, `SHOPIFY_CLIENT_SECRET` in `.env` — same as every other Shopify script in the repo.

## Module map

| File | Responsibility | When I'd touch it |
|---|---|---|
| `matcher.py` | CLI entry for stage 1; resume loop; calls `Matcher.match()` per row, hands result to `MatchSelector` | New CLI flags, output columns |
| `matching.py` | `Matcher` class: TF-IDF candidate retrieval → RapidFuzz rerank → optional Claude tie-breaker. Holds the `-AMZ` suffix filter | Pipeline weights, Claude model, FBA filter rules |
| `normalize.py` | `normalize_text` + `DEFAULT_STOPWORDS` + stopwords loader | Lowercasing/punctuation behaviour |
| `tui.py` | `MatchSelector` — text-prompt loop (no curses), pagination, `q`/`s`/`u` actions | UI changes |
| `io_utils.py` | `load_csv`, `append_match`, `append_jsonl`, `load_state`/`save_state`, `get_matched_skus` (matcher side) | CSV schema, state file shape |
| `shopify_updater.py` | CLI entry for stage 2; argparse, per-row prompts, state save after every row | UX flow, prompt copy |
| `shopify_api.py` | `ShopifyAPI` wrapper around `scripts.shopify_client.ShopifyClient`. `ShopifyProduct`/`ShopifyVariant` dataclasses. GraphQL only | Anything that talks to Shopify |
| `shopify_io.py` | `load_matches_csv`, state JSON, JSONL audit log, `get_log_summary` (updater side) | Log schema, state shape |
| `filter_handle_only.py` | Standalone CSV cleanup util — strips rows where only `Handle` is populated | Rarely |
| `requirements.txt` | Upstream pin list — kept for reference; the project pyenv already has everything | Don't bother |

## CSV / state / log schemas

`matches.csv` (output of stage 1, input of stage 2):
```
sku_a,title_a,sku_b,title_b,score,method
SBS20CB,SPECTRUM 40V Cordless Drill,SBS-20-CB,Spectrum 40V Drill Driver,93.20,tfidf+rapidfuzz
```
`method` is one of `tfidf+rapidfuzz` or `tfidf+rapidfuzz+claude`.

Side files:
- `matches_skipped.jsonl` — `{"sku_a", "title_a"}` for items the operator pressed `s` on (revisit later)
- `matches_unmatched.jsonl` — same shape, items confirmed as having no counterpart

`state.json` (matcher resume cursor):
```json
{"current_index": 42, "matched_skus": ["SBS20CB", ...]}
```

`shopify_update_state.json` (updater resume cursor):
```json
{
  "current_index": 42,
  "updated_skus":  ["OLD-1", "OLD-2"],
  "skipped_skus":  ["OLD-3"],
  "failed_skus":   [{"sku": "OLD-4", "error": "..."}],
  "config": {"matches_file": "...", "shopify_sku_col": "...", ...}
}
```
Both state files are auto-deleted on full completion. Delete manually to start fresh.

`<matches>_shopify_updates.log` (JSONL, one entry per row decision):
```json
{"timestamp":"...Z","status":"success","index":0,"shopify_sku":"OLD","target_sku":"NEW",
 "product_id":"gid://shopify/Product/123","product_title":"...","product_handle":"...",
 "variant_updates":[{"variant_id":"gid://shopify/ProductVariant/9","variant_title":"Default Title","old_sku":"OLD","new_sku":"NEW"}],
 "match_score":95.2,"match_method":"tfidf+rapidfuzz","dry_run":false,"error":null}
```
`status` ∈ {success, skipped, failed}. Variant IDs are **GraphQL GIDs** since the rewrite — not numeric.

Useful jq one-liners:
```bash
jq -c 'select(.status=="failed") | {sku:.shopify_sku, error}'   matches_shopify_updates.log
jq -r 'select(.status=="success") | .shopify_sku'               matches_shopify_updates.log | sort -u
```

## Matching pipeline (matching.py)

1. **TF-IDF candidates** — `TfidfVectorizer(ngram_range=(1,2), max_features=1000)`. Top `k` (default 50) by cosine similarity. Index is rebuilt every run; fine ≤10k rows, slow above.
2. **`-AMZ` suffix gate** — `Matcher.get_candidates` filters candidates to only `-AMZ` SKUs when the query SKU ends in `-AMZ` (case-insensitive). Real business rule for FBA-only matching, not a quirk to remove.
3. **RapidFuzz rerank** — combined score `0.6·token_set_ratio + 0.3·WRatio + 0.1·partial_ratio` on the normalized strings.
4. **Claude tie-breaker** — fires only when `--claude` and top-2 fuzz scores within 3 points and top score < 90. Reranks up to `--max-claude` (default 10) candidates with a single-number similarity prompt. Final = `0.5·fuzz + 0.5·claude`. Model: `claude-haiku-4-5-20251001`.

`normalize_text` lowercases, swaps `-_` to spaces, drops non-alphanumerics, strips stopwords. Default stopwords are tiny (English articles only); pass `--stopwords file.txt` to add brand/junk tokens.

## Shopify API semantics (shopify_api.py)

- All auth + transport goes through `scripts.shopify_client.ShopifyClient` (client-credentials, API version `2026-01`). No `shpat_…` token, no REST, no rate-limiting code of our own.
- **Lookup**: `find_products_by_sku(sku)` issues one GraphQL `productVariants(first: 25, query: "sku:X")` call. Hard cap of 25 matching variants — fine in practice (a SKU shouldn't be on more than one product). Returns a deduped list of `ShopifyProduct`, each with **all** sibling variants (first 100 per product) so the multi-variant suffix prompt has the data it needs.
- **Write**: `productVariantsBulkUpdate` with `[{id, inventoryItem: {sku}}, ...]`. SKU lives under `inventoryItem` — not on the variant root (REST quirk that the deprecated `PUT /variants/{id}.json` used to allow).
- **Scope**: the mutation is **scoped to a single product per call**. `update_multiple_variant_skus(product_id, [(vid, sku), ...])` enforces this — it's only correct for variants of the same product.
- **Atomicity**: if any variant in the payload fails validation, Shopify rejects the entire mutation. The wrapper marks every input row failed with the same combined error in that case (no partial writes).
- GIDs everywhere: `gid://shopify/Product/N`, `gid://shopify/ProductVariant/N`. Don't try to construct numeric IDs.

## Resume semantics

Both stages save state after every committed action.

- **Matcher**: `matches.csv` is append-only; matched SKUs go into `state.json["matched_skus"]`. On restart it skips both anything in the state file *and* anything already in `matches.csv` (`get_matched_skus()` re-reads the output file). `--redo` ignores both.
- **Updater**: writes state after every row decision (success, skipped, failed). `KeyboardInterrupt` is caught and saved cleanly. Already-processed SKUs (updated + skipped — *not* failed) are auto-skipped on resume. Failed rows are retried; clean them out of `failed_skus` if you want to permanently skip them.
- Both quit cleanly on the in-prompt `q` action *and* on `Ctrl+C`.

## Known limits / things I'd hit eventually

- TF-IDF index rebuild every run — fine up to ~10k rows, then noticeable. Cache to `joblib` if it ever gets painful.
- `find_products_by_sku` caps at 25 matching variants and 100 sibling variants per product. Both are way above any realistic case for MowDirect, but if I'm running this against a giant catalogue I should bump the query first.
- `productVariantsBulkUpdate` is one product per call → multi-variant products incur one HTTP round-trip each, sequentially. Plenty fast for hundreds of rows; for thousands, batch via async or accept the wall-clock.
- `prompt_variant_skus` joins suffix with `_` by default, or with the suffix's leading `_`/`-` if you provide one. No way to override the separator from the CLI — change the code if needed.
- Claude tie-breaker is hardcoded to Haiku 4.5. Bump in `matching.py` when a newer Haiku ships; don't switch to Sonnet here — the prompt is a number-out task that doesn't need it.

## Related memory

- [[sku-mismatch-problem]] — why this tool exists
- [[sku-matcher-tool]] — higher-level index of this folder
- [[shopify-updater-auth-divergence]] — resolved; kept as paper trail
