---
name: blog-post
description: Create a MowDirect Shopify blog article as an unpublished draft. Interview-style, one article per run. Generates SEO-optimised long-form copy in MowDirect voice (buying guide / how-to / FAQ / seasonal / roundup) from a topic brief or by repurposing a MowDirect/Compass URL, inserts Spectrum-first internal links, runs a gated AI image stage (Gemini, product-likeness aware) with optional handoff, and creates the article via articleCreate. Wayne reviews and publishes in Shopify admin.
---

# /blog-post — create a Shopify blog article (draft)

Produces **one** article, created as an **unpublished draft** in the
"How tos and Garden Advice" blog (`news`). Wayne reviews and publishes inside
Shopify admin — this skill never publishes live.

`scripts/blog_publish.py` does all the deterministic plumbing (Shopify search,
URL fetch, title-collision check, Gemini image gen, image upload, articleCreate).
**You** (Claude) do the writing, the internal-link curation, and the image-prompt
work. Run every command from the repo root with `PYTHONPATH=.`.

## Inputs and conventions

- **Invocation:** `/blog-post [optional topic or MowDirect/Compass URL]`.
- **Two input modes:** (a) a topic brief, or (b) repurpose an existing
  MowDirect/Compass URL. They merge into the same pipeline.
- **Working files:** `workdir/blog/` (scraped source, article spec JSON,
  generated images, run state). The deliverable is the Shopify draft itself —
  never write anything to `reports/`.
- **Blog target:** defaults to handle `news`. Pass `--blog-handle` only if that
  ever changes.

Read these once at the start and apply them throughout:
- `.claude/skills/enrich-bq/BRAND_VOICE.md` (canonical voice — British English,
  banned phrases, Spectrum-first)
- `.claude/skills/blog-post/ARTICLE_VOICE.md` (long-form rules + HTML hygiene +
  FAQ JSON-LD + CTA)
- `.claude/skills/blog-post/ARCHETYPES.md` (structure skeletons)

---

## Step 1 — Interview

If a topic or URL was passed as an argument, use it to pre-fill. Then ask for
anything still missing, one short batch:

1. **Topic / angle** — what's the article about? (Or confirm the passed topic.)
2. **Input mode** — fresh from brief, or repurpose a URL? If a URL, capture it.
3. **Archetype** — buying-guide / how-to / faq / seasonal / roundup. Offer your
   inferred pick from the topic (see ARCHETYPES.md table) and let Wayne confirm
   or override.
4. **Primary keyword** — the search phrase to target (drives title, handle, SEO
   meta). Infer a sensible default from the topic and confirm.
5. **Products/collections to feature** — any specific SKUs/ranges he wants linked,
   or let you choose from `search-catalogue`. Spectrum-first either way.
6. **Image** — supplied file/URL? a source page to pull a lifestyle image from?
   or AI-generate? (Default cascade resolves this in Step 5; just capture any
   supplied image or preference now.)

Keep it brief — infer and propose defaults rather than interrogating.

## Step 2 — Source material (repurpose mode only)

If repurposing a URL:

```
PYTHONPATH=. .venv/bin/python scripts/blog_publish.py fetch-source \
    --url "<url>" --out workdir/blog/<slug>_source.json
```

Read the JSON. Use `text` as raw material to **transform, never copy** (duplicate-
content discipline). Note `image_candidates` — these (plus any linked product
images) feed the image cascade in Step 5.

## Step 3 — Internal-link candidates

Search the catalogue for products/collections relevant to the topic (run it a
few times with different keywords if needed):

```
PYTHONPATH=. .venv/bin/python scripts/blog_publish.py search-catalogue \
    --query "<keyword or product type>"
```

Read the JSON. Products come Spectrum-first (`is_spectrum`). Pick 2–5 to link in
the body and the CTA block, using the relative `path` values. Keep the featured
`image_url` of any linked product handy for the image cascade.

## Step 4 — Write the article

Following the chosen archetype skeleton and the voice docs, write:

- **title** — keyword-led, British, no emoji/em-dash.
- **body_html** — clean semantic HTML (see ARTICLE_VOICE.md hygiene rules). H2/H3
  structure per the archetype. Internal links inserted as relative `<a href>`.
  FAQ section + `FAQPage` JSON-LD if the archetype includes one. Spectrum-first
  CTA block at the end.
- **summary_html** — a 1–2 sentence excerpt for the blog index (existing posts
  leave this blank — don't).
- **seo_title** — ≤ 60 chars, keyword-led, can append " | MowDirect".
- **seo_description** — ≤ 155 chars, benefit-led, includes the keyword.
- **tags** — 1–3 lowercase tags (archetype + topic, e.g. `["buying guide","cordless"]`).
- **handle** — omit it; the script derives a clean one from the title. Only set it
  to override.

Write the spec to `workdir/blog/<slug>_article.json` (without `image_url` yet):

```json
{
  "title": "...",
  "body_html": "...",
  "summary_html": "...",
  "seo_title": "...",
  "seo_description": "...",
  "tags": ["...", "..."]
}
```

## Step 5 — Title collision check

```
PYTHONPATH=. .venv/bin/python scripts/blog_publish.py check-title --title "<title>"
```

Overlapping topics are fine (the blog favours newest). Only on `exact_collision: true`,
**rewrite the title** to be genuinely distinct (not just a suffix) and re-run the
check. Update the spec JSON with the new title.

## Step 6 — Image stage (gated, iterative)

Resolve the hero image via this cascade (stop at the first that applies):

1. **Supplied** image (file or URL) → use it.
2. **Lifestyle image** from the repurposed page's `image_candidates` → pick the
   best lifestyle shot (not a white-background studio cutout).
3. **AI-generate** a lifestyle hero (default). It may include the real product —
   pass the product's `image_url` as a `--reference` so the likeness is preserved.

White-background product studio shots go **inline** next to product mentions, not
in the hero slot.

### 6a. API loop (default)

Write an image prompt (a lifestyle garden scene matching the article; if a product
features, describe it and rely on the reference image for likeness — never describe
a mangled product).

**Composition rules — always include these in the prompt.** The blog theme crops
the hero to a wide banner, so a portrait or tightly-framed shot loses the top of
heads and key detail:

- **Wide landscape orientation**, composed as a banner (roughly 16:9 / 1536×640).
- **Headroom and safe margins** — keep people, the product and any focal detail
  well inside the frame with clear space above; nothing important near the top
  edge (heads get cropped otherwise) or hard against the sides.
- **Subject in the lower-two-thirds**, horizon/sky giving breathing room up top,
  so a banner crop still reads.
- Natural, realistic photography; soft natural light; shallow depth of field.

Save to `workdir/blog/<slug>_imgprompt.txt`, then:

```
PYTHONPATH=. .venv/bin/python scripts/blog_publish.py gen-image \
    --prompt-file workdir/blog/<slug>_imgprompt.txt \
    --reference "<product image_url>" \
    --name <slug> --out-dir workdir/blog
```

**Before showing Wayne — self-review the image against this checklist.** The
goal is to catch obvious failures (anatomy, miscounted parts, prompt
non-compliance) before involving him. Read the PNG with the Read tool and work
through each item explicitly. Be calibrated — only mark a fail if you can point
to the specific issue.

```
Self-review checklist (every gen-image call):
1. Anatomy & figures: any person rendered intact? Both hands? Both feet
   grounded (not levitating)? Limbs attached at the right joints?
   Faces non-melted? No extra fingers/heads?
2. Reference fidelity: does the rendered product match the reference's
   key structural features? Count the specific parts that matter for the
   product type (wheels, blades, handles, battery slots, fuel-tank vs
   battery, two-stroke vs four-stroke giveaways). Note the exact
   constraint the prompt named and check it visibly held.
3. Prompt compliance — explicit constraints: scan the prompt for any
   "CRITICAL" or "must" clauses. Confirm each one visibly held in the
   image. Examples: "mower aligned with stripes", "two front wheels not
   four", "full-body grounded", "no visible birds".
4. Banner-crop safety: mentally crop the image to a centred 16:9 strip
   (top ~25% will be lost on the blog index card crop). Does the subject
   still read? Is anything important sitting within 40-60px of the top
   edge that the crop will eat?
5. Era / vibe: does the rendered product look like a current-era model
   that MowDirect would actually sell, or is it stuck in 1990s aesthetics?
   (You're less reliable at this than the human — flag honestly when
   unsure; don't assert "modern" if you can't tell.)
6. Brand/text: no spurious logos, signs, banners, watermarks, or
   readable text strings the prompt didn't ask for?
```

Produce a short structured verdict before deciding what to do:

```
Self-review: <slug>_<N>.png
  Anatomy:           ✓ / ✗ <one-line note>
  Reference fidelity:✓ / ✗ <one-line note>
  Prompt compliance: ✓ / ✗ <one-line note>
  Banner-safe:       ✓ / ✗ <one-line note>
  Era / vibe:        ✓ / ? / ✗ <one-line note — ? if you can't tell>
  Brand/text:        ✓ / ✗ <one-line note>
  Verdict: pass | auto-revise | show-to-Wayne-anyway
```

Decision rules:

- **Any ✗ on Anatomy, Reference fidelity, Prompt compliance, or Brand/text**
  → revise the prompt to specifically address the failure, regenerate, re-review.
  This is an **auto-loop without involving Wayne**, capped at **2 auto-revisions
  per image stage** (so up to 3 total generations). Each auto-revision must
  target a *different* fault than the previous one — if the same fault recurs,
  stop auto-looping and surface to Wayne with the full attempt history.
- **✗ on Banner-safe alone** → one auto-revision attempting tighter composition
  rules; if it persists, surface to Wayne (he can still approve if the full
  hero looks right).
- **? on Era / vibe, or any other ambiguous case** → surface to Wayne without
  auto-revising. You are less reliable on aesthetic judgement.
- **All ✓** → surface to Wayne for sign-off (you don't approve on his behalf).

When you surface to Wayne, **show the self-review verdict alongside the image**
(so he knows what you checked and what you flagged). Use the SendUserFile tool
for the image; include the verdict block in the caption or message body. If
you auto-revised, summarise the attempt history in one sentence so he can spot
patterns:

```
Auto-revised 2× (attempt 1: legless figure; attempt 2: still missing feet —
fixed in attempt 3 by adding "feet visible on grass" explicitly). This is
attempt 3 of 3.
```

Then **wait for his approval**. If he gives feedback, revise the prompt and
re-run, with a fresh self-review on the result. Loop until he approves. (This
stage is async — he may come back later; resume from his feedback.)

If `GEMINI_API_KEY` is missing or `google-genai` isn't installed, the command
says so — switch to handoff mode (6b).

### 6b. Handoff mode (when Wayne prefers an external UI)

Write the prompt and download the reference image(s) into `workdir/blog/`. Tell
Wayne to generate in his tool of choice and drop the approved file into
`workdir/blog/`. When he confirms the filename, continue.

### 6c. Upload (only for local AI/handoff files)

A supplied/scraped/product **URL** needs no upload — pass it straight to the
article. For a **local file** (AI-generated or handoff), upload it first:

```
PYTHONPATH=. .venv/bin/python scripts/blog_publish.py upload-image \
    --file workdir/blog/<slug>_1.png --alt "<descriptive alt text>"
```

Take the returned `cdn_url`.

Add `image_url` (the URL or `cdn_url`) and `image_alt` to the spec JSON.

## Step 7 — Create the draft

Dry-run first to eyeball the assembled input:

```
PYTHONPATH=. .venv/bin/python scripts/blog_publish.py create-article \
    --json workdir/blog/<slug>_article.json --dry-run
```

If it looks right, create it for real (drop `--dry-run`). The article is created
**unpublished**.

## Step 8 — Report

Read the create result and tell Wayne, concisely:

```
Draft created: "<title>"
  Blog:    How tos and Garden Advice
  Handle:  <handle>
  Archetype: <archetype> | Keyword: <keyword>
  Internal links: <N> (Spectrum-first: <list>)
  Image:   <hero source — supplied / scraped / AI-generated>
  SEO:     title_tag + description_tag set
  Status:  UNPUBLISHED — review & publish here:
           <admin_url>
```

If the script auto-renamed on a collision (`auto_renamed: true`), flag it.

---

## Failure modes — handle directly, never silently

- **No topic resolvable** → ask Wayne; don't invent one.
- **`fetch-source` fails** (403/timeout) → tell Wayne; offer to proceed from the
  brief alone or have him paste the source text.
- **`search-catalogue` returns nothing relevant** → broaden the keyword and retry;
  if still nothing, write without internal links and say so (don't fabricate handles).
- **`exact_collision`** → rewrite the title distinctly and re-check. Don't rely on
  the script's defensive auto-suffix; that's a last-resort guard.
- **`GEMINI_API_KEY` missing / `google-genai` not installed** → switch to handoff
  mode (6b). Note for setup: `GEMINI_API_KEY` goes in `.env`; install with
  `.venv/bin/pip install google-genai`.
- **`articleCreate` userErrors** → surface them verbatim; fix the offending field
  (often a metafield type or an HTML issue) and retry. Don't leave a half-made draft
  unreported.
- **Image upload times out** → the file may still process; re-run `upload-image`
  or set the image manually in admin. Tell Wayne.

## Artefacts (all in `workdir/blog/`)

- `<slug>_source.json` — fetch-source output (repurpose mode)
- `<slug>_article.json` — the article spec you build (the create input)
- `<slug>_imgprompt.txt` — image prompt
- `<slug>_N.png` — generated/handoff images

These are throwaway working state. The durable deliverable is the Shopify draft.
