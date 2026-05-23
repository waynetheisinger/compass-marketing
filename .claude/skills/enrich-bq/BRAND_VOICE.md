# MowDirect brand voice

Canonical voice doc for MowDirect product copy. Used by `/enrich-bq` to rewrite
prose for B&Q listings, and (eventually) by `/import-products` for MowDirect
itself. **Single source of truth — if you tweak the voice, tweak it here.**

## Core voice

- Professional, British English, benefit-led.
- Grounded in specifics (cutting width, runtime in minutes, battery voltage) — not adjectives.
- Address the gardener, not the spec sheet. The reader is buying a tool to do a job; tell them how the tool helps.
- Sentences ≤ 25 words. One concrete fact per sentence in bullets.

## Duplicate-content discipline

Each surface (mowdirect.co.uk, B&Q product page, future marketplaces) needs
distinct copy. Search engines de-duplicate, and B&Q's domain authority will
demote MowDirect's copy if they match.

For `/enrich-bq` specifically — when rewriting prose for B&Q:

1. **Open the MowDirect Shopify product page in another tab.** Skim it once
   to understand what's been said. Then **close it.** Do not paraphrase or
   reshuffle. Write fresh.
2. **Different lead sentence.** If MowDirect leads on durability, the B&Q
   version leads on usability (or vice versa). If MowDirect's first verb is
   "powers", yours is something else.
3. **Different paragraph structure.** If MowDirect uses three short
   paragraphs, use two medium ones. If MowDirect uses a list, use prose.
4. **Different verbs.** Don't copy verbs from either compass or MowDirect.
5. **Same product truths.** Cutting width is still 38cm. Battery is still
   40V. Don't reinvent facts to be different — only restructure.

Result test: pick any sentence from the B&Q rewrite. If you can't find a
sentence in MowDirect or compass that's recognisably "the same idea", you've
gone too far. If you can find a near-duplicate, you haven't gone far enough.

## Banned moves

**Never** in any MowDirect-voice copy on a B&Q listing:

- Mention B&Q, Mirakl, Kingfisher, or any retailer name. (The retailer owns
  that messaging — sellers don't.)
- Mention click-and-collect, store pickup, in-store services, B&Q warranty,
  or anything tying the product to a retailer.
- Reference price, sale, discount, or promotional terms. (Those live on the
  offer side, not the product copy.)
- Reference MowDirect by name. (Weird on a B&Q listing.)
- Reference compassgm.co.uk or any supplier.

**Banned phrases / Americanisms** (use the right side):

| Don't | Do |
|---|---|
| gas / gas-powered | petrol / petrol-powered |
| weed eater | strimmer |
| trash, garbage | rubbish |
| color | colour |
| meter (the unit) | metre |
| revolutionary, game-changing, cutting-edge | (just delete; describe the actual benefit) |
| state of the art | (delete) |
| world-class, best-in-class | (delete) |
| robust, rugged, heavy-duty without a concrete number | back it with a spec ("built around a 6mm steel deck") |

## Style markers (positive)

- **Open with what the gardener gets**, not what the product is.
  ✓ "Tackle 800m² of lawn between charges with the SBS460CLM."
  ✗ "The SBS460CLM is a cordless lawnmower."
- **One concrete spec per sentence in bullets.** Bullets work as a scannable
  spec list, not as marketing one-liners.
- **British colloquial OK but restrained.** "Bigger lawns are no bother"
  is fine. "Mate, this thing's a beast" is not.
- **Numbers without units feel hollow.** Always pair: "38cm cutting deck",
  not "wide cutting deck"; "45 minutes per charge", not "long runtime".

## Field-specific style

### Product title
- Lead with brand (Spectrum, Honda, Stiga…) then model code then descriptor.
- 130-char Kingfisher cap — apply `clean_name()` from `mirakl_operators.py`.
- No em-dash, no en-dash, no ×, no degree symbol.
- ✓ "Spectrum SBS460CLM 40V Cordless Lawnmower with 46cm Cutting Deck"

### Marketing description / Product description (multi-paragraph)
- 2-3 short paragraphs (≈ 40-80 words each).
- Para 1: the job the tool does, with the lead spec.
- Para 2: how it does it (battery system, build, ergonomics).
- Para 3 (optional): warranty + battery compatibility for kit builds.

### Feature bullets / Selling_points
- 4-6 bullets, each ≤ 12 words.
- Each bullet states one concrete benefit anchored to a spec.
- ✓ "Powers through 800m² per charge with the 40V 4.0Ah battery"
- ✗ "Powerful battery for long runtime"

### Specs (Spec_*, Tech_*)
- These are factual fields. Not rewriteable. Apply transforms (cm→mm,
  value-list code lookups) per `mirakl_operators.py`.
- The voice rules don't apply here — you're filling enum codes and numbers,
  not writing prose.

## Exemplars

Hierarchy-specific good rewrites live in `EXEMPLARS/`. Read the relevant one
before rewriting. They show the voice in action better than this doc can
describe it.

- `EXEMPLARS/lawnmower-bare.md`
- `EXEMPLARS/lawnmower-kit.md` (not yet written)
- `EXEMPLARS/hedge-trimmer-bare.md` (not yet written)
- `EXEMPLARS/hedge-trimmer-pole.md` (not yet written)
- `EXEMPLARS/leaf-blower-bare.md` (not yet written)
- `EXEMPLARS/battery-bare.md` (not yet written)
- `EXEMPLARS/charger-bare.md` (not yet written)

If your hierarchy has no exemplar yet, follow the voice rules above and
save your rewrite to a new exemplar file at the end of the run.
