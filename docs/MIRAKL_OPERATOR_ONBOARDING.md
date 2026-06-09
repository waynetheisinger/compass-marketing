# Onboarding a new Mirakl operator (Tesco, The Range, …)

The Mirakl API surface is identical across operators (Kingfisher/B&Q, Tesco,
The Range). What differs per operator is: credentials, the category hierarchy +
attribute schema, the value-list codes for enum attributes, and the
copy-compliance rules. This runbook is the repeatable method for bringing a new
operator online, proven on B&Q and used verbatim for Tesco. Follow it in order;
each step gates the next.

The flow is **parameterised by operator** — there is no operator-specific
hardcoding in the row builders. Everything operator-specific lives in
`scripts/mirakl_operators.py` as an `OperatorConfig` (+ `ComplianceProfile`).

---

## 0. Content basis — reuse, don't rewrite

Listing content is **seeded from the existing B&Q-enriched Spectrum records**
(`bq/enriched/*_resolved.json` + the generated CSVs in `bq/csv-output/`): the
MowDirect-voice copy, USPs, key features, images, EAN and specs, including the
hand-filled gaps. We do **not** rewrite the prose for voice — the only thing
that ever forced a rewrite (duplicate content vs our own site) is already
solved. We re-map those values onto the new operator's category/attribute schema
and fall back to Shopify only for values the B&Q batch didn't capture.

Copy **is** sanitised per operator (next section) — reuse ≠ paste-as-is.

---

## 1. Provision the account → env vars  *(HITL)*

Get the seller account created on the operator's Mirakl instance, then add to
`.env` (never commit):

```
MIRAKL_<NAME>_BASE_URL=https://<instance-host>/api
MIRAKL_<NAME>_API_KEY=<seller api key>
```

`<NAME>` is the upper-case operator key used everywhere (e.g. `TESCO`).
`scripts/mirakl_client.py` reads these by convention — no code change needed.

## 2. Connectivity check

```
python scripts/mirakl_connectivity.py --operator <NAME> --hierarchies
```

Read-only. Confirms the credentials resolve and `/version` + `/hierarchies`
return. Exit 0 = reachable and authenticated. This is the acceptance gate before
any import. (`--values-lists` also pulls the enum lists.)

## 3. Resolve the category hierarchy + attribute schema  *(HITL)*

Two complementary sources, same as B&Q:

1. **Seller-portal category template (primary).** Download the operator's
   category template(s) from its seller portal for the subcategories the
   Spectrum range maps to (mowers, hedge trimmers, blower-vacs, batteries,
   chargers, pressure washers). Save under `bq/templates/` (or a per-operator
   templates dir). The template's column headers are the authoritative attribute
   schema — portal display labels ≠ CSV column names, and the mapping is **not**
   a derivable transform (prefixes get added, some labels renamed/truncated).
2. **`/hierarchies` + `/values_lists` walk (secondary).** Use the connectivity
   script's `--hierarchies` to read category codes back, and `/values_lists` to
   attach code maps to value-list-backed enums (brand, guarantee, voltage, …).
   **Submit the code, never the human-readable label** — single most common
   source of transform errors.

Record the product-type → category-code mapping and the value-list codes in the
operator's `OperatorConfig` in `scripts/mirakl_operators.py`
(`by_product_type` + `common_attributes` + `per_sku_overrides`). New hierarchies
discovered at runtime can go in `config/bq_operator_extensions.json` as
data-not-code.

## 4. Confirm / extend the compliance profile

Each operator carries a `ComplianceProfile` on its `OperatorConfig`:

- `char_replacements` — restricted special chars → ASCII (default set is safe).
- `field_length_caps` — portal-label → max chars (name auto-truncates at a word
  boundary; reconcile caps against the downloaded template).
- `banned_phrases` — operator-flagged cross-promotional/off-brand wording.
- `retailer_scrub` — **competitor / host-retailer brand names that must never
  appear in a listing on this operator.** This is critical for marketplaces that
  are themselves retailers: a Tesco listing naming "B&Q"/Kingfisher or a rival
  retailer is a fast route to suppression. Excludes ambiguous common words; does
  **not** include "Spectrum" (the brand we sell).
- `banned_promo` — promo phrasing ("best price", "free delivery", …). URLs,
  emails and UK phone numbers are scrubbed globally regardless.

`build_product_row(op, p, report=...)` runs the profile over `name` and
`Body Copy` and records every removed fragment in `report["scrub_hits"][sku]`
for human review. Clean product prose passes through untouched.

## 5. Generate a validated CSV (dry-run) + review

Generate the import CSV for at least one subcategory, dry-run first. Review:
- the **scrub log** (`scrub_hits`) — confirm nothing meaningful was removed;
- **required-attribute gaps** — any mandatory column left blank.

## 6. Submit + read the transformation error report

Submit via `/products/imports`, then read the `transformation_error_report`
(CSV for API submissions). Don't poll for `COMPLETE` — Mirakl imports sit in
`SENT` for hours/days while the operator's catalogue team integrates. Use the
break-early condition: `lines_read == lines_ok + lines_in_error`.

## 7. Offers — parity priced  *(HITL upload)*

Create offers (`/offers/imports`) at **website-parity prices** — Amazon Fair
Pricing discipline, no per-marketplace markup. `build_offer_row` already pulls
`price_gbp` from the catalogue.

---

## Operator status

| Operator | Platform | Creds | Schema | Compliance profile | Status |
|---|---|---|---|---|---|
| Kingfisher (B&Q) | Mirakl | ✅ | ✅ 7 hierarchies | ✅ | 15 SKUs `SENT` (2026-05-07) |
| **Tesco** | Mirakl | ❌ awaiting account | ❌ needs template + walk | ✅ cross-retailer scrub ready | Profile + tooling ready; blocked on account |
| The Range | Mirakl | ❌ | ❌ | reuse Tesco method | Not started (#15) |

When you discover an operator quirk during onboarding, record it next to B&Q's
in `.claude/skills/enrich-bq/BQ_QUIRKS.md` (or a per-operator crib) so the next
run benefits immediately.
