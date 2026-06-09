---
name: enrich-tesco
description: Enrich ONE MowDirect product for Tesco Marketplace with a distinct-voice rewrite. The Tesco analogue of /enrich-bq — for a single product done carefully, where the prose is rewritten (not reused verbatim) so the Tesco listing doesn't duplicate mowdirect.co.uk or leak the MowDirect identity. Resolves a Shopify SKU (or a mowdirect/compass URL), auto-resolves the Shopify-taxonomy category, re-hosts images to JPEG, maps optional spec fields, and emits BOTH a push-ready 1-row CSV and a portal-fill markdown doc. Use for hero/important products, or any product where duplicate-content with our own site matters.
---

# /enrich-tesco — Carefully list one product on Tesco (voice-rewritten)

Sibling to `/tesco-csv-batch` (bulk, copy reused verbatim + name-scrubbed) and
`/enrich-bq` (single product, B&Q portal-fill). This skill is the **single-
product, voice-rewritten** path for Tesco: it produces fresh prose so a Tesco
listing is distinct from mowdirect.co.uk and carries no MowDirect identity —
then emits both a push-ready CSV and a portal-fill doc.

**Why rewrite at all?** `/tesco-csv-batch` reuses Shopify copy verbatim (name-
scrubbed). That's fine for bulk, but the source IS mowdirect.co.uk's copy, so
across many listings it duplicates our own site (search engines de-duplicate;
Tesco's domain authority can demote ours) and reads in MowDirect's voice. For
products that matter, rewrite.

The script `scripts/tesco_enrich.py` does the deterministic plumbing (base-row
build, image re-host, optional-field mapping, scrub, portal-doc render).
**You** (Claude) do the voice rewrite per the shared voice bible.

## Voice — the shared bible

Read `.claude/skills/enrich-bq/BRAND_VOICE.md` (it's the single source of truth,
written for any surface). Apply its **Core voice**, **Duplicate-content
discipline**, **Banned moves**, and **Field-specific style** sections. Tesco
specifics on top:

- The source surface to differ from is **mowdirect.co.uk** (the Shopify copy).
  Skim it, then write fresh — different lead, structure, verbs; same facts.
- **Never name** MowDirect, Compass, Tesco, Mirakl, any retailer, price,
  delivery, or warranty-as-a-service. (The deterministic scrub is a backstop,
  not a licence to be sloppy — write clean.)
- British English, benefit-led, specifics over adjectives (see the bible).

## What this skill does (and doesn't)

Does: resolve one product → build its base Tesco row (category from Shopify
taxonomy, JPEG-re-hosted images, optional spec fields mapped via
`config/tesco_overrides.json`) → you rewrite the prose → emit a 1-row CSV +
a portal-fill markdown doc. Optionally push the CSV.

Doesn't: rewrite factual fields (specs, dimensions, EAN — those are mapped/
auto-filled, not prose); bulk multiple products (use `/tesco-csv-batch`);
submit without review.

---

## Step 1 — Gather

Entry is a **Shopify SKU**. (If given a mowdirect.co.uk / compassgm.co.uk URL,
resolve it to the SKU first — e.g. via the product handle / `lookups/` — then
proceed.)

```
.venv/bin/python scripts/tesco_enrich.py gather --sku <SKU>
```

This builds the base row and writes `workdir/mirakl-tesco/enrich/<SKU>_context.json`.
Read it in full. If it returns an `error` (held back / not in Shopify), stop and
tell Wayne why (unsellable category, brand unregistered, no Shopify match) —
those are upstream fixes, not rewrites.

The context has:
- `category` — the resolved Tesco (Shopify-taxonomy) gid.
- `base_row` — every field the pipeline already filled (category, images,
  brand, EAN, mapped spec fields, constants).
- `source` — the **raw** Shopify copy to rewrite FROM: `title`, `body_text`,
  `bullets` (the feature-bullet list).
- `rewrite_fields` — the prose fields you own: `description` (title),
  `marketingText` (body), `productFeatures1..3` (bulletpoints), `whatIsInBox`.

Also read `.claude/skills/enrich-bq/BRAND_VOICE.md` once.

## Step 2 — Rewrite the prose

Write fresh copy for the `rewrite_fields`, following the voice bible:

- **description** (the Tesco "Title"): brand + model + key descriptor; keep it
  accurate. Usually the Shopify title is fine — tidy it, don't pad.
- **marketingText** (the body): 2-3 short paragraphs; lead on the job the tool
  does with the lead spec; different lead/structure/verbs from the Shopify body.
- **productFeatures1..3**: 3 scannable benefit bullets, each anchored to a spec,
  ≤ 12 words, rewritten from (not copied from) the source bullets.
- **whatIsInBox** (if the category has it and the source supports it): the
  contents, plainly.

Hard rules: same product facts (cutting width, voltage, capacity stay correct);
no MowDirect/Compass/retailer/price/delivery references.

Write the rewrite to a JSON file, e.g. `workdir/mirakl-tesco/enrich/<SKU>_rewrite.json`:

```json
{
  "description": "Spectrum DCT38M-SDM 38\" Ride-On Lawn Tractor with Manual Drive",
  "marketingText": "Cut large lawns from the seat … (2-3 paragraphs, fresh voice)",
  "productFeatures1": "38-inch twin-blade deck clears wide passes in fewer laps",
  "productFeatures2": "Seven cutting heights set from one lever",
  "productFeatures3": "Bag, mulch or side-discharge from the seat",
  "whatIsInBox": "Tractor, grass collector, ignition key, manual",
  "attrs": { "lawnMowerModelNumber": "DCT38M-SDM" }
}
```

`attrs` is optional — fill any category-specific spec field the mapping didn't
cover (check the context's `base_row` for blanks; `/tesco-shopify-shape`
`attrs <gid>` lists what the category wants).

## Step 3 — Apply

```
.venv/bin/python scripts/tesco_enrich.py apply --sku <SKU> \
    --row-json workdir/mirakl-tesco/enrich/<SKU>_rewrite.json
```

This overlays the rewrite (scrubbing as a backstop), then writes:
- `workdir/mirakl-tesco/enrich/<SKU>.csv` — push-ready 1-row CSV
- `workdir/mirakl-tesco/enrich/<SKU>_portal.md` — Tesco portal labels → values
  for manual entry/review

Report `scrub_hits` to Wayne if non-empty — it means your rewrite still named
something it shouldn't have (fix the rewrite and re-apply; don't rely on the
backstop).

## Step 4 — Review + (optionally) push

Hand Wayne the portal doc to eyeball. To go live (products only; not sellable
until offers):

```
.venv/bin/python scripts/mirakl_push_products.py --operator TESCO \
    --file workdir/mirakl-tesco/enrich/<SKU>.csv
```

Read the transformation report; act on errors per `/tesco-csv-batch`
(1005 leaf remap, missing required attr, etc.).

---

## Failure modes — handle directly

- **`gather` returns error / no base row** → held back (unsellable category,
  brand unregistered, no Shopify match) or not on Shopify. Upstream fix via
  `/tesco-shopify-shape` (category remap / exclude) — not a rewrite.
- **`scrub_hits` non-empty after apply** → the rewrite named a retailer / our
  own store / promo term. Rewrite cleaner; re-apply.
- **Image rehost slow** → first run uploads JPEGs to Shopify Files; cached after.
- **`gather` overwrites the shared working CSV** (`bq_active_to_tesco_products.csv`)
  while building the base row. Don't run this mid-batch; enrich's own deliverables
  live under `workdir/mirakl-tesco/enrich/`.

## Persistence

Per SKU in `workdir/mirakl-tesco/enrich/`: `<SKU>_context.json` (gather),
`<SKU>_rewrite.json` (your copy), `<SKU>.csv` + `<SKU>_portal.md` (deliverables).
Voice bible: `.claude/skills/enrich-bq/BRAND_VOICE.md`. Context:
[[tesco_mirakl_notes]], `docs/MIRAKL_OPERATOR_ONBOARDING.md`. Issue #11 / PR #20.
