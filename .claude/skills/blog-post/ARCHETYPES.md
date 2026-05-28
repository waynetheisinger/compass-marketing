# Article archetypes

Structure skeletons for the recurring MowDirect article types. The brief names
one; if it doesn't, infer the best fit from the topic and tell Wayne which you
picked. These are skeletons, not scripts — adapt headings to the actual topic.
All of them obey `ARTICLE_VOICE.md`.

Every archetype ends with the **Spectrum-first CTA block** (see ARTICLE_VOICE.md).

---

## 1. Buying guide  — `buying-guide`

For "how to choose a …" / "best … for …" intent. Highest commercial value.

- **Intro** (1 short para): the decision the reader faces, why it matters.
- **H2: The key things that decide it** — the 3–6 factors (e.g. lawn size,
  terrain, power source, cutting width). One H3 or bullet per factor, each
  anchored to a concrete spec.
- **H2: Petrol vs cordless vs mains** (or the relevant either/or for the category) —
  honest trade-offs.
- **H2: Our pick(s)** — Spectrum first, then traffic-driver brands. Link products.
- **H2: FAQs** — 3–5 questions + FAQ JSON-LD.
- **CTA block.**

## 2. How-to / tips  — `how-to`

For task intent ("how to …", "tips for …"). Practical, seasonal-friendly.

- **Intro**: the job and the payoff of doing it right.
- **H2: What you'll need** — tools/kit (link relevant products naturally).
- **H2: Step-by-step** (or "N tips for …") — numbered `<ol>` or H3 per step.
  Each step is concrete and ordered.
- **H2: Common mistakes** (optional) — what goes wrong and how to avoid it.
- **H2: FAQs** (optional) — + JSON-LD if included.
- **CTA block.**

## 3. FAQ  — `faq`

For question-cluster intent ("… and other FAQs"). Q&A-led throughout.

- **Intro** (1 para): frames the cluster of questions.
- **H2 per theme**, **H3 per question**, answer in `<p>` directly under it.
- 6–12 questions grouped into 2–4 themes.
- **FAQ JSON-LD is mandatory** for this archetype — mirror every visible Q&A.
- **CTA block.**

## 4. Seasonal / topical  — `seasonal`

For calendar-driven content (spring prep, winter care, "get the garden ready
for …"). Ties to the hero-product calendar.

- **Intro**: the season/occasion and what it demands of the garden.
- **H2: The jobs that matter now** — 3–5 timely tasks, each a short section.
- **H2: Kit that helps** — link seasonally relevant products, Spectrum first.
- **H2: FAQs** (optional).
- **CTA block.**

## 5. Product roundup / comparison  — `roundup`

For "best X" / "X vs Y" / range overviews.

- **Intro**: who this roundup is for and how you chose.
- **H2 per product (or per tier)** — H3 name, a short para, key specs as bullets,
  who it suits. Spectrum entries first. Link each product.
- **H2: How they compare** — a small `<table>` of the headline specs (optional but
  strong for this archetype).
- **H2: FAQs** (optional) — + JSON-LD if included.
- **CTA block.**

---

## Choosing when unspecified

| Topic smells like… | Archetype |
|---|---|
| "how to choose / best … for" | buying-guide |
| "how to / tips for / steps to" | how-to |
| "questions about / what is / do I need" | faq |
| month/season/occasion-driven | seasonal |
| "best X" / "X vs Y" / range overview | roundup |

When in doubt between buying-guide and roundup: buying-guide teaches the
*decision*; roundup compares the *options*. A page can lean on one and borrow a
section from the other.
