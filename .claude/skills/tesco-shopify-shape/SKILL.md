---
name: tesco-shopify-shape
description: Maintain the Tesco listing overrides config (config/tesco_overrides.json) — per-brand/per-SKU baseColour, country/VAT defaults, leaf-category remaps for products Shopify tagged with a non-leaf taxonomy node, SKU exclusions, Tesco-unsellable categories, and category-specific attribute values. Reads the live Tesco attribute schema to show which required fields a category needs and whether each is already covered. Standalone, or invoked reactively from /tesco-csv-batch when a run surfaces gaps or Tesco rejects a row.
---

# /tesco-shopify-shape — Shape products for Tesco Mirakl

This skill maintains `config/tesco_overrides.json`, the single source of truth
for the per-product decisions that turn MowDirect's Shopify catalogue into
valid Tesco listings. Sibling skill `/tesco-csv-batch` reads this config when it
generates the import CSV.

Tesco is unlike B&Q ([[mirakl_kingfisher_attribute_quirks]] / `/bq-shopify-shape`):
its category system **is** the Shopify product taxonomy and its attribute schema
is **API-discoverable**. The *required* fields auto-fill, but each category also
exposes **dozens of optional spec fields** (Cutting Width, Grass Box Capacity,
Voltage, dimensions, Bulletpoints, What's Included, Power Source, Warranty…) that
make a listing rich and rank well. This skill's **main job** is mapping Shopify
data onto those fields (`attribute_mappings`) — plus the per-product override
knobs. Don't settle for required-only; that's a bare listing.

The Python script `scripts/tesco_shopify_shape.py` is pure config I/O plus a
couple of live Tesco reads (attribute schema, taxonomy search). **You** (Claude)
drive the conversation and call its subcommands.

## What auto-fills (never needs this skill)

`shopifyHierarchyId` (the product's Shopify taxonomy category), `sku`,
`barcode` (EAN), `description` (title), `marketingText` (body, scrubbed),
`brand` (B&Q offer brand / Shopify vendor), and `image1..N` (re-hosted JPEGs).
`/tesco-csv-batch` does all of this deterministically.

## What this skill decides (the override config)

| Section | What it is | When you need it |
|---|---|---|
| `defaults` | constant value-list fields — `countryOfOriginName`, `ageRestriction`, `unitQuantity`, `vatRate` | rarely; the seeded values are catalogue-wide |
| `brand_colour` / `sku_colour` | `baseColour` (Tesco-required, varies per product) — by brand, or per-SKU override | a non-Spectrum brand, or any product whose livery isn't the brand default |
| `sku_category` | leaf-category gid override | Shopify tagged a product with a **non-leaf** taxonomy node → Tesco error 1005 |
| `exclude_skus` | SKU → reason held back | product you don't want on Tesco, or one with no sellable Tesco category |
| `unsellable_gids` | Shopify taxonomy nodes Tesco doesn't open to sellers | a new category 404s on `/products/attributes` |
| **`attribute_mappings`** | **Shopify-source → Tesco optional-field mappings** `{<gid|'*'>: {attrCode: {source, transform}}}` | **the main job — fill spec fields (Cutting Width, dimensions, Bulletpoints, …) from Shopify metafields/specs/weight** |
| `attribute_extras` | constant/per-SKU attribute values `{attrCode: {SKU_or_'*': value}}` | a value that isn't in Shopify (e.g. a hand-entered `batterySize`) |

## Identifier invariants

- **Categories are Shopify-taxonomy gids:** `gid://shopify/TaxonomyCategory/<code>`.
  The script accepts a bare code (`hg-12-3-4-1`) or a full gid; it normalises.
- **Only LEAF categories are valid on Tesco** — a non-leaf gid is rejected with
  error 1005. Use `resolve-category` to find the right leaf.
- **Value-list codes == labels on Tesco** (e.g. brand `Spectrum`, colour `Green`),
  unlike Kingfisher's numeric codes.

---

## Step 1 — Status

```
.venv/bin/python scripts/tesco_shopify_shape.py status
```

Read the JSON, report one line:

```
Overrides: <N> sku-colours, <M> category remaps, <E> excluded, <U> unsellable cats.
Defaults: country=<…> vat=<…>
```

## Step 2 — The targeted loop

This skill is **targeted** — never auto-walk. Ask Wayne what to do:

```
What do you want to set? Options:

  attrs <category gid|query>      — show a category's attrs + coverage (start here:
                                     it reveals which optional spec fields are
                                     still UNCOVERED and worth mapping)
  sources <SKU>                   — dump a product's mappable Shopify data
                                     (metafields, display_attributes, weight)
  map <attrCode> <source> [transform] [--cat <gid>]
                                  — map a Shopify source onto a Tesco attribute
                                     (the main job; see forms below)
  colour <brand|SKU> <Colour>     — baseColour. Tesco colours: Black, Blue, Bronze,
                                     Brown, Chrome, Clear, Copper, Cream, Gold,
                                     Green, Grey, Multi, Orange, Pink, Purple, Red,
                                     Silver, White, Yellow
  category <SKU> <query|gid>      — leaf-category override (I'll search the taxonomy)
  exclude <SKU> [reason]          — hold a product back (or 'exclude <SKU> remove')
  default <key> <value>           — change a catalogue-wide constant
  extra <attrCode> <SKU|*> <value>— set a constant/per-SKU attribute value
  unsellable <gid> [remove]       — mark/unmark a Tesco-unsellable category
  quit                            — exit

  > _
```

Handle replies by calling the matching subcommand, then loop back.

### The mapping workflow (do this for each category you list into)

1. `attrs <gid>` → read `required_uncovered` AND scan the full list for optional
   spec fields worth filling (Cutting Width, Grass Box Capacity, dimensions,
   Bulletpoints, What's Included, Power Source, Warranty, Voltage, …). Each
   shows `coverage: auto|default|mapped|extra|UNCOVERED` and its `code`.
2. `sources <SKU>` (a representative product in that category) → see what Shopify
   offers: `metafields` (e.g. `filter.cutting_width`, `custom.feature_bullets`),
   `display_attributes` (e.g. `engine_capacity`), and `weight`.
3. `map <tescoCode> <source> [transform] --cat <gid>` for each field you can
   fill. **Map lawn-mower/category-specific codes (`lawnMower*`) under `--cat`,
   never globally** — they'd be rejected on categories that don't have them.
   Generic codes present on all your categories (e.g. `productFeatures1..3`,
   `width`/`height`/`depth`/`grossWeight`) can go global (omit `--cat`).

### map / sources
```
.venv/bin/python scripts/tesco_shopify_shape.py shopify-sources --sku <SKU>
.venv/bin/python scripts/tesco_shopify_shape.py set-mapping --category <gid> \
    --attr lawnMowerCuttingWidth --source metafield:filter.cutting_width --transform dim_value
.venv/bin/python scripts/tesco_shopify_shape.py set-mapping \
    --attr productFeatures1 --source metafield:custom.feature_bullets --transform bullet:1
.venv/bin/python scripts/tesco_shopify_shape.py del-mapping --category <gid> --attr <code>
```
**Source forms:** `metafield:<ns.key>`, `display_attribute:<code>`,
`field:title|vendor`, `weight`, `const:<value>`.
**Transforms:** `dim_value` / `dim_unit` (for `{value,unit}` dimension metafields),
`weight_value` / `weight_unit`, `list_first` / `list_join` (for JSON list
metafields), `strip_html`, `bullet:N` (Nth `<li>` of a feature-bullets field),
or omit for the raw value.
**UOM fields** (Product Weight UOM, Dimensions UOM, Voltage UOM) are value-list
backed — set them with `extra`/`map const:` to the exact Tesco code (check the
value list via `attrs`); the `*_unit` transforms return Shopify's raw unit which
may not match Tesco's value list.

### colour
```
.venv/bin/python scripts/tesco_shopify_shape.py set-colour --brand "Mountfield" --colour Orange
.venv/bin/python scripts/tesco_shopify_shape.py set-colour --sku C-XYZ --colour Red
```
Validate the colour is in the allowed list above before saving; if not, tell
Wayne the valid set and re-ask.

### category
If Wayne gives a search word, find the leaf first:
```
.venv/bin/python scripts/tesco_shopify_shape.py resolve-category "<word>"
```
Show the matches; **only `leaf:true` entries are valid**. Confirm the gid, then:
```
.venv/bin/python scripts/tesco_shopify_shape.py set-category --sku <SKU> --gid <gid>
```

### exclude
```
.venv/bin/python scripts/tesco_shopify_shape.py exclude --sku <SKU> --reason "<why>"
.venv/bin/python scripts/tesco_shopify_shape.py exclude --sku <SKU> --remove
```

### default
```
.venv/bin/python scripts/tesco_shopify_shape.py set-default --key vatRate --value 20
```

### extra
```
.venv/bin/python scripts/tesco_shopify_shape.py set-extra --attr batterySize --sku SBS40CB --value "40V 4.0Ah"
.venv/bin/python scripts/tesco_shopify_shape.py set-extra --attr <code> --sku '*' --value "<applies to all>"
.venv/bin/python scripts/tesco_shopify_shape.py set-extra --attr <code> --sku <SKU> --remove
```

### attrs (inspect a category's schema + coverage)
```
.venv/bin/python scripts/tesco_shopify_shape.py list-attributes --category <gid-or-code>
```
Report `required_uncovered` prominently — those are the fields that will be
blank on the CSV for products in that category, i.e. what Wayne needs to fill
via `extra` (or that need a Shopify metafield). Everything marked `auto` /
`default` / `extra` is handled.

### unsellable
```
.venv/bin/python scripts/tesco_shopify_shape.py set-unsellable --gid <gid>
.venv/bin/python scripts/tesco_shopify_shape.py set-unsellable --gid <gid> --remove
```

### quit
Run `status` once more, report final counts, exit.

---

## Invocation contexts

**Standalone** (`/tesco-shopify-shape` typed by Wayne): run steps 1 → 2.

**Reactive from `/tesco-csv-batch`:** when a generate run surfaces gaps (a
non-Spectrum SKU with no colour, a product whose Shopify category is non-leaf,
a new unsellable 404) or a push returns transformation errors, this skill is
invoked to capture the fix. Resolve the specific items, then control returns to
`/tesco-csv-batch`, which regenerates and re-pushes.

## Failure modes — handle directly

- **`config/tesco_overrides.json` missing** → it's committed; if absent, restore
  from git (`git checkout config/tesco_overrides.json`). Don't recreate by hand.
- **`list-attributes` / `resolve-category` fails** → needs `MIRAKL_TESCO_*` in
  `.env`; run `scripts/mirakl_connectivity.py --operator TESCO` to diagnose.
  Tesco rate-limits `/products/attributes` (429) — retry after a pause.
- **A colour Wayne types isn't in Tesco's value list** → reject, show the valid
  set, re-ask. Don't save an invalid colour (Tesco will reject the row).
- **`set-category` to a non-leaf gid** → warn (Tesco error 1005); confirm via
  `resolve-category` that the gid is `leaf:true` before saving.

## Persistence and audit trail

Single file: `config/tesco_overrides.json`, git-committed — every decision is
reviewable via `git log` / `git diff`. No separate audit log.
See [[tesco_mirakl_notes]] and `docs/MIRAKL_OPERATOR_ONBOARDING.md` for context.
