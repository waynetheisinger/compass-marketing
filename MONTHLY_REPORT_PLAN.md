# Plan: Monthly Marketing Spend Report for Funders

## Context

MowDirect needs a monthly report for investors/funders showing total marketing spend across all channels: marketplace fees (commissions, storage, other charges) and paid ad spend. Scope is eBay, Amazon, B&Q (Mirakl), Google Ads, and Shopify direct. Meta Ads excluded for now. The script pulls from all connected APIs, outputs a formatted Excel spreadsheet, and handles missing credentials gracefully with clear "NOT CONNECTED" notes rather than failing.

### Cost classification decision

Through prototyping (see below) the costs were split into three distinct buckets, which funders need to see separately:

| Bucket | What's in it | Why separate |
|--------|-------------|--------------|
| **Marketplace commissions & fees** | Referral/commission fees, listing charges, subscriptions, payment processing, eBay Promoted Listings, shipping labels | Cost of selling on each channel — analogous to a marketing/distribution cost |
| **Amazon FBA cost of sales** | FBA fulfilment fees, FBA storage fees, FBA inbound shipping, FBA prep & labelling | Operational cost of physical fulfilment — not marketing, sits alongside product cost of sales |
| **Paid ad spend** | Google Ads (PMax + Shopping), Amazon Sponsored Products | Discretionary marketing budget — directly comparable to the plan's £43,564 March budget |

Amazon referral fees are treated as a commission (marketing cost), not FBA cost of sales, because they are a percentage of revenue charged per sale rather than a physical handling cost.

### Mock report / prototype

A prototype spreadsheet was built before implementing the live script to validate the format with funders.

- **Script:** `scripts/mock_report.py` — generates the mock using hardcoded illustrative figures
- **Output:** `reports/marketing_spend_mock_2026-03.xlsx`
- **Run:** `python scripts/mock_report.py`

The live `monthly_report.py` must produce the same 5-tab structure as the mock.

---

## Files to Create / Modify

```
scripts/
├── ebay_finances_client.py     NEW — eBay Finances API (all fee types + promoted listing spend)
├── amazon_client.py            NEW — Amazon SP-API: settlement reports (fees, storage, all deductions)
├── amazon_ads_client.py        NEW — Amazon Advertising API (Sponsored Products spend)
├── google_ads_client.py        NEW — Google Ads API (PMax + Shopping campaign spend)
├── google_ads_auth.py          NEW — one-time OAuth helper (mirrors ebay_auth.py pattern)
├── monthly_report.py           NEW — CLI entry point / orchestrator
└── report/
    ├── __init__.py             NEW
    ├── data_sources.py         NEW — one fetch function per channel
    ├── transforms.py           NEW — aggregation, rounding helpers
    └── excel_writer.py         NEW — openpyxl spreadsheet builder

reports/                        NEW dir — git-ignored output directory

scripts/ebay_client.py          MODIFY — add sell.finances.readonly to _SCOPES
```

**Existing clients reused as-is:**
- `scripts/baselinker_client.py` — supplementary: detect active channels, cross-reference orders
- `scripts/mirakl_client.py` — B&Q (Kingfisher) orders + commissions
- `scripts/shopify_client.py` — Shopify direct sales + payment fees
- `scripts/ebay_client.py` — auth base for eBay Finances client

---

## Architecture

```
python scripts/monthly_report.py --month 2026-03 [--output reports/]
        │
        ▼
monthly_report.py  →  report/data_sources.py  →  report/transforms.py
                                │
        ┌───────────────────────┼─────────────────────────────┐
        │                       │                             │
EBayFinancesClient         AmazonClient               MiraklClient("KINGFISHER")
/sell/finances/v1/         Settlement Reports          /orders + /invoices
transaction                (fees, FBA storage,         (commission + any charges)
→ FINAL_VALUE_FEE          referral fees, etc.)
   AD_FEE (Promoted)
   SHIPPING_LABEL           AmazonAdsClient            GoogleAdsClient
   SUBSCRIPTION_FEE         Sponsored Products         GAQL: PMax + Shopping
   etc.                     spend report               campaign spend

        │                       │                             │
        └───────────────────────┴─────────────────────────────┘
                                │
                        report/excel_writer.py
                                │
                  marketing_spend_2026-03.xlsx
```

---

## Data Coverage Per Channel

### eBay
| Data type | API | Transaction type |
|-----------|-----|-----------------|
| Referral/commission fees | eBay Finances API | `FINAL_VALUE_FEE` |
| Promoted Listings ad spend | eBay Finances API | `AD_FEE` |
| Shipping label costs | eBay Finances API | `SHIPPING_LABEL` |
| Store subscription | eBay Finances API | `SUBSCRIPTION_FEE` |
| Refunds issued | eBay Finances API | `REFUND` |
| Dispute/chargeback | eBay Finances API | `DISPUTE` |

All from the same endpoint `/sell/finances/v1/transaction` — no extra credentials beyond existing eBay OAuth.

### Amazon
| Data type | API | Notes |
|-----------|-----|-------|
| Referral fees | SP-API Settlement Reports | Per-order deduction |
| FBA fulfilment fees | SP-API Settlement Reports | Per-unit pick/pack/ship |
| FBA storage fees | SP-API Settlement Reports | Monthly storage charge |
| Other service fees | SP-API Settlement Reports | Subscription, refund admin, etc. |
| Sponsored Products spend | Amazon Advertising API | Separate auth from SP-API |
| Sponsored Brands/Display | Amazon Advertising API | If live — included in report |

Settlement Reports give a complete reconciliation of all Amazon deductions in one download. More reliable than per-order commission estimates from BaseLinker.

### B&Q (Mirakl / Kingfisher)
| Data type | API | Notes |
|-----------|-----|-------|
| Commission per order | Mirakl `/orders` | `commission_amount` field per order |
| Other charges / billing | Mirakl `/invoices` | Listing fees, storage if applicable |

### Google Ads
| Data type | API | Notes |
|-----------|-----|-------|
| PMax campaign spend | Google Ads API (GAQL) | `advertising_channel_type = PERFORMANCE_MAX` |
| Shopping campaign spend | Google Ads API (GAQL) | `advertising_channel_type = SHOPPING` |

### Shopify Direct
| Data type | API | Notes |
|-----------|-----|-------|
| Payment processing fees | Shopify GraphQL | `orders → transactions → fees` |

---

## Spreadsheet Schema (5 tabs)

Matches the structure validated in `reports/marketing_spend_mock_2026-03.xlsx`.

**Tab 1 — Summary**
Three buckets with key metrics and % of gross revenue: marketplace commissions & fees, Amazon FBA cost of sales, paid ad spend. Per-channel commission table and per-platform ad spend table.

**Tab 2 — Marketplace Fees & Commissions**
One row per channel × fee type (commissions, referral fees, subscriptions, payment processing). Amazon FBA costs explicitly excluded with a note pointing to Tab 3.

| Channel | Fee Type | Amount (£) | Channel Revenue (£) | Fee as % of Revenue | Data Source | Coverage |
|---------|----------|-----------|--------------------|--------------------|-------------|----------|

**Tab 3 — FBA Cost of Sales** *(Amazon only)*
FBA fulfilment fees, storage fees, inbound shipping, prep & labelling — each as a separate line with % of Amazon revenue and % of total gross. Includes a side-by-side comparison of all Amazon costs (commission + ads + FBA) so funders see the full Amazon cost picture.

**Tab 4 — Ad Spend**
Google Ads campaigns (PMax + Shopping), eBay Promoted Listings, Amazon Sponsored Products — with impressions, clicks, CTR.

**Tab 5 — Raw Data**
Sample transaction rows from each source: eBay Finance transactions, Amazon settlement line items, Google Ads campaign rows.

---

## Phased Rollout

### Phase 1 — Build now (credentials already in .env)

| Data | Source | Action needed |
|------|--------|---------------|
| eBay all fees + Promoted Listings spend | eBay Finances API | Add `sell.finances.readonly` scope + re-run `ebay_auth.py` |
| B&Q commission + billing charges | Mirakl `/orders` + `/invoices` | No changes — credentials already set |
| Shopify payment fees | Shopify GraphQL | No changes |
| Amazon order commission (estimate) | BaseLinker fallback | Until SP-API is connected |

### Phase 2 — Needs new credentials

| Data | Client | Setup section |
|------|--------|---------------|
| Amazon fees + storage (authoritative) | `amazon_client.py` | See [Amazon SP-API](#amazon-sp-api-phase-2a) |
| Amazon Sponsored Products spend | `amazon_ads_client.py` | See [Amazon Advertising API](#amazon-advertising-api-phase-2b) |
| Google Ads spend | `google_ads_client.py` | See [Google Ads](#google-ads-phase-2c) |

---

## Credentials to Set Up

### eBay Finances scope (one-time, Phase 1)

Add `sell.finances.readonly` to `_SCOPES` in `ebay_client.py`, then run:
```bash
python scripts/ebay_auth.py
```
This regenerates `EBAY_REFRESH_TOKEN` with the new scope. All existing eBay functionality unchanged.

---

### Amazon SP-API (Phase 2a)

Amazon SP-API uses LWA (Login With Amazon) OAuth — one-time browser auth, then auto-refreshes.

**Setup steps:**
1. [Seller Central](https://sellercentral.amazon.co.uk) → Apps & Services → Develop Apps → Create application
2. Register as a developer (select "Private developer" for internal use)
3. Create an SP-API application → get Client ID + Client Secret
4. Authorise the application against the MowDirect seller account → get Refresh Token
5. Find your Seller ID: Seller Central → Account Info → Merchant Token

```
AMAZON_SP_CLIENT_ID=...
AMAZON_SP_CLIENT_SECRET=...
AMAZON_SP_REFRESH_TOKEN=...
AMAZON_SELLER_ID=...
AMAZON_MARKETPLACE_ID=A1F83G8C2ARO7P    # Amazon UK (fixed value)
```

**Key SP-API endpoints:**
- `GET /finances/v0/financialEventGroups` — lists settlement periods
- `GET /finances/v0/financialEvents` — all fee line items within a settlement period

**Install:** `pip install python-amazon-sp-api`

---

### Amazon Advertising API (Phase 2b)

Separate OAuth application from SP-API — different developer console.

**Setup steps:**
1. [Amazon Ads console](https://advertising.amazon.com) → Tools → API access
2. Apply for API access (select "Internal use / reporting")
3. Create application → get Client ID + Client Secret
4. Complete OAuth flow to get Refresh Token for the MowDirect advertising account
5. Find Profile ID: after auth, call `GET /v2/profiles` → note the UK profile ID

```
AMAZON_ADS_CLIENT_ID=...
AMAZON_ADS_CLIENT_SECRET=...
AMAZON_ADS_REFRESH_TOKEN=...
AMAZON_ADS_PROFILE_ID=...       # UK advertising profile ID
```

**Key endpoint:** `POST /reporting/reports` (async report) → poll until ready → download Sponsored Products spend by campaign for the month.

---

### Google Ads (Phase 2c)

**Setup steps:**
1. [Google Cloud Console](https://console.cloud.google.com) → Enable **Google Ads API** → Create OAuth 2.0 credentials (Desktop app) → get Client ID + Secret
2. [Google Ads API Center](https://ads.google.com/aw/apicenter) → Apply for **Basic access** developer token (24–72h approval)
3. Run `python scripts/google_ads_auth.py` — opens browser, you log in as Google Ads account owner, approve, paste redirect URL back → saves `GOOGLE_ADS_REFRESH_TOKEN` to `.env`
4. Find Customer ID in Google Ads UI top-right (format XXX-XXX-XXXX → store without dashes)

```
GOOGLE_ADS_CLIENT_ID=...
GOOGLE_ADS_CLIENT_SECRET=...
GOOGLE_ADS_DEVELOPER_TOKEN=...
GOOGLE_ADS_CUSTOMER_ID=...              # digits only, no dashes
GOOGLE_ADS_REFRESH_TOKEN=...            # set by google_ads_auth.py
GOOGLE_ADS_LOGIN_CUSTOMER_ID=...        # only if using a Manager/MCC account (optional)
```

**Install:** `pip install google-ads`

---

## Key Implementation Details

**Graceful credential handling:** Every `data_sources.py` function returns `(data, None)` on success or `(None, "NOT CONNECTED — add X, Y to .env")` if credentials are missing. The script never crashes — missing sources appear as greyed rows in the spreadsheet with the note text.

**BaseLinker as Amazon fallback:** Until SP-API is connected, Amazon commission data comes from BaseLinker `getOrders(filter_order_source=amazon, include_commission_data=true)`. Coverage Note reads "PARTIAL — BaseLinker estimate, connect SP-API for storage fees".

**BaseLinker channel detection:** On first run, calls `getOrderSources()` to discover all active marketplace channels and logs them.

**BaseLinker pagination:** `getOrders` returns max 100 per call — loops using `date_from` offset until empty response.

**eBay Finances pagination:** `/sell/finances/v1/transaction` max 200 per page — loops with `offset` until `total` reached.

**Amazon settlement timing:** Amazon settles every 14 days, not monthly. The script requests all settlement groups that overlap the target month and sums all fee events within that window.

**Date handling:** BaseLinker uses Unix timestamps; all other APIs use `YYYY-MM-DD`. `transforms.py` handles conversion. Month boundaries are midnight UTC on first/last day of the month.

**CLI usage:**
```bash
python scripts/monthly_report.py --month 2026-03
python scripts/monthly_report.py --month 2026-03 --output reports/
```

---

## Verification

1. Run Phase 1 with `--month 2026-03` → confirm eBay Finance transactions appear (requires re-auth with new scope first)
2. Verify B&Q Mirakl orders and invoices populate Tab 2
3. Confirm "NOT CONNECTED" rows appear for Amazon SP-API, Amazon Ads, Google Ads — no errors thrown
4. After Phase 2 credentials added: cross-check Amazon fees against a March Seller Central settlement statement
5. Cross-check Google Ads spend against the Google Ads UI March total
6. Open `.xlsx` and verify all 5 tabs match the structure in `reports/marketing_spend_mock_2026-03.xlsx`

---

## Manual QA Checklist

Run this after generating each monthly report before sharing with funders. The goal is to catch API timing issues, missing data, or classification errors before they reach an investor.

**Acceptable variance:** Small differences (< 1–2%) are normal due to VAT rounding, mid-month currency snapshots, or transactions that straddle a month boundary. Differences > 2% on any single fee line need investigation.

---

### eBay

**Where to check:** Seller Hub → Payments → [month] → Transaction report (downloadable CSV)

| Report line | Where to verify in Seller Hub | What to compare |
|-------------|------------------------------|-----------------|
| Referral / final value fees | Payments → Transactions → filter by type "Final value fee" | Sum of all FVF for the month |
| Promoted Listings spend | Payments → Transactions → filter by type "Promoted listings fee" | Monthly total |
| Shipping label costs | Payments → Transactions → filter by type "Shipping label" | Monthly total |
| Store subscription | Payments → Transactions → filter by type "Subscription fee" | Should be a single fixed charge |
| Total eBay fees | Payments → Monthly summary → "Total fees" line | Cross-check against sum of all eBay fee lines in Tab 2 |

**Known gotchas:**
- eBay Finances API uses transaction date, not payout date. Transactions from the last few days of the month may appear in the next payout cycle. Always verify the date range filter matches exactly.
- Final value fee adjustments (from buyer refunds) appear as credits — check these are netted off correctly in the report.

---

### Amazon

**Where to check:** Seller Central → Reports → Payments → All Statements → select the settlement(s) covering the month

| Report line | Where to verify in Seller Central | What to compare |
|-------------|----------------------------------|-----------------|
| Referral fees | Statement → Fee summary → "Referral fee" | Sum across all settlements in the month |
| FBA fulfilment fees | Statement → Fee summary → "FBA fees" | Should match the report's fulfilment line |
| FBA storage fees | Statement → Fee summary → "FBA inventory storage fee" | Note: charged mid-month, may appear in adjacent settlement |
| FBA inbound shipping | Reports → Fulfilment → Inbound shipments → filter by month | Compare total inbound shipping costs |
| Amazon Sponsored Products spend | Advertising console → Campaigns → filter by month → "Spend" column | Compare against Tab 4 Ad Spend total |

**Known gotchas:**
- Amazon settles every 14 days, so a calendar month typically spans 2–3 settlements. Ensure all overlapping settlements are included.
- FBA storage fees are charged on the 7th–15th of the following month for the prior month's storage. The report should attribute them to the month of storage, not the charge date — verify this is handled correctly.
- Inbound shipping costs may be invoiced separately (via Amazon Partnered Carrier) and not appear in the standard settlement report. Check both sources.
- Referral fee percentages vary by category. The report total should be compared against the statement total, not recalculated from a flat rate.

---

### B&Q (Mirakl / Kingfisher)

**Where to check:** B&Q Seller Portal → Orders → export order report for the month; also Invoices section for platform charges

| Report line | Where to verify in portal | What to compare |
|-------------|--------------------------|-----------------|
| Commission per order | Orders export → "commission_amount" column → sum | Should match Tab 2 B&Q commission total |
| Platform / listing charges | Invoices → filter by month | Any fixed monthly charges not tied to individual orders |
| Total B&Q deductions | Invoices → monthly summary if available | Cross-check Tab 2 B&Q subtotal |

**Known gotchas:**
- Mirakl commissions are calculated on the order total including VAT in some configurations. Verify whether the API returns VAT-inclusive or VAT-exclusive commission amounts, and ensure consistency with the other channels.
- Cancelled or refunded orders may still show a commission entry that is later credited — confirm refund credits are included.

---

### Google Ads

**Where to check:** Google Ads UI → Campaigns → set date range to exact month → "Cost" column

| Report line | Where to verify in Google Ads | What to compare |
|-------------|------------------------------|-----------------|
| PMax campaign spend | Campaigns tab → filter type = Performance Max → Cost | Should match Tab 4 PMax row |
| Shopping campaign spend | Campaigns tab → filter type = Shopping → Cost | Should match Tab 4 Shopping row |
| Total Google spend | Overview → date range → total cost | Should match Tab 4 Google Ads subtotal |

**Known gotchas:**
- Google Ads reports in the account's local timezone; the API may return data in UTC. A day's spend at month boundaries can shift by up to ±1 day. Use the Google Ads UI date range that matches the API query exactly.
- If a Manager (MCC) account is used, confirm the customer ID in `.env` is the direct account ID, not the MCC ID, otherwise spend from sub-accounts may be aggregated or missing.
- Invoice total (from Google Billing) will differ from campaign spend if there are credits, promotional adjustments, or threshold billing timing. Compare against campaign spend, not the invoice.

---

### Shopify (direct sales)

**Where to check:** Shopify Admin → Analytics → Finances → Payments → filter by month

| Report line | Where to verify in Shopify | What to compare |
|-------------|--------------------------|-----------------|
| Payment processing fees | Finances → Payouts → select month → "Fees" column total | Should match Tab 2 Shopify payment processing line |
| Gross revenue (Shopify direct) | Analytics → Sales → filter channel = Online Store | Cross-check Tab 1 Shopify gross revenue figure |

**Known gotchas:**
- Shopify Payments fees vary by plan (Basic vs Shopify vs Advanced). If the plan changes mid-month the blended rate will differ from the headline rate.
- Third-party payment gateways (PayPal, Klarna) have separate fee structures not captured by `transactions → fees` in the GraphQL API. Check if these are in use and add them if so.

---

### Cross-tab sanity checks (do these last)

After verifying each channel individually, run these cross-checks on the spreadsheet itself:

1. **Tab 1 Summary totals = sum of Tab 2 + Tab 3 + Tab 4** — the three tabs should add up exactly to the combined total on the Summary. If not, something is double-counted or missing.
2. **Tab 3 FBA total is Amazon-only** — no other channel should have entries in the FBA tab.
3. **eBay Promoted Listings appears in both Tab 2 and Tab 4** — it is both a channel fee and an ad spend. Confirm it is included in the Tab 1 ad spend bucket (not double-counted in both the fees bucket and ads bucket).
4. **Gross revenue sum (Tab 2) ≈ GROSS_TURNOVER on Summary** — minor differences acceptable if refunds are excluded; investigate if > 2%.
5. **Month boundaries** — spot-check 2–3 raw transactions on Tab 5 to confirm none have dates outside the report month.
