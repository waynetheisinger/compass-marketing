---
name: blog-ideas
description: Brainstorm a fresh blog-article subject by mining MowDirect's retired blog (old.mowdirect.co.uk) for same-time-of-year articles, then hand off to /blog-post. Surfaces a shortlist of 3-5 creative angles, each a modern re-take on a seasonal old article (never a reproduction), grounded in current Shopify products and the month's hero context, always including at least one fresh idea. Defaults to today's season; can target any month to plan ahead. Once a subject is agreed, hands the brief to /blog-post.
---

# /blog-ideas — seasonal article ideation, then hand off to /blog-post

Front-end to `/blog-post`. Mines the **retired** blog
(`old.mowdirect.co.uk/blog/`) for articles published at the same time of year,
brainstorms a shortlist of fresh angles off them, agrees one with Wayne, then
hands a brief to `/blog-post`. The old article is a **kick-off point, never
reproduced** — the deliverable of this skill is an agreed *subject*, not copy.

`scripts/blog_ideas.py` does the harvest + live-blog dedupe. Product grounding
reuses `scripts/blog_publish.py search-catalogue`. **You** (Claude) do the
creative work: re-angling, grounding, the shortlist. Run from the repo root with
`PYTHONPATH=.`.

## Inputs and conventions

- **Invocation:** `/blog-ideas [optional month — e.g. "June"]`.
- **Target season:** defaults to **today's month**; if Wayne names a month
  (planning ahead — content is usually drafted before it publishes), target that.
- **Working files:** `workdir/blog/` (shared with blog-post; throwaway state).
- The deliverable is an agreed subject handed to `/blog-post` — this skill never
  writes or publishes an article itself.

---

## Step 1 — Resolve the target month

Today's date is known. Default to the current calendar month. If the argument or
Wayne names a different month, use that. Note the month number `M` (1-12).

## Step 2 — Harvest the retired blog

```
PYTHONPATH=. .venv/bin/python scripts/blog_ideas.py harvest --month M
```

Read the JSON. Key fields per candidate: `date`, `year`, `url`, `title`,
`excerpt`. Already-revived posts are dropped silently by the helper (deduped
against the live blog) — you won't see them. Note `candidate_count`:

- **Plenty (≥4):** you have a rich seasonal pool. Good.
- **Thin (1-3):** you'll top up the shortlist with fresh ideas (Step 5).
- **Zero:** the shortlist is entirely fresh ideas (Step 5).

If the harvest errors or returns nothing and you suspect a fetch problem (not
genuinely-empty archives), say so — don't silently pretend the archive is empty.

## Step 3 — Hero context (background only)

Read the current month's row in `docs/MARKETING_PLAN_2026.md` §3.2 (Monthly Hero
Product Calendar). This is **context only** — it tells you what's being promoted
this month so you can *note* when an idea happens to support a hero. It does not
constrain the angles; seasonal relevance and creativity lead.

## Step 4 — Optional deep read

Pick the 1-2 candidates whose `title`/`excerpt` are most promising and read the
full old article for depth (only the finalists — keep it fast):

```
PYTHONPATH=. .venv/bin/python scripts/blog_publish.py fetch-source --url "<old url>"
```

Use this to understand the original angle, spot what's gone **out of date**
(discontinued products, dated references, old advice), and find the seed of a
modern re-take.

## Step 5 — Brainstorm the shortlist (3-5 angles)

Produce 3-5 angles. Rules:

- **Always at least one fresh idea** (not derived from an old article) — drawn
  from your gardening knowledge + the project/season context. Top up with more
  fresh ideas when the seasonal pool is thin.
- **Creative re-takes, never reproductions.** From an old article, take the
  seasonal hook or theme and reinvent it: modernise dated advice, swap retired
  products for current ones, change the angle, or riff thematically onto a
  related topic. If your angle reads like a reworded version of the old post,
  push further.
- **Ground each in real products.** Run `search-catalogue` for the angle's topic
  and name 1-2 real current products/collections (Spectrum-first). Don't invent
  SKUs.

```
PYTHONPATH=. .venv/bin/python scripts/blog_publish.py search-catalogue --query "<topic>"
```

Present each angle compactly:

```
N. <punchy working title>
   Source: <old article title + year> | or: Fresh idea
   Pitch:  <one line — what the article is and its fresh angle>
   Why now: <seasonal/topical hook for this month>
   Archetype: <buying-guide | how-to | faq | seasonal | roundup>
   Could feature: <1-2 real products/collections, Spectrum-first>
   [Supports hero: <hero product> — only if it naturally does]
```

Then ask Wayne to pick one, ask for more, or refine.

## Step 6 — Agree the subject

Once Wayne picks (and any refinement is settled), confirm the brief back in one
block: working title, angle, why-now, archetype, candidate products, and the
source old-article URL (if any).

## Step 7 — Hand off to /blog-post

Invoke the `blog-post` skill and seed its interview with the agreed brief:

- **topic / angle** — the agreed subject and angle.
- **archetype** — your suggestion.
- **keyword** — the primary keyword implied by the title (blog-post confirms).
- **products to feature** — the candidate products you grounded the idea in.
- **old article URL** — pass it **only as background**, explicitly flagged
  *"inspiration — do not reproduce; generate fresh from the brief."* Do **not**
  feed it into blog-post's repurpose-scrape mode.

blog-post then runs its own pipeline (write → image stage → Shopify draft).

---

## Failure modes — handle directly, never silently

- **Harvest returns zero but archives clearly exist** (fetch error, site down) →
  tell Wayne; offer to proceed with all-fresh ideas or retry.
- **Genuinely empty season** → say so, and build an all-fresh shortlist from
  gardening + project context (Step 5 still applies; just no old-article anchors).
- **Every seasonal candidate is weak/dated-beyond-saving** → use them only as
  loose thematic seeds and lean on fresh ideas; tell Wayne that's what you did.
- **`search-catalogue` finds nothing for an angle** → broaden the keyword; if
  still nothing, keep the angle but say it lacks an obvious product anchor (don't
  fabricate one).
- **Wayne rejects the whole shortlist** → ask what direction he'd prefer
  (different category, more playful, a specific old article) and regenerate.

## Artefacts (all in `workdir/blog/`)

- harvest JSON (if you save it) and any fetch-source output for finalists.

Throwaway working state. The real output is the agreed subject handed to
`/blog-post`.
