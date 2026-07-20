---
name: bq-csv-batch
description: Convert a list of MowDirect Shopify SKUs into a Mirakl-importable CSV for one B&Q subcategory. Bare invocation; asks for B&Q category then SKUs. Reads the chosen subcategory's downloaded Mirakl template (bq/templates/*.xlsx) as the source of truth for which columns exist, pulls Shopify data, applies the Shopify→B&Q mapping config, runs one AI pass per SKU, validates, writes CSV + markdown log.
---

# /bq-csv-batch — Generate a B&Q import CSV for a SKU batch

This skill produces `bq/csv-output/<batch>_<ts>.csv` (the file you upload
to Mirakl) plus a sibling `<batch>_<ts>.log.md` listing every UNKNOWN
cell with `[REQUIRED] | [RECOMMENDED] | [OPTIONAL] | [VALIDATOR]` tags.
The CSV is a working artefact you edit to fill gaps before submitting.

The Python script `scripts/bq_csv_batch.py` handles all the deterministic
plumbing (Shopify pulls, template parsing, value-list validation, CSV
writing). **You** (Claude) do the AI work per SKU: extracting values from
Shopify data using the mapping config, picking from value-lists,
rewriting prose in MowDirect voice, and explicitly marking UNKNOWN
anything you won't commit to.

## What this skill does (and doesn't)

Does:

- Pulls each SKU's full Shopify product + variant + metafields (derefs
  metaobject references).
- Reads the chosen B&Q subcategory's template metadata: per-column
  required/recommended/NA, value-lists from `ReferenceData`, units and
  numeric-only flags from descriptions.
- Applies `config/bq_shopify_mapping.json` to know which Shopify fields
  feed which B&Q columns and how (free-text hint per mapping).
- Runs one AI pass per SKU — you decide each cell's value or mark it
  UNKNOWN with a reason.
- Runs a deterministic validator on top: value-list membership, numeric
  parsing, `name` ≤130 chars + no banned chars per BQ_QUIRKS. Failures
  get demoted to UNKNOWN automatically.
- **Physical-dimension guard** (`scripts/marketplace_dims.py`, shared with
  the Tesco/Range builders): after merge + upsert restore, any blank product
  OR shipping/package dimension/weight column present in the template is
  flagged `[DIMENSION]` in the log and returned as `dimensions_blank` in each
  SKU's JSON — even when the template marks it OPTIONAL, so a missing
  measurement never regresses silently. **Surface `dimensions_blank` to Wayne.**
- **Upsert safety net.** If `bq/csv-upsert/` contains B&Q catalogue
  exports, every UNKNOWN/omitted cell is auto-restored from the matching
  existing product (EAN-first, shop_sku-fallback) so a blank in our CSV
  never risks clearing data B&Q already has. Restored cells are
  revalidated and tagged `[RESTORED]` in the log.
- **PIM code auto-fetch.** Column A needs the Kingfisher PIM hierarchy
  code (e.g. `PIM_13202`), not the breadcrumb. Mappings live in
  `config/bq_subcategory_pim_map.json`. On cache miss, the script calls
  Mirakl `/hierarchies`, resolves the breadcrumb to a PIM code, and
  persists it back to the config so subsequent runs are cached. If the
  API is unreachable or credentials are missing, falls back to the
  breadcrumb with a `[CATEGORY WARNING]` in the log (Mirakl will reject
  with error 1001/1004 — fix manually before upload).
- Writes one row per SKU to the CSV — including rows with empty REQUIRED
  cells (Wayne fills these manually before upload).
- Detects metafield gaps before the run: if SKUs use metafields not in
  `config/bq_shopify_mapping.json`, surfaces them so you can add a mapping
  entry inline (or leave them — the per-SKU pass reads the full metafield
  set regardless and can use any of them with judgement).

Doesn't:

- Upload to Mirakl. The CSV is the deliverable.
- Validate image URLs against B&Q's image rules (white background,
  resolution, etc.). Shopify CDN URLs pass through verbatim.
- Mix subcategories. One subcategory per batch — if your SKU list
  spans corded + cordless, run the skill twice.
- Hold incomplete rows back. Every SKU gets a row; the log tells you
  what needs filling.

## Identifier invariants and conventions

- **Batch ID format:** `<batch_name>_<YYYYMMDD_HHMM>`. Output files all
  share this prefix. State lives at `bq/csv-output/.state_<batch_id>.json`.
- **`name` column gets column-A category path auto-set.** The script
  fills `category` from the subcategory's full path; you don't need to
  generate it in the row JSON.
- **One subcategory per batch.** All rows in the CSV share the same
  `category` value.

---

## Step 1 — List templates

```
PYTHONPATH=. .venv/bin/python scripts/bq_csv_batch.py list-templates
```

Read the JSON. If `templates` is empty:

```
No templates in bq/templates/. Drop a downloaded Mirakl XLSX template
into bq/templates/ and re-run. The template tells me which B&Q columns
are required for which subcategory.
```

Exit. Otherwise build the numbered menu from `all_subcategories`.

## Step 2 — Q1: B&Q category

Show the numbered menu and ask:

```
Pick a B&Q subcategory by number:

  1. <subcategory 1>
  2. <subcategory 2>
  3. ...

  > _
```

Wait for Wayne's reply. Resolve to the exact `subcategory` string from
the JSON (you'll pass this verbatim to `init-batch --category`).

## Step 3 — Q2: SKUs

```
Shopify SKUs for this batch (space-separated):
  > _
```

Wait for reply. Tokenize on whitespace.

## Step 4 — Check metafield coverage

```
PYTHONPATH=. .venv/bin/python scripts/bq_csv_batch.py check-metafields \
    --skus "S1 S2 S3 ..."
```

Read the JSON output. If `has_unmapped_metafields` is true:

```
These SKUs use metafields not yet in config/bq_shopify_mapping.json:
  PRODUCT:        <list>
  PRODUCTVARIANT: <list>
```

These are **not** a blocker. The mapping config only pre-routes a Shopify
field to a B&Q column with a transform hint; the per-SKU pass (step 6)
reads the full metafield set anyway, so you can use any of these with
judgement even if unmapped. For each one that *clearly* feeds a B&Q
column in this subcategory and is worth reusing across batches, add an
entry to `config/bq_shopify_mapping.json` directly (the file's existing
entries show the shape: keyed by `<namespace>.<key>` under
`shopify_metafields.PRODUCT` / `.PRODUCTVARIANT`, each with `bq_targets`
and an optional free-text `hint`). Ask Wayne which, if any, are worth
mapping; skip the rest. Then proceed.

If `has_unmapped_metafields` is false, proceed directly.

Also surface any `skus_not_found` to Wayne — these will fail at `gather`
time anyway, so flag them now so Wayne can drop them from the batch
before init.

## Step 5 — Initialize the batch

```
PYTHONPATH=. .venv/bin/python scripts/bq_csv_batch.py init-batch \
    --category "<exact subcategory string from step 2>" \
    --skus "S1 S2 S3 ..." \
    --batch-name "<slugified batch name — e.g. 'cordless-mowers-may'>"
```

Read the JSON. Pin `state_path` for the rest of the run. Report to Wayne:

```
Batch <batch_id> initialized.
  Template:    <template>
  Subcategory: <subcategory>
  Required columns: <N>
  Recommended:      <N>
  Outputs:
    bq/csv-output/<batch_id>.csv  (header row written)
    bq/csv-output/<batch_id>.log.md
```

## Step 6 — Per-SKU AI loop

For each SKU in the batch (in input order):

### 6a. Gather context

```
PYTHONPATH=. .venv/bin/python scripts/bq_csv_batch.py gather \
    --batch-state <state_path> --sku <sku>
```

This emits `bq/csv-output/<batch_id>_<sku>_context.json`. Read its full
contents with the Read tool. The context has:

- `shopify.product` and `shopify.metafields` — full Shopify data
  (descriptionHtml, vendor, productType, images, derefed metaobjects)
- `mapping_config_relevant` — only the entries whose `bq_targets`
  intersect with active columns in this subcategory. Read each `hint`.
- `bq_columns` — for every active column (REQUIRED + RECOMMENDED;
  NA columns are filtered out): `api_code`, `portal_label`,
  `description`, `required`, `value_list`, `unit`, `numeric_only`.
- `existing_b_and_q_values` — if `bq/csv-upsert/` has a downloaded export
  with a row matching this SKU (by EAN, then shop_sku), each active column
  with an existing value appears here. **You do NOT need to manually
  copy these into `filled`** — `write-row` auto-restores any active
  column you leave UNKNOWN or omit, so blank cells never risk clearing
  data on B&Q. Override one only when you have a better value (just fill
  the cell normally — your value wins).
- `existing_b_and_q_match` — `{match_kind: "EAN" | "SHOP_SKU", source_file: "..."}`
  when a match exists; `null` otherwise.
- `upsert_safety_note` — a reminder of the auto-restore behaviour. Tell
  Wayne inline if the match exists (e.g. "matched 47 existing values
  from `chargers-export-2026-05-19.xlsx` — these will fill any cells I
  leave blank").
- `warnings` — surface these to Wayne inline (zero stock, productType
  mismatch).

Also read these once per batch (cache them mentally; you'll reuse for
every SKU):

- `.claude/skills/enrich-bq/BRAND_VOICE.md`
- `.claude/skills/enrich-bq/BQ_QUIRKS.md`
- `.claude/skills/enrich-bq/EXEMPLARS/<product-type>.md` if a matching
  one exists for the subcategory.

### 6b. Generate the row

For each active column, decide what to write. **Default to UNKNOWN.**
Only fill cells you're confident the Shopify data supports.

Rules of judgment (apply in order):

1. **Trivial copies** — `shop_sku`, `ean`, image URLs, weight value:
   straight from Shopify, apply the hint's transform if any.

2. **Value-list fields** — write only an exact string from `value_list`.
   If the closest Shopify value doesn't match anything in the list,
   mark UNKNOWN with reason e.g. `"vendor='X' not in Acquisition brand
   value list (26273 entries; nearest looking: ...)"`. Don't invent.

3. **Numeric fields with units** — extract the raw number, drop the unit
   (the column description always says "Please provide numeric values
   only, <unit> will be applied automatically"). For dimensions across
   units (e.g. Shopify `46cm` → B&Q `Hose_length (m)` wants `0.46`),
   convert.

4. **The `name` column** — start from `product.title`. Strip nothing
   except as needed for BQ_QUIRKS (no em-dash, en-dash, ×, °, smart
   quotes — substitute or drop). Truncate to ≤130 chars; preserve the
   most discriminating part (SKU + brand + key spec).

5. **`Body Copy`** — rewrite from `product.descriptionHtml`. Strip HTML.
   Apply BRAND_VOICE.md rules (MowDirect voice, distinct from compass
   AND mowdirect.co.uk itself). Apply BQ_QUIRKS rules. Different lead
   sentence, different paragraph structure, different verbs than the
   Shopify source. Length: aim for 80-150 words; B&Q doesn't impose a
   limit but anything longer reads as filler.

6. **`Selling Copy`** — shorter than Body Copy, more punchy.
   Range/specific product messaging.

7. **`Unique Selling Point 01..08`** — primarily from
   `custom.feature_bullets` (an `<ul><li>...</li></ul>` block). Split
   to one bullet per cell, rewrite each, drop B&Q-as-retailer references
   ("free delivery", "click and collect"), pad with empty if fewer than
   8.

8. **`Key_Feature`** — a single short callout for the search-result tile.
   Pull the most distinctive spec — e.g. "46cm Cutting Deck", "60L Grass
   Bag", "1600W Brushless Motor".

9. **Anything you don't see clear evidence for** — UNKNOWN with a short
   reason. The validator and Wayne will trust UNKNOWN means "I genuinely
   couldn't tell"; don't dilute it with confident guesses.

### 6c. Write the row JSON

Output schema (write to a temp file under `bq/csv-output/`):

```json
{
  "filled": {
    "shop_sku": "SBS460CLM",
    "ean": "5065022450634",
    "name": "...",
    "Body Copy": "...",
    "Acquisition brand": "Spectrum",
    "Cordless": "Cordless",
    "Product_weight": "25.6",
    "image_main_1": "https://cdn.shopify.com/...",
    "image_secondary_1": "https://cdn.shopify.com/...",
    "Unique Selling Point 01": "...",
    "...": "..."
  },
  "unknown": [
    { "column": "Hose_length", "reason": "Shopify has no equivalent metafield" },
    { "column": "Std_IP rating", "reason": "Not set on this Shopify product" }
  ]
}
```

Save it at `bq/csv-output/<batch_id>_<sku>_row.json`.

### 6d. Validate + write

```
PYTHONPATH=. .venv/bin/python scripts/bq_csv_batch.py write-row \
    --batch-state <state_path> --sku <sku> \
    --row-json bq/csv-output/<batch_id>_<sku>_row.json
```

Read the JSON output. Report one line to Wayne:

```
[i/N] <sku> — filled C, restored R, unknown U, validator failures V
```

If `cells_restored > 0`, mention briefly that the safety net populated
those cells from `bq/csv-upsert/<source_file>` (the `upsert_match` field
gives the source).

If `validator_failures` is non-empty, list them inline so Wayne sees
which cells you wrote that got demoted by the validator. He may want to
investigate a pattern.

If `restore_validator_failures` is non-empty, flag it — it means the
upsert file contains values that don't validate against the current
template (e.g. value-list codes that changed since the data was first
entered). The cell stayed blank; Wayne may want to refresh the upsert
export or correct it manually.

If `pim_autofetched` is true, mention it briefly: the script just
resolved the PIM code from Mirakl `/hierarchies` and cached it in
`config/bq_subcategory_pim_map.json`. No action needed — subsequent
runs in this subcategory will be cache hits.

If `category_warning` is non-null, the cache miss *and* the Mirakl
auto-fetch both failed (no credentials, API unreachable, or breadcrumb
not found in `/hierarchies`). The category cell fell back to the
breadcrumb, which Mirakl rejects with error 1001/1004. Tell Wayne to
investigate before uploading.

If the script fails or returns an error, stop the loop and tell Wayne.
Do not silently skip.

## Step 7 — Finalize

```
PYTHONPATH=. .venv/bin/python scripts/bq_csv_batch.py finalize \
    --batch-state <state_path>
```

Read the JSON. Tell Wayne:

```
Batch complete.
  SKUs processed: <N>
  Cells filled:   <P>
  Cells UNKNOWN:  <Q> (see log for which)
  Validator demotions: <V>

Files:
  bq/csv-output/<batch_id>.csv      — upload this to Mirakl after
                                      filling the gaps from the log
  bq/csv-output/<batch_id>.log.md   — your gap-fill todo list,
                                      grouped by SKU

Suggested next step:
  open the .log.md, scan for [REQUIRED] tags, fill those cells in
  the CSV; then upload.
```

Exit cleanly.

---

## Failure modes — handle directly, never silently

- **No templates in `bq/templates/`** → tell Wayne, exit at step 1.
- **`config/bq_shopify_mapping.json` missing** → it's committed; restore
  it from git (`git checkout config/bq_shopify_mapping.json`), then resume
  from step 1. Don't recreate it by hand.
- **A SKU isn't on Shopify** → `check-metafields` reports it in
  `skus_not_found`. Tell Wayne; ask whether to drop it from the batch
  or abort. Don't proceed to `init-batch` until resolved.
- **`gather` fails for a SKU mid-loop** → stop the loop. Report which
  SKU failed and why. The batch state preserves which SKUs were
  processed so far — those rows are already in the CSV.
- **`write-row` rejects the row JSON as malformed** → stop, surface the
  error. Don't continue with the next SKU until the row is fixed (run
  6b/6c/6d for the same SKU again).
- **Validator demotion ratio is high (e.g. >25% of filled cells)** →
  flag it to Wayne after step 6d. Often signals the value-list lookup
  is wrong (e.g. matching "5 Years (T&Cs Apply)" against "5 years" —
  whitespace/case issue) or the AI is hallucinating.
- **Upsert file fails to parse** → the script prints `[warn]` to stderr
  and continues without that file. Tell Wayne so he knows that file
  isn't contributing to the safety net.
- **`restore_validator_failures` is non-empty** → existing B&Q values
  don't match the current template's value lists / numeric format. Flag
  inline; cell stays blank; Wayne may need to refresh the upsert export
  or fix manually.
- **`category_warning` non-null** → PIM cache miss + Mirakl
  `/hierarchies` auto-fetch failed (missing `MIRAKL_KINGFISHER_*` env
  vars, API down, or the breadcrumb isn't in the live hierarchy). The
  CSV row carries the breadcrumb in column A — Mirakl will reject with
  error 1001/1004. Fix `config/bq_subcategory_pim_map.json` by hand
  before upload.

## Persistence and audit trail

Per batch in `bq/csv-output/`:

- `.state_<batch_id>.json` — batch metadata + per-SKU progress
- `<batch_id>.csv` — the deliverable
- `<batch_id>.log.md` — gap inventory
- `<batch_id>_<sku>_context.json` — gather output, for forensics
- `<batch_id>_<sku>_row.json` — your AI output, for forensics

The state file means you could resume a partially-failed batch (skip
already-processed SKUs from `processed_skus`), though re-running the
skill bare and providing only the un-processed SKUs is the simpler path.

The mapping config (`config/bq_shopify_mapping.json`) is the long-lived
artefact; the per-batch files are short-lived working state.
