# SPECTRUM10 — 10% off promo + homepage countdown banner

Two pieces: **(1)** the discount code, **(2)** the homepage banner. Both are manual
clicks because the automation app lacks `write_discounts` and `write_themes` scopes —
this is faster than a Dev Dashboard scope-release cycle and fully reversible.

---

## 1. Create the discount code (Shopify Admin — ~2 min)

**Admin → Discounts → Create discount → Amount off products**

| Field | Value |
|---|---|
| Method | **Discount code** |
| Code | `SPECTRUM10` |
| Value | **Percentage → 10%** |
| Applies to | **Specific collections → Spectrum** (handle `spectrum`, 101 products) |
| Minimum requirements | None |
| Customer eligibility | All customers |
| Usage limits | (optional) tick *Limit to one use per customer* |
| Combinations | Decide whether it stacks with other discounts (default: off) |
| Active dates | Start: now · **End date: 31 May 2026, 23:59** |

Save. Test at checkout with one Spectrum item + one non-Spectrum item to confirm the
10% only applies to the Spectrum line.

> Scoping to the **Spectrum collection** is what makes "any Spectrum item" work
> automatically — new Spectrum products added to that collection are covered with no
> change to the code.

---

## 2. Add the homepage banner (`spectrum-promo-bar.liquid`)

A self-contained Empire section: brand-matched (orange `#ff6600` on `#121212`), live
ticking countdown, click-to-copy code chip, "Shop Spectrum" CTA → `/collections/spectrum`.
It **hides itself automatically** once the deadline passes, so no cleanup needed.

**Install (paste once):**
1. **Admin → Online Store → Themes → Empire → ⋯ → Edit code**
2. Under **Sections**, click **Add a new section** → name it `spectrum-promo-bar` →
   it creates `spectrum-promo-bar.liquid`.
3. Delete the auto-generated contents, paste the full contents of
   `spectrum-promo-bar.liquid` from this folder, **Save**.

**Place it on the homepage:**
4. **Customize** the theme → on the **Home page** template → **Add section** →
   **Spectrum promo bar** → drag it to the top (just under the header / announcement bar).
5. Everything (copy, code, deadline, colours, button link) is editable in the section
   settings panel. Defaults are already correct. **Save.**

**To remove early:** in Customize, hide or delete the section. (It also auto-hides after
the deadline.)

### Notes
- Deadline is stored in UTC: `2026-05-31T22:59:59Z` = midnight 31 May BST. The timer
  counts down correctly for every visitor regardless of their timezone.
- If you'd rather I push the section straight into the theme instead of pasting, that
  needs the `write_themes` scope added to the automation app first.
