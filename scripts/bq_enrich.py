"""
Plumbing for the /enrich-bq skill.

Each subcommand is one step in the skill's flow. The skill (SKILL.md) tells
Claude when to call which subcommand and when to pause for user input.

Subcommands:
    resolve <compass_url>           — scrape compass + Shopify lookup → resolved.json
    mirakl-lookup <sku>             — call /products?product_references=EAN|<ean>
    hierarchy-status <PIM>          — MAPPED+LABELS / MAPPED+NO_LABELS / UNMAPPED
    parse-form-dump --pim ...       — parse a portal text-dump paste
                                      (bootstrap: --input <path>, no --sku)
                                      (populated: --input <path> --sku <sku>)
    enrich-vlc <sku>                — annotate value-list fields with their dropdown options
    prepare-gate2 <sku>             — emit the portal-fill markdown

All files use a consistent timestamped naming convention in bq/enriched/:
    <sku>_<YYYYMMDD_HHMM>_<step>.{json,md}

Resume: the skill chooses the timestamp once at run start and reuses it
across subcommands by reading the most recent unfinished run from the dir.

Discovery model:
    The Kingfisher API does not expose a usable attribute schema. Field
    discovery is done by parsing a copy-paste of the seller-portal editable
    form. First time hitting a hierarchy → bootstrap (value-free paste,
    saves label set). Subsequent runs in that hierarchy → populated paste
    cross-referenced against the saved labels.

    /values_lists is used by `enrich-vlc` only to attach dropdown options
    to value-list-backed fields (so Wayne sees the available choices in
    the output markdown). The skill does not submit to the API.

Output model:
    The end deliverable is bq/enriched/<sku>_<ts>_gate2.md — a portal-fill
    document Wayne copy-pastes into the B&Q seller portal. No CSV builder,
    no /products/imports submit, no clipboard loop. The file IS the result.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Ensure imports work whether invoked from repo root or scripts/
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

_REPO_ROOT = os.path.dirname(_THIS_DIR)
_ENRICHED_DIR = os.path.join(_REPO_ROOT, "bq/enriched")
_EXT_FILE = os.path.join(_REPO_ROOT, "config", "bq_operator_extensions.json")


# ---------------------------------------------------------------------------
# File naming and the run-state helpers
# ---------------------------------------------------------------------------

def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M")


def _latest_run(sku: str, step: str | None = None) -> str | None:
    """Find the most recent <sku>_<stamp>_<step>.{json,md} in bq/enriched.

    If step is None, returns the latest run prefix (sku_stamp) for which any
    file exists. If step is given, returns the path of the latest matching file.
    """
    if not os.path.isdir(_ENRICHED_DIR):
        return None
    pat = re.compile(rf"^{re.escape(sku)}_(\d{{8}}_\d{{4}})_(.+)\.(json|md)$")
    candidates: list[tuple[str, str, str]] = []
    for f in os.listdir(_ENRICHED_DIR):
        m = pat.match(f)
        if not m:
            continue
        candidates.append((m.group(1), m.group(2), f))
    if not candidates:
        return None
    if step is None:
        # Latest stamp regardless of step
        stamp = max(c[0] for c in candidates)
        return f"{sku}_{stamp}"
    matches = [c for c in candidates if c[1] == step]
    if not matches:
        return None
    latest = max(matches, key=lambda c: c[0])
    return os.path.join(_ENRICHED_DIR, latest[2])


def _run_path(sku: str, stamp: str, step: str, ext: str) -> str:
    os.makedirs(_ENRICHED_DIR, exist_ok=True)
    return os.path.join(_ENRICHED_DIR, f"{sku}_{stamp}_{step}.{ext}")


def _read_resolved(sku: str) -> dict:
    """Read the most recent resolved.json for a SKU. Raise if not found."""
    path = _latest_run(sku, "resolved")
    if not path:
        raise SystemExit(
            f"No resolved.json for {sku} — run `bq_enrich.py resolve <compass_url>` first."
        )
    with open(path) as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# resolve — compass scrape + Shopify SKU/EAN lookup
# ---------------------------------------------------------------------------

def _scrape_compass(url: str) -> dict:
    """Run the existing compass scraper. Returns its parsed output dict."""
    import subprocess

    tmp = os.path.join(_ENRICHED_DIR, ".compass_scrape.tmp.json")
    os.makedirs(_ENRICHED_DIR, exist_ok=True)
    cmd = [
        sys.executable,
        os.path.join(_THIS_DIR, "compassgm_scraper.py"),
        url,
        "--output", tmp,
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = _REPO_ROOT + ":" + env.get("PYTHONPATH", "")
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit(
            f"compass scraper failed (exit {res.returncode}):\n"
            f"STDOUT: {res.stdout}\nSTDERR: {res.stderr}"
        )
    with open(tmp) as fh:
        data = json.load(fh)
    os.remove(tmp)
    if isinstance(data, list):
        # Scraper returns a list when given multiple URLs; we sent one
        data = data[0]
    return data


def _shopify_search(field: str, value: str) -> list[dict]:
    """Search Shopify productVariants by sku or barcode. Returns raw node list."""
    from shopify_client import ShopifyClient

    query = """
    query($q: String!) {
      productVariants(first: 5, query: $q) {
        edges {
          node {
            sku
            barcode
            product {
              id
              title
              vendor
              productType
              onlineStoreUrl
            }
          }
        }
      }
    }
    """
    with ShopifyClient() as client:
        result = client.execute(query, variables={"q": f"{field}:{value}"})
    # shopify_client.execute() already unwraps the 'data' field — index directly.
    return [
        e["node"]
        for e in result.get("productVariants", {}).get("edges", [])
    ]


def _shopify_resolve(compass_sku: str, compass_ean: str) -> dict:
    """Resolve a compass scrape to a MowDirect Shopify variant.

    Identifier invariant: EAN is canonical. SKUs can drift; barcodes don't.

    Resolution chain:
      1. Try Shopify by SKU. If found AND barcode matches compass EAN → use it.
      2. If SKU match found but barcode differs → distrust and fall through
         (same SKU string identifies different products).
      3. Try Shopify by barcode (EAN). If found → use Shopify's SKU as the
         canonical MowDirect identifier; log the SKU drift for sku_matcher.
      4. Neither matched → hard fail per the design rule.

    Returns the resolved variant dict with an extra `drift` field describing
    any mismatch.
    """
    drift: list[str] = []

    # Step 1: search by SKU
    sku_hits = [n for n in _shopify_search("sku", compass_sku) if n["sku"] == compass_sku]
    if sku_hits:
        node = sku_hits[0]
        if node.get("barcode") == compass_ean:
            return _shopify_to_dict(node, drift)
        drift.append(
            f"SKU={compass_sku!r} matched on Shopify but barcode differs: "
            f"compass={compass_ean!r} vs shopify={node.get('barcode')!r}. "
            f"Distrusting the SKU match (EAN is canonical)."
        )

    # Step 2: fall through to EAN search
    if not compass_ean:
        raise SystemExit(
            f"Compass scrape did not include a barcode for SKU {compass_sku}, "
            f"and Shopify SKU search did not produce a barcode-matched hit. "
            f"Add a barcode to the compass page or to MowDirect Shopify and retry."
        )
    ean_hits = [n for n in _shopify_search("barcode", compass_ean) if n.get("barcode") == compass_ean]
    if ean_hits:
        node = ean_hits[0]
        if node["sku"] != compass_sku:
            drift.append(
                f"EAN={compass_ean} matched on Shopify under SKU={node['sku']!r}, "
                f"but compass page reports SKU={compass_sku!r}. "
                f"Using Shopify SKU as canonical (run scripts/sku_matcher to fix the drift)."
            )
        return _shopify_to_dict(node, drift)

    # Step 3: nothing matched. Hard fail per design.
    raise SystemExit(
        f"Neither SKU {compass_sku!r} nor EAN {compass_ean} found on MowDirect Shopify.\n"
        f"This is a data hygiene problem — the product hasn't been added to MowDirect "
        f"yet, or the EAN on compass is wrong. Fix MowDirect first."
    )


def _shopify_to_dict(node: dict, drift: list[str]) -> dict:
    return {
        "shopify_id": node["product"]["id"],
        "sku": node["sku"],
        "title": node["product"]["title"],
        "ean": node.get("barcode") or None,
        "vendor": node["product"].get("vendor", ""),
        "product_type": node["product"].get("productType", ""),
        "shopify_url": node["product"].get("onlineStoreUrl") or None,
        "drift": drift,
    }


def cmd_resolve(args: argparse.Namespace) -> None:
    """Step 1: scrape compass, look up Shopify (SKU then EAN), write resolved.json."""
    url = args.url
    print(f"→ Scraping {url}", file=sys.stderr)
    compass = _scrape_compass(url)
    compass_sku = compass.get("sku") or compass.get("variants", [{}])[0].get("sku")
    compass_ean = compass.get("barcode") or compass.get("ean") or ""
    if not compass_sku:
        raise SystemExit(f"compass scrape did not yield a SKU: {url}")
    print(f"→ Compass SKU={compass_sku} EAN={compass_ean or '(none)'}. "
          f"Resolving on MowDirect Shopify…", file=sys.stderr)
    shopify = _shopify_resolve(compass_sku, compass_ean)

    canonical_sku = shopify["sku"]
    canonical_ean = shopify["ean"] or compass_ean
    if not canonical_ean:
        raise SystemExit(
            f"Resolved to Shopify SKU {canonical_sku} but no barcode (EAN) on "
            f"either side. Set the barcode on the Shopify variant and retry "
            f"(EAN is the only working Mirakl product lookup key)."
        )

    if shopify["drift"]:
        print("⚠ Identifier drift detected:", file=sys.stderr)
        for line in shopify["drift"]:
            print(f"  - {line}", file=sys.stderr)

    stamp = _stamp()
    resolved = {
        "sku": canonical_sku,
        "ean": canonical_ean,
        "compass_url": url,
        "compass_sku": compass_sku,
        "compass_ean": compass_ean,
        "compass_scrape": compass,
        "shopify": shopify,
        "drift": shopify["drift"],
        "stamp": stamp,
    }
    path = _run_path(canonical_sku, stamp, "resolved", "json")
    with open(path, "w") as fh:
        json.dump(resolved, fh, indent=2)
    print(f"✓ Wrote {path}")
    print(f"   sku={canonical_sku} ean={canonical_ean}")
    if shopify["drift"]:
        print("   (drift logged — fix via scripts/sku_matcher when convenient)")


# ---------------------------------------------------------------------------
# mirakl-lookup — /products?product_references=EAN|<ean>
# ---------------------------------------------------------------------------

def cmd_mirakl_lookup(args: argparse.Namespace) -> None:
    """Step 2: look up Mirakl by EAN. Updates the resolved.json with the result."""
    from mirakl_client import MiraklClient

    sku = args.sku
    resolved = _read_resolved(sku)
    ean = resolved["ean"]
    client = MiraklClient("KINGFISHER")
    try:
        data = client.get("/products", params={"product_references": f"EAN|{ean}"})
    except Exception as e:
        raise SystemExit(f"Mirakl lookup failed: {e}")
    products = data.get("products", []) or []
    if products:
        p = products[0]
        resolved["mirakl"] = {
            "found": True,
            "product_id": p.get("product_id"),
            "category_code": p.get("category_code"),
            "category_label": p.get("category_label"),
            "product_title": p.get("product_title"),
        }
        print(
            f"✓ Product is LIVE in B&Q catalogue "
            f"(id={p.get('product_id')}, category={p.get('category_code')})"
        )
    else:
        resolved["mirakl"] = {"found": False}
        print("✓ Product NOT YET in B&Q catalogue — this run will be initial push + enrichment.")

    # Re-write the same resolved file (same stamp)
    stamp = resolved["stamp"]
    path = _run_path(sku, stamp, "resolved", "json")
    with open(path, "w") as fh:
        json.dump(resolved, fh, indent=2)


# ---------------------------------------------------------------------------
# hierarchy-status — three-way: MAPPED+LABELS, MAPPED+NO_LABELS, UNMAPPED
# ---------------------------------------------------------------------------

def _hierarchies_for_operator(op_name: str = "KINGFISHER") -> dict[str, dict]:
    """Return {category_code → {product_type, core_product_type}} for an operator."""
    from mirakl_operators import OPERATORS
    op = OPERATORS[op_name]
    out: dict[str, dict] = {}
    for pt, cfg in op.by_product_type.items():
        cat = cfg.get("category")
        if cat:
            out[cat] = {
                "product_type": pt,
                "core_product_type": cfg.get("core_product_type"),
            }
    return out


def _load_extensions_json() -> dict:
    """Return the parsed bq_operator_extensions.json (or empty stub)."""
    if not os.path.exists(_EXT_FILE):
        return {"KINGFISHER": {"hierarchies": {}, "display_label_to_api_name_overrides": {}}}
    with open(_EXT_FILE) as fh:
        data = json.load(fh)
    data.setdefault("KINGFISHER", {})
    data["KINGFISHER"].setdefault("hierarchies", {})
    data["KINGFISHER"].setdefault("display_label_to_api_name_overrides", {})
    return data


def _save_extensions_json(data: dict) -> None:
    # Preserve the _description and _schema if present
    existing = {}
    if os.path.exists(_EXT_FILE):
        try:
            existing = json.load(open(_EXT_FILE))
        except (OSError, json.JSONDecodeError):
            pass
    for k in ("_description", "_schema"):
        if k in existing and k not in data:
            data[k] = existing[k]
    with open(_EXT_FILE, "w") as fh:
        json.dump(data, fh, indent=2)


def cmd_hierarchy_status(args: argparse.Namespace) -> None:
    """Print one of MAPPED+LABELS, MAPPED+NO_LABELS, UNMAPPED.

    Exit codes: 0 (labels saved), 1 (mapped but no labels), 2 (unmapped).
    """
    pim = args.pim_code
    hiers = _hierarchies_for_operator()
    ext = _load_extensions_json()
    saved_hiers = ext["KINGFISHER"]["hierarchies"]
    has_labels = pim in saved_hiers and bool(saved_hiers[pim].get("labels"))

    if pim in hiers:
        info = hiers[pim]
        if has_labels:
            n = len(saved_hiers[pim]["labels"])
            print(f"MAPPED+LABELS: {pim} → {info['product_type']} ({n} labels saved)")
            sys.exit(0)
        else:
            print(f"MAPPED+NO_LABELS: {pim} → {info['product_type']}")
            print(f"(Run parse-form-dump --pim {pim} on a value-free portal paste "
                  f"to bootstrap the label set.)")
            sys.exit(1)
    else:
        if has_labels:
            print(f"UNMAPPED+LABELS: {pim} (labels saved but no operator config). "
                  f"Add a by_product_type entry to mirakl_operators.KINGFISHER.")
            sys.exit(2)
        print(f"UNMAPPED: {pim}")
        print("Known hierarchies:")
        for code, info in sorted(hiers.items()):
            saved = "(labels saved)" if code in saved_hiers else "(no labels)"
            print(f"  {code} → {info['product_type']} {saved}")
        sys.exit(2)


# ---------------------------------------------------------------------------
# parse-form-dump — turn a portal text-dump into a fields.json or saved
# label set, depending on whether --sku is supplied.
# ---------------------------------------------------------------------------

def _read_dump_text(input_path: str | None) -> str:
    """Read the dump text from --input file or stdin."""
    if input_path and input_path != "-":
        with open(input_path) as fh:
            return fh.read()
    return sys.stdin.read()


def _split_label_and_required(line: str) -> tuple[str, bool]:
    """Strip trailing whitespace and detect the '*' required marker."""
    s = line.rstrip()
    if s.endswith("*"):
        return s[:-1].rstrip(), True
    return s, False


def _parse_dump(text: str, known_labels: dict[str, str | None] | None = None) -> list[dict]:
    """Parse a portal text-dump into a list of field records.

    Bootstrap mode (known_labels=None): every non-section/unit/blank line is
    a label. No values are extracted.

    Populated mode (known_labels given): lines matching a known label start
    a new field; subsequent lines until the next known label / section /
    unit / "Add image" are joined as that field's value.
    """
    from mirakl_operators import (
        KINGFISHER_PORTAL_SECTIONS as SECTIONS,
        KINGFISHER_INLINE_UNITS as UNITS,
    )

    lines = text.splitlines()
    fields: list[dict] = []
    current_section = "top"

    def is_section(s: str) -> bool:
        return s.strip() in SECTIONS

    def is_unit(s: str) -> bool:
        return s.strip() in UNITS

    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        if is_section(stripped):
            current_section = stripped
            continue
        if is_unit(stripped):
            if fields and not fields[-1].get("unit"):
                fields[-1]["unit"] = stripped
            continue
        if stripped == "Add image":
            if fields and fields[-1].get("section") in ("Images", "Documents"):
                fields[-1]["value"] = ""
            continue

        label, required = _split_label_and_required(raw)
        label = label.rstrip()

        if known_labels is None:
            # Bootstrap mode — every non-skipped line is a label
            fields.append({
                "label": label,
                "required": required,
                "section": current_section,
                "unit": None,
                "value": None,
            })
            continue

        # Populated mode — only known labels start new fields
        if label in known_labels:
            fields.append({
                "label": label,
                "required": required,
                "section": current_section,
                "unit": None,
                "value": "",
            })
            continue

        # Treat as the previous field's value
        if fields:
            prev = fields[-1]
            existing = prev.get("value") or ""
            sep = "\n" if prev["label"] == "Body Copy" else " "
            joined = raw if not existing else f"{existing}{sep}{raw}"
            prev["value"] = joined.strip()

    return fields


def cmd_parse_form_dump(args: argparse.Namespace) -> None:
    """Two modes:

    Bootstrap (no --sku):
      Parses a value-free dump and saves the discovered label set to
      config/bq_operator_extensions.json under the hierarchy. Cross-
      references against KINGFISHER_DISPLAY_TO_API to attach API names.

    Populated (--sku given):
      Parses this product's populated dump using the saved label set to
      separate labels from values. Writes bq/enriched/<sku>_<ts>_fields.json.
    """
    from mirakl_operators import KINGFISHER_DISPLAY_TO_API

    pim = args.pim
    text = _read_dump_text(args.input)
    ext = _load_extensions_json()
    saved = ext["KINGFISHER"]["hierarchies"].get(pim, {})
    saved_labels = saved.get("labels") or []
    known_label_set: dict[str, str | None] | None = (
        {entry["label"]: entry.get("api_name") for entry in saved_labels}
        if saved_labels else None
    )

    if args.sku is None:
        # Bootstrap mode
        if saved_labels and not args.force:
            print(f"⚠ Hierarchy {pim} already has {len(saved_labels)} labels saved. "
                  f"Pass --force to overwrite.")
            sys.exit(3)
        parsed = _parse_dump(text, known_labels=None)
        for f in parsed:
            f["api_name"] = KINGFISHER_DISPLAY_TO_API.get(f["label"])
        hier_entry = {
            "product_type": args.product_type,
            "labels": parsed,
            "bootstrapped_at": datetime.now().isoformat(timespec="seconds"),
            "bootstrap_source": args.input or "(stdin)",
        }
        ext["KINGFISHER"]["hierarchies"][pim] = hier_entry
        _save_extensions_json(ext)
        n_known = sum(1 for f in parsed if f.get("api_name"))
        n_unknown = sum(1 for f in parsed if f.get("api_name") is None)
        print(f"✓ Bootstrap saved for {pim}: {len(parsed)} labels "
              f"({n_known} with known API names, {n_unknown} unknown).")
        if n_unknown:
            print("Labels without API mappings (need Wayne to set, or fall to clipboard):")
            for f in parsed:
                if not f.get("api_name"):
                    flag = "req" if f["required"] else "opt"
                    print(f"  - {f['label']!r} ({f.get('section', 'top')}, {flag})")
        return

    # Populated mode
    sku = args.sku
    if not saved_labels:
        raise SystemExit(
            f"Hierarchy {pim} has no saved label set. Bootstrap first:\n"
            f"  bq_enrich.py parse-form-dump --pim {pim} "
            f"--product-type <LABEL> --input <value-free paste>"
        )
    parsed = _parse_dump(text, known_labels=known_label_set)
    saved_by_label = {entry["label"]: entry for entry in saved_labels}
    for f in parsed:
        s = saved_by_label.get(f["label"])
        if s:
            if not f.get("api_name"):
                f["api_name"] = s.get("api_name")
            if not f.get("unit"):
                f["unit"] = s.get("unit")
            if not f.get("section") or f["section"] == "top":
                f["section"] = s.get("section") or f["section"]

    resolved = _read_resolved(sku)
    stamp = resolved["stamp"]
    out = {
        "sku": sku,
        "pim": pim,
        "fields": parsed,
        "parsed_at": datetime.now().isoformat(timespec="seconds"),
    }
    path = _run_path(sku, stamp, "fields", "json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=2)
    print(f"✓ Parsed {len(parsed)} fields from populated dump → {path}")
    n_with_val = sum(1 for f in parsed if (f.get("value") or "").strip())
    n_req_missing = sum(1 for f in parsed
                        if f.get("required") and not (f.get("value") or "").strip())
    print(f"   {n_with_val} fields have values; "
          f"{n_req_missing} required fields are still empty.")


# ---------------------------------------------------------------------------
# enrich-vlc — annotate parsed fields with /values_lists code maps
# ---------------------------------------------------------------------------

def cmd_enrich_vlc(args: argparse.Namespace) -> None:
    """Attach value-list code maps to value-list-backed fields in fields.json.

    Reads bq/enriched/<sku>_<ts>_fields.json (written by parse-form-dump),
    walks /values_lists for the hierarchy, and adds a `value_list_codes`
    array on any field whose API name has a matching value list.

    This is the *secondary* role for /values_lists — primary discovery
    happens via parse-form-dump. enrich-vlc is purely for code-to-label
    resolution (e.g. Cordless="2" not "Yes") on value-list-backed fields.
    """
    from mirakl_client import MiraklClient

    sku = args.sku
    fields_path = _latest_run(sku, "fields")
    if not fields_path:
        raise SystemExit(f"No fields.json for {sku} — run parse-form-dump first.")
    with open(fields_path) as fh:
        doc = json.load(fh)
    pim = doc["pim"]

    client = MiraklClient("KINGFISHER")
    try:
        data = client.get("/values_lists")
    except Exception as e:
        print(f"⚠ /values_lists call failed ({e}); skipping VLC annotation.",
              file=sys.stderr)
        return
    all_lists = data.get("values_lists", []) or []
    suffix = f"_{pim}"

    # Build a lookup: candidate_api_name → [code maps]
    by_attr: dict[str, list[dict]] = {}
    for vl in all_lists:
        code = vl.get("code") or ""
        attr = code[: -len(suffix)] if code.endswith(suffix) else code
        values = [
            {"code": v.get("code"), "label": v.get("label")}
            for v in (vl.get("values") or [])
        ]
        by_attr[attr] = values

    enriched = 0
    for f in doc["fields"]:
        api = f.get("api_name")
        if not api:
            continue
        if api in by_attr:
            f["value_list_codes"] = by_attr[api]
            enriched += 1

    with open(fields_path, "w") as fh:
        json.dump(doc, fh, indent=2)
    print(f"✓ Annotated {enriched} field(s) with value-list codes in {fields_path}")


# ---------------------------------------------------------------------------
# prepare-gate2 — fill specs and emit the gate-2 markdown for Wayne to edit
# ---------------------------------------------------------------------------

# Compass spec-table keys often differ from B&Q attribute names. This map is
# best-effort — Wayne edits the markdown for anything that's wrong.
_COMPASS_TO_BQ_SPEC = {
    "cutting_width": "Spec_Cutting_width",
    "cutting_capacity": "Spec_Cutting_width",  # some pages
    "cutting_height_min": "Spec_Cutting_height_min",
    "cutting_height_max": "Spec_Cutting_height_max",
    "grass_box_capacity": "Spec_Grass_box_capacity",
    "battery_voltage": "Spec_Battery_voltage",
    "battery_capacity": "Spec_Battery_capacity",
    "battery_runtime": "Spec_Battery_runtime",
    "weight": "Product_weight",
    "length": "Product_length",
    "width": "Product_width",
    "height": "Product_height",
}


def _resolve_compass_value(field_name: str, compass_specs: dict) -> str | None:
    """Best-effort lookup of a field's value in the compass display_attributes."""
    inv_map = {bq: cm for cm, bq in _COMPASS_TO_BQ_SPEC.items()}
    cm_key = inv_map.get(field_name)
    if cm_key and cm_key in compass_specs:
        return compass_specs[cm_key]
    # Direct hit (some compass pages use the B&Q name verbatim)
    if field_name in compass_specs:
        return compass_specs[field_name]
    # Heuristic: lowercase normalised match
    nf = field_name.lower().replace("_", " ").replace("spec ", "").strip()
    for k, v in compass_specs.items():
        if k.lower().replace("_", " ") == nf:
            return v
    return None


# Prose-field heuristic: only the genuinely-marketing prose fields get the
# TODO marker. Compliance / safety / manufacturer text are NOT marketing
# prose — they have specific factual answers and shouldn't be invented by
# Claude. Default those to <SKIP> (optional) or <ASK> (required).
_PROSE_LABEL_PATTERNS = (
    "Body Copy",
    "Selling Copy",
    "Key Feature",
    "Unique Selling Point",
)


def _is_prose_label(label: str, section: str | None) -> bool:
    if label in ("name", "Name"):
        return True
    return any(pat in label for pat in _PROSE_LABEL_PATTERNS)


def cmd_prepare_gate2(args: argparse.Namespace) -> None:
    """Step 9: emit the portal-fill markdown from the parsed fields.json.

    The output uses PORTAL LABELS as headings (what Wayne sees and selects
    in the B&Q seller portal). Values are display labels — what Wayne
    types or selects in dropdowns. API codes (4592, 81, 17, etc.) don't
    appear in the output; they were for /products/imports CSV submission,
    which this skill no longer does.

    Field priority:
      1. Parsed portal value (if non-empty) — Wayne keeps what's already in
         the portal
      2. Prose TODO marker for prose fields — Claude fills in chat
      3. Compass spec fallback for factual fields where the portal value
         was empty
      4. <SKIP> for optional empties; <ASK: required> for required empties
    """
    sku = args.sku
    resolved = _read_resolved(sku)
    fields_path = _latest_run(sku, "fields")
    if not fields_path:
        raise SystemExit(f"No fields.json for {sku} — run parse-form-dump first.")
    with open(fields_path) as fh:
        fields_doc = json.load(fh)

    compass_specs = (
        resolved["compass_scrape"].get("display_attributes")
        or resolved["compass_scrape"].get("specs")
        or {}
    )

    stamp = resolved["stamp"]
    lines: list[str] = []
    lines.append(f"# {sku} — B&Q portal-fill document")
    lines.append("")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"Compass URL: {resolved['compass_url']}")
    lines.append(f"Hierarchy: {fields_doc['pim']}")
    mirakl = resolved.get("mirakl", {})
    if mirakl.get("found"):
        lines.append(f"Mirakl status: LIVE (product_id={mirakl.get('product_id')})")
    else:
        lines.append("Mirakl status: not in catalogue (this will be a new listing)")
    lines.append("")
    lines.append("## How to use this document")
    lines.append("")
    lines.append("Work top to bottom through the B&Q seller portal. For each")
    lines.append("section below, find the matching portal field (the heading is")
    lines.append("the portal label) and paste/select the **Value:**. Notes are")
    lines.append("context for you — they're not entered into the portal.")
    lines.append("")
    lines.append("Markers:")
    lines.append("- `<SKIP>` — leave the field blank in the portal")
    lines.append("- `<ASK: …>` — value not known; supply it yourself before filling")
    lines.append("")
    lines.append("---")
    lines.append("")

    from mirakl_operators import KINGFISHER_FIELD_LENGTH_CAPS as CAPS

    for f in fields_doc["fields"]:
        label = f["label"]
        section = f.get("section") or "top"
        required = f.get("required", False)
        unit = f.get("unit")
        existing = (f.get("value") or "").strip()
        cap = CAPS.get(label)

        # Heading is the PORTAL LABEL — what Wayne sees in the form
        lines.append(f"## {label}")
        if required:
            lines.append("Required: yes")
        if section and section != "top":
            lines.append(f"Section: {section}")
        if unit:
            lines.append(f"Unit: {unit}")
        if cap is not None:
            lines.append(f"Max length: {cap} characters")
        if f.get("value_list_codes"):
            opts = f["value_list_codes"]
            top = opts[:8]
            lines.append("Dropdown options (select one):")
            for v in top:
                lines.append(f"  - {v['label']}")
            if len(opts) > 8:
                lines.append(f"  - ... (+{len(opts) - 8} more)")

        # Pick the value
        value: str
        if existing:
            value = existing
        elif _is_prose_label(label, section):
            value = f"<TODO: rewrite {label} — see BRAND_VOICE.md and EXEMPLARS/>"
        else:
            compass_val = _resolve_compass_value(label, compass_specs)
            if compass_val is not None:
                value = str(compass_val)
            elif required:
                value = "<ASK: required field, no value found>"
            else:
                value = "<SKIP>"

        # Flag cap violation (only for real values, not <SKIP>/<TODO>/<ASK>)
        if cap is not None and not value.startswith("<") and len(value) > cap:
            lines.append(f"⚠ OVER CAP: value is {len(value)} chars, max is {cap}")

        lines.append("Value:")
        lines.append(value)
        lines.append("")

    path = _run_path(sku, stamp, "gate2", "md")
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"✓ Wrote {path}")


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bq_enrich.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("resolve", help="Scrape compass + Shopify lookup")
    s.add_argument("url")
    s.set_defaults(func=cmd_resolve)

    s = sub.add_parser("mirakl-lookup", help="Look up product in Mirakl by EAN")
    s.add_argument("sku")
    s.set_defaults(func=cmd_mirakl_lookup)

    s = sub.add_parser("hierarchy-status", help="Check if a PIM_code is mapped")
    s.add_argument("pim_code")
    s.set_defaults(func=cmd_hierarchy_status)

    s = sub.add_parser("parse-form-dump",
                       help="Parse a portal text-dump (bootstrap or populated)")
    s.add_argument("--pim", required=True, help="Hierarchy code, e.g. PIM_13202")
    s.add_argument("--sku", default=None,
                   help="If given, parse as a populated dump for this SKU. "
                        "If omitted, parse as a value-free bootstrap for the PIM.")
    s.add_argument("--product-type", default=None,
                   help="ALL_CAPS_WITH_UNDERSCORES label (bootstrap mode only)")
    s.add_argument("--input", default=None,
                   help="Path to the dump file (default: stdin)")
    s.add_argument("--force", action="store_true",
                   help="Overwrite an existing bootstrap label set")
    s.set_defaults(func=cmd_parse_form_dump)

    s = sub.add_parser("enrich-vlc",
                       help="Annotate value-list fields with /values_lists code maps")
    s.add_argument("sku")
    s.set_defaults(func=cmd_enrich_vlc)

    s = sub.add_parser("prepare-gate2", help="Emit the portal-fill markdown")
    s.add_argument("sku")
    s.set_defaults(func=cmd_prepare_gate2)

    return p


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
