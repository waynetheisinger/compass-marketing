Closes the two gaps from the initial B&Q→Tesco skill port (you flagged that merging the first pair was a bit premature — this completes the set so the Tesco skills match the B&Q trio).

## 1. `--shopify-skus` — direct sourcing
`/tesco-csv-batch` previously sourced only from **B&Q active offers**, so any product not on B&Q couldn't be listed on Tesco. Added a `--shopify-skus` mode to `mirakl_bq_to_tesco.py` that sources directly from arbitrary Shopify SKUs (brand + EAN from Shopify vendor/variant-barcode); everything else — category resolution, JPEG re-host, optional-field mapping, cross-retailer scrub — is reused.

## 2. `/enrich-tesco` — single-product, voice-rewritten (the `enrich-bq` analogue)
The bulk batch **reuses** Shopify copy verbatim (name-scrubbed). That's fine for volume, but it duplicates mowdirect.co.uk's prose and reads in MowDirect's voice. `/enrich-tesco` does one product carefully:
- `scripts/tesco_enrich.py gather` builds the base row and surfaces the **raw** Shopify copy.
- Claude **rewrites** the prose into a distinct voice per the shared `BRAND_VOICE.md` (different lead/structure/verbs from mowdirect.co.uk; never names MowDirect/Compass/Tesco/retailer/price/delivery).
- `apply` overlays the rewrite (deterministic scrub as a backstop) and emits **both** a push-ready 1-row CSV **and** a portal-fill markdown doc (Tesco portal labels → values) for manual entry/review.

## Voice / leak protection (your specific concern)
- **Name leaks** (MowDirect, our URLs, phones, retailers, promo) — stripped deterministically by the cross-retailer scrub in *all* Tesco paths.
- **Duplicate-content / voice** — `/enrich-tesco` rewrites; `/tesco-csv-batch` now carries an explicit note routing hero/important products to it.

## Verified
- `--shopify-skus DCT38M-SDM` sources direct (Spectrum / EAN from Shopify, category `hg-12-3-5-1`, cutting width / weight / bulletpoints mapped).
- `enrich gather → apply` round-trips to a CSV + portal doc with the rewrite merged and scrub clean.

Nothing pushed to Tesco. Refs #11.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
