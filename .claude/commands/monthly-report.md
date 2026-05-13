Run the MowDirect monthly marketing spend report and produce the Excel spreadsheet.

Optional argument: $ARGUMENTS

- If `$ARGUMENTS` is empty, generate the report for **the previous calendar month** (relative to today).
- If `$ARGUMENTS` starts with a `YYYY-MM` string (e.g. `2026-03`), use that month.
- A `--wayne-commission VALUE` flag may follow (e.g. `2026-03 --wayne-commission 18750.50`).
  When present, that £ value overrides the auto-computed 4% Wayne commission line —
  use this for the post-audit re-run that reflects true returns/refunds.

## Steps

1. **Determine the target month.**
   - If `$ARGUMENTS` is non-empty and starts with `YYYY-MM`, use that month.
   - Otherwise compute the previous month from today (e.g. on 2026-05-01 → `2026-04`). Use Bash with `date` rather than guessing — `date -v-1m +%Y-%m` on macOS.

2. **Run the report:**
   ```
   PYTHONPATH=. python3.11 scripts/monthly_report.py --month {YYYY-MM} [--wayne-commission {GBP}]
   ```
   Pass `--wayne-commission` through verbatim if the user supplied it. The script
   writes `reports/marketing_spend_{YYYY-MM}.xlsx` and prints a summary to stdout.

3. **Report back to the user** with:
   - The headline figures from the script's stdout (gross revenue, marketplace fees, FBA cost of sales, ad spend, net contribution).
   - The output filename.
   - Any `NOT CONNECTED` / `PARTIAL` / `ERROR` notes the script surfaced — flag these explicitly so the user knows which channels are missing.

4. **Do not** modify the script, regenerate other months, or open the spreadsheet — just run the one command and summarise.

## Connected data sources (as of 2026-05-01)

- eBay Finances (fees + Promoted Listings)
- Shopify (payment processing fees)
- Mirakl Kingfisher / B&Q (orders + invoices)
- Amazon SP-API Finances (full fee breakdown)
- Google Ads (campaign spend/impressions/clicks)

## Still pending

- Amazon Ads (Sponsored Products) — application approved, awaiting LWA association
- Meta Ads, TikTok Ads, Awin — not started

If any of these later become connected, this command needs no change — `monthly_report.py` picks them up automatically once their env vars are set.
