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

from typing import Any, Callable
from dataclasses import dataclass


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
    notes: str = ""

    def __post_init__(self):
        if self.per_sku_overrides is None:
            self.per_sku_overrides = {}


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
_K_PACK_TYPE          = "3"      # Core_Pack type — 3 maps to "Box" or similar (verbatim from known templates)
_K_FSC_PEFC_CERT      = "No"
_K_CONTAINS_WOOD      = "2"      # 2 = No
_K_REACH_VERIFIED     = "Yes"
_K_CORDLESS_YES       = "2"      # 2 = Yes (cordless)
_K_WEEE_REGULATED     = "2"      # 2 = Yes
_K_BATTERIES_SUPPLIED_NO  = "5"  # bare tool — no battery in box
_K_BATTERIES_SUPPLIED_YES = "1"  # kit — battery included (best guess; verify via dry-run)
_K_TECH_RECHARGEABLE  = "1"
_K_USB_NO             = "2"      # USB-related no/N-A flags

KINGFISHER = OperatorConfig(
    name="KINGFISHER",
    channel="BQ_UK",
    state_code="11",
    leadtime_to_ship=1,
    name_max_chars=130,                          # B&Q caps product name length
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
# TESCO — stub. Populate when account goes live.
# ---------------------------------------------------------------------------

TESCO = OperatorConfig(
    name="TESCO",
    channel="",                       # TBC
    common_attributes={},
    by_product_type={},
    notes="Stub — populate when Tesco Mirakl account is provisioned. "
          "Walk /hierarchies + /values_lists when API key is in env.",
)


# ---------------------------------------------------------------------------
# THERANGE — stub.
# ---------------------------------------------------------------------------

THERANGE = OperatorConfig(
    name="THERANGE",
    channel="",
    common_attributes={},
    by_product_type={},
    notes="Stub — populate when The Range Mirakl account is provisioned.",
)


OPERATORS: dict[str, OperatorConfig] = {
    "KINGFISHER": KINGFISHER,
    "TESCO":      TESCO,
    "THERANGE":   THERANGE,
}


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------

# Characters Mirakl/Kingfisher rejects as "restricted special characters" in
# product names (per dry-run error 2021 on 2026-05-07). Map them to ASCII
# equivalents that keep the title readable.
_NAME_CHAR_REPLACEMENTS = {
    "—": " - ",   # em-dash → " - "
    "–": "-",     # en-dash → hyphen
    "×": "x",     # multiplication × → x
    "°": "",      # degree symbol — restricted on Kingfisher (per dry-run 1471724); strip
    "“": '"',     # left smart quote
    "”": '"',     # right smart quote
    "‘": "'",     # left smart apostrophe
    "’": "'",     # right smart apostrophe
    "•": "*",     # bullet
    "…": "...",   # ellipsis
}


def clean_name(s: str, max_chars: int | None = None) -> str:
    """
    Make a product title acceptable to Mirakl (or specifically Kingfisher):
      1. Replace 'restricted special characters' with ASCII equivalents
      2. Collapse whitespace
      3. Truncate to max_chars at a word boundary if specified
    """
    out = s
    for ch, rep in _NAME_CHAR_REPLACEMENTS.items():
        out = out.replace(ch, rep)
    # Collapse multiple spaces introduced by replacements
    out = " ".join(out.split())
    if max_chars and len(out) > max_chars:
        # Truncate at the last word boundary that fits
        truncated = out[:max_chars]
        cut = truncated.rfind(" ")
        if cut > max_chars * 0.7:   # only word-break if we don't lose too much
            truncated = truncated[:cut]
        out = truncated.rstrip(" ,.;-")
    return out


def build_product_row(op: OperatorConfig, p: Any) -> dict[str, str]:
    """
    Build a Mirakl /products/imports CSV row dict from an SBSProduct + operator config.

    Returns: dict of column-name → string-value. Caller is responsible for serialising
    to CSV with the correct column order and delimiter (Mirakl uses ';').

    p is a sbs_catalogue.SBSProduct (duck-typed — uses sku, ean, product_type,
    title, body_copy, image_url, weight_kg, dim_l_cm, dim_w_cm, dim_h_cm, raw_specs).

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

    row: dict[str, str] = {
        # ---- Core mandatory columns (universal across Mirakl operators) ----
        "category":      pt_cfg["category"],
        "shop_sku":      p.sku,
        "name":          clean_name(p.title, op.name_max_chars),
        "ean":           p.ean,
        "image_main_1":  p.image_url,
        "Body Copy":     p.body_copy.replace("\r", " ").replace("\n", " ").strip(),

        # ---- Numeric dims/weight (Mirakl: numeric, no unit, ≥0.01) ----
        # Weight stays in kg. L/W/H multiplied by op.dimension_unit_multiplier
        # (Kingfisher = 10, so cm → mm).
        "Product_weight": f"{p.weight_kg:.2f}",
        "Product_length": f"{p.dim_l_cm * op.dimension_unit_multiplier:.2f}",
        "Product_width":  f"{p.dim_w_cm * op.dimension_unit_multiplier:.2f}",
        "Product_height": f"{p.dim_h_cm * op.dimension_unit_multiplier:.2f}",
    }

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
    "KINGFISHER", "TESCO", "THERANGE",
    "OPERATORS",
    "build_product_row", "build_offer_row",
]
