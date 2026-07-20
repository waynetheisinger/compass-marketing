---
name: tesco-csv-batch
description: Convert a list of MowDirect Shopify SKUs into a Mirakl-importable CSV for one Tesco category. Template-first, exactly like /bq-csv-batch — reads the downloaded Tesco Mirakl category template (tesco/templates/*.xlsx) as the source of truth for which attributes exist and which are required. NEVER hits Tesco's /products/attributes API. Auto-selects the right template by matching each SKU's Shopify taxonomy category against the loaded templates (flags SKUs that span categories or have no template). Pulls Shopify data, fills the deterministic Tesco core (scrubbed copy, JPEG-rehosted images, category gid, defaults), runs one AI pass per SKU to enrich the spec fields, validates, writes a ';'-delimited CSV + markdown log. Push the reviewed CSV with scripts/mirakl_push_products.py.
---

# /tesco-csv-batch — Generate a Tesco import CSV for a SKU batch

This skill produces `tesco/csv-output/<batch>_<ts>.csv` (the file you push to
Tesco via Mirakl) plus a sibling `<batch>_<ts>.log.md` listing every blank
REQUIRED/RECOMMENDED/OPTIONAL cell. It is the **exact Tesco parallel of
`/bq-csv-batch`**, built the same way and on the same principle:

> **The category template is the single source of truth for the shape.** Which
> attributes a Tesco category has — and which are REQUIRED — comes ENTIRELY from
> a Mirakl category template XLSX you download from the Tesco seller portal and
> drop into `tesco/templates/`. This skill NEVER calls Tesco's
> `/products/attributes` API to discover fields. (That API-first approach was
> error-prone and was deleted — see [[mirakl_template_is_source_of_truth]].)

The Python script `scripts/tesco_csv_batch.py` does the deterministic plumbing
(template parsing, Shopify pulls, JPEG re-host, cross-retailer scrub, value-list
validation, CSV writing). **You** (Claude) do one AI pass per SKU: filling the
rich *spec* columns (dimensions, Power Source, Voltage, Bulletpoints, What's
Included, Warranty…) from Shopify data, and marking UNKNOWN anything you can't
support.

## How the work splits (script vs you)

- **The script fills the deterministic Tesco core** in `gather`: `shopifyHierarchyId`
  (the Shopify category gid), `sku`, `barcode` (EAN), `description` (scrubbed
  title), `marketingText` (scrubbed body), `brand`, `baseColour`, the catalogue
  defaults (`countryOfOriginName`/`ageRestriction`/`unitQuantity`/`vatRate`), and
  `image1..N` (re-hosted to real JPEG — Tesco accepts JPEG only). You never touch
  these; they arrive in the context as `auto_filled`.
- **You fill the spec columns** (`spec_columns` in the context) — the
  RECOMMENDED/OPTIONAL fields that make a listing rich and rank well, from the
  product's Shopify metafields / weight / dimensions / feature bullets.

## What this skill does (and doesn't)

Does:
- Reads the chosen Tesco category's template: per-column REQUIRED/RECOMMENDED/
  OPTIONAL marker, value-lists, units — all from the XLSX, no API.
- Pulls each SKU's Shopify product + variant + metafields (derefs metaobjects).
- Fills the deterministic core, including JPEG re-host and the cross-retailer
  copy scrub (strips B&Q/Amazon/MowDirect/"free delivery"/etc.).
- Runs one AI pass per SKU to enrich the spec columns.
- Runs a deterministic validator: value-list membership + numeric parse.
  Failures get demoted to UNKNOWN.
- Runs the shared physical-dimension guard (`scripts/marketplace_dims.py`): any
  blank product OR shipping/package dimension/weight column that exists in the
  template is flagged `[DIMENSION]` in the log and returned as `dimensions_blank`
  in each SKU's JSON — regardless of the template's REQUIRED marker, so missing
  measurements never slip through. **Surface `dimensions_blank` to Wayne.**
- Writes one `;`-delimited, fully-quoted row per SKU (Tesco's import format).

Doesn't:
- Call Tesco's `/products/attributes` (or any Tesco schema API). Template only.
- Push to Mirakl. The CSV is the deliverable; push is a separate explicit step.
- Mix categories. One Tesco category per batch (one template file). Span two →
  run the skill twice.
- Hold incomplete rows back. Every SKU gets a row; the log says what to fill.

## Identifier invariants

- **One category per template file.** Each `tesco/templates/*.xlsx` is one leaf
  category; its breadcrumb is the Columns-sheet header at index 4+.
- **Category VALUE is the Shopify gid** (`gid://shopify/TaxonomyCategory/…`),
  read off each product's Shopify `category.id`. The template's breadcrumb is
  used only to validate the SKU belongs in this batch. A non-leaf / mismatched
  product is fixed with a `sku_category` override in `config/tesco_overrides.json`.
- **Value-list codes == labels on Tesco** (e.g. brand `Spectrum`, colour `Green`).
- **CSV is `;`-delimited, every field quoted.**

---

## Step 1 — List templates

```
PYTHONPATH=. .venv/bin/python scripts/tesco_csv_batch.py list-templates
```

Read the JSON. If `templates` is empty:

```
No templates in tesco/templates/. Download a Tesco category template from the
Mirakl seller portal (the category's import template XLSX) and drop it into
tesco/templates/, then re-run. The template tells me which Tesco attributes are
required for that category.
```

Exit. Otherwise note what's loaded — Step 3 auto-selects the right one from the
SKUs, so you don't hand Wayne a menu unless that auto-match is ambiguous (SKUs
span categories) or comes up empty (no template for a SKU's category).

## Step 2 — Q1: SKUs

```
Shopify SKUs for this batch (space-separated):
  > _
```

Tokenize on whitespace. (If Wayne already passed SKUs as the skill argument,
skip the question and use those.)

## Step 3 — Auto-select the template from the SKUs

Don't ask Wayne to pick the category blind — match the SKUs to a template by
their Shopify taxonomy:

```
PYTHONPATH=. .venv/bin/python scripts/tesco_csv_batch.py suggest-template \
    --skus "S1 S2 S3 ..."
```

Read the JSON and act on it:

- **`single_template: true`** → one template cleanly covers every SKU. Tell
  Wayne which one and proceed to Step 4 with that `category`:
  ```
  All N SKUs map to <leaf> (<template>). Using that template.
  ```
- **Multiple `suggestions`** → the SKUs span categories. One batch = one
  template, so this needs **one batch per template**. Show the split and ask
  Wayne which category to run first (then he can re-run for the others):
  ```
  These SKUs span N categories — they need separate batches:
    <leaf A>:  S1, S3   (<template A>)
    <leaf B>:  S2       (<template B>)
  Which category to run now?
  ```
- **`unmatched_skus`** → a SKU's Shopify category has no loaded template. Tell
  Wayne to download that category's template into `tesco/templates/` (show the
  `shopify_category`), or to set a `sku_category` override and pick a template
  manually. Don't put it in a mismatched batch.
- **`skus_not_found`** → not on Shopify *by that SKU*. This is usually the
  SKU-mismatch case: the **Tesco shop_sku differs from our Shopify SKU** (Tesco
  often prefixes with `C-`, e.g. Tesco `C-DM26L` = Shopify `DM26L`). If you have
  the product's EAN (e.g. from the Tesco catalogue / import transformed-files),
  re-run with a `SKU=EAN` token so it falls back to a barcode lookup:
  ```
  suggest-template --skus "C-DM26L=5065022450030 C-TG40SE=5065022450115 ..."
  ```
  Only if it's still not found by EAN should you treat it as genuinely absent.

Carry the chosen `category` (leaf or breadcrumb) and its matched SKUs into Step 4.

**Sourcing the existing Tesco catalogue.** To regenerate CSVs for what's already
on Tesco: there are no *offers* to pull, so list the seller's *product imports*
(`MiraklClient("TESCO").get("/products/imports")`), download each import's
`/products/imports/<id>/transformed_file`, and union the `sku` + `barcode`
columns — that's the catalogue (shop_sku → EAN), which feeds `suggest-template`
and the `--ean` fallback below.

## Step 4 — Initialize the batch

```
PYTHONPATH=. .venv/bin/python scripts/tesco_csv_batch.py init-batch \
    --category "<leaf or breadcrumb substring>" \
    --skus "S1 S2 S3 ..." \
    --batch-name "<slug — e.g. 'lawn-aerators-june'>"
```

Read the JSON. Pin `state_path`. Report:

```
Batch <batch_id> initialized.
  Template:  <template>
  Category:  <breadcrumb>
  Columns:   <N> (<R> REQUIRED)
  Outputs:
    tesco/csv-output/<batch_id>.csv   (header row written)
    tesco/csv-output/<batch_id>.log.md
```

## Step 5 — Per-SKU loop

For each SKU in input order:

### 5a. Gather

```
PYTHONPATH=. .venv/bin/python scripts/tesco_csv_batch.py gather \
    --batch-state <state_path> --sku <sku> [--ean <ean>]
```

Pass `--ean` for a SKU-mismatch product (Tesco shop_sku ≠ Shopify SKU): gather
falls back to a barcode lookup, but the CSV `sku` column **keeps the Tesco
shop_sku** you passed (re-import matches on that), with all data from the
barcode-matched Shopify product. A `warnings` entry records the EAN match.

This pulls Shopify, **re-hosts images to JPEG** (may take a few seconds the
first time per image; cached after), computes the deterministic core, and writes
`tesco/csv-output/<batch_id>_<sku>_context.json`. Read its full contents.

The context has:
- `auto_filled` — the deterministic Tesco core. **Don't touch these.**
- `category_gid` + `warnings` — surface any warning inline (out of stock,
  category mismatch, missing gid, image re-host failures). If `gather` returns
  `excluded: true`, the SKU is in `exclude_skus` — skip it and move on.
- `spec_columns` — the columns you fill, each with `api_code`, `portal_label`,
  `required` (REQUIRED/RECOMMENDED/OPTIONAL), `value_list`, `unit`, `numeric_only`,
  `description`. **This list came from the template — it is the authoritative set
  of fields for this category.**
- `shopify` — title, descriptionHtml, vendor, category, weight, images, and the
  full flattened `metafields` (with derefed metaobjects).

Also read once per batch (reuse for every SKU): the Tesco brand-voice / scrub
context if present under `.claude/skills/enrich-tesco/` (the deterministic core
already scrubbed the copy; you only need voice cues if you write prose into a
spec field).

### 5b. Fill the spec columns

For each entry in `spec_columns`, decide a value or leave it UNKNOWN. **Default
to UNKNOWN** — only fill what the Shopify data supports.

Rules of judgment:
1. **Dimensions / weight** — `width`/`height`/`depth`/`grossWeight` from the
   product's `weight` and dimension metafields; numeric only, set the matching
   `dimensionsUom`/`weightUom` from its value-list (e.g. `cm`, `kg`).
2. **Value-list fields** — write only an EXACT string from the column's
   `value_list`. No match → UNKNOWN with the nearest candidates noted.
3. **Bulletpoints** — `productFeatures1..3` from `custom.feature_bullets`
   (`<li>` items), one per cell, rewritten short.
4. **`outdoorLiving*` spec family** — Power Source, Voltage, Material Type,
   Warranty, Special Feature, Size — from metafields / display attributes where
   present; UNKNOWN otherwise.
5. **Anything without clear Shopify evidence** — UNKNOWN with a short reason.

### 5c. Write the row JSON

Write to `tesco/csv-output/<batch_id>_<sku>_row.json`:

```json
{
  "filled": {
    "width": "46", "dimensionsUom": "cm",
    "grossWeight": "12.5", "weightUom": "kg",
    "productFeatures1": "...", "outdoorLivingPowerSource": "Battery"
  },
  "unknown": [
    { "column": "outdoorLivingVoltage", "reason": "no voltage metafield on this product" }
  ]
}
```

Only the spec columns — the script merges the deterministic core itself.

### 5d. Validate + write

```
PYTHONPATH=. .venv/bin/python scripts/tesco_csv_batch.py write-row \
    --batch-state <state_path> --sku <sku> \
    --row-json tesco/csv-output/<batch_id>_<sku>_row.json
```

Read the JSON. Report one line:

```
[i/N] <sku> — filled C, unknown U, validator failures V
```

If `required_blank` is non-empty, flag it — those are REQUIRED Tesco cells with
no value (Tesco will reject the row). Usually means a missing `baseColour`
override, an unregistered brand, or no usable image — fix in
`config/tesco_overrides.json` and re-gather. If `validator_failures` is
non-empty, list them inline.

If the script errors, stop the loop and tell Wayne. Don't silently skip.

## Step 6 — Finalize + push

```
PYTHONPATH=. .venv/bin/python scripts/tesco_csv_batch.py finalize \
    --batch-state <state_path>
```

Report processed/missing counts + stats and the output paths. Then tell Wayne:

```
Review tesco/csv-output/<batch_id>.csv (fill REQUIRED gaps from the log), then
push it:
  python scripts/mirakl_push_products.py --operator TESCO \
      --file tesco/csv-output/<batch_id>.csv --dry-run     # preview
  python scripts/mirakl_push_products.py --operator TESCO \
      --file tesco/csv-output/<batch_id>.csv               # submit + read report
```

Don't push automatically — pushing is an explicit, outward action Wayne triggers
after reviewing the CSV.

---

## Capturing gaps / fixes (the overrides config)

`config/tesco_overrides.json` holds the per-product decisions the template can't:
`sku_category` (leaf-category gid override for non-leaf/mismatched products),
`sku_colour`/`brand_colour` (baseColour), `exclude_skus`, and `defaults`
(catalogue constants). When a run surfaces a gap — a non-Spectrum SKU with no
colour, a product whose Shopify category mismatches the batch, a SKU to hold back
— edit `config/tesco_overrides.json` directly (it's committed; the entries are
self-describing), then re-`gather` that SKU. There is no separate "shape" skill;
the template defines the shape and this config defines the per-product knobs.

## Failure modes — handle directly, never silently

- **No templates in `tesco/templates/`** → tell Wayne to download a category
  template from the Tesco Mirakl portal and drop it in; exit at step 1.
- **A SKU isn't on Shopify** → `gather` returns an error for it; tell Wayne, ask
  whether to drop it or abort. Don't write a row for it.
- **`gather` reports a category mismatch** → the SKU's Shopify category differs
  from the batch's template category. Either it belongs in a different batch, or
  add a `sku_category` override gid in `config/tesco_overrides.json`.
- **Image re-host fails** → `gather` warns and the JPEG slot is left blank.
  `image1` is REQUIRED — if it failed, the row will be rejected; investigate the
  source image before pushing.
- **`required_blank` after write-row** → a REQUIRED Tesco column is empty. Fix
  the source (override colour / brand / category, or the missing image) and
  re-gather + re-write that SKU.
- **Shopify auth fails** → hard fail; Wayne checks `.env`. The Shopify client
  refreshes its own token, so this is rare.

## Persistence and audit trail

Per batch in `tesco/csv-output/`:
- `.state_<batch_id>.json` — batch metadata + per-SKU progress
- `<batch_id>.csv` — the deliverable (`;`-delimited, quoted)
- `<batch_id>.log.md` — gap inventory, grouped by SKU
- `<batch_id>_<sku>_context.json` — gather output (holds `auto_filled`), forensics
- `<batch_id>_<sku>_row.json` — your AI spec output, forensics

The committed long-lived artefacts are the templates (`tesco/templates/*.xlsx`,
the source of truth) and `config/tesco_overrides.json` (per-product decisions).
See [[tesco_mirakl_notes]] and [[mirakl_template_is_source_of_truth]].
