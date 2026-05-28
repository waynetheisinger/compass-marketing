# B&Q Catalogue Support Request — split merged product into three

**Subject:** Please split incorrectly-merged product — 3 × Spectrum Cement Mixer Drums (seller: Compass and Spectre Ltd)

---

**Seller:** Compass and Spectre Ltd
**Marketplace / channel:** B&Q (BQ_UK)
**Category:** Marketplace Categories / Power Tools / Power Tool Replacement Parts / Cement Mixer Drums
**Brand:** Spectrum

## What's wrong

Three separate products have been consolidated into a **single master product**. The one product page currently carries multiple EANs (e.g. 5065022450764 and 5065022450740 both show under "Product identifiers"), and our three shop SKUs all feed it as sources. They should be **three distinct products**, each with its own listing on diy.com — they are not variants of one another.

## How it happened

Our original import carried a single EAN — **5065022450740** — across all three shop SKUs in error. That seeded one master product and bound all three SKUs to it. We have since corrected the EANs and re-submitted, but the corrected SKUs remain attached to the same master (the master now lists more than one EAN). A seller-side re-import updates the existing master rather than splitting it, so we can't separate them from our end.

## What we need

Please **dissociate the three shop SKUs from the shared master product** and create / match each to its own distinct product — one product per EAN — so each appears as a separate listing on diy.com.

The three products, with their correct identifiers:

1. **Shop SKU:** 100134Y  **EAN:** 5065022450764
   **Name:** Spectrum 100134Y Replacement Concrete Mixer Drum - fits Belle, Build Buddy & Screwfix Mixers

2. **Shop SKU:** 100134S  **EAN:** 5065022450771
   **Name:** Spectrum 100134S Replacement Concrete Mixer Drum - fits Belle, Build Buddy & Screwfix Mixers

3. **Shop SKU:** 100134O  **EAN:** 5065022450740
   **Name:** Spectrum 100134O Replacement Concrete Mixer Drum - fits Belle, Build Buddy & Screwfix Mixers

## Notes / current state

- Product status is currently **"New / Not synchronized"** and both seller sources are flagged **"Invalid data."** We are resolving the validation errors in parallel — please advise if these need to be cleared before you can action the split.
- We could not supply a B&Q `product_id` via the seller API (`/products` returns nothing while the product is unsynchronised). The shop SKUs and EANs above should let you locate the merged record.

## Confirmation requested

Please confirm once the split is done so we can verify each EAN resolves to its own product page on diy.com.
