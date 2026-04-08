#!/bin/bash
# Scrape compassgm.co.uk product URLs and prepare them for Claude Code import.
#
# Usage:
#   ./scripts/new_batch.sh https://compassgm.co.uk/product/... https://...
#
# What it does:
#   1. Scrapes all provided URLs → scraped_products/batch_TIMESTAMP.json
#   2. Prints the exact prompt to paste into a fresh Claude Code session
#      so Claude can rewrite the copy and push to Shopify.

set -e

if [ "$#" -eq 0 ]; then
  echo "Usage: ./scripts/new_batch.sh URL1 URL2 ..."
  exit 1
fi

TIMESTAMP=$(date +%Y%m%d_%H%M)
OUTPUT="scraped_products/batch_${TIMESTAMP}.json"

echo ""
echo "Scraping ${#} product(s)..."
echo ""

PYTHONPATH=. python scripts/compassgm_scraper.py "$@" --output "$OUTPUT"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Scrape complete → $OUTPUT"
echo ""
echo "  Open a new Claude Code session in this directory and paste:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
cat <<PROMPT
I have scraped Spectrum product data from compassgm.co.uk saved in ${OUTPUT}.

Please:
1. Read ${OUTPUT}
2. Rewrite all marketing copy (title, description_html, custom.feature_bullets, custom.bullet_two, custom.bullet_three) in MowDirect's brand voice — fully original content to avoid duplicate content and plagiarism. Keep all factual/spec data (display_attributes, sku, barcode, price) unchanged. Set vendor to "Spectrum" if blank. Correct product_type if clearly wrong (e.g. batteries/chargers showing as "Lawnmowers").
3. Save rewritten version as ${OUTPUT/.json/_rewritten.json}
4. Push to Shopify: PYTHONPATH=. python scripts/compassgm_to_shopify.py ${OUTPUT/.json/_rewritten.json}
PROMPT
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
