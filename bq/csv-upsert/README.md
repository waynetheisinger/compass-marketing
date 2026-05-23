# bq-csv-upsert/

Drop B&Q catalogue exports here when running `/bq-csv-batch` on SKUs that
already exist on B&Q. The skill uses these as a **safety net**: for any
active column that the AI would otherwise leave blank, the script looks up
the existing B&Q value here and copies it forward so the upsert does not
risk clearing data.

## How it's used

- `gather` adds an `existing_b_and_q_values` block to each SKU's context
  JSON, scoped to the columns active in the current subcategory. The AI
  sees this so it knows what is already on B&Q.
- `write-row` auto-restores any active column the AI marked UNKNOWN or
  omitted from `filled`, if the upsert file has a value for it. Restored
  values are re-validated; failures are dropped and logged so we never
  re-assert invalid data.
- The per-SKU log gets a `### Restored from upsert` section listing every
  cell that came from this folder, tagged `[RESTORED]`.

The AI still overrides this folder when it has a better value — just fill
the cell normally. The safety net only kicks in for cells you would
otherwise leave blank.

## File format

Two shapes are accepted:

1. **CSV** — first row is the header, columns named with B&Q api codes
   (`shop_sku`, `ean`, `name`, `Body Copy`, `Acquisition brand`, etc.).
2. **XLSX (Mirakl shape)** — if the workbook has a `Data` sheet, row 1
   (0-indexed) is treated as the api-code header and row 2+ is data. This
   matches the format of Mirakl product upload templates and exports.
3. **XLSX (flat shape)** — workbooks without a `Data` sheet use row 0 as
   the header.

Matching to a batch SKU is **EAN-first, shop_sku-fallback** (consistent
with `BQ_QUIRKS.md`: B&Q's catalogue lookup only resolves by EAN, not by
shop SKU). The script reports the match kind and source filename in the
gather output and the log.

Empty cells in the upsert file are dropped — they are not treated as
"intentional blanks". Only populated cells are surfaced as existing
values.

## What to put here

- Mirakl product exports filtered to your seller (`P13 Export all products`
  via API, or the portal's "Export" button on the product inventory page).
- The file does **not** need to be filtered to the SKUs in your batch —
  the script reads everything and looks up by SKU/EAN per row.
- Multiple files are merged: a later file's values overwrite an earlier
  file's for the same SKU. Sort filenames so the freshest is last.

## What NOT to put here

- The original Mirakl import templates (those live in `bq_templates/`).
- Files starting with `.`, ending in `.md`/`.txt`/`.log` — these are
  skipped (so this README is safely ignored).
- Multiple subcategories worth of products — that's fine and supported.
  The script only uses columns active in the current batch's subcategory.
