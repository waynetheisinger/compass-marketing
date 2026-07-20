"""
Shared physical-dimension completeness guard for the marketplace CSV builders
(bq_csv_batch, tesco_csv_batch, range_csv_batch).

Every product listed on a marketplace must carry BOTH:

  * product dimensions   — length / width / height / depth + product weight
                           (required by The Range, and by product-detail
                           attributes generally); and
  * shipping / package   — box length / width / height + packed weight
    dimensions             (required for Amazon FBA and marketplace
                           Click & Collect fulfilment).

The templates name these cleanly ("Length", "Width", "Height", "Depth",
"Weight", "Shipping Weight", "Product Height", ...) whereas *spec* dimensions
are always qualified ("Cutting Width", "Blade Length", "Weight Capacity",
"Hose length"). This module detects the physical dimension/weight value columns
that actually exist in a given template and flags any left blank in the
assembled row, so a missing measurement can never slip through silently.

See memory: marketplace_dimensions_standard.
"""
from __future__ import annotations

import re
from typing import Any

# Bare dimension nouns that denote a PHYSICAL product- or package-level
# measurement. Deliberately excludes "diameter" (always a spec — bore, blade,
# hose — never the product/box footprint even when a template labels it bare).
_DIM_WORDS = {"length", "width", "height", "depth", "weight", "dimensions"}

# Leading qualifiers that keep a label "physical" (a whole-product or whole-box
# measurement) rather than a spec attribute of a part.
_QUALIFIERS = {
    "product", "shipping", "package", "packaged", "item",
    "gross", "net", "packed", "parcel", "carton", "box", "overall",
}

# Qualifiers that mean the measurement is of the SHIPPING/package, not the bare
# product — used only to label the warning.
_SHIPPING_QUALIFIERS = {
    "shipping", "package", "packaged", "packed", "parcel", "carton", "box",
}


def _normalise_label(label: str) -> str:
    """Lower-case a portal label and strip a trailing unit annotation
    ("(mm)", "(kg)") and a trailing "Unit"/"UOM" word."""
    s = (label or "").strip().lower()
    s = re.sub(r"\([^)]*\)\s*$", "", s).strip()      # drop trailing "(mm)" / "(kg)"
    s = re.sub(r"\s+(unit|uom)\s*$", "", s).strip()  # drop trailing "Unit" / "UOM"
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _is_uom(col: dict[str, Any]) -> bool:
    """A unit-of-measure companion column (e.g. `width_4_uom`, `weightUom`,
    label 'Width Unit') — carries the unit, not the value."""
    code = str(col.get("api_code") or "").lower()
    label = (col.get("portal_label") or "").strip().lower()
    return code.endswith("_uom") or code.endswith("uom") \
        or label.endswith("unit") or label.endswith("uom")


def is_physical_dimension(col: dict[str, Any]) -> bool:
    """True if `col` is a physical product/package dimension or weight *value*
    column — not a spec dimension ("Cutting Width"), not a unit companion."""
    if _is_uom(col):
        return False
    label = _normalise_label(col.get("portal_label") or "")
    if not label:
        # Unlabelled template: fall back to the api_code stem (e.g. "width_4").
        stem = re.split(r"[_\d]", str(col.get("api_code") or "").lower(), 1)[0]
        return stem in _DIM_WORDS
    words = label.split()
    # Strip a single leading qualifier: "product height" -> "height".
    if len(words) == 2 and words[0] in _QUALIFIERS:
        words = words[1:]
    return len(words) == 1 and words[0] in _DIM_WORDS


def physical_dimension_columns(columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The physical dimension/weight value columns present in this template."""
    return [c for c in columns if is_physical_dimension(c)]


def _kind(col: dict[str, Any]) -> str:
    """'shipping' or 'product' — used only to label the warning."""
    label = _normalise_label(col.get("portal_label") or "")
    first = label.split()[0] if label else ""
    code_stem = str(col.get("api_code") or "").lower()
    if first in _SHIPPING_QUALIFIERS or code_stem.startswith(tuple(_SHIPPING_QUALIFIERS)):
        return "shipping"
    return "product"


def missing_dimension_gaps(
    columns: list[dict[str, Any]],
    row: dict[str, Any],
) -> list[dict[str, str]]:
    """Physical dimension columns that exist in the template but are blank in
    `row` (an api_code -> value mapping). Each gap dict mirrors the scripts'
    existing `unknown`-list shape plus a `dimension` marker:

        {"column": api_code,
         "reason": "PHYSICAL DIMENSION blank (product|shipping) — ...",
         "dimension": True}
    """
    gaps: list[dict[str, str]] = []
    for col in physical_dimension_columns(columns):
        code = col["api_code"]
        val = row.get(code)
        if val is None or str(val).strip() == "":
            gaps.append({
                "column": code,
                "reason": (
                    f"PHYSICAL DIMENSION blank ({_kind(col)}) — required for "
                    f"The Range + Amazon FBA / Click & Collect"
                ),
                "dimension": True,
            })
    return gaps
