"""
Mirakl operator configurations — per-operator overlays for the operator-agnostic
Mirakl pusher.

Mirakl's API is identical across all operator instances (Kingfisher/B&Q,
Tesco, The Range, etc.) but each operator defines its own:

  - channel codes (BQ_UK for Kingfisher; TBC for others)
  - hierarchy_code per product category (PIM_xxxxx)
  - attribute schema (which columns each category requires)
  - value-list codes for enum attributes (e.g. brand=4592, guarantee=78
    on Kingfisher; different numbers on Tesco)

This module captures those operator-specific values so mirakl_sbs_push.py can
push to any operator with `--operator KINGFISHER|TESCO|THERANGE`.

KINGFISHER is fully populated as of 2026-05-07 — categories confirmed product-
by-product against the live operator portal taxonomy. TESCO and THERANGE are
stubs to be filled when those marketplace accounts go live.

Usage:
    from scripts.mirakl_operators import OPERATORS, build_product_row, build_offer_row

    op = OPERATORS["KINGFISHER"]
    row = build_product_row(op, sbs_product)   # dict of column → value
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Callable
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Compliance profile — per-operator copy sanitisation
# ---------------------------------------------------------------------------
#
# Each Mirakl operator enforces its own rules on free-text fields: restricted
# characters, length caps, banned promotional/contact phrasing, and (critically
# for marketplaces that are themselves retailers) a ban on competitor-retailer
# references. A listing on Tesco that names "B&Q" or "Screwfix" is a fast route
# to suppression. This profile carries all those rules per operator so the
# operator-agnostic row builders can sanitise reused copy without hardcoding any
# one operator's quirks.

# Restricted special characters most Mirakl operators reject in text fields.
# Kingfisher discovered these via dry-run error 2021 (2026-05-07); the set is a
# safe baseline for any operator. Maps each to an ASCII-safe equivalent.
_DEFAULT_CHAR_REPLACEMENTS: dict[str, str] = {
    "—": " - ",   # em-dash → " - "
    "–": "-",     # en-dash → hyphen
    "×": "x",     # multiplication × → x
    "°": "",      # degree symbol — restricted; strip
    "“": '"',     # left smart quote
    "”": '"',     # right smart quote
    "‘": "'",     # left smart apostrophe
    "’": "'",     # right smart apostrophe
    "•": "*",     # bullet
    "…": "...",   # ellipsis
}

# Contact-detail patterns scrubbed from prose on every operator (URLs, emails,
# UK phone numbers). Listings must not route the buyer off the marketplace.
_CONTACT_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"https?://\S+", re.IGNORECASE),
    re.compile(r"\bwww\.\S+", re.IGNORECASE),
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", re.IGNORECASE),
    re.compile(r"\b\S+\.(?:co\.uk|com|net|org)(?:/\S*)?\b", re.IGNORECASE),
    re.compile(r"(?<!\d)(?:\+?44\s?|0)(?:\d\s?){9,10}(?!\d)"),
)


def _term_pattern(term: str) -> str:
    """Build a case-insensitive, boundary-aware regex source for a brand term.

    Handles multi-word terms (whitespace-flexible, e.g. ``Robert Dyas`` / ``B & Q``)
    and terms containing internal punctuation (``Diy.com``). Boundaries are plain
    word boundaries (``\\w``), so a trailing sentence period — ``Homebase.`` — or
    apostrophe — ``B&Q's`` — does not block the match.
    """
    escaped = re.escape(term)
    # Let any run of whitespace in the term match flexibly (zero-or-more, so
    # "B&Q" and "B & Q" both match a single "B & Q" entry).
    escaped = re.sub(r"\\?\s+", r"\\s*", escaped)
    return r"(?<!\w)" + escaped + r"(?!\w)"


@dataclass
class ComplianceProfile:
    """Per-operator copy-sanitisation rules.

    Fields:
      char_replacements : restricted char → ASCII replacement (applied to all text)
      field_length_caps : portal-display-label → max chars (informational +
                          name auto-truncation)
      banned_phrases    : lowercase phrase → replacement (operator-flagged
                          cross-promotional / off-brand wording, e.g. Kingfisher's
                          "the range" → "the lineup")
      retailer_scrub    : competitor / host-operator retailer brand names that
                          must never appear in a listing on this operator
      banned_promo      : promotional phrases banned by the operator
                          ("best price", "free delivery", …)
    """
    char_replacements: dict[str, str] = field(default_factory=lambda: dict(_DEFAULT_CHAR_REPLACEMENTS))
    field_length_caps: dict[str, int] = field(default_factory=dict)
    banned_phrases: dict[str, str] = field(default_factory=dict)
    retailer_scrub: tuple[str, ...] = ()
    banned_promo: tuple[str, ...] = ()

    def _replace_chars(self, s: str) -> str:
        for ch, rep in self.char_replacements.items():
            s = s.replace(ch, rep)
        return s

    def clean_name(self, s: str, max_chars: int | None = None) -> str:
        """Make a product title acceptable: char-replace, scrub, collapse
        whitespace, then truncate to max_chars at a word boundary."""
        out, _ = self.scrub(self._replace_chars(s))
        out = " ".join(out.split())
        if max_chars and len(out) > max_chars:
            truncated = out[:max_chars]
            cut = truncated.rfind(" ")
            if cut > max_chars * 0.7:
                truncated = truncated[:cut]
            out = truncated.rstrip(" ,.;-")
        return out

    def scrub(self, s: str) -> tuple[str, list[str]]:
        """Remove retailer references, banned promo phrasing and contact details.

        Returns (cleaned_text, hits) where hits is every removed fragment — the
        caller logs these so a human can confirm nothing meaningful was lost.
        """
        if not s:
            return s, []
        out = s
        hits: list[str] = []

        def _strip(pattern: re.Pattern):
            nonlocal out
            found = pattern.findall(out)
            if found:
                hits.extend(m if isinstance(m, str) else " ".join(m) for m in found)
                out = pattern.sub(" ", out)

        # Contact details first, as whole units — otherwise a retailer/own-store
        # token inside a URL/email (mowdirect.co.uk) would be picked off and
        # leave a broken fragment behind.
        for pat in _CONTACT_PATTERNS:
            _strip(pat)
        for term in self.retailer_scrub:
            _strip(re.compile(_term_pattern(term), re.IGNORECASE))
        for phrase in self.banned_promo:
            _strip(re.compile(_term_pattern(phrase), re.IGNORECASE))

        # Tidy whitespace and punctuation left by removals.
        out = re.sub(r"\s{2,}", " ", out)
        # Drop a separator dash orphaned between two removed fragments, incl.
        # one left dangling before end punctuation ("... - !" → "...!").
        out = re.sub(r"\s+[-–]\s*(?=[,.;:!?]|$)", "", out)
        out = re.sub(r"(?:^|(?<=[.;:!?]))\s*[-–]\s+", " ", out)
        out = re.sub(r"\s+([,.;:!?)])", r"\1", out)
        out = re.sub(r"\(\s+", "(", out)
        out = re.sub(r"\s{2,}", " ", out)
        # Strip only dangling separators/whitespace at the ends — never a
        # legitimate sentence-ending period.
        out = out.strip(" ,;:-–")
        return out.strip(), [h.strip() for h in hits if h.strip()]

    def sanitise_prose(self, s: str) -> tuple[str, list[str]]:
        """Full sanitisation for a prose field (Body Copy, USPs, Selling Copy).

        Applies char replacement, operator banned-phrase rewrites, then the
        retailer/promo/contact scrub. Returns (clean_text, hits)."""
        if not s:
            return s, []
        out = self._replace_chars(s)
        for phrase, rep in self.banned_phrases.items():
            out = re.compile(_term_pattern(phrase), re.IGNORECASE).sub(rep, out)
        return self.scrub(out)


# ---------------------------------------------------------------------------
# Field schema — per-operator CSV column names for the core product fields
# ---------------------------------------------------------------------------
#
# Mirakl operators do NOT share column names. Kingfisher's product import uses
# `name` / `ean` / `Body Copy` / `image_main_1`; Tesco's uses `description` /
# `barcode` / `marketingText` / `image1`. The row builder maps each logical
# product field to the operator's actual column via this schema, so no column
# name is hardcoded in build_product_row().

@dataclass
class FieldSchema:
    """Logical product field → operator CSV column-name mapping.

    A field set to None means the operator does not take that field as a core
    column (e.g. Tesco's minimal tracer omits weight/dimensions — they are
    optional, not required)."""
    category: str          # column holding the category code
    sku: str               # seller SKU
    name: str              # product title
    ean: str               # barcode / EAN
    body: str              # marketing / long description
    image_main: str        # primary image
    extra_images: tuple[str, ...] = ()   # additional image slots, in order
    weight: str | None = None
    length: str | None = None
    width: str | None = None
    height: str | None = None
    variant_group: str | None = None


# Default schema reproduces Kingfisher's historical column names so any operator
# without an explicit field_schema behaves exactly as before this change.
_DEFAULT_FIELD_SCHEMA = FieldSchema(
    category="category",
    sku="shop_sku",
    name="name",
    ean="ean",
    body="Body Copy",
    image_main="image_main_1",
    weight="Product_weight",
    length="Product_length",
    width="Product_width",
    height="Product_height",
)


# ---------------------------------------------------------------------------
# Operator config dataclass
# ---------------------------------------------------------------------------

@dataclass
class OperatorConfig:
    name: str                                       # e.g. "KINGFISHER"
    channel: str                                    # e.g. "BQ_UK"
    common_attributes: dict[str, str]               # applied to every product row
    by_product_type: dict[str, dict[str, Any]]      # per-product-type overlays
    per_sku_overrides: dict[str, dict[str, str]] = None  # per-SKU attribute overrides
    state_code: str = "11"                          # offer state — 11 = "New"
    leadtime_to_ship: int = 1                       # days
    logistic_class: str | None = None               # operator-specific shipping class
    name_max_chars: int | None = None               # operator-imposed product name length cap
    dimension_unit_multiplier: float = 1.0          # catalogue cm → operator unit
                                                    # (Kingfisher: 10 because Product_length/width/height
                                                    # are stored in mm. Verified 2026-05-07 by reading
                                                    # SBS560CHT back from the seller portal: our 21cm
                                                    # rendered as "21.00 mm".)
    compliance: ComplianceProfile = None             # per-operator copy-sanitisation rules
    field_schema: FieldSchema = None                 # per-operator core CSV column names
    notes: str = ""

    def __post_init__(self):
        if self.per_sku_overrides is None:
            self.per_sku_overrides = {}
        if self.compliance is None:
            self.compliance = ComplianceProfile()
        if self.field_schema is None:
            self.field_schema = _DEFAULT_FIELD_SCHEMA


# ---------------------------------------------------------------------------
# KINGFISHER (B&Q UK) — fully populated 2026-05-07
# ---------------------------------------------------------------------------
#
# Categories resolved product-by-product against the live operator portal.
# See memory/spectrum_sbs_kingfisher_categories.md for source.
#
# Common attribute baseline lifted from the 2 known-good transformed CSVs
# (imports 1471148 PIM_11486 BATTERY and 1471127 PIM_12681 HEDGE_TRIMMER on
# 2026-05-07). Per-product-type extras are best-effort guesses; unknown
# attributes will be discovered via dry-run transformation_error_report.

# Value-list codes specific to Kingfisher (different numbers on other operators)
_K_SPECTRUM_BRAND     = "4592"   # Acquisition brand value list (verified renders as "Spectrum")
_K_FIVE_YEAR_GTEE     = "81"     # Spec_Guarantee value list — code 81 = "5 years"
                                  # (originally sent 78 thinking 5y; portal rendered "2 years",
                                  # corrected after reading the SBS560CHT page back 2026-05-07)
_K_CHEMISTRY_TOOL     = "17"     # Battery_chemistry in tool categories
_K_CHEMISTRY_BATTERY  = "9"      # Battery_chemistry in PIM_11486 battery category — different domain
_K_PACK_QTY           = "1"      # Core_Pack quantity = single unit
_K_PACK_TYPE          = "3"      # Core_Pack type — 3 = "Each" (verified by reading the
                                  # SBS460CLM details page back from the portal 2026-05-19).
                                  # Earlier code comment said "Box"; that was wrong.
_K_FSC_PEFC_CERT      = "No"
_K_CONTAINS_WOOD      = "2"      # 2 = No
_K_REACH_VERIFIED     = "Yes"
_K_CORDLESS_YES       = "2"      # 2 = Yes (cordless)
_K_WEEE_REGULATED     = "2"      # 2 = Yes
_K_BATTERIES_SUPPLIED_NO  = "5"  # bare tool — no battery in box
_K_BATTERIES_SUPPLIED_YES = "1"  # kit — battery included (best guess; verify via dry-run)
_K_TECH_RECHARGEABLE  = "1"
_K_USB_NO             = "2"      # USB-related no/N-A flags

# Kingfisher compliance profile — codifies BQ_QUIRKS.md sanitisation rules.
# B&Q is the host operator for these listings, so no cross-retailer scrub is
# applied (only its own flagged phrase, "the range" on chargers, 2026-05-20).
_KINGFISHER_COMPLIANCE = ComplianceProfile(
    char_replacements=dict(_DEFAULT_CHAR_REPLACEMENTS),
    field_length_caps={"Name": 130, "Key Feature": 30},
    banned_phrases={"the range": "the lineup"},
    retailer_scrub=(),
    banned_promo=(),
)

KINGFISHER = OperatorConfig(
    name="KINGFISHER",
    channel="BQ_UK",
    state_code="11",
    leadtime_to_ship=1,
    name_max_chars=130,                          # B&Q caps product name length
    compliance=_KINGFISHER_COMPLIANCE,
    dimension_unit_multiplier=10.0,              # Kingfisher stores L/W/H in mm; catalogue is in cm
    common_attributes={
        # Applied to every SBS product regardless of category
        "Acquisition brand":   _K_SPECTRUM_BRAND,
        "Core_Pack quantity":  _K_PACK_QTY,
        "Core_Pack type":      _K_PACK_TYPE,
        "Guarantee":           _K_FIVE_YEAR_GTEE,
        "reach_verified":      _K_REACH_VERIFIED,
        "contains_wood":       _K_CONTAINS_WOOD,
        "fsc_pecl_certified":  _K_FSC_PEFC_CERT,
    },
    by_product_type={
        # ---- Lawn mowers (Core_Product type: 12396 = Lawnmower) ----
        "LAWN_MOWER_BARE": {
            "category": "PIM_13202",
            "core_product_type": "12396",
            "fixed_attributes": {
                "Battery_chemistry":  _K_CHEMISTRY_TOOL,
                "Cordless":           _K_CORDLESS_YES,
                "Batteries_supplied": _K_BATTERIES_SUPPLIED_NO,
                "WEEE_regulated":     _K_WEEE_REGULATED,
            },
        },
        "LAWN_MOWER_KIT": {
            "category": "PIM_13201",
            "core_product_type": "12396",
            "fixed_attributes": {
                "Battery_chemistry":  _K_CHEMISTRY_TOOL,
                "Cordless":           _K_CORDLESS_YES,
                "Batteries_supplied": _K_BATTERIES_SUPPLIED_YES,
                "WEEE_regulated":     _K_WEEE_REGULATED,
            },
        },

        # ---- Leaf blower-vacuums (Core_Product type: 32125 = Garden blower & vacuum) ----
        "LEAF_BLOWER_BARE": {
            "category": "PIM_12657",
            "core_product_type": "32125",
            "fixed_attributes": {
                "Battery_chemistry":  _K_CHEMISTRY_TOOL,
                "Cordless":           _K_CORDLESS_YES,
                "Batteries_supplied": _K_BATTERIES_SUPPLIED_NO,
                "WEEE_regulated":     _K_WEEE_REGULATED,
            },
        },
        "LEAF_BLOWER_KIT": {
            "category": "PIM_12656",
            "core_product_type": "32125",
            "fixed_attributes": {
                "Battery_chemistry":  _K_CHEMISTRY_TOOL,
                "Cordless":           _K_CORDLESS_YES,
                "Batteries_supplied": _K_BATTERIES_SUPPLIED_YES,
                "WEEE_regulated":     _K_WEEE_REGULATED,
            },
        },

        # ---- Hedge trimmers (regular + pole share the category but have
        #      different Core_Product type codes — 22611 vs 33988. The
        #      default is regular; pole SKUs use per_sku_overrides.) ----
        "HEDGE_TRIMMER_BARE": {
            "category": "PIM_12681",
            "core_product_type": "22611",     # default: regular hedge trimmer
            "fixed_attributes": {
                "Battery_chemistry":  _K_CHEMISTRY_TOOL,
                "Cordless":           _K_CORDLESS_YES,
                "Batteries_supplied": _K_BATTERIES_SUPPLIED_NO,
                "WEEE_regulated":     _K_WEEE_REGULATED,
            },
        },
        "HEDGE_TRIMMER_KIT": {
            "category": "PIM_12680",
            "core_product_type": "22611",     # default: regular hedge trimmer
            "fixed_attributes": {
                "Battery_chemistry":  _K_CHEMISTRY_TOOL,
                "Cordless":           _K_CORDLESS_YES,
                "Batteries_supplied": _K_BATTERIES_SUPPLIED_YES,
                "WEEE_regulated":     _K_WEEE_REGULATED,
            },
        },

        # ---- Bare batteries (Core_Product type: 25791 = Battery) ----
        "BATTERY_BARE": {
            "category": "PIM_11486",
            "core_product_type": "25791",
            "fixed_attributes": {
                "Battery_chemistry":           _K_CHEMISTRY_BATTERY,
                "Tech_Rechargeable":           _K_TECH_RECHARGEABLE,
                "USB_Type_C_charger_included": _K_USB_NO,
                "USB_power_delivery":          _K_USB_NO,
                # Maximum/Minimum charging wattage are battery-spec specific —
                # set per-SKU below (Wayne's known-good values + 1C/0.5C derivation
                # for the 2.0Ah pack).
            },
        },

        # ---- Pressure washers, corded electric (Core_Product type: TBD —
        #      discover via dry-run; Cordless code for "No" also TBD) ----
        "PRESSURE_WASHER_CORDED": {
            "category": "PIM_12754",
            "core_product_type": None,        # TBD — discover via dry-run
            "fixed_attributes": {
                "WEEE_regulated":      _K_WEEE_REGULATED,
                "Power_voltage_supply": "16",  # 240V (same code as charger)
                # Cordless = "No" code TBD; Battery_* attributes N/A for corded.
            },
        },

        # ---- Chargers (Core_Product type: 27202 = Battery charger) ----
        "CHARGER_BARE": {
            "category": "PIM_13946",
            "core_product_type": "27202",
            "fixed_attributes": {
                "Battery_chemistry":  _K_CHEMISTRY_TOOL,
                "WEEE_regulated":     _K_WEEE_REGULATED,
                # Power_voltage_supply is required and must be a value-list
                # CODE (not label) — code 16 = "240V" in Spec_Power_voltage_supply.
                "Power_voltage_supply": "16",
            },
        },
    },
    per_sku_overrides={
        # Pole hedge trimmer SKUs — same category as regular hedge trimmer
        # but different Core_Product type code (33988 = Pole hedge trimmer).
        "SBS240CPHT":     {"Core_Product type": "33988"},
        "SBS240CPHT-KIT": {"Core_Product type": "33988"},

        # Battery charging wattage — Wayne's known-good for SBS40CB; SBS20CB
        # derived as 1C max / 0.5C min × 40V (i.e. 80W max / 40W min for 2.0Ah).
        "SBS40CB":        {"Maximum_charging_wattage": "160.00",
                           "Minimum_charging_wattage": "80.00"},
        "SBS20CB":        {"Maximum_charging_wattage": "80.00",
                           "Minimum_charging_wattage": "40.00"},
    },
    notes=(
        "Categories confirmed product-by-product on 2026-05-07. "
        "Core_Product type known for HEDGE_TRIMMER_BARE (33988) and "
        "BATTERY_BARE (25791); others to be discovered via dry-run errors. "
        "Many fixed_attributes are educated guesses copied from the 2 "
        "known-good templates — refine after first dry-run."
    ),
)


# ---------------------------------------------------------------------------
# TESCO — Mirakl instance. Category/attribute schema TBC (needs the Tesco
# seller-portal category template + a /hierarchies + /values_lists walk once
# the API key is in .env). The compliance profile below is ready now and is the
# headline of this slice: Tesco is itself a retailer, so any competitor- or
# host-retailer reference in a listing is a fast route to suppression.
# ---------------------------------------------------------------------------

# UK retailer / marketplace brand names that must never leak into a Tesco
# listing. Word-boundary, case-insensitive. Deliberately excludes ambiguous
# common words (e.g. "Very") to avoid mangling legitimate prose; every scrub
# hit is logged for human review regardless. Does NOT include "Spectrum" —
# that is the brand we are selling.
_UK_RETAILER_NAMES: tuple[str, ...] = (
    "B&Q", "B & Q", "Kingfisher", "Diy.com",
    "Screwfix", "Wickes", "Homebase", "Toolstation", "Robert Dyas",
    "Argos", "Amazon", "eBay", "ManoMano", "OnBuy", "Rackhams", "Wilko",
    "The Range",
    # Our own storefronts — listings must not route the buyer off Tesco.
    "MowDirect", "Mow Direct", "Compass", "compassgm",
)

# Promotional phrasing operators routinely ban from listing copy. The slice
# brief names "best price" and "free delivery"; the rest are common variants.
_BANNED_PROMO: tuple[str, ...] = (
    "best price", "lowest price", "cheapest", "unbeatable price",
    "free delivery", "free postage", "free p&p", "free shipping",
    "buy now", "shop now", "best value",
)

_TESCO_COMPLIANCE = ComplianceProfile(
    char_replacements=dict(_DEFAULT_CHAR_REPLACEMENTS),
    # Caps unconfirmed until the Tesco template is downloaded; 130 mirrors the
    # B&Q name cap as a safe default and is informational-only (manual rewrite).
    field_length_caps={"Name": 130},
    banned_phrases={},
    retailer_scrub=_UK_RETAILER_NAMES,
    banned_promo=_BANNED_PROMO,
)

# Tesco column names (from /products/attributes, 2026-06-09) — completely
# different from Kingfisher's. Tracer maps the required core fields only; weight
# and dimensions are optional on Tesco and omitted for now.
_TESCO_FIELD_SCHEMA = FieldSchema(
    category="shopifyHierarchyId",
    sku="sku",
    name="description",          # Tesco "Title"
    ean="barcode",
    body="marketingText",        # Tesco "Description"
    image_main="image1",
    extra_images=tuple(f"image{i}" for i in range(2, 11)),
    variant_group="variationId",
)

# Tesco categories use the Shopify product taxonomy (gid://shopify/...), NOT
# Kingfisher's PIM_xxxxx. Resolved from /hierarchies 2026-06-09. The Spectrum
# range sits in the Outdoor Power Equipment tree (hg-12-3...).
_TX = "gid://shopify/TaxonomyCategory/"

TESCO = OperatorConfig(
    name="TESCO",
    channel=os.environ.get("MIRAKL_TESCO_CHANNEL", ""),   # from .env (instance-specific)
    # Required value-list / decimal constants common to every Spectrum row.
    # Codes == labels on Tesco (verified 2026-06-09). vatRate is a plain decimal
    # (20% standard-rated) — confirm format on first submission.
    common_attributes={
        "brand":               "Spectrum",
        "baseColour":          "Green",     # Spectrum livery (Wayne, 2026-06-09)
        "countryOfOriginName": "China",     # Wayne, 2026-06-09
        "ageRestriction":      "No",
        "unitQuantity":        "Each",
        "vatRate":             "20",
    },
    by_product_type={
        # category-specific required attrs (e.g. lawnMowerCuttingWidth) are
        # optional for the tracer; only the generic required core is enforced.
        "LAWN_MOWER_BARE":        {"category": _TX + "hg-12-3-5-4"},   # Walk-Behind Mowers
        "LAWN_MOWER_KIT":         {"category": _TX + "hg-12-3-5-4"},
        "LEAF_BLOWER_BARE":       {"category": _TX + "hg-12-3-7"},     # Leaf Blowers
        "LEAF_BLOWER_KIT":        {"category": _TX + "hg-12-3-7"},
        "HEDGE_TRIMMER_BARE":     {"category": _TX + "hg-12-3-3"},     # Hedge Trimmers
        "HEDGE_TRIMMER_KIT":      {"category": _TX + "hg-12-3-3"},
        "BATTERY_BARE":           {"category": _TX + "hg-12-4-7"},     # OPE Batteries
        "CHARGER_BARE":           {"category": _TX + "ha-14-17"},      # Power Tool Chargers
        "PRESSURE_WASHER_CORDED": {"category": _TX + "hg-12-3-12"},    # Pressure Washers
    },
    name_max_chars=None,              # Tesco title cap TBC — no truncation for now
    compliance=_TESCO_COMPLIANCE,
    field_schema=_TESCO_FIELD_SCHEMA,
    notes="Connected 2026-06-09 (base tescouk-prod.mirakl.net). Categories = "
          "Shopify taxonomy; attribute schema is API-discoverable via "
          "/products/attributes?hierarchy=<gid> (no portal template needed). "
          "Required core verified on Walk-Behind Mowers (11 fields); other "
          "categories share the generic core but may add category-specific "
          "required attrs — verify per category on first dry-run.",
)


# ---------------------------------------------------------------------------
# THERANGE — Mirakl instance, connected 2026-06-17 (therangeuk-prod.mirakl.net,
# shop_id 2568). Dialect learned template-first from range/templates/*.xlsx:
# same 3-sheet Mirakl workbook as Tesco/B&Q, but with its OWN column names and
# a category VALUE that is the breadcrumb path itself (e.g.
# "DIY/Power Tools/Chainsaws"), not a Shopify gid (Tesco) or PIM code (B&Q).
# The Range catalogue is MULTI-BRAND (Spectrum + Feider/Alpina/Mountfield/…), so
# brand comes from the Shopify vendor per-product — there is no blanket brand.
# ---------------------------------------------------------------------------

# The Range is itself a retailer, so other-retailer references must be scrubbed —
# but NOT "The Range" (the host) — and Tesco is added (a fellow marketplace we
# also list on). Reuses the Tesco/UK retailer set minus the host plus Tesco.
_THERANGE_RETAILER_NAMES: tuple[str, ...] = tuple(
    n for n in _UK_RETAILER_NAMES if n != "The Range"
) + ("Tesco",)

_THERANGE_COMPLIANCE = ComplianceProfile(
    char_replacements=dict(_DEFAULT_CHAR_REPLACEMENTS),
    field_length_caps={"Title": 130},   # cap unconfirmed; 130 safe default, informational
    banned_phrases={},
    retailer_scrub=_THERANGE_RETAILER_NAMES,
    banned_promo=_BANNED_PROMO,
)

# The Range core column names (from range/templates Data sheet, row 1 = api codes).
# Note: EAN column is `gtin` (not Tesco's `barcode` nor B&Q's `ean`); 20 image
# slots image_1..image_20 after main_image.
_THERANGE_FIELD_SCHEMA = FieldSchema(
    category="category",
    sku="shop_sku",
    name="title",
    ean="gtin",
    body="description",
    image_main="main_image",
    extra_images=tuple(f"image_{i}" for i in range(1, 21)),
    variant_group="variant_group_code",
)

THERANGE = OperatorConfig(
    name="THERANGE",
    channel=os.environ.get("MIRAKL_THERANGE_CHANNEL", ""),
    # Catalogue-wide value-list defaults that are real Range columns. `brand` and
    # `colour_*` are deliberately NOT here — The Range is multi-brand, so brand is
    # the Shopify vendor and colour is resolved per-brand/per-SKU in the batcher.
    common_attributes={
        "made_to_order": "No",
    },
    by_product_type={},   # unused by the template-first range_csv_batch flow
    name_max_chars=None,
    compliance=_THERANGE_COMPLIANCE,
    field_schema=_THERANGE_FIELD_SCHEMA,
    notes="Connected 2026-06-17 (base therangeuk-prod.mirakl.net, shop_id 2568). "
          "Category shape is template-first from range/templates/*.xlsx — NEVER the "
          "API. Category value = the template breadcrumb string. Multi-brand "
          "catalogue (brand = Shopify vendor). Heavier REQUIRED set than Tesco "
          "(colour trio, feature_1..3, dimensions + _uom, material). See "
          "scripts/range_csv_batch.py + the /range-csv-batch skill.",
)


OPERATORS: dict[str, OperatorConfig] = {
    "KINGFISHER": KINGFISHER,
    "TESCO":      TESCO,
    "THERANGE":   THERANGE,
}


# ---------------------------------------------------------------------------
# Portal display label → CSV column (API) name map
#
# Kingfisher's seller portal shows human-readable labels; the /products/imports
# CSV uses different column names. The mapping is NOT a derivable transform
# — some labels gain a Spec_/Tech_/Core_/BQ_ prefix, some get renamed entirely,
# some get truncated. This dict is hand-curated from observed mappings and
# grows as new labels are encountered during /enrich-bq runs.
#
# Conventions:
#   - key = exact label as it appears in the portal (trim trailing space and
#     trailing "*" before looking up)
#   - value = exact CSV column header Mirakl accepts
#   - value of None = no API mapping known; field can only be entered manually
#     via the portal (clipboard fallback in /enrich-bq)
#
# To add a new mapping: drop it in here keyed by the exact portal label.
# ---------------------------------------------------------------------------

KINGFISHER_DISPLAY_TO_API: dict[str, str | None] = {
    # ----- Identity / structural -----
    "Name":                                "name",
    "Shop SKU":                            "shop_sku",
    "EAN":                                 "ean",
    "Category":                            "category",
    "Variant Group Code":                  "variant_group_code",
    "Main Image 1":                        "image_main_1",
    "Body Copy":                           "Body Copy",

    # ----- Brand / packaging -----
    "Acquisition brand":                   "Acquisition brand",
    "Pack quantity":                       "Core_Pack quantity",
    "Pack type":                           "Core_Pack type",
    "Manufacturer guarantee":              "Guarantee",
    "Manufacturer Name":                   None,        # API name not yet confirmed
    "MultiSKU Product Group ID":           None,

    # ----- Compliance / certifications -----
    "REACh Verified":                      "reach_verified",
    "Contains wood and/or paper":          "contains_wood",
    "FSC or PEFC certified":               "fsc_pecl_certified",   # historical typo preserved
    "WEEE regulated":                      "WEEE_regulated",
    "GPSR Exempt":                         None,
    "Made from 100% recycled wood and/or paper": None,
    "Legal information":                   None,

    # ----- Product taxonomy -----
    "Product type":                        "Core_Product type",
    "Lawnmower type":                      None,
    "Propulsion type":                     None,

    # ----- Tool attributes -----
    "Corded/cordless":                     "Cordless",
    "Battery chemistry":                   "Battery_chemistry",
    "Batteries included":                  "Batteries_supplied",
    "Battery voltage":                     None,
    "Cutting width":                       None,
    "Bare unit (without batteries)":       None,
    "Collection capacity":                 None,
    "Function(s)":                         None,
    "Model name/number":                   None,
    "Noise level":                         None,
    "Power source":                        "Tech_Power_source",
    "Power type":                          None,
    "Navigation type":                     "BQ_Nav_type",
    "Grass collector type":                None,
    "Handle type":                         "Spec_Handle_type",
    "Included":                            None,

    # ----- Dimensions -----
    "Product weight":                      "Product_weight",
    "Product length":                      "Product_length",
    "Product width":                       "Product_width",
    "Product height":                      "Product_height",

    # ----- Marketing prose (API names TBD — all may need a one-time
    #       dry-run probe via /products/imports to learn the accepted
    #       column names) -----
    "Selling Copy":                        None,
    "Key Feature":                         None,
    "Unique Selling Point 1":              None,
    "Unique Selling Point 2":              None,
    "Unique Selling Point 3":              None,
    "Unique Selling Point 4":              None,
    "Unique Selling Point 5":              None,
    "Unique Selling Point 6":              None,
    "Unique Selling Point 7":              None,
    "Unique Selling Point 8":              None,

    # ----- Image slots -----
    "Secondary Image 1":                   "image_secondary_1",    # guess; verify on first submit
    "Secondary Image 2":                   "image_secondary_2",
    "Secondary Image 3":                   "image_secondary_3",
    "Secondary Image 4":                   "image_secondary_4",
    "Secondary Image 5":                   "image_secondary_5",
    "Secondary Image 6":                   "image_secondary_6",
    "Secondary Image 7":                   "image_secondary_7",
    "Secondary Image 8":                   "image_secondary_8",
    "Product Identification 1":            None,
    "Product Identification 2":            None,
    "Product Identification 3":            None,

    # ----- Documents / videos (not API-fillable; clipboard / portal upload) -----
    "Product Guide":                       None,
    "Product Instruction Manual":          None,
    "Safety Manual":                       None,
    "Video":                               None,
    "Declaration of Conformity":           None,
    "Declaration of Identity":             None,
    "EU Warning_Safety Information":       None,
    "Safety Test Report 1":                None,
    "Safety Test Report 2":                None,
    "Safety Test Report 3":                None,
    "International Compliance Documents Details": None,
    "International Compliance Status":     None,

    # ----- Safety information (10 slots) -----
    "Warnings and Safety Information 01":  None,
    "Warnings and Safety Information 02":  None,
    "Warnings and Safety Information 03":  None,
    "Warnings and Safety Information 04":  None,
    "Warnings and Safety Information 05":  None,
    "Warnings and Safety Information 06":  None,
    "Warnings and Safety Information 07":  None,
    "Warnings and Safety Information 08":  None,
    "Warnings and Safety Information 09":  None,
    "Warnings and Safety Information 10":  None,
    "Safety Information Asset 1 - Digital Assets 01W": None,
    "Safety Information Asset 2 - Digital Assets 02W": None,
    "Safety Information Asset 3 - Digital Assets 03W": None,
    "Safety Information Asset 4 - Digital Assets 04W": None,
    "Safety Information Asset 5 - Digital Assets 05W": None,
}


# Per-field length caps that Kingfisher enforces (over-cap values are
# rejected by the portal/Mirakl). Keyed by portal display label.
# Back-compat alias — canonical source is now the KINGFISHER compliance profile.
# scripts/bq_enrich.py imports this name. See enrich-bq/BQ_QUIRKS.md.
KINGFISHER_FIELD_LENGTH_CAPS: dict[str, int] = _KINGFISHER_COMPLIANCE.field_length_caps


# Section headers that appear in portal text dumps — used by the parser to
# detect group boundaries, not as fields themselves.
KINGFISHER_PORTAL_SECTIONS = frozenset([
    "Images", "Videos", "Documents", "Specifications",
])

# Inline unit markers — when a line is exactly one of these, it's the unit
# annotation for the previous numeric field (not a field or value of its own).
KINGFISHER_INLINE_UNITS = frozenset([
    "mm", "cm", "m", "kg", "g", "V", "W", "kW", "A", "Ah", "l", "L", "ml", "Hz",
    "dB", "rpm", "min", "h", "°C", "%",
])


# ---------------------------------------------------------------------------
# Extensions loader — merges hierarchies discovered after the May 2026 SBS
# push (written by /enrich-bq's gate-0.5 mapping flow) into the operator
# config. New hierarchies are data-not-code so they're trivial to review
# in PRs and to roll back without touching this file.
# ---------------------------------------------------------------------------

def _load_extensions() -> None:
    """Merge runtime additions from config/bq_operator_extensions.json.

    Currently merges only the operator-level display-label-to-API-name
    overrides into the in-memory KINGFISHER_DISPLAY_TO_API dict (these are
    learned at runtime via /enrich-bq and don't warrant a code edit).

    The per-hierarchy `labels` block is read directly by scripts/bq_enrich.py
    on demand — it doesn't mutate OperatorConfig.

    Silent no-op if the file is missing or malformed.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    ext_path = os.path.join(repo_root, "config", "bq_operator_extensions.json")
    if not os.path.exists(ext_path):
        return
    try:
        with open(ext_path) as fh:
            ext = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return
    kf = ext.get("KINGFISHER", {})
    if not isinstance(kf, dict):
        return
    overrides = kf.get("display_label_to_api_name_overrides", {})
    if isinstance(overrides, dict):
        for label, api in overrides.items():
            # Overrides take precedence over in-code defaults
            KINGFISHER_DISPLAY_TO_API[label] = api


_load_extensions()


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------

# Back-compat aliases. The canonical char map + name-cleaning logic now live on
# ComplianceProfile; these names are retained for any importer that still uses
# them. clean_name() delegates to the Kingfisher profile (its historical home).
_NAME_CHAR_REPLACEMENTS = _DEFAULT_CHAR_REPLACEMENTS


def clean_name(s: str, max_chars: int | None = None) -> str:
    """Make a product title acceptable to Mirakl (Kingfisher profile).

    Back-compat wrapper around ``KINGFISHER.compliance.clean_name``. New code
    should call ``op.compliance.clean_name(...)`` for the operator in hand."""
    return _KINGFISHER_COMPLIANCE.clean_name(s, max_chars)


def build_product_row(op: OperatorConfig, p: Any, report: dict | None = None) -> dict[str, str]:
    """
    Build a Mirakl /products/imports CSV row dict from an SBSProduct + operator config.

    Returns: dict of column-name → string-value. Caller is responsible for serialising
    to CSV with the correct column order and delimiter (Mirakl uses ';').

    p is a sbs_catalogue.SBSProduct (duck-typed — uses sku, ean, product_type,
    title, body_copy, image_url, weight_kg, dim_l_cm, dim_w_cm, dim_h_cm, raw_specs).

    Copy reuse + per-operator sanitisation: ``name`` and ``Body Copy`` are passed
    through ``op.compliance`` (char replacement, banned-phrase rewrite, and the
    cross-retailer / promo / contact scrub). If a ``report`` dict is supplied,
    it is populated with ``report["scrub_hits"]`` (every fragment removed, per
    field) so the caller can surface them in a log for human review.

    Application order (later wins):
      1. core mandatory columns
      2. operator common_attributes
      3. per-product-type fixed_attributes
      4. core_product_type (per-product-type default)
      5. per_sku_overrides for this SKU
    """
    pt_cfg = op.by_product_type.get(p.product_type)
    if not pt_cfg:
        raise ValueError(
            f"Operator {op.name} has no by_product_type entry for {p.product_type} "
            f"(SKU {p.sku}). Populate mirakl_operators.{op.name}.by_product_type."
        )

    cp = op.compliance
    name_clean = cp.clean_name(p.title, op.name_max_chars)
    body_raw = (p.body_copy or "").replace("\r", " ").replace("\n", " ").strip()
    body_clean, body_hits = cp.sanitise_prose(body_raw)
    _, name_hits = cp.scrub(p.title)
    if report is not None:
        scrub_hits = {}
        if name_hits:
            scrub_hits["name"] = name_hits
        if body_hits:
            scrub_hits["Body Copy"] = body_hits
        if scrub_hits:
            report.setdefault("scrub_hits", {})[p.sku] = scrub_hits

    # ---- Core columns, named per the operator's field schema ----
    fs = op.field_schema
    row: dict[str, str] = {
        fs.category:   pt_cfg["category"],
        fs.sku:        p.sku,
        fs.name:       name_clean,
        fs.ean:        p.ean,
        fs.image_main: p.image_url,
        fs.body:       body_clean,
    }

    # Additional image slots, in order (operators that expose image2..imageN).
    extra_urls = getattr(p, "image_urls", None) or []
    for col, url in zip(fs.extra_images, extra_urls[1:]):
        if url:
            row[col] = url

    # ---- Numeric dims/weight (only operators whose schema names them) ----
    # Weight stays in kg. L/W/H multiplied by op.dimension_unit_multiplier
    # (Kingfisher = 10, so cm → mm).
    if fs.weight:
        row[fs.weight] = f"{p.weight_kg:.2f}"
    if fs.length:
        row[fs.length] = f"{p.dim_l_cm * op.dimension_unit_multiplier:.2f}"
    if fs.width:
        row[fs.width] = f"{p.dim_w_cm * op.dimension_unit_multiplier:.2f}"
    if fs.height:
        row[fs.height] = f"{p.dim_h_cm * op.dimension_unit_multiplier:.2f}"

    # Apply operator-wide common attributes
    row.update(op.common_attributes)

    # Apply per-product-type fixed attributes
    row.update(pt_cfg.get("fixed_attributes", {}))

    # Core_Product type from product-type default
    cpt = pt_cfg.get("core_product_type")
    if cpt:
        row["Core_Product type"] = str(cpt)

    # Per-SKU overrides take final precedence
    sku_over = op.per_sku_overrides.get(p.sku) or {}
    row.update(sku_over)

    return row


def build_offer_row(op: OperatorConfig, p: Any) -> dict[str, str]:
    """
    Build a Mirakl /offers/imports CSV row from an SBSProduct + operator config.

    Mirakl offer files use a different column set than product files — this is
    the seller's price/stock offer against an existing catalogue product.
    """
    return {
        "shop-sku":          p.sku,
        "product-id":        p.ean,
        "product-id-type":   "EAN",
        "description":       "",                      # offer-specific copy; blank inherits product
        "internal-description": "",
        "price":             f"{p.price_gbp:.2f}",
        "quantity":          str(max(0, p.stock)),
        "min-quantity-alert": "",
        "state-code":        op.state_code,           # 11 = New
        "available-start-date": "",
        "available-end-date":   "",
        "discount-price":    "",
        "discount-start-date": "",
        "discount-end-date":   "",
        "leadtime-to-ship":  str(op.leadtime_to_ship),
        "update-delete":     "update",                 # upsert by shop-sku
        "logistic-class":    op.logistic_class or "",
    }


__all__ = [
    "OperatorConfig",
    "ComplianceProfile",
    "FieldSchema",
    "KINGFISHER", "TESCO", "THERANGE",
    "OPERATORS",
    "build_product_row", "build_offer_row",
    "clean_name",
]
