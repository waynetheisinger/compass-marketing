Settles the Mirakl tooling-architecture question for the marketplace rollout and delivers the AFK half of #11 (Spectrum live on Tesco). The CSV-generation flow is parameterised by operator, with all operator-specific copy rules lifted out of Kingfisher-hardcoded globals — proven on B&Q, reused for Tesco and The Range (#15).

## What's in here

- **`ComplianceProfile` on `OperatorConfig`** (`scripts/mirakl_operators.py`) — `char_replacements`, `field_length_caps`, `banned_phrases`, `retailer_scrub`, `banned_promo`. Kingfisher's existing rules (130-char name cap, restricted chars, `the range`→`the lineup`) refactored onto it **behaviour-preservingly**; `KINGFISHER_FIELD_LENGTH_CAPS` / `_NAME_CHAR_REPLACEMENTS` / `clean_name` kept as back-compat aliases so `bq_enrich.py` imports unchanged.
- **Cross-retailer-name scrub** — the Tesco profile scrubs B&Q/Kingfisher + competitor retailers + our own storefronts, plus banned promo phrasing and URLs/emails/phone numbers. Whole URLs/emails removed as one unit; clean product prose passes through **byte-identical**; every removed fragment is logged (`report["scrub_hits"][sku]`) for human review. `build_product_row(op, p, report=...)` now sanitises `name` + `Body Copy`.
- **`scripts/mirakl_connectivity.py`** — operator-agnostic readiness check. Verified live against Kingfisher (reachable, 6,570 hierarchies); for Tesco it prints the exact `.env` lines and exits 2.
- **`docs/MIRAKL_OPERATOR_ONBOARDING.md`** — repeatable category/attribute-resolution runbook so The Range follows the method.

## Acceptance criteria

- [x] Flow parameterised by instance (no B&Q hardcoding blocks reuse)
- [x] Per-operator compliance profile exists for Tesco
- [x] Cross-retailer-name scrub runs on all copy
- [x] Category-resolution method captured (runbook)
- [ ] Tesco Mirakl instance configured + connectivity check passes — **blocked: needs account → `.env`**
- [ ] Validated import CSV for a Tesco subcategory — **blocked: needs Tesco category template**
- [ ] Spectrum live on Tesco at parity prices — **blocked: needs account + portal upload (HITL)**

## Blocked on (HITL)

1. Provision the Tesco Mirakl seller account → add `MIRAKL_TESCO_BASE_URL` / `MIRAKL_TESCO_API_KEY` to `.env`.
2. Download the Tesco category template(s) from the seller portal (authoritative attribute schema).
3. Then: generate validated CSV from the B&Q-enriched records, review, submit, create parity-priced offers.

## Test notes

- `mirakl_operators`, `mirakl_sbs_push`, `bq_enrich` all import/parse clean.
- Compliance behaviour exercised by hand: Kingfisher char-replace + banned-phrase preserved; Tesco scrub removes retailers/promo/contact whole, logs hits, leaves clean prose untouched.
- `mirakl_connectivity.py` run live against Kingfisher (exit 0) and Tesco stub (exit 2 with guidance).

Refs #11.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
