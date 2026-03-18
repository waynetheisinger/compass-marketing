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
