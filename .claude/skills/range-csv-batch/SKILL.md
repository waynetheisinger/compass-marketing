---
name: range-csv-batch
description: Convert a list of MowDirect Shopify SKUs into a Mirakl-importable CSV for one The Range category. Template-first, exactly like /tesco-csv-batch and /bq-csv-batch — reads the downloaded Range Mirakl category template (range/templates/*.xlsx) as the source of truth for which attributes exist and which are required. NEVER hits The Range's API to discover fields. Routes each SKU to the right template via config/range_category_map.json (Shopify category → Range template), splitting mowers by power source (Cordless/Electric/Petrol). Pulls Shopify data, fills the deterministic Range core (scrubbed copy, JPEG-rehosted images, breadcrumb category, brand from vendor, colour, defaults), runs one AI pass per SKU to enrich the spec fields, validates, writes a ';'-delimited CSV + markdown log. Push the reviewed CSV with scripts/mirakl_push_products.py --operator THERANGE.
---

# /range-csv-batch — Generate a The Range import CSV for a SKU batch

Produces `range/csv-output/<batch>_<ts>.csv` (the file you push to The Range via
Mirakl) plus a sibling `<batch>_<ts>.log.md` listing every blank REQUIRED/
RECOMMENDED/OPTIONAL cell. It is the **The Range parallel of `/tesco-csv-batch`**,
built the same way and on the same principle:

> **The category template is the single source of truth for the shape.** Which
> attributes a Range category has — and which are REQUIRED — comes ENTIRELY from
> a Mirakl category template XLSX you download from The Range seller portal and
> drop into `range/templates/`. This skill NEVER calls The Range's API to
> discover fields. (See [[mirakl_template_is_source_of_truth]].)

The Python script `scripts/range_csv_batch.py` does the deterministic plumbing
(template parsing, Shopify pulls, JPEG re-host, cross-retailer scrub, value-list
validation, CSV writing). **You** (Claude) do one AI pass per SKU: filling the
rich *spec* columns (dimensions + units, material, wattage, feature bullets,
What's Included…) from Shopify data, marking UNKNOWN anything you can't support.

## The Range dialect — how it differs from Tesco/B&Q

Same 3-sheet Mirakl workbook, but its own column names and a different category
value. Confirmed template-first 2026-06-17:

- **Core columns:** `category` / `shop_sku` / `title` / `brand` / **`gtin`** (the
  EAN — not Tesco's `barcode`) / `description` / `main_image` + `image_1..20` /
  `feature_1..10` / `variant_group_code`. From `THERANGE.field_schema`.
- **Category VALUE = the breadcrumb string itself** (e.g.
  `Garden/.../Cordless Lawn Mowers`), fixed per template — NOT a Shopify gid
  (Tesco) or PIM code (B&Q). The script sets it as a constant per batch; it
  validates against its own 1-entry value-list normally.
- **Multi-brand catalogue.** `brand` = the Shopify vendor per-product (Spectrum,
  Feider, Alpina, Mountfield…). There is no Spectrum default.
- **Colour is per-brand/per-SKU**, REQUIRED as a trio (`colour_group` value-list
  + `colour_display_name` + `colour_hex_code`). Resolved from
  `config/range_overrides.json` (`brand_colour`/`sku_colour` + `colour_hex`).
- **Heavier REQUIRED set than Tesco:** colour trio, `feature_1..3`,
  `main_product_image`(Yes/No)+`main_product_name`, `variant_group_code`, and on
  machinery dimensions/material/wattage with **separate `*_uom` value-list
  partner columns** (`width_4` + `width_4_uom`).

## How the work splits (script vs you)

- **The script fills the deterministic Range core** in `gather`: `category`
  (breadcrumb), `shop_sku`, `gtin`, `title`/`description` (scrubbed), `brand`
  (vendor), the colour trio (from overrides), `variant_group_code` (= shop_sku),
  `main_product_image`/`main_product_name`, catalogue defaults (`made_to_order`),
  any GPSR economic-operator fields you've filled in the overrides, the Shopify
  weight → the template's weight column + `_uom`, and `main_image` + `image_N`
  (re-hosted to real JPEG). These arrive in the context as `auto_filled` — don't
  touch them. Only keys that are real columns in *this* template survive.
- **You fill the spec columns** (`spec_columns`) — dimensions+`_uom`, `material`,
  `wattage`+`_uom`, `feature_1..N`, capacity, etc., from Shopify metafields /
  weight / feature bullets.

## What this skill does (and doesn't)

Doesn't:
- Call any Range schema API. Template only.
- Push to Mirakl. The CSV is the deliverable; push is a separate explicit step.
- Mix categories. One Range category per batch (one template file).
- Hold incomplete rows back. Every SKU gets a row; the log says what to fill.

## Identifier invariants

- **One category per template file.** Each `range/templates/*.xlsx` is one leaf
  category; its breadcrumb is the Columns-sheet header at index 4+.
- **Category VALUE is the template breadcrumb** (constant per batch).
- **EAN column is `gtin`.**
- **CSV is `;`-delimited, every field quoted.**

---

## Step 1 — List templates

```
PYTHONPATH=. .venv/bin/python scripts/range_csv_batch.py list-templates
```

If `templates` is empty, tell Wayne to download a Range category template from
the Mirakl seller portal into `range/templates/` and exit. Otherwise note what's
loaded. **Sanity-check for wrong-download duplicates** (two entries with the same
breadcrumb means one file is a mis-saved copy of another — re-download it).

## Step 2 — Q1: SKUs

```
Shopify SKUs for this batch (space-separated):
  > _
```

Tokenize on whitespace. (If Wayne passed SKUs as the skill argument, use those.)

## Step 3 — Route the SKUs to a template

The Range breadcrumbs are its own taxonomy and never equal Shopify's, so routing
goes through `config/range_category_map.json` (Shopify category → Range template
leaf):

```
PYTHONPATH=. .venv/bin/python scripts/range_csv_batch.py suggest-template \
    --skus "S1 S2 S3 ..."
```

Read the JSON and act on it:

- **`single_template: true`** → proceed to Step 4 with that `category`.
- **Multiple `suggestions`** → SKUs span categories; one batch = one template.
  Show the split, run one batch per template.
- **`unmatched_skus`** → the SKU's Shopify category has no map entry (or maps to a
  leaf with no template). Either add a mapping to `config/range_category_map.json`
  (show the `shopify_category`), or — if The Range has no template for that
  category at all — tell Wayne it's **out of scope until he downloads that
  template**. Don't force it into a mismatched batch.
- **`skus_not_found`** → not on Shopify by that SKU. Retry with a `SKU=EAN` token
  for the barcode fallback (B&Q/Tesco shop_skus often prefix Shopify SKUs with
  `C-`). Only if still absent by EAN is it genuinely missing.

**Mowers split by power.** The map routes `Walk-Behind Mowers` →
`__MOWER_BY_POWER__`; the script then detects cordless/electric/petrol from the
product (title/type/tags/metafields) and picks Cordless/Electric/Petrol Lawn
Mowers. If detection returns nothing (rare — e.g. a title with no power keyword),
the SKU comes back unmatched; assign it by adding a clearer keyword path or
handling that SKU in its own batch.

**Sourcing the product set.** The Range seed set is the union of what's on B&Q +
Tesco. B&Q's catalogue is enumerable via `MiraklClient("KINGFISHER").get("/offers")`
(each offer carries `shop_sku` + EAN in `product_references` + `category_label`);
Tesco's is reconstructed from `/products/imports` transformed-files. Feed those
`shop_sku=EAN` pairs to `suggest-template`.

## Step 4 — Initialize the batch

```
PYTHONPATH=. .venv/bin/python scripts/range_csv_batch.py init-batch \
    --category "<leaf or breadcrumb substring>" \
    --skus "S1 S2 S3 ..." \
    --batch-name "<slug>"
```

Pin `state_path`. Report template, breadcrumb, column count + REQUIRED count, and
the output paths.

## Step 5 — Per-SKU loop

For each SKU in input order:

### 5a. Gather

```
PYTHONPATH=. .venv/bin/python scripts/range_csv_batch.py gather \
    --batch-state <state_path> --sku <sku> [--ean <ean>]
```

Pass `--ean` for a SKU-mismatch product: gather falls back to a barcode lookup,
but the CSV `shop_sku` column **keeps the SKU you passed**. This pulls Shopify,
re-hosts images to JPEG (cached after first time), computes the deterministic
core, and writes `range/csv-output/<batch_id>_<sku>_context.json`. Read it fully.

The context has `auto_filled` (don't touch), `warnings` (surface inline — out of
stock, **unresolved colour**, image failures; `excluded: true` means skip),
`spec_columns` (the columns you fill — authoritative, from the template), and
`shopify` (title, body, vendor, weight, images, flattened metafields).

### 5b. Fill the spec columns

Default to UNKNOWN; fill only what Shopify supports.

1. **Dimensions / weight** — `width_4`/`height_7`/`depth_23`/`weight_61` from
   dimension metafields / variant weight; numeric only, and set each one's
   `*_uom` partner from its value-list (`Centimetres`, `Kilograms`, …).
2. **Value-list fields** (incl. every `*_uom`, `material_11`, `colour_group`) —
   write only an EXACT string from the column's `value_list`. No match → UNKNOWN.
3. **`feature_1..N`** — from `custom.feature_bullets` (`<li>` items), one per cell.
4. **`wattage_67` / `capacity_1164` / `cutting_height_112` / `blade_length_130`**
   — from spec metafields; numeric + `_uom`.
5. Anything without clear Shopify evidence → UNKNOWN with a short reason.

### 5c. Write the row JSON

Write `range/csv-output/<batch_id>_<sku>_row.json` with `filled` (spec columns
only) + `unknown`. The script merges the deterministic core itself.

### 5d. Validate + write

```
PYTHONPATH=. .venv/bin/python scripts/range_csv_batch.py write-row \
    --batch-state <state_path> --sku <sku> \
    --row-json range/csv-output/<batch_id>_<sku>_row.json
```

Report `[i/N] <sku> — filled C, unknown U, validator failures V`. If
`required_blank` is non-empty, flag it — usually an unresolved colour (add a
`brand_colour`/`sku_colour` entry), a missing image, or a REQUIRED spec gap. Fix
and re-gather. If the script errors, stop and tell Wayne.

**Also surface `dimensions_blank`** (from the same JSON): the shared physical-
dimension guard (`scripts/marketplace_dims.py`) lists any blank product or
shipping/package dimension/weight column that exists in this template — flagged
`[DIMENSION]` in the log too. These are mandatory for The Range + Amazon FBA /
Click & Collect and are flagged even when the template marks them OPTIONAL, so a
missing measurement can't regress silently. Fill from Shopify dimension
metafields / variant weight, or collect via `reports/product_dimensions_to_collect.xlsx`.

## Step 6 — Finalize + push

```
PYTHONPATH=. .venv/bin/python scripts/range_csv_batch.py finalize \
    --batch-state <state_path>
```

Report processed/missing counts + stats + paths. Then:

```
Review range/csv-output/<batch_id>.csv (fill REQUIRED gaps from the log), then:
  python scripts/mirakl_push_products.py --operator THERANGE \
      --file range/csv-output/<batch_id>.csv --dry-run     # preview
  python scripts/mirakl_push_products.py --operator THERANGE \
      --file range/csv-output/<batch_id>.csv               # submit + read report
```

Don't push automatically — pushing is an explicit, outward action Wayne triggers
after reviewing the CSV.

---

## Capturing gaps / fixes (the config)

- `config/range_overrides.json` — per-product knobs: `brand_colour`/`sku_colour`
  + `colour_hex` (the colour trio), `defaults` (catalogue constants), `gpsr`
  (economic-operator block — REQUIRED for compliance; fill from the real
  registered details, never guess an address), `exclude_skus`.
- `config/range_category_map.json` — Shopify-category → Range-template routing.
  Add an entry when `suggest-template` reports an unmatched Shopify category that
  *does* have a Range template.

## Failure modes — handle directly, never silently

- **No / duplicate templates** → re-download from the Range portal.
- **A SKU isn't on Shopify (even by EAN)** → tell Wayne; don't write a row.
- **Unmatched Shopify category with no Range template** → out of scope until the
  template is downloaded; list it for Wayne, don't force it.
- **Unresolved colour / `required_blank`** → add the override, re-gather.
- **Image re-host fails** → `main_image` is REQUIRED; investigate before push.

## Persistence and audit trail

Per batch in `range/csv-output/`: `.state_<id>.json`, `<id>.csv` (deliverable),
`<id>.log.md` (gaps), `<id>_<sku>_context.json` + `_row.json` (forensics). The
committed long-lived artefacts are the templates (`range/templates/*.xlsx`),
`config/range_overrides.json`, and `config/range_category_map.json`. See
[[therange_mirakl_notes]] and [[mirakl_template_is_source_of_truth]].
