## Connected to Tesco + validated tracer CSV generated ✅

Creds are in `.env` — Tesco Mirakl is live and the tracer CSV is generated and schema-valid. Three findings made this much faster than the B&Q path:

- **Tesco categories = Shopify product taxonomy** (`gid://shopify/TaxonomyCategory/…`), not B&Q's PIM codes. The Spectrum range maps cleanly into the Outdoor Power Equipment tree (mowers `hg-12-3-5-4`, hedge `hg-12-3-3`, blowers `hg-12-3-7`, pressure washers `hg-12-3-12`, OPE batteries `hg-12-4-7`, chargers `ha-14-17`).
- **Attribute schema is API-discoverable** (`/products/attributes?hierarchy=<gid>`) — so the portal-template-download step is **not needed** for Tesco. Schema is also much lighter (mowers: 11 required fields vs B&Q's dozens).
- **Spectrum is a registered Tesco brand** — no brand-registry hurdle.

### Done

- ✅ **AC: Tesco instance configured + connectivity check passes** — `mirakl_connectivity.py --operator TESCO` returns exit 0 (`/version` + `/hierarchies`).
- ✅ **AC: parameterised by instance** — added `FieldSchema` to `OperatorConfig` (Tesco's columns `description`/`barcode`/`marketingText`/`image1` differ entirely from B&Q's). KINGFISHER output is byte-for-byte unchanged.
- ✅ **AC: content seeded from B&Q-enriched records, reused not rewritten** — copy comes straight from the existing enrichment.
- ✅ **AC: per-operator compliance profile + cross-retailer scrub** — verified live: B&Q/competitor/promo references stripped from Tesco copy, B&Q host listing untouched.
- ✅ **AC: validated import CSV for a Tesco subcategory** — dry-run generated all **15 Spectrum SKUs across 6 categories**; **every row satisfies its category's live required-attribute set** with zero unknown columns. CSV: `workdir/mirakl-tesco/sbs_tesco_products_20260609.csv`.
- ✅ **AC: category-resolution method captured** — `docs/MIRAKL_OPERATOR_ONBOARDING.md`.

### Remaining before "live"

1. **Tesco channel code** — needed for actual submission (dry-run doesn't need it). What's Tesco's Mirakl channel/shop code? (B&Q's is `BQ_UK`.)
2. **Confirm `vatRate` format** — I've set `20` (standard-rated); first submission will tell us if Tesco wants `20`, `20.00` or `0.20`.
3. **Submit + offers** — push the product CSV, read the transformation error report, then create **parity-priced** offers. (Offer-file columns may differ from B&Q's — I'll verify against Tesco's offer schema before submitting.)

All on PR #20. Give me the channel code and I'll do the live submission run.
