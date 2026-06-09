---
name: tesco-csv-batch
description: Generate a Tesco-Mirakl-importable products CSV from MowDirect's catalogue and push it. Sources products from B&Q's active offers (the live Tesco expansion set), auto-resolves each to its Shopify-taxonomy category, reuses the Shopify copy (cross-retailer scrubbed), re-hosts non-JPEG images to JPEG (Tesco accepts JPEG only), applies the config/tesco_overrides.json decisions, validates, writes a CSV + gaps report, then submits via the push script and reads Tesco's transformation report. Reactively invokes /tesco-shopify-shape to capture gaps/fixes.
---

# /tesco-csv-batch — Generate & push a Tesco import CSV

Produces `workdir/mirakl-tesco/bq_active_to_tesco_products.csv` (the file you
submit to Tesco) plus `bq_active_to_tesco_REPORT.md` (held-back products,
baseColour gaps, scrub hits). Then submits it and reports Tesco's per-row result.

Unlike `/bq-csv-batch`, almost everything is **deterministic and automated** —
Tesco's category system is the Shopify taxonomy and its schema is API-discoverable,
so there is no per-SKU AI column-filling. Your job (Claude) is to **orchestrate,
review the gaps, route fixes to `/tesco-shopify-shape`, and iterate on Tesco's
transformation report** — not to hand-author rows.

## What this skill does (and doesn't)

Does:

- **Sources products from B&Q active offers** (the agreed Tesco expansion set —
  we list on Tesco what's live on B&Q). Scope a subset with `--eans`/`--skus`.
- Resolves each product's **Tesco category from its Shopify taxonomy gid** (no
  guessing); copy + images come from Shopify, matched by EAN then SKU.
- Runs all copy through the **Tesco cross-retailer compliance scrub**
  (B&Q/competitor/own-store names, promo phrasing, URLs/phones — logged).
- **Re-hosts non-JPEG images to JPEG** — Tesco accepts JPEG only. PNG/WebP are
  fetched, converted (Pillow, white-flatten), uploaded to Shopify Files as real
  `.jpg`, and cached (`image_jpg_cache.json`). Storefront images untouched.
- Applies `config/tesco_overrides.json` (baseColour, leaf-category remaps,
  exclusions, defaults, attribute extras) — maintained by `/tesco-shopify-shape`.
- **Holds back** products with no sellable Tesco category, no Shopify match, or
  an unregistered brand — listed in the report, never silently dropped.
- Submits the CSV (`--no-offers`, products only) and prints Tesco's
  **transformation report** (the real validator).

Doesn't:

- Create **offers** (price/stock). Products aren't sellable until offers are
  pushed — that's a separate, deliberate step (parity pricing).
- List products that aren't B&Q active offers (the current product source).
- Touch storefront product images.

## Identifier invariants

- Output is fixed-path (overwritten each run): `workdir/mirakl-tesco/`.
- Products are keyed by **EAN** (Kingfisher offer ↔ Shopify barcode ↔ Tesco).
- Tesco channel code comes from `MIRAKL_TESCO_CHANNEL` in `.env`.

---

## Step 1 — Preflight connectivity

```
.venv/bin/python scripts/mirakl_connectivity.py --operator TESCO
```

Exit 0 = reachable + authenticated. If it prints missing-credentials guidance,
stop and tell Wayne to add `MIRAKL_TESCO_BASE_URL` / `MIRAKL_TESCO_API_KEY`
(and `MIRAKL_TESCO_CHANNEL` for submission) to `.env`.

## Step 2 — Scope the batch

Ask Wayne:

```
Which products? Options:
  all            — every B&Q active offer (the full Tesco expansion set)
  eans <list>    — only these barcodes (comma/space-separated)
  skus <list>    — only these B&Q shop_skus
  > _
```

## Step 3 — Generate the CSV

```
# all:
.venv/bin/python scripts/mirakl_bq_to_tesco.py
# subset:
.venv/bin/python scripts/mirakl_bq_to_tesco.py --eans "<e1,e2,…>"
.venv/bin/python scripts/mirakl_bq_to_tesco.py --skus "<s1,s2,…>"
```

This pulls offers, resolves categories, re-hosts images to JPEG (first run for
new images is slow — uploads to Shopify; cached after), scrubs copy, applies the
overrides config, and writes the CSV + report. Read the tail: `N in CSV, M held
back, K need baseColour`.

## Step 4 — Review the report + close gaps

Read `workdir/mirakl-tesco/bq_active_to_tesco_REPORT.md`. Surface to Wayne:

- **Held back** — products with reasons (unsellable category, no Shopify match,
  brand unregistered). For each fixable one, route to `/tesco-shopify-shape`:
  - *non-leaf / wrong category* → `category <SKU> <leaf>` (resolve-category finds it)
  - *unsellable category* → recategorise in Shopify, or `exclude <SKU>`
  - *brand unregistered* → can't list until Tesco registers the brand; `exclude`
- **baseColour blanks** (non-Spectrum SKUs) → `/tesco-shopify-shape` `colour <SKU> <Colour>`.
- **Scrub hits** — confirm nothing meaningful was removed from the copy.

After any override change, **re-run step 3** (cached images make it fast) so the
CSV reflects the fixes. Hand the CSV to Wayne to eyeball before pushing.

## Step 5 — Push (products only) + read the transformation report

```
# dry-run first to sanity-check rows/columns/blanks:
.venv/bin/python scripts/mirakl_push_products.py --operator TESCO \
    --file workdir/mirakl-tesco/bq_active_to_tesco_products.csv --dry-run
# then submit:
.venv/bin/python scripts/mirakl_push_products.py --operator TESCO \
    --file workdir/mirakl-tesco/bq_active_to_tesco_products.csv
```

Report Tesco's result: `read=N ok=X err=Y`. If `err>0`, read the printed
transformation report and act on the error codes:

- **1005 — "must be mapped to a leaf operator catalog category"** → Shopify gave
  a non-leaf gid. For each failing SKU, `/tesco-shopify-shape` `category <SKU>
  <leaf>` (use `resolve-category`), then regenerate (step 3) and re-push.
- **Missing required attribute** (e.g. `batterySize` on a battery category) →
  `/tesco-shopify-shape` `extra <attrCode> <SKU> <value>`, regenerate, re-push.
- **Image format invalid** → should not occur (images are re-hosted to JPEG).
  If it does, a source image failed conversion — check the report's rehost
  warnings; that product is held back with "no usable image".

Loop steps 3→5 until `err=0`.

## Step 6 — Confirm + hand off

```
Submitted to Tesco: import <id>, <N>/<N> accepted (products only).
Held back: <M> (see report).
Products are SENT — awaiting Tesco catalogue approval. NOT yet sellable.

Next: offers (parity pricing) — a separate push, on your go-ahead.
```

After Tesco integrates, products with non-JPEG sources should show valid images
in the seller portal (the re-host fix). If any show "invalid data", pull the
flagged barcodes and re-run scoped to them.

---

## Failure modes — handle directly, never silently

- **Connectivity fails (step 1)** → stop; credentials/`.env` issue.
- **Tesco 429 during generate** → the image-rehost path retries; transient.
  The generator no longer pre-validates against `/products/attributes` (that
  was the rate-limit source) — the push transformation report is the validator.
- **A product not found in Shopify by EAN/SKU** → held back ("no Shopify match");
  it needs its Shopify barcode fixed, or `exclude` it.
- **All of a product's images fail conversion** → held back ("no usable image");
  check the source images in Shopify.
- **`MIRAKL_TESCO_CHANNEL` unset** → product dry-run works, but the real push
  needs it. The push script blocks live submission without it.
- **Offers** are out of scope here — never push `/offers/imports` from this skill
  without explicit instruction; products-only keeps nothing sellable by mistake.

## Persistence and audit trail

In `workdir/mirakl-tesco/`:
- `bq_active_to_tesco_products.csv` — the deliverable (overwritten per run)
- `bq_active_to_tesco_REPORT.md` — held-back + gaps + scrub log
- `image_jpg_cache.json` — source-URL → re-hosted-.jpg map (keeps re-runs cheap)

Long-lived decisions live in `config/tesco_overrides.json` (via
`/tesco-shopify-shape`). Tooling + findings: [[tesco_mirakl_notes]],
`docs/MIRAKL_OPERATOR_ONBOARDING.md`. Work tracked in issue #11 / PR #20.
