# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repository Is

This is a **marketing planning and automation workspace** for **MowDirect** — a UK-based Shopify garden machinery retailer. It combines strategic marketing documents with scripts and tooling that integrate the APIs of the platforms used to execute that strategy.

Primary artefacts:

- `MARKETING_PLAN_2026.md` — The full 2026 marketing plan (sections 1–17)
- `marketing budget forecast 2026.ods` — Budget and financial forecasting spreadsheet

## APIs and Integrations in Use

When tasks involve data retrieval, automation, or reporting, expect to write or run code against these APIs:

| Service | Purpose |
|---|---|
| **Shopify** | Product catalogue, orders, inventory, Metafields, storefront content |
| **Base.com / BaseLinker** | Marketplace order management and listing synchronisation |
| **Amazon SP-API** | Listing management, inventory, advertising, orders |
| **eBay API** | Listings, orders, Promoted Listings |
| **Other marketplaces** | ManoMano, OnBuy — listings and order data as integrations are built |
| **SimilarWeb API** | Competitor traffic intelligence, market benchmarking |
| **Ahrefs API** | SEO metrics, keyword data, backlink analysis, content gap discovery |

Credentials and API keys are managed externally — never hardcode them; always read from environment variables or a local `.env` file (not committed).

## Shopify App & API Setup

### Dev Dashboard
The current Shopify developer tooling is centred on the **Dev Dashboard** at `dev.shopify.com/dashboard` (also accessible via Shopify Admin → store name → Dev Dashboard, or via Partners Dashboard → App distribution → Visit Dev Dashboard). This replaced the old Private Apps model.

**Reference docs:** https://shopify.dev/docs/apps/build/dev-dashboard

### App type for internal automation
For server-side scripts and automation (product creation, inventory sync, etc.) the correct approach is an **organisation app** using the **client credentials grant** — not a custom app created in Shopify Admin, and not a public app.

- Create the app in the Dev Dashboard
- Credentials (Client ID + Client Secret) are on the app's Settings page
- Exchange client ID + secret for a short-lived access token (valid 24 h):
  ```
  POST https://{shop}.myshopify.com/admin/oauth/access_token
  Body: client_id=... & client_secret=... & grant_type=client_credentials
  ```
- Use the returned token in all API requests: `X-Shopify-Access-Token: {token}`
- This flow requires no user interaction — suitable for scripts and cron jobs

**Reference docs:** https://shopify.dev/docs/apps/build/authentication-authorization

### Base client module
All Shopify scripts must use `scripts/shopify_client.py` — do not reimplement auth or sessions. It handles token acquisition, caching, refresh, and session lifecycle via the `ShopifyAPI` library (`pip install ShopifyAPI`). Usage:

```python
from scripts.shopify_client import ShopifyClient

with ShopifyClient() as client:
    data = client.execute("{ shop { name } }")
```

### Admin API
Shopify's preferred API is **GraphQL** (not REST). Current stable version: `2026-01`.

**Endpoint:** `POST https://{shop}.myshopify.com/admin/api/2026-01/graphql.json`

Use `productCreate` mutation for product creation. Rate limiting is cost-based (GraphQL) rather than call-count-based (REST).

**Reference docs:** https://shopify.dev/docs/api/admin-graphql

### Required scopes (product management)
- `write_products` — create/update products and variants
- `read_products` — query existing products (for idempotency checks)
- `write_inventory` — manage inventory levels (if needed)

### Env vars convention
```
SHOPIFY_STORE_DOMAIN=mowdirect.myshopify.com
SHOPIFY_CLIENT_ID=...
SHOPIFY_CLIENT_SECRET=...
SHOPIFY_API_VERSION=2026-01
```
Never commit `.env`; never hardcode credentials.

## Mirakl Marketplace API

**Reference docs:** https://developer.mirakl.com/content/product/mmp/rest/seller/openapi3

### Overview
Mirakl is the white-label platform powering several retailer marketplaces. Each retailer runs an independent instance with its own base URL and API key — the API surface is identical across all of them.

**Current instances:**

| Marketplace | Operator | Channel code | Base URL |
|---|---|---|---|
| B&Q UK | Kingfisher | `BQ_UK` | `https://marketplace.kingfisher.com/api` |
| Tesco | Tesco | TBC | TBC — separate account needed |
| The Range | The Range | TBC | TBC — separate account needed |

### Authentication
Static API key passed as `Authorization` header — no OAuth, no expiry.

```
GET https://{instance-base-url}/orders
Header: Authorization: {api_key}
```

### Key endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/orders` | GET | Retrieve orders |
| `/offers` | GET | Retrieve active offers/listings |
| `/offers` | POST | Create/update listings |
| `/offers/imports` | POST | Bulk listing import |
| `/inventory/imports` | POST | Bulk stock update |

### Base client module
All Mirakl scripts must use `scripts/mirakl_client.py`. Supports multiple instances via named env vars. Usage:

```python
from scripts.mirakl_client import MiraklClient

client = MiraklClient("KINGFISHER")
orders = client.get("/orders")
```

### Env vars convention
```
MIRAKL_KINGFISHER_BASE_URL=https://marketplace.kingfisher.com/api
MIRAKL_KINGFISHER_API_KEY=...

MIRAKL_TESCO_BASE_URL=...
MIRAKL_TESCO_API_KEY=...

MIRAKL_THERANGE_BASE_URL=...
MIRAKL_THERANGE_API_KEY=...
```

## BaseLinker (Base.com) API

**Reference docs:** https://api.baselinker.com/

### Overview
BaseLinker (rebranded as Base.com) is the central order management and marketplace sync platform. It aggregates orders from Amazon, eBay, ManoMano, OnBuy and other channels, and links to Shopify as an external storage.

### API style
RPC over HTTP POST — single endpoint, `method` parameter selects the operation:

```
POST https://api.baselinker.com/connector.php
Header: X-BLToken: {token}
Body:   method=getOrders&parameters={"date_from": 1234567890}
```

- **Auth:** Static API token from BaseLinker panel → Account → My Account → API. No OAuth, no expiry — token is long-lived.
- **Rate limit:** 100 requests/minute
- **Response:** JSON, UTF-8

### Key method groups

| Group | Methods |
|---|---|
| Orders | `getOrders`, `addOrder`, `setOrderStatus`, `setOrderPayment` |
| Product Catalog | `getInventoryProductsList`, `getInventoryProductsData`, `addInventoryProduct`, `deleteInventoryProduct` |
| Stock & Pricing | `updateInventoryProductsStock`, `updateInventoryProductsPrices`, `getInventoryProductsPrices` |
| External Storages | `getExternalStoragesList`, `getExternalStorageProductsData`, `updateExternalStorageProductsQuantity` |
| Invoices | `addInvoice`, `getInvoices`, `getInvoiceFile` |

### Base client module
All BaseLinker scripts must use `scripts/baselinker_client.py` — do not reimplement the HTTP layer. Usage:

```python
from scripts.baselinker_client import BaseLinkerClient

client = BaseLinkerClient()
orders = client.call("getOrders", {"date_from": 1234567890})
```

### Env vars convention
```
BASELINKER_API_TOKEN=...
```

## Business Context

**Company:** MowDirect (operated by Wayne Theisinger / Compass)
**Platform:** Shopify
**Out of scope:** compassgm.co.uk and the Showroom — this plan covers MowDirect only
**Research tools in use:** SimilarWeb (competitor traffic) + Ahrefs (SEO, backlinks, content gaps)

**Own-label brand:** Spectrum — highest margin, 5-year warranty, the priority for all paid and organic channels

**Revenue forecast baseline (2026):** ~£4.07M gross, anchored to January 2026 actuals (£54,324) and projected forward using 2025 seasonal shift indices. The forecast assumes zero marketing uplift — all campaign performance is measured as additive over this baseline.

**Peak season:** March–May (March alone is a ×5.75 month-on-month jump). April is the highest-revenue month (~£752K baseline).

## Document Structure (MARKETING_PLAN_2026.md)

| Section | Topic |
|---|---|
| 1 | Forecast methodology and seasonal model |
| 2 | Situational analysis, SWOT, competitive landscape |
| 3 | Hero product calendar — monthly SKU-level promotion plan |
| 4 | Marketplace expansion (ManoMano, OnBuy, Amazon, eBay) |
| 5 | Paid advertising (Google PMax, Meta, TikTok) |
| 6 | Affiliate marketing (Awin) |
| 7 | Social platform strategy |
| 8 | PR & brand awareness |
| 9 | Organic SEO & content strategy |
| 10 | Conversion rate optimisation (CRO) |
| 11 | Additional channels |
| 12 | Research loop & competitive intelligence |
| 13 | Budget framework & investment case |
| 14 | Budget governance & daily spend management |
| 15 | Measurement & KPIs |
| 16 | Implementation roadmap |
| 17 | In-house resourcing estimate |

## Key Strategic Principles

- **Hero product model:** Each month has designated hero SKUs (chosen on margin ≥35%, stock depth ≥50 units, competitive price position, seasonal fit, and content-readiness) that receive concentrated spend across all channels simultaneously — not equal budget across everything.
- **Spectrum-first:** Own-label Spectrum products are prioritised due to higher margin. Honda and other brands are used as traffic drivers with on-site funnel logic (comparison modules, cross-sell) to convert visitors onto Spectrum.
- **Channel coordination:** Hero products receive channel-specific treatment (dedicated PMax asset groups, influencer Spark Ads, Klaviyo segmented emails, affiliate commission boosts) — not just "listed everywhere."
- **Baseline vs. uplift discipline:** Never bake marketing uplift into the forecast. Measure all campaign impact as delta over the seasonal baseline.
