# Kingfisher (B&Q) Mirakl quirks — validation crib

Per-operator quirks discovered iteratively during the SBS push (2026-05-07).
Apply these during fill, gate-2 review, and post-submission diagnosis. If
they save you a transformation round, they pay for themselves.

Source: project memory `mirakl_kingfisher_attribute_quirks`. Cross-reference
before adding new quirks here.

## Hard validation rules

### Per-field length caps

Kingfisher enforces strict character limits on several fields. The
`KINGFISHER_FIELD_LENGTH_CAPS` dict in `mirakl_operators.py` is the
canonical list; `prepare-gate2` warns inline when a value exceeds the
cap so Wayne can shorten before pasting.

Known caps:

| Portal label | Cap (chars) |
|---|---|
| `Name` | 130 |
| `Key Feature` | 30 |

Add new caps to `KINGFISHER_FIELD_LENGTH_CAPS` when discovered. `Name`
also runs through `clean_name()` for restricted-character replacement +
word-boundary truncation; other capped fields don't auto-truncate (cap is
informational only — manual rewrite is the right behaviour for prose).

### Restricted special characters in `name` (and probably other text fields)
Kingfisher rejects these characters in product names — likely also rejects
them in description fields:

| Reject | Replace with |
|---|---|
| `—` em-dash (U+2014) | ` - ` (space-hyphen-space) |
| `–` en-dash (U+2013) | `-` (hyphen) |
| `×` multiplication (U+00D7) | `x` (lowercase x) |
| `°` degree symbol (U+00B0) | (strip — or write "degrees") |
| `"` `"` smart quotes | `"` straight |
| `'` `'` smart apostrophe | `'` straight |
| `•` bullet | `*` |
| `…` ellipsis | `...` |

`_NAME_CHAR_REPLACEMENTS` in `mirakl_operators.py` is the canonical map.
Reuse it for description fields too.

### Value-list-backed attributes take the CODE, not the LABEL
This is the single most common source of transform errors. **Always submit
the code from the value list, never the human-readable label.**

Examples (Kingfisher-specific codes):

| Attribute | Label | Code (verified) |
|---|---|---|
| Acquisition brand | Spectrum | `4592` |
| Power_voltage_supply | 240V | `16` |
| Guarantee | 5 years | `81` (NOT `78` — 78 is 2 years) |
| Battery_chemistry (tool category) | Li-ion | `17` |
| Battery_chemistry (PIM_11486 battery) | Li-ion | `9` |
| Core_Pack quantity | 1 | `1` |
| Core_Pack type | Box | `3` |
| Cordless | Yes | `2` |
| Batteries_supplied (bare tool) | No / none in box | `5` |
| Batteries_supplied (kit) | Yes | `1` |
| WEEE_regulated | Yes | `2` |
| contains_wood | No | `2` |
| Tech_Rechargeable | Yes | `1` |
| reach_verified | Yes | "Yes" (this one is a string, not a code) |
| fsc_pecl_certified | No | "No" (also a string) |

If a value-list code isn't in this table, walk the `/values_lists` endpoint
for the hierarchy and read the codes back. The skill does this automatically
at step 4 (discovery).

### `Battery_chemistry` codes differ between tool and battery hierarchies
Same chemistry, different value-list domain:
- Tool categories (lawnmower, hedge trimmer, blower, charger): code `17`
- Battery category (PIM_11486): code `9`

When discovery says "Li-ion", check which domain you're in before picking.

### `Core_Product type` codes are per-hierarchy
The value list is `Core_Product_type_PIM_<hierarchy_code>`. Each list has
1-6 values.

Known codes (verified during SBS push):

| Hierarchy | Category | Code | Label |
|---|---|---|---|
| Lawnmower bare | PIM_13202 | `12396` | Lawnmower |
| Lawnmower kit | PIM_13201 | `12396` | Lawnmower |
| Blower-vac bare | PIM_12657 | `32125` | Garden blower & vacuum |
| Blower-vac kit | PIM_12656 | `32125` | Garden blower & vacuum |
| Hedge trimmer bare | PIM_12681 | `22611` (regular) / `33988` (pole) | Hedge trimmer / Pole hedge trimmer |
| Hedge trimmer kit | PIM_12680 | `22611` / `33988` | same split |
| Battery | PIM_11486 | `25791` | Battery |
| Charger | PIM_13946 | `27202` | Battery charger |

**Pole vs regular hedge trimmers share a hierarchy but have different
Core_Product type codes.** The SBS push handles this via `per_sku_overrides`
in `mirakl_operators.py`. New pole-style products in other hierarchies need
the same treatment.

### Banned phrases in prose fields
Mirakl emits error/warning 2031 (`The '<attr>' attribute cannot contain the
word(s): <phrase>`) on certain free-text fields when they contain phrases
B&Q has flagged as cross-promotional or off-brand. Strip these from `name`,
`Body Copy`, `Selling Copy`, `Unique Selling Point 01..08`, and any other
prose attribute before submission.

| Banned phrase | Rewrite as |
|---|---|
| `the range` (chargers, 2026-05-20) | `the pack` / `the lineup` / strip entirely |

Add new banned phrases here as transformation errors surface them.

## Unit and dimension rules

### `Product_length` / `Product_width` / `Product_height` are in MILLIMETRES
Verified 2026-05-07 — submitted 21cm rendered as "21.00 mm". Mirakl renders
the value as `123.45 mm` regardless of the unit you intended. Sending cm
silently produces a microscopic product.

`mirakl_operators.KINGFISHER.dimension_unit_multiplier = 10.0` handles this
in `build_product_row()`. If you build the CSV by hand, multiply cm × 10.

### `Product_weight` is correctly in kg
No multiplier. Submit as decimal kg.

## API behaviour quirks

### `/products/attributes` returns 404 on Kingfisher
No exposed schema-discovery endpoint. **The reliable discovery path is a
copy-paste of the seller-portal editable form text.** `/values_lists` only
surfaces value-list-backed enums (~8 of ~80 fields per hierarchy) so it is
a time-waster as a primary discovery mechanism.

The /enrich-bq skill uses:

1. **Portal text-dump paste — primary discovery.** First time hitting a
   hierarchy → bootstrap (Wayne pastes a value-free editable form;
   parser saves the label set to config/bq_operator_extensions.json).
   Subsequent products in same hierarchy → populated paste; parser
   cross-references labels against the saved set.
2. **`/values_lists` walk — secondary annotation.** After labels are known,
   walks `/values_lists` to attach code maps to value-list-backed fields
   (so we know `Cordless` = `2`, `Battery chemistry` = `17`, etc.).

### Portal display labels ≠ CSV column names
The mapping from a portal display label (e.g. `Manufacturer guarantee`) to
the API CSV column header (e.g. `Guarantee`) is **not** a derivable transform.
Some labels gain `Spec_` / `Tech_` / `Core_` / `BQ_` prefixes, some are
renamed entirely, some are truncated.

The mapping lives in `mirakl_operators.KINGFISHER_DISPLAY_TO_API`. Unknown
labels (api_name = None) can't be submitted via the CSV — they fall to the
clipboard fallback (manual portal entry).

### `/products` is a thin lookup, not a schema endpoint
Returns ONLY: `category_code`, `category_label`, `product_id`,
`product_id_type`, `product_sku`, `product_title`. No attributes, no images,
no specs. All `expand` / `include` / `attributes` query params are ignored.

Practical impact: **we cannot read a product's current attribute values
from the API.** The skill cannot diff against what's in B&Q's catalogue
before submission. That's why gate 2 has the portal-edit-clobber warning.

### `/products` lookup ONLY works by EAN, not SHOP_SKU
`product_references=EAN|<ean>` returns the product;
`product_references=SHOP_SKU|<sku>` returns 0 even for live products.
EAN is the canonical reference type for inventory matching.

### `/products/export` is permission-gated
403 → 429 chain even for sellers with `/products/imports` access. Not
reliably available for schema discovery.

## Submission-timing quirks

### Imports stay in `SENT` status for hours/days
While B&Q's catalogue team integrates accepted rows. **Don't poll for
`COMPLETE`** — you'll wait forever.

**Break-early condition:**
```python
status = client.get(f"/products/imports/{import_id}")
if status["transform_lines_read"] > 0 and \
   status["lines_read"] == status["lines_ok"] + status["lines_in_error"]:
    break
```

This says "transformation is done; we can read the error report now". The
catalogue integration phase is opaque from the API.

### Transformation error report format depends on submission channel
- API-submitted import → error report returned as **CSV**
- Portal-submitted import → error report returned as **XML**

The skill submits via API, so expect CSV. `csv.DictReader` on the response
body.

## Recent verified state (as of 2026-05-07)

- 15 SBS SKUs submitted; transformation clean (0 errors) on import 1471842.
- All 15 in `SENT` status, awaiting catalogue integration.
- 7 hierarchies populated in `mirakl_operators.KINGFISHER`.
- 2 prior portal-submitted SKUs (SBS240CPHT, SBS40CB) also `SENT`.

## How to add new quirks

When you discover a new quirk during an `/enrich-bq` run:

1. Save the transform error / portal screenshot to `bq/enriched/quirk_<date>_<sku>.json`.
2. Add a row to this file under the appropriate section.
3. If it's a value-list code, also update the codes table.
4. Commit the change. The next run benefits immediately.
