Scrape one or more compassgm.co.uk product URLs, rewrite the marketing copy in MowDirect's brand voice, and create the products in Shopify.

The URLs to process are: $ARGUMENTS

## Steps

1. **Scrape** all URLs using the scraper (run in background):
   ```
   PYTHONPATH=. python scripts/compassgm_scraper.py $ARGUMENTS --output scraped_products/batch_TIMESTAMP.json
   ```
   Use a real timestamp (YYYYMMDD_HHMM) in the filename.

2. **Read** the scraped JSON once complete.

3. **Rewrite** all marketing copy — produce a `_rewritten.json` file with:
   - `title` — clean, benefit-led product title in MowDirect style
   - `description_html` — fully original HTML description in MowDirect's voice (professional, British English, benefit-focused). Never copy the supplier text verbatim.
   - `custom.feature_bullets` — original `<ul><li>` list of key features
   - `custom.bullet_two` / `custom.bullet_three` — two standout selling point sentences
   - `vendor` — set to "Spectrum" if blank
   - `product_type` — correct if wrong (batteries → "Garden Tool Batteries", chargers → "Battery Chargers", mowers → "Cordless Lawnmowers", etc.)
   - All factual data (`display_attributes`, `sku`, `barcode`, `price`, `images`) passes through unchanged.

4. **Dry run** to verify:
   ```
   PYTHONPATH=. python scripts/compassgm_to_shopify.py scraped_products/batch_TIMESTAMP_rewritten.json --dry-run
   ```

5. **Push to Shopify** after confirming the dry run looks correct:
   ```
   PYTHONPATH=. python scripts/compassgm_to_shopify.py scraped_products/batch_TIMESTAMP_rewritten.json
   ```
