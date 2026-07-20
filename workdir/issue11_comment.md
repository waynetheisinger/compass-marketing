## Progress — reusable core built (AFK), live steps blocked on Tesco account

Did the architecture half of this slice — the part that settles the tooling question and is reused for The Range (#15). The remaining ACs are blocked on provisioning the Tesco Mirakl account, which only you can do.

### Done now (no Tesco account needed)

- **Per-operator compliance profile** — new `ComplianceProfile` on `OperatorConfig` (`scripts/mirakl_operators.py`): `char_replacements`, `field_length_caps`, `banned_phrases`, `retailer_scrub`, `banned_promo`. Kingfisher's existing rules (130-char name cap, restricted chars, `the range`→`the lineup`) refactored onto it **behaviour-preservingly** — old `KINGFISHER_FIELD_LENGTH_CAPS` / `clean_name` kept as back-compat aliases (so `bq_enrich.py` still works). ✅ AC: compliance profile exists.
- **Cross-retailer-name scrub** — `TESCO` profile scrubs B&Q/Kingfisher + competitor retailers (Screwfix, Wickes, Homebase, Toolstation, Robert Dyas, Argos, Amazon, eBay, ManoMano, OnBuy, Rackhams, Wilko, The Range) and our own storefronts, plus banned promo phrasing and URLs/emails/phone numbers. Whole URLs/emails removed as one unit; clean product prose passes through byte-identical; **every removed fragment is logged** (`report["scrub_hits"][sku]`) for review. `build_product_row` now runs the profile over `name` + `Body Copy`. ✅ AC: cross-retailer scrub runs.
- **Flow parameterised by instance** — confirmed; no B&Q hardcoding blocks reuse. Row builders already take `OperatorConfig`. ✅ AC: parameterised.
- **Generic connectivity check** — `scripts/mirakl_connectivity.py --operator TESCO`. Verified against live Kingfisher (reachable, pulled 6,570 hierarchies); against Tesco it prints the exact `.env` lines needed and exits 2. ✅ AC mechanism ready (pending creds).
- **Onboarding runbook** — `docs/MIRAKL_OPERATOR_ONBOARDING.md` captures the repeatable category/attribute-resolution method so The Range follows it. ✅ AC: method captured.

### Blocked on you (HITL)

1. **Provision the Tesco Mirakl seller account**, then add to `.env`:
   ```
   MIRAKL_TESCO_BASE_URL=https://<instance-host>/api
   MIRAKL_TESCO_API_KEY=<seller api key>
   ```
   Then I run the connectivity check to clear AC#1.
2. **Download the Tesco category template(s)** from the seller portal for the Spectrum subcategories (mowers, hedge trimmers, blower-vacs, batteries, chargers). The template headers are the authoritative attribute schema — I then map product types → Tesco category codes + value-list codes in `mirakl_operators.TESCO`.
3. After 1+2: I generate the validated CSV (seeded from the B&Q-enriched records, run through the Tesco compliance profile, gaps + scrub logged), we review, submit, and create **parity-priced** offers.

Once you've kicked off the account request, tell me and I'll pick up the schema mapping the moment the template + creds land.
