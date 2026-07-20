# B&Q active offers → Tesco — products CSV

- Active offers: **1** (after dropping inactive)
- **In CSV (ready to review & push): 1**
- Held back (need a fix first): 0

## Before you push

- `baseColour` is pre-filled **Green** for Spectrum. 0 non-Spectrum row(s) have it **blank** — fill before push: none.
- A few categories want extra required fields (batteries: `batterySize`, recycling info; tyred items: tyre dims). Tesco's transformation report after push will name any still missing — fill those in the CSV and re-push.
- `countryOfOriginName` defaulted to **China**, `vatRate` **20** — override per row if wrong.

## In CSV — by Tesco category

| Tesco category (from Shopify) | # |
|---|---|
| Home & Garden > Lawn & Garden > Outdoor Power Equipment > Lawn Aerators & Dethatchers > Lawn Aerators | 1 |

## Cross-retailer scrub hits (review nothing meaningful lost)

- `TG45SC-E`: {'marketingText': ['MowDirect']}
