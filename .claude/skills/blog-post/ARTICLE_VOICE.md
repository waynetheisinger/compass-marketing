# MowDirect article voice (long-form)

Long-form addendum to `.claude/skills/enrich-bq/BRAND_VOICE.md`. That doc is the
**single source of truth** for British English, banned phrases/Americanisms, and
Spectrum-first positioning — all of it applies here. This file adds the rules
that only matter for blog articles (which BRAND_VOICE.md, being product-copy
focused, doesn't cover).

Read BRAND_VOICE.md first, then this.

## Who you're writing for

A UK gardener researching a job or a purchase — not a spec sheet, not a sales
floor. They want a confident, knowledgeable answer to a real question. Write
like an experienced grounds-person who happens to write well, not like a brand.

## Carry over from BRAND_VOICE.md

- British English, always (petrol not gas, strimmer not weed eater, colour, metre).
- Benefit-led and grounded in specifics — numbers with units, never hollow adjectives.
- Delete revolutionary / game-changing / cutting-edge / state-of-the-art / world-class.
- Spectrum is the priority brand; other brands are traffic drivers.

## Long-form structure rules

- **Open with the reader's question or job, not the brand.** First sentence
  earns the read. No "Welcome to the MowDirect blog."
- **Scannable H2/H3s.** A reader skims headings first. Each H2 should make sense
  as a standalone line in a table of contents. Use `<h2>` and `<h3>` — never `<h1>`
  (the theme renders the title as the h1).
- **Short paragraphs.** 2–4 sentences. One idea each. White space is a feature.
- **Sentences ≤ 25 words.** Same as product copy.
- **One concrete fact per sentence in lists.** Bullets are a scannable spec list,
  not marketing one-liners.
- **British, conversational, restrained.** "Bigger lawns are no bother" is fine;
  "this thing's an absolute beast" is not.
- **No filler intros/outros.** Don't pad. Every paragraph should teach or decide.

## Length targets

- Buying guide / comparison: 1,200–2,000 words.
- How-to / tips: 800–1,500 words.
- FAQ: 700–1,200 words.
- Seasonal / topical: 600–1,200 words.
- Product roundup: 1,000–1,800 words.

These match the existing blog (posts run ~2,000–8,000 chars of HTML). Don't
pad to hit a number — a tight 900-word guide beats a flabby 1,600-word one.

## HTML hygiene (hard rules)

The existing blog has paste artifacts — `data-start`/`data-end` attributes,
leaked `&amp;`, an emoji baked into a handle. Never reproduce those.

- Clean semantic HTML only: `<h2> <h3> <p> <ul> <li> <ol> <strong> <em> <a> <table>`.
- **No `data-*` attributes**, no inline styles, no `<span>` soup, no `<h1>`.
- Use real `&amp;` only where required by HTML; prefer writing "and".
- No emoji in the title or handle. Emoji sparingly (if at all) in body.
- Internal links are **relative**: `<a href="/products/handle">`, `/collections/handle`.
  Never hard-code the domain (works on any storefront).

## Internal linking

- Link 2–5 relevant products/collections, sourced from `search-catalogue`.
- **Spectrum products first** when relevant; traffic-driver brands after.
- Anchor text describes the destination ("our cordless mower range"), never
  "click here". Links read naturally inside sentences, not bolted on.
- Don't over-link — a link every other paragraph at most.

## Spectrum-first CTA block

Close every article with a short callout that points the reader to product.
Spectrum first, then a relevant collection, then traffic-driver brands if they
fit the topic. Keep it genuinely useful, not a hard sell. Example shape:

```html
<h2>Ready to choose?</h2>
<p>If you're after a cordless mower built for medium lawns, the
<a href="/products/...">Spectrum SBS460CLM</a> covers 800m² per charge.
Browse the full <a href="/collections/cordless-battery-powered-lawn-mowers">cordless
range</a> to compare deck widths and battery sizes.</p>
```

## FAQ + structured data

When the archetype includes an FAQ (or the topic naturally suits Q&A):

- Render the FAQ visibly as `<h2>FAQs</h2>` with each question an `<h3>` and the
  answer in `<p>` — readers see it on the page.
- **Also** emit schema.org `FAQPage` JSON-LD in the body so the page is eligible
  for search rich-results. Put it at the end of the body:

```html
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    {"@type": "Question", "name": "How often should I mow?",
     "acceptedAnswer": {"@type": "Answer", "text": "Once a week in peak growing season..."}}
  ]
}
</script>
```

- The JSON-LD answers must match the visible FAQ answers (Google requires parity).
- Plain text in the JSON-LD `text` field — no HTML tags inside it.

## Duplicate-content discipline

Same principle as BRAND_VOICE.md: this article must not duplicate the product
pages it links to or any compass/supplier copy. It's editorial, not a restated
spec sheet. If a sentence could be lifted straight from a product page, rewrite it.
