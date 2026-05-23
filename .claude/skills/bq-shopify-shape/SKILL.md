---
name: bq-shopify-shape
description: Map a specific Shopify metafield to its B&Q CSV column target (or skip it). Targeted — you tell the skill which one to handle, the skill helps you decide, saves to config/bq_shopify_mapping.json, then asks if you want to do another. Also supports bulk-skipping junk namespaces in one keystroke. Dual-mode — standalone, or reactively invoked from /bq-csv-batch when it detects metafield gaps mid-run.
---

# /bq-shopify-shape — Map a Shopify metafield to B&Q

This skill maintains `config/bq_shopify_mapping.json`. It's deliberately
**targeted**: you don't walk through every unmapped entry. You tell the
skill which metafield you want to map (or skip), it helps you decide,
saves, then asks if you want to do another. Sibling skill `/bq-csv-batch`
consumes the config to generate import CSVs.

The Python script `scripts/bq_shopify_shape.py` is pure I/O: it pulls
from Shopify, writes the JSON. **You** (Claude) drive the conversation
and call the script's subcommands to read/write state.

## What this skill does (and doesn't)

Does:

- Refreshes Shopify metafield definitions silently in the background
  (catches schema changes without bothering Wayne).
- Asks Wayne which metafield to handle next — supports specific keys,
  substring search, or `list` for the full unmapped inventory.
- For each chosen metafield: shows the type + example value, asks for
  B&Q target column(s) + optional transform hint, saves via `set-mapping`.
- Supports `skip <reason>` (records `skip_reason` so it doesn't surface
  again) and `bulk-skip <pattern>` (blast-skip an entire namespace in
  one command — useful for `judgeme.*`, `mm-google-shopping.*`, etc.).
- Walks a sample of SKUs on request to populate `example_value` and
  surface ad-hoc shop-owner-added metafields.
- Drift report on schema changes (metafield deleted, type changed).

Doesn't:

- Loop through every unmapped entry. Wayne picks what to work on; the
  skill never auto-walks. This is intentional — there are typically
  ~60+ unmapped entries and most aren't worth Wayne's time per session.
- Validate B&Q column names against a template. The skill trusts Wayne's
  typed answer; `/bq-csv-batch` catches typos at run time via the
  validator.
- Submit anything to Mirakl.

## Identifier invariants

- **Entry key format.** `shopify_metafields` entries are keyed by
  `<namespace>.<key>` (e.g. `custom.bullet_two`, `shopify.battery-technology`).
  `shopify_core_fields` entries are keyed by the field path
  (e.g. `title`, `featuredImage.url`, `inventoryItem.measurement.weight`).
- **Decision states.**
  - `bq_targets=[] AND skip_reason=null` → needs a decision.
  - `bq_targets=[] AND skip_reason="<text>"` → decided not to map; won't
    resurface.
  - `bq_targets=[...]` → mapped.

---

## Step 1 — State + silent refresh

```
PYTHONPATH=. .venv/bin/python scripts/bq_shopify_shape.py status
PYTHONPATH=. .venv/bin/python scripts/bq_shopify_shape.py refresh
```

Refresh always runs — it's idempotent and quick. Report one line to Wayne:

```
Config: <N> mapped, <M> unmapped, <S> skipped.  [Refresh: <added>/<missing>/<type-changed>]
```

If refresh reports drift (`drift_missing` or `drift_type_changed`
non-empty), surface it inline as a warning. Don't act on it; just flag.

## Step 2 — Sample SKUs (optional, ask only if invoked standalone)

When invoked standalone (not from `/bq-csv-batch`), ask Wayne:

```
Want to scan SKU(s) to populate example values and catch shop-owner-added
metafields? Paste space-separated SKUs, or 'skip'.
```

If SKUs given, run:

```
PYTHONPATH=. .venv/bin/python scripts/bq_shopify_shape.py scan-skus <s1> <s2> ...
```

Report what was added / examples populated. Then proceed to step 3.

**When invoked reactively from `/bq-csv-batch`,** skip this question — the
caller already passes the batch SKUs to scan-skus automatically before
delegating to this skill, so examples are already populated.

## Step 3 — The targeted loop

Ask Wayne:

```
What do you want to map next? Options:

  <namespace.key>          — map this specific metafield
                             (e.g. shopify.battery-technology)
  list                     — show all unmapped entries (paged)
  list <substring>         — filter the list (e.g. 'list shopify' or 'list filter')
  bulk-skip <pattern>      — blast-skip every unmapped entry matching the
                             pattern (e.g. 'bulk-skip judgeme', 'bulk-skip mm-google')
                             — I'll ask for a reason and confirm before saving
  quit                     — save state and exit

  > _
```

Handle replies:

### "list" (with or without filter)

Run:

```
PYTHONPATH=. .venv/bin/python scripts/bq_shopify_shape.py list-unmapped
```

Filter the JSON result by Wayne's substring (case-insensitive match on
`section`, `owner_type`, `key`, or `name`). If filter is empty, show all.

Print a numbered list. For each entry show:

```
  [<i>] <section>/<owner_type>/<key>
        type: <type>   example (from <sku>): <truncated_example>
```

Cap at 30 rows per page; tell Wayne to filter further if it overruns.

After listing, ask again: "Which to map? (key, number, or another command)"

If Wayne types a number, resolve it to the entry from the list you just
showed and proceed as if he typed the key directly.

### "bulk-skip <pattern>"

First dry-run:

```
PYTHONPATH=. .venv/bin/python scripts/bq_shopify_shape.py bulk-skip \
    --pattern "<pattern>" --dry-run
```

Show Wayne what would be skipped. Then ask:

```
Skip these N entries? Reason for skip (or 'no' to cancel):
  > _
```

If Wayne provides a reason, apply it:

```
PYTHONPATH=. .venv/bin/python scripts/bq_shopify_shape.py bulk-skip \
    --pattern "<pattern>" --reason "<reason>"
```

Report `matched_count` saved. Return to step 3.

**Suggest reasons for common junk namespaces** if Wayne doesn't volunteer
one:

| Namespace | Suggested reason |
|---|---|
| `judgeme` | Review widget HTML, not a product attribute |
| `mm-google-shopping` | Google Shopping feed metadata, not B&Q-relevant |
| `reviews` | Star ratings, not in B&Q schema |
| `shopify--discovery` | Shopify search-ranking internals |
| `shopify.item-condition` / `filter.condition` | MowDirect only sells new — N/A |
| `shopify.color-pattern` | Not a B&Q attribute for power equipment |
| `shopify.material` | Not a B&Q attribute for power equipment |

### A specific namespace.key (e.g. `shopify.battery-technology`)

Find the entry in the config. The script doesn't have a "get-one"
subcommand by default, so:

1. Run `list-unmapped` and find the matching entry in the JSON.
2. If not found, tell Wayne "<key> is already mapped, skipped, or doesn't
   exist" and return to step 3.

Show the entry's metadata:

```
<json_path>
  Type:    <type>
  Name:    <name>
  Example (from <sku>): <example_value, truncated to 200 chars>
  Shop-owner-added: <true/false>

B&Q target column(s)? Comma-separated names (e.g. "Battery_chemistry"),
or:
  skip <reason>     — no B&Q equivalent
  cancel            — back to step 3 without changing this entry
Hint (optional, free-text — how to transform at runtime):
  > _
```

Apply Wayne's reply:

```
PYTHONPATH=. .venv/bin/python scripts/bq_shopify_shape.py set-mapping \
    --section <section> --owner-type <owner_type> --key <key> \
    --bq-targets "Col1,Col2,..." --hint "<hint>"
```

OR

```
PYTHONPATH=. .venv/bin/python scripts/bq_shopify_shape.py set-mapping \
    --section <section> --owner-type <owner_type> --key <key> \
    --skip-reason "<reason>"
```

Read the script's output. Confirm to Wayne:

```
Mapped: <key> → <targets>   (hint saved)
```

Return to step 3.

### "quit"

Run `status` once more, report final counts, exit cleanly.

### Anything else / unrecognised

Re-print step 3's prompt with the valid options.

---

## Invocation contexts

**Standalone refresh** (`/bq-shopify-shape` typed by Wayne):

Run steps 1 → 3 in full. Step 2 fires (asking about sample SKUs).

**Reactive from `/bq-csv-batch`:**

When `/bq-csv-batch` detects metafields on the batch's SKUs that aren't
in the config, it invokes this skill with the batch's specific
metafields as the work set. The orchestrating skill (`/bq-csv-batch`)
will have already run `scan-skus <batch-skus>` so example values are
populated. Skip step 2. In step 3, the calling Claude can pre-populate
Wayne's reply with the specific unmapped metafield keys one at a time
(rather than asking "what to map next?"), making it feel seamless. When
all the gaps are resolved (or Wayne quits), control returns to
`/bq-csv-batch` which resumes the row-generation flow.

---

## Failure modes — handle directly

- **Shopify auth fails** → hard fail, print the exception, exit. Wayne
  needs to check `.env`. The Shopify client refreshes its own token, so
  this should be rare.
- **A SKU in step 2 isn't on Shopify** → reported in `products_not_found`;
  warn and continue.
- **Wayne types a `<namespace.key>` that doesn't exist** → tell him,
  return to step 3 prompt.
- **`set-mapping` reports an error** → surface the script's stderr,
  return to step 3 for that entry.

## Persistence and audit trail

Single file: `config/bq_shopify_mapping.json`. Every entry tracks
`discovered_at` and `last_seen_at` for forensics. The file is
git-committed; mapping decisions are reviewable via `git log` /
`git diff`.

The skill has no separate audit log — its side-effects all land in the
config file itself.
