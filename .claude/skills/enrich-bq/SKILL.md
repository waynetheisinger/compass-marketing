---
name: enrich-bq
description: Produce a portal-fill document for one B&Q (Kingfisher Mirakl) product. Resolves a compassgm.co.uk URL to a MowDirect SKU + EAN, looks up the B&Q hierarchy, parses the seller-portal editable-form text-dump, rewrites prose in MowDirect voice (distinct from compass AND mowdirect.co.uk), and emits a single markdown file Wayne uses to copy-paste values into the B&Q seller portal. No API submission — the file IS the deliverable.
---

# /enrich-bq — Produce a B&Q portal-fill document for one product

Single compass URL per invocation. Produces one markdown file that Wayne
copy-pastes into the B&Q seller portal.

The compass URL: `$ARGUMENTS`

## What this skill does (and doesn't)

Does:

- Resolves the compass URL to a MowDirect Shopify SKU + EAN
- Looks up B&Q (Mirakl) for context (live / not-yet-listed)
- Parses your seller-portal editable-form text-dump to discover the field
  set for the hierarchy
- Rewrites prose fields in MowDirect voice, distinct from compass *and*
  mowdirect.co.uk
- Emits a single markdown file with every field's proposed value, ready
  for sequential paste into the seller portal

Doesn't:

- Submit to `/products/imports` (the Mirakl API covers only ~30% of the
  fields B&Q's portal accepts; doing partial API + partial portal was
  more friction than just filling the portal sequentially)
- Run a clipboard loop
- Modify the live B&Q listing in any way

The output file is the final state. Wayne reviews it, then copies each
section's value into the corresponding portal field at his own pace.

## Identifier invariant — EAN is canonical

When two systems disagree on a product's SKU, **the EAN decides which one is
right**. SKUs drift; EANs are manufacturer-assigned and don't.

- Compass provides `sku` and `barcode` (EAN). Shopify variants also have
  both. Mirakl looks up products by EAN, not SKU.
- If compass SKU and Shopify SKU match AND barcodes match → fine.
- If SKUs match but barcodes differ → **distrust the SKU match.**
- If barcodes match but SKUs differ → trust the barcode match; use the
  Shopify SKU as the canonical identifier; log the drift.
- If neither barcode nor SKU matches anything on Shopify → hard fail.
  Data hygiene problem to fix upstream.

## Discovery invariant — text-dump from the seller portal

The Kingfisher API does not expose a usable attribute schema (`/products/
attributes` returns 404; `/products` returns 6 thin columns; `/values_lists`
only surfaces value-list-backed enums, ~8 of ~80 fields per hierarchy). The
reliable discovery path is **copy-paste of the editable-form text from the
seller portal**:

- **First time hitting a hierarchy → bootstrap.** Wayne pastes a value-free
  editable form from an empty draft product. Parser saves the label set to
  `config/bq_operator_extensions.json`. **Bootstrap paste MUST come from an
  empty draft.** Reject populated pastes.
- **Subsequent products in the same hierarchy → populated paste.** Wayne
  pastes this product's editable form. Parser cross-references labels
  against the saved set; everything that matches is a label, everything
  else is that label's value.

Portal display labels are what Wayne sees in the seller portal — these are
also what he selects from dropdowns and types into the portal. The output
file uses portal labels as headings. (CSV column names from `mirakl_
operators.KINGFISHER_DISPLAY_TO_API` are retained for the parser but don't
appear in the output — they're API-only and we don't submit.)

## Before you start — read these once

1. `.claude/skills/enrich-bq/BRAND_VOICE.md` — MowDirect voice + duplicate-content rules + banned moves. **Follow during the rewrite.**
2. `.claude/skills/enrich-bq/BQ_QUIRKS.md` — Kingfisher validation crib (130-char title cap, restricted chars). **Apply at gate-2 review.**
3. The relevant exemplar in `EXEMPLARS/<product-type>.md`. **Lift voice, not phrasing.**

## Resume check

Before doing any work, look for `bq/enriched/<sku>_*_gate2.md` from the
last 24 h. If found, ask: *"Resume from <timestamp>? [Y/n]"*. Default yes.

---

## Step 1 — Resolve compass URL to canonical SKU + EAN

```
PYTHONPATH=. .venv/bin/python scripts/bq_enrich.py resolve "$ARGUMENTS"
```

Applies the EAN-canonical resolution chain. Hard-fails if neither SKU nor
EAN is on MowDirect Shopify. Writes `bq/enriched/<sku>_<ts>_resolved.json`.
Mention any drift to Wayne but proceed.

## Step 2 — Look up Mirakl (informational only)

```
PYTHONPATH=. .venv/bin/python scripts/bq_enrich.py mirakl-lookup <sku>
```

Prints "Product is LIVE…" or "Product NOT YET in B&Q catalogue". Either
path proceeds — the skill produces the same document either way; Wayne
decides whether he's creating a new listing or updating an existing one
when he uses the file in the portal.

## Step 3 — Check hierarchy bootstrap status

```
PYTHONPATH=. .venv/bin/python scripts/bq_enrich.py hierarchy-status <PIM_code>
```

Returns one of:

- **MAPPED+LABELS** — proceed to step 5 (populated paste)
- **MAPPED+NO_LABELS** — proceed to step 4 (bootstrap labels)
- **UNMAPPED** — proceed to step 4 (bootstrap + map)

## Step 4 — Bootstrap hierarchy labels (only if needed)

**Hard rule: the bootstrap paste MUST come from an empty draft product.**
Never accept a populated product's form for bootstrap. If Wayne pastes a
form with values, refuse and re-ask.

Tell Wayne:

```
This hierarchy needs a one-time label bootstrap. The bootstrap paste must
come from an EMPTY DRAFT product (so every line is a field label, no values
to confuse the parser).

  1. In the B&Q seller portal, create a new DRAFT product in hierarchy
     <PIM_code>. Don't fill anything in.
  2. Switch to EDIT mode (form view).
  3. Cmd-A inside the right pane and copy.
  4. Paste inline in your next message.

If this hierarchy isn't in mirakl_operators.py either (UNMAPPED), also
provide:
  - internal product_type label (ALL_CAPS_WITH_UNDERSCORES, e.g.
    "CORDLESS_DRILL_BARE")
```

Save the paste to `bq/enriched/.bootstrap_<pim>.txt` and run:

```
PYTHONPATH=. .venv/bin/python scripts/bq_enrich.py parse-form-dump \
    --pim <PIM_code> [--product-type <LABEL>] \
    --input bq/enriched/.bootstrap_<pim>.txt
```

## Step 5 — Parse this product's populated form

Tell Wayne:

```
Paste the editable form text for THIS product from the B&Q seller portal.
Include the current values — the parser uses the saved label set to
separate them.
```

Save to `bq/enriched/.<sku>_paste.txt` and run:

```
PYTHONPATH=. .venv/bin/python scripts/bq_enrich.py parse-form-dump \
    --sku <sku> --pim <PIM_code> \
    --input bq/enriched/.<sku>_paste.txt
```

Writes `bq/enriched/<sku>_<ts>_fields.json`.

## Step 6 — `/values_lists` enrichment

```
PYTHONPATH=. .venv/bin/python scripts/bq_enrich.py enrich-vlc <sku>
```

Adds value-list option maps to fields that have them, so Wayne sees the
available dropdown choices in the output file.

## Step 7 — Gate 1 (set approval)

**Render the full field table in the SAME chat message as the decision.**
Read `bq/enriched/<sku>_<ts>_fields.json` and build a markdown table:

`#`, `Portal label`, `Required`, `Section`, `Unit`, `Current value` (truncated to 40 chars), `Value-list options` (count + brief).

Flag inline above the table:

- Anomalies (duplicate-looking pairs, suspicious single-option value lists)
- Required-but-empty fields
- Fields where the parser may have mis-attributed a value to the wrong label

Decision prompt:

```
"go"                    — proceed as shown
"drop 5 9 14"           — exclude rows by number
"ask 2 17"              — mark rows for Wayne-supplied values at step 8
"add <label>"           — add a field discovery missed (rare)
"abort"                 — exit
```

## Step 8 — Handle "ask" fields

Present marked fields as one batch. Wayne supplies values inline, or
types "to-file" to defer.

## Step 9 — Fill specs and rewrite prose

```
PYTHONPATH=. .venv/bin/python scripts/bq_enrich.py prepare-gate2 <sku>
```

Emits the gate-2 markdown at `bq/enriched/<sku>_<ts>_gate2.md`. For
factual fields with parsed values: keep verbatim. For value-list fields,
keep the display label (NOT the API code — Wayne types/selects labels in
the portal). For prose fields (`Body Copy`, `Selling Copy`, `Key Feature`,
`Unique Selling Point 1-8`): write `<TODO: rewrite>` markers. For
compliance/safety fields: default `<SKIP>`.

**Then Claude rewrites the prose in chat:**

1. Open `BRAND_VOICE.md` and the relevant `EXEMPLARS/<product-type>.md`.
2. Open the MowDirect Shopify product (`mowdirect_url` in resolved JSON)
   in another tab. Skim once, then close. **Do not lift phrasing.**
3. For each prose field:
   - Different lead sentence than MowDirect's
   - Different paragraph structure
   - Different verbs from both compass and MowDirect
   - Apply BQ_QUIRKS rules: no em-dash (use " - "), no en-dash, no ×, no °, no smart quotes
   - Apply BRAND_VOICE banned-moves list: no B&Q-as-retailer, no
     click-and-collect, no MowDirect by name, no Americanisms, no fluff
4. Save the populated markdown back to the gate-2 file.

## Step 10 — Gate 2 (interactive review loop)

This is **not** a binary approve / edit gate. Filling a B&Q form well
involves judgment calls — "this mower mulches, so does Included get 'mulch
plug'?" or "what's a sensible Noise level for a 40V mower?" or "Function(s)
is multi-select — what are the options?". The skill must support
open-ended questions, not just edit commands.

Tell Wayne:

```
Your portal-fill document is ready at:

  bq/enriched/<sku>_<ts>_gate2.md

This is a conversation, not a binary gate. Reply however you like:

  - "done"                            — you're satisfied; I'll write the summary and exit
  - "edit Lawnmower type = Mulching"  — apply a concrete change
  - any question                      — I'll think it through with you
                                        ("does the mulch function mean Included should
                                         say 'mulch plug included'?", "what are typical
                                         options for Function(s)?", "what's a B&Q-acceptable
                                         noise level estimate?", etc.)
  - "show <field>"                    — re-show one field's current value
  - "abort"                           — exit without writing the summary

The document follows the order of fields in the seller portal — work top
to bottom in the portal as you go through it.
```

Handle replies as follows:

- **"done"** → write `_summary.md`, print summary, exit.
- **"edit <changes>"** → apply to the markdown, show the modified section(s),
  re-prompt.
- **A question** → answer it. Pull on the body copy, compass scrape, BRAND_VOICE,
  BQ_QUIRKS, prior conversation, and your own product knowledge to give a
  considered answer. After answering, **always ask** whether Wayne wants to
  apply the answer as an edit (and which field), or move on. The default
  outcome of a question is *no change to the markdown* unless Wayne
  explicitly asks for one.
- **"show <field>"** → echo the section verbatim.
- **A statement that implies an edit** ("yeah, change Function(s) to
  Mulching, Collection") → apply it.
- **"abort"** → exit without `_summary.md`. Cached files stay in place
  for resume.

Stay in this loop until "done" or "abort". Open-ended questions are
expected; the document quality benefits from the dialogue.

## Step 11 — End state

Write `bq/enriched/<sku>_<ts>_summary.md`:

```
# <sku> — /enrich-bq summary
Run: <ts>

Portal-fill document: bq/enriched/<sku>_<ts>_gate2.md
Fields: N total
  Required filled: A
  Optional filled: B
  Skipped: C
  No value found: D (these may need manual answers from documentation)

Hierarchy: <PIM_code> (<product_type>)
Mirakl status: <LIVE | not-yet-listed>

Verify after filling the portal (allow 4-48h for B&Q catalogue integration):
  https://www.diy.com/departments/.../<sku>/_.html

Re-run /enrich-bq with the same compass URL to update.
```

Tell Wayne the file paths and exit cleanly.

---

## Failure modes — handle directly, never silently

- **Compass URL unreachable** → hard fail at step 1 with URL printed
- **Shopify resolution fails** → hard fail per EAN-canonical rule
- **Hierarchy bootstrap aborted mid-paste** → save partial; tell Wayne; exit
- **Wayne pastes a populated form into bootstrap** → refuse, re-ask
- **Unrecognised reply at any gate** → re-print gate, list valid replies

## Persistence and audit trail

Per run, in `bq/enriched/<sku>_<ts>_*`:

- `_resolved.json` — compass scrape + Shopify resolve + Mirakl lookup
- `.bootstrap_<pim>.txt` — raw bootstrap paste (forensics)
- `.<sku>_paste.txt` — raw populated paste (forensics)
- `_fields.json` — parsed field set with values
- `_asks.md` — only if Wayne used "to-file" for asks
- `_gate2.md` — the portal-fill document (final deliverable)
- `_summary.md` — end-state summary

`config/bq_operator_extensions.json` accumulates hierarchy label sets and
operator-wide label-API-name overrides discovered during bootstraps.
