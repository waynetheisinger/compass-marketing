# Plan: March 2026 Week 1–2 Content Campaign

## Context

Two blog articles, each with a paired email, covering the first two weeks of March — the season ignition window. Both articles are published as **drafts with scheduled publish dates**, giving Wayne time to review and approve in Shopify before anything goes live. Emails are similarly held as drafts with scheduled send times.

---

## Schedule

| # | Piece | Type | Draft created | Scheduled publish/send |
|---|---|---|---|---|
| 1 | Best Lawnmowers for 2026 | Blog article | On script run | Mon 9 Mar, 09:00 GMT |
| 2 | Spring Is Here email | Email campaign | Content file + manual import | Thu 12 Mar, 10:00 GMT |
| 3 | Spring Is Here: Is Your Lawn Ready? | Blog article | On script run | Mon 16 Mar, 09:00 GMT |
| 4 | Lawn Ready email | Email campaign | Content file + manual import | Thu 19 Mar, 10:00 GMT |

---

## Content Specifications

### Blog 1: "The Best Lawnmowers for 2026: Which Spectrum Model Is Right for Your Garden?"

Target keywords: `best lawnmower 2026`, `which lawnmower to buy`, `self-propelled mower UK`, `Spectrum lawnmower`

Structure:
1. Intro — grass is growing, now is the time to choose
2. How to match mower to garden size (small / medium / large / acreage)
3. Push mowers — Spectrum entry range (product list with prices)
4. Self-propelled mowers — TG40PD (£149.95), TG40SE (£249.95), TG46S (£199.95), TG46SE (£225), TG51PD (£229)
5. Ride-on / tractor — DC24-4 (£1,399.95), DCT38H
6. Cordless/electric — for compact gardens
7. CTA: shop the full Spectrum range
8. Trust close: 60,000+ orders, 5-year warranty, free delivery

---

### Email 1: Paired with Blog 1

- **Subject:** The Best Lawnmowers for 2026 — Find Yours Now
- **Preview text:** Self-propelled from £149.95. Ride-ons from £1,399.95. 5-year Spectrum warranty.
- **Body:**
  - Opening teaser paragraph (3–4 sentences): spring is here, choosing the right mower matters, we've written the guide → CTA button "Read the guide"
  - Product list block: all Spectrum mowers mentioned in the blog (image, name, price, CTA button per product)
  - Footer CTA: "Shop all lawnmowers"

---

### Blog 2: "Spring Is Here: Is Your Lawn Ready for Its First Cut?"

Target keywords: `first cut of the year`, `when to start mowing`, `spring lawn care UK`, `lawn ready for spring`

Structure:
1. Intro — signs your lawn needs its first cut (grass height, weather, soil)
2. How to prepare: checking the mower, blade condition, oil/fuel check
3. The right cut height for first spring mow
4. What if your current mower isn't up to it — upgrade section featuring Spectrum range
5. Product list: hero self-propelled models (same SKUs as Blog 1, different framing)
6. CTA: shop the Spectrum range
7. Trust close

---

### Email 2: Paired with Blog 2

- **Subject:** Spring Is Here: Is Your Lawn Ready for Its First Cut?
- **Preview text:** Here's what to check before you mow — and what to do if your mower isn't ready.
- **Body:**
  - Opening teaser paragraph: seasonal hook, lawn care angle, → CTA button "Read: Get your lawn ready"
  - Product list block: Spectrum self-propelled range (image, name, price, CTA per product)
  - Footer CTA: "Shop all lawnmowers"

---

## Technical Implementation

### API: Shopify Admin REST API

**Credentials required (stored in `.env`, never committed):**
```
SHOPIFY_SHOP_DOMAIN=mowdirect.myshopify.com
SHOPIFY_ACCESS_TOKEN=<private app token>
SHOPIFY_BLOG_ID=<discovered via get_blog_id script>
```

Private app needs scopes: `write_content`, `read_content`

---

### Blog publishing

**Endpoint:**
```
POST /admin/api/2024-10/blogs/{blog_id}/articles.json
```

Payload sets:
- `published: false` — saves as draft
- `published_at` — ISO 8601 future datetime (e.g. `2026-03-09T09:00:00+00:00`)

Shopify will hold the article as a draft visible in admin, then publish it at the scheduled time automatically once the admin approves it.

---

### Email campaigns — Shopify Email limitation

Shopify Email does not expose a public REST or GraphQL API for programmatically creating and scheduling campaigns. Therefore:

- Scripts generate **complete email HTML files** with all content, product blocks, and CTAs
- Templates use standard responsive HTML compatible with Shopify Email's "Custom HTML" editor
- Wayne opens Shopify Email → New Campaign → Custom HTML → pastes the generated HTML → sets the schedule manually
- This is a ~3 minute task per email; all creative work is done by the script

**Future path:** If this manual step becomes a bottleneck as campaign volume grows, migrate broadcast emails to **Klaviyo** (already specified in the marketing plan, Section 6) which has a full campaign creation + scheduling API.

---

## File Structure

```
marketingPlan/
  scripts/
    shopify_get_blog_id.py            # One-time: lists all blogs to find blog_id
    shopify_publish_article.py        # Publishes a content file as a scheduled draft
  content/
    blog/
      2026-03-09-best-lawnmowers-2026.md
      2026-03-16-spring-lawn-ready.md
    email/
      2026-03-12-best-lawnmowers-email.html
      2026-03-19-spring-lawn-ready-email.html
  .env.example                        # Variable names only — no real values
```

---

## Implementation Steps

1. Create `.env.example` with required variable names
2. Write `shopify_get_blog_id.py` — lists blogs, outputs IDs → Wayne identifies correct blog_id and adds to `.env`
3. Write `shopify_publish_article.py` — reads a markdown file, converts to HTML, POSTs to Shopify as a scheduled draft
4. Write `content/blog/2026-03-09-best-lawnmowers-2026.md` — full blog article 1
5. Write `content/email/2026-03-12-best-lawnmowers-email.html` — email 1 with teaser + product list
6. Write `content/blog/2026-03-16-spring-lawn-ready.md` — full blog article 2
7. Write `content/email/2026-03-19-spring-lawn-ready-email.html` — email 2 with teaser + product list
8. Run `shopify_publish_article.py` for both blog files → two draft articles appear in Shopify admin with scheduled dates
9. Wayne reviews both articles in Shopify admin and approves
10. Wayne creates two Shopify Email campaigns using the generated HTML files, schedules as per the table above

---

## Verification

1. `shopify_get_blog_id.py` → returns list of blog IDs without error (confirms auth)
2. `shopify_publish_article.py 2026-03-09-best-lawnmowers-2026.md` → article visible in Shopify Admin → Blog Posts as a draft with correct scheduled date
3. Same for article 2
4. Open each email HTML file in a browser → confirm product list renders correctly before pasting into Shopify Email
5. After pasting into Shopify Email: send a test email to Wayne's address before scheduling
