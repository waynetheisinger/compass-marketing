"""
Discovery engine for the /bq-shopify-shape skill.

Maintains config/bq_shopify_mapping.json — the Shopify-side inventory of
metafields (and core product/variant fields) with hand-annotated B&Q targets.

The script is pure I/O. Conversational orchestration (asking Wayne to fill
bq_targets/hint for unmapped entries) lives in SKILL.md and is driven by
Claude reading `list-unmapped` output and editing the JSON inline.

Subcommands:
    refresh
        Pull metafieldDefinitions (PRODUCT + PRODUCTVARIANT) from Shopify,
        merge into config. First run also seeds shopify_core_fields with
        sensible defaults. Existing entries are never modified — only new
        ones added. Produces a drift report.

    scan-skus <sku> [<sku>...]
        For each SKU, pull product + all metafields. Discover ad-hoc
        metafields (set on products without a definition). Capture
        example_value + example_from_sku for any entry that doesn't have one
        yet. Adds new entries; never overwrites existing examples or mappings.

    status
        Report config state: totals, mapped, unmapped, skipped.

    list-unmapped
        Emit JSON list of entries needing decisions (no bq_targets, no
        skip_reason). One entry per line for easy iteration by the skill.

    set-mapping --section S --owner-type O --key K [--bq-targets COL,COL...]
                [--hint TEXT] [--skip-reason TEXT] [--note TEXT]
        Apply a mapping decision to a single entry. The skill calls this
        once per Wayne-answered question rather than editing the JSON file
        directly (entry keys can contain dots, so text-based edits are
        fragile). Pass --bq-targets OR --skip-reason, not both.

    bulk-skip --pattern STR --reason TEXT [--dry-run]
        Set skip_reason on every unmapped entry whose `<section>/<owner>/<key>`
        contains the pattern substring. Used for blast-skipping obvious junk
        namespaces (judgeme, mm-google-shopping, reviews, shopify--discovery)
        in one keystroke instead of one-by-one. Dry-run lists what would be
        skipped without writing.

Config shape:
    {
      "schema_version": 1,
      "shopify_core_fields": {
        "PRODUCT":        { "<field_path>": { ...entry... } },
        "PRODUCTVARIANT": { "<field_path>": { ...entry... } }
      },
      "shopify_metafields": {
        "PRODUCT":        { "<namespace>.<key>": { ...entry... } },
        "PRODUCTVARIANT": { "<namespace>.<key>": { ...entry... } }
      }
    }

Entry shape (metafield):
    {
      "type":               "list.metaobject_reference",
      "name":               "Battery technology",
      "shop_owner_added":   false,
      "example_value":      ["Li-ion"],
      "example_from_sku":   "SBS460CLM",
      "bq_targets":         [],          # Wayne fills, [] = unmapped
      "hint":               null,        # Wayne fills, free-text
      "skip_reason":        null,        # Wayne fills if no B&Q equivalent
      "note":               null,        # Wayne fills, free-text
      "discovered_at":      "2026-05-20T15:30:00Z",
      "last_seen_at":       "2026-05-20T15:30:00Z"
    }

`bq_targets=[] AND skip_reason=null` means "needs a decision" — surfaced by
`list-unmapped` for the skill to walk Wayne through.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

_REPO_ROOT = os.path.dirname(_THIS_DIR)
_CONFIG_PATH = os.path.join(_REPO_ROOT, "config", "bq_shopify_mapping.json")
_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Pre-seeded core field entries (written on first refresh; never modified after)
# ---------------------------------------------------------------------------

def _core_field_seeds() -> dict[str, dict[str, dict[str, Any]]]:
    """Core Shopify fields that aren't metafields but still need mapping.

    Pre-populated with bq_targets and hints because these are invariant —
    `product.title` always means the same thing. Wayne can edit later.
    """
    return {
        "PRODUCT": {
            "title": {
                "type": "string",
                "name": "Product title",
                "shop_owner_added": False,
                "example_value": None,
                "example_from_sku": None,
                "bq_targets": ["name"],
                "hint": "Truncate to <=130 chars. Apply BQ_QUIRKS rules: no em-dash (use ' - '), no en-dash, no smart quotes, no ° or ×.",
                "skip_reason": None,
                "note": None,
            },
            "descriptionHtml": {
                "type": "html",
                "name": "Product description (HTML)",
                "shop_owner_added": False,
                "example_value": None,
                "example_from_sku": None,
                "bq_targets": ["Body Copy"],
                "hint": "Strip HTML. Rewrite in MowDirect voice per BRAND_VOICE.md. Distinct from compassgm.co.uk AND the MowDirect Shopify PDP itself. Apply BQ_QUIRKS rules. Different lead sentence, different paragraph structure, different verbs.",
                "skip_reason": None,
                "note": None,
            },
            "vendor": {
                "type": "string",
                "name": "Vendor / brand",
                "shop_owner_added": False,
                "example_value": None,
                "example_from_sku": None,
                "bq_targets": ["Acquisition brand"],
                "hint": "Match against B&Q value-list. Spectrum is approved on B&Q (confirmed via SBS push 2026-05-07).",
                "skip_reason": None,
                "note": None,
            },
            "featuredImage.url": {
                "type": "url",
                "name": "Featured image URL",
                "shop_owner_added": False,
                "example_value": None,
                "example_from_sku": None,
                "bq_targets": ["image_main_1"],
                "hint": "Pass through Shopify CDN URL verbatim. B&Q image rules (white background, >=1000px, no watermark) are assumed met by the source.",
                "skip_reason": None,
                "note": None,
            },
            "images": {
                "type": "url_list",
                "name": "Secondary image URLs (excluding featured)",
                "shop_owner_added": False,
                "example_value": None,
                "example_from_sku": None,
                "bq_targets": ["image_secondary_1", "image_secondary_2", "image_secondary_3", "image_secondary_4", "image_secondary_5", "image_secondary_6", "image_secondary_7", "image_secondary_8"],
                "hint": "Skip the featured image (already in image_main_1). Map remaining images to image_secondary_1..8 in order. Pad with empty if fewer than 8.",
                "skip_reason": None,
                "note": None,
            },
        },
        "PRODUCTVARIANT": {
            "sku": {
                "type": "string",
                "name": "Variant SKU",
                "shop_owner_added": False,
                "example_value": None,
                "example_from_sku": None,
                "bq_targets": ["shop_sku"],
                "hint": "Verbatim.",
                "skip_reason": None,
                "note": None,
            },
            "barcode": {
                "type": "string",
                "name": "Variant barcode (EAN)",
                "shop_owner_added": False,
                "example_value": None,
                "example_from_sku": None,
                "bq_targets": ["ean"],
                "hint": "Verbatim. EAN is canonical identifier per identifier invariant.",
                "skip_reason": None,
                "note": None,
            },
            "inventoryItem.measurement.weight": {
                "type": "weight",
                "name": "Variant weight",
                "shop_owner_added": False,
                "example_value": None,
                "example_from_sku": None,
                "bq_targets": ["Product_weight"],
                "hint": "Extract numeric value in kg. Reject if unit is not KILOGRAMS or if value is 0.0 (not yet set on Shopify).",
                "skip_reason": None,
                "note": None,
            },
        },
    }


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------

def _load_config() -> dict[str, Any]:
    if not os.path.exists(_CONFIG_PATH):
        return {
            "schema_version": _SCHEMA_VERSION,
            "shopify_core_fields": _core_field_seeds(),
            "shopify_metafields": {"PRODUCT": {}, "PRODUCTVARIANT": {}},
        }
    with open(_CONFIG_PATH) as fh:
        cfg = json.load(fh)
    # Defensive: ensure required keys exist
    cfg.setdefault("schema_version", _SCHEMA_VERSION)
    cfg.setdefault("shopify_metafields", {"PRODUCT": {}, "PRODUCTVARIANT": {}})
    cfg["shopify_metafields"].setdefault("PRODUCT", {})
    cfg["shopify_metafields"].setdefault("PRODUCTVARIANT", {})
    cfg.setdefault("shopify_core_fields", _core_field_seeds())
    return cfg


def _save_config(cfg: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
    with open(_CONFIG_PATH, "w") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Shopify queries
# ---------------------------------------------------------------------------

def _pull_definitions(owner_type: str) -> list[dict[str, Any]]:
    """Pull metafield definitions for an ownerType. Returns list of dicts."""
    from shopify_client import ShopifyClient

    query = """
    query($owner: MetafieldOwnerType!, $cursor: String) {
      metafieldDefinitions(ownerType: $owner, first: 100, after: $cursor) {
        edges {
          node {
            namespace
            key
            name
            description
            type { name }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
    """
    out: list[dict[str, Any]] = []
    cursor: str | None = None
    with ShopifyClient() as client:
        while True:
            res = client.execute(query, variables={"owner": owner_type, "cursor": cursor})
            conn = res["metafieldDefinitions"]
            for edge in conn["edges"]:
                n = edge["node"]
                out.append({
                    "namespace": n["namespace"],
                    "key": n["key"],
                    "name": n["name"],
                    "description": n.get("description") or "",
                    "type": n["type"]["name"],
                })
            if not conn["pageInfo"]["hasNextPage"]:
                break
            cursor = conn["pageInfo"]["endCursor"]
    return out


def _deref_metaobjects(gids: list[str]) -> dict[str, str]:
    """Resolve metaobject gids to human-readable display labels.

    Tries displayName first, falls back to first field value if displayName
    isn't useful. Returns {gid: display_label}.
    """
    from shopify_client import ShopifyClient

    if not gids:
        return {}
    query = """
    query($ids: [ID!]!) {
      nodes(ids: $ids) {
        ... on Metaobject {
          id
          displayName
          handle
          fields { key value }
        }
      }
    }
    """
    out: dict[str, str] = {}
    with ShopifyClient() as client:
        # Chunk in batches of 50 to be safe
        for i in range(0, len(gids), 50):
            chunk = gids[i : i + 50]
            res = client.execute(query, variables={"ids": chunk})
            for node in res["nodes"]:
                if not node:
                    continue
                gid = node["id"]
                # Prefer displayName if it's not an opaque hash; else first field
                display = node.get("displayName") or node.get("handle") or gid
                # Shopify standard taxonomy metaobjects often store the human
                # label in a field called "label" or "name"
                for f in node.get("fields") or []:
                    if f["key"] in ("label", "name") and f.get("value"):
                        display = f["value"]
                        break
                out[gid] = display
    return out


def _format_example_value(metafield_type: str, raw_value: str, gid_lookup: dict[str, str]) -> Any:
    """Convert Shopify's raw metafield value string into a human-readable example.

    Most types are JSON-stringified. Metaobject references get derefed via
    gid_lookup. JSON types get parsed. Everything else is returned as-is.
    """
    if raw_value is None:
        return None
    if metafield_type == "list.metaobject_reference":
        try:
            gids = json.loads(raw_value)
        except (json.JSONDecodeError, TypeError):
            return raw_value
        return [gid_lookup.get(g, g) for g in gids]
    if metafield_type == "metaobject_reference":
        return gid_lookup.get(raw_value, raw_value)
    if metafield_type == "json":
        try:
            return json.loads(raw_value)
        except (json.JSONDecodeError, TypeError):
            return raw_value
    if metafield_type.startswith("list."):
        try:
            return json.loads(raw_value)
        except (json.JSONDecodeError, TypeError):
            return raw_value
    if metafield_type in ("dimension", "volume", "weight"):
        try:
            return json.loads(raw_value)
        except (json.JSONDecodeError, TypeError):
            return raw_value
    return raw_value


def _pull_product_full(sku: str) -> dict[str, Any] | None:
    """Pull a single product (by variant SKU) with all metafields,
    plus variant-level metafields. Returns the variant node or None.
    """
    from shopify_client import ShopifyClient

    query = """
    query($q: String!) {
      productVariants(first: 5, query: $q) {
        edges {
          node {
            sku
            barcode
            inventoryItem { measurement { weight { value unit } } }
            metafields(first: 100) {
              edges { node { namespace key type value } }
            }
            product {
              id
              title
              descriptionHtml
              vendor
              productType
              tags
              status
              featuredImage { url }
              images(first: 10) { edges { node { url } } }
              metafields(first: 100) {
                edges { node { namespace key type value } }
              }
              category { id name fullName }
            }
          }
        }
      }
    }
    """
    with ShopifyClient() as client:
        res = client.execute(query, variables={"q": f"sku:{sku}"})
    edges = res["productVariants"]["edges"]
    # Be exact: match by sku, not fuzzy
    for e in edges:
        if e["node"]["sku"] == sku:
            return e["node"]
    return None


# ---------------------------------------------------------------------------
# Merge / discovery logic
# ---------------------------------------------------------------------------

def _new_entry_from_definition(d: dict[str, Any], shop_owner_added: bool = False) -> dict[str, Any]:
    return {
        "type": d["type"],
        "name": d["name"],
        "shop_owner_added": shop_owner_added,
        "example_value": None,
        "example_from_sku": None,
        "bq_targets": [],
        "hint": None,
        "skip_reason": None,
        "note": d.get("description") or None,
        "discovered_at": _utcnow_iso(),
        "last_seen_at": _utcnow_iso(),
    }


def _cmd_refresh() -> int:
    """Pull definitions, merge, drift report."""
    cfg = _load_config()
    is_first_run = not os.path.exists(_CONFIG_PATH)

    summary: dict[str, Any] = {
        "first_run": is_first_run,
        "added": {"PRODUCT": [], "PRODUCTVARIANT": []},
        "drift_missing": {"PRODUCT": [], "PRODUCTVARIANT": []},
        "drift_type_changed": {"PRODUCT": [], "PRODUCTVARIANT": []},
        "totals": {},
    }

    for owner in ("PRODUCT", "PRODUCTVARIANT"):
        defs = _pull_definitions(owner)
        seen_keys: set[str] = set()
        for d in defs:
            ns_key = f"{d['namespace']}.{d['key']}"
            seen_keys.add(ns_key)
            existing = cfg["shopify_metafields"][owner].get(ns_key)
            if existing is None:
                cfg["shopify_metafields"][owner][ns_key] = _new_entry_from_definition(d)
                summary["added"][owner].append(ns_key)
            else:
                # Existing — only update last_seen_at and detect type drift
                existing["last_seen_at"] = _utcnow_iso()
                if existing.get("type") != d["type"]:
                    summary["drift_type_changed"][owner].append({
                        "key": ns_key,
                        "old_type": existing.get("type"),
                        "new_type": d["type"],
                    })
        # Detect entries that exist in config but no longer in definitions
        # (shop_owner_added=true entries are exempt — they're not from definitions)
        for ns_key, entry in cfg["shopify_metafields"][owner].items():
            if entry.get("shop_owner_added"):
                continue
            if ns_key not in seen_keys:
                summary["drift_missing"][owner].append(ns_key)
        summary["totals"][owner] = len(cfg["shopify_metafields"][owner])

    _save_config(cfg)
    print(json.dumps(summary, indent=2))
    return 0


def _cmd_scan_skus(skus: list[str]) -> int:
    """Walk SKUs, populate example_values, discover ad-hoc metafields."""
    cfg = _load_config()
    summary: dict[str, Any] = {
        "products_processed": 0,
        "products_not_found": [],
        "ad_hoc_added": {"PRODUCT": [], "PRODUCTVARIANT": []},
        "examples_populated": {"PRODUCT": [], "PRODUCTVARIANT": []},
    }

    for sku in skus:
        variant = _pull_product_full(sku)
        if variant is None:
            summary["products_not_found"].append(sku)
            continue
        summary["products_processed"] += 1

        # Collect all metaobject GIDs across this product so we can deref in
        # one round-trip.
        all_metafields: list[tuple[str, dict[str, Any]]] = []
        for mf_edge in variant["product"]["metafields"]["edges"]:
            all_metafields.append(("PRODUCT", mf_edge["node"]))
        for mf_edge in variant["metafields"]["edges"]:
            all_metafields.append(("PRODUCTVARIANT", mf_edge["node"]))

        gids_to_deref: list[str] = []
        for _, mf in all_metafields:
            if mf["type"] == "metaobject_reference" and mf["value"]:
                gids_to_deref.append(mf["value"])
            elif mf["type"] == "list.metaobject_reference" and mf["value"]:
                try:
                    gids_to_deref.extend(json.loads(mf["value"]))
                except (json.JSONDecodeError, TypeError):
                    pass

        gid_lookup = _deref_metaobjects(gids_to_deref) if gids_to_deref else {}

        for owner, mf in all_metafields:
            ns_key = f"{mf['namespace']}.{mf['key']}"
            entry = cfg["shopify_metafields"][owner].get(ns_key)
            if entry is None:
                # Ad-hoc / shop-owner-added — not in definitions
                cfg["shopify_metafields"][owner][ns_key] = {
                    "type": mf["type"],
                    "name": ns_key,
                    "shop_owner_added": True,
                    "example_value": _format_example_value(mf["type"], mf["value"], gid_lookup),
                    "example_from_sku": sku,
                    "bq_targets": [],
                    "hint": None,
                    "skip_reason": None,
                    "note": None,
                    "discovered_at": _utcnow_iso(),
                    "last_seen_at": _utcnow_iso(),
                }
                summary["ad_hoc_added"][owner].append(ns_key)
            else:
                entry["last_seen_at"] = _utcnow_iso()
                # Populate example_value if currently empty
                if entry.get("example_value") is None and mf["value"]:
                    entry["example_value"] = _format_example_value(mf["type"], mf["value"], gid_lookup)
                    entry["example_from_sku"] = sku
                    summary["examples_populated"][owner].append(ns_key)

        # Populate core field examples too
        core_product = cfg["shopify_core_fields"]["PRODUCT"]
        if core_product["title"]["example_value"] is None:
            core_product["title"]["example_value"] = variant["product"]["title"]
            core_product["title"]["example_from_sku"] = sku
        if core_product["descriptionHtml"]["example_value"] is None:
            html = variant["product"].get("descriptionHtml") or ""
            core_product["descriptionHtml"]["example_value"] = html[:500] + ("..." if len(html) > 500 else "")
            core_product["descriptionHtml"]["example_from_sku"] = sku
        if core_product["vendor"]["example_value"] is None:
            core_product["vendor"]["example_value"] = variant["product"]["vendor"]
            core_product["vendor"]["example_from_sku"] = sku
        if core_product["featuredImage.url"]["example_value"] is None and variant["product"].get("featuredImage"):
            core_product["featuredImage.url"]["example_value"] = variant["product"]["featuredImage"]["url"]
            core_product["featuredImage.url"]["example_from_sku"] = sku
        if core_product["images"]["example_value"] is None:
            urls = [e["node"]["url"] for e in variant["product"]["images"]["edges"]]
            core_product["images"]["example_value"] = urls
            core_product["images"]["example_from_sku"] = sku

        core_variant = cfg["shopify_core_fields"]["PRODUCTVARIANT"]
        if core_variant["sku"]["example_value"] is None:
            core_variant["sku"]["example_value"] = variant["sku"]
            core_variant["sku"]["example_from_sku"] = sku
        if core_variant["barcode"]["example_value"] is None:
            core_variant["barcode"]["example_value"] = variant["barcode"]
            core_variant["barcode"]["example_from_sku"] = sku
        if core_variant["inventoryItem.measurement.weight"]["example_value"] is None:
            weight = (variant.get("inventoryItem") or {}).get("measurement", {}).get("weight")
            if weight:
                core_variant["inventoryItem.measurement.weight"]["example_value"] = weight
                core_variant["inventoryItem.measurement.weight"]["example_from_sku"] = sku

    _save_config(cfg)
    print(json.dumps(summary, indent=2))
    return 0


def _cmd_status() -> int:
    cfg = _load_config()
    summary: dict[str, Any] = {"shopify_core_fields": {}, "shopify_metafields": {}}
    for section_name, section in (("shopify_core_fields", cfg["shopify_core_fields"]),
                                  ("shopify_metafields", cfg["shopify_metafields"])):
        for owner in ("PRODUCT", "PRODUCTVARIANT"):
            entries = section[owner]
            total = len(entries)
            mapped = sum(1 for e in entries.values() if e.get("bq_targets"))
            skipped = sum(1 for e in entries.values() if e.get("skip_reason"))
            unmapped = total - mapped - skipped
            with_example = sum(1 for e in entries.values() if e.get("example_value") is not None)
            shop_owner = sum(1 for e in entries.values() if e.get("shop_owner_added"))
            summary[section_name][owner] = {
                "total": total,
                "mapped": mapped,
                "skipped": skipped,
                "unmapped": unmapped,
                "with_example": with_example,
                "shop_owner_added": shop_owner,
            }
    print(json.dumps(summary, indent=2))
    return 0


def _cmd_set_mapping(
    section: str,
    owner_type: str,
    key: str,
    bq_targets: list[str] | None,
    hint: str | None,
    skip_reason: str | None,
    note: str | None,
) -> int:
    """Apply a mapping decision to a single entry. Skill calls this rather
    than editing the JSON file directly (entry keys can contain dots)."""
    if section not in ("shopify_core_fields", "shopify_metafields"):
        print(json.dumps({"error": f"invalid section: {section}"}), file=sys.stderr)
        return 2
    if owner_type not in ("PRODUCT", "PRODUCTVARIANT"):
        print(json.dumps({"error": f"invalid owner_type: {owner_type}"}), file=sys.stderr)
        return 2
    if bq_targets and skip_reason:
        print(json.dumps({"error": "pass --bq-targets OR --skip-reason, not both"}), file=sys.stderr)
        return 2

    cfg = _load_config()
    entries = cfg.get(section, {}).get(owner_type, {})
    if key not in entries:
        print(json.dumps({"error": f"no entry at {section}/{owner_type}/{key}"}), file=sys.stderr)
        return 2

    entry = entries[key]
    if bq_targets is not None:
        entry["bq_targets"] = bq_targets
        entry["skip_reason"] = None
    if skip_reason is not None:
        entry["skip_reason"] = skip_reason
        entry["bq_targets"] = []
    if hint is not None:
        entry["hint"] = hint or None
    if note is not None:
        entry["note"] = note or None

    _save_config(cfg)
    print(json.dumps({"ok": True, "updated": f"{section}/{owner_type}/{key}", "entry": entry}, indent=2))
    return 0


def _cmd_bulk_skip(pattern: str, reason: str, dry_run: bool) -> int:
    """Skip every unmapped entry whose section/owner/key contains pattern."""
    if not pattern:
        print(json.dumps({"error": "pattern is required"}), file=sys.stderr)
        return 2
    if not reason and not dry_run:
        print(json.dumps({"error": "reason is required (unless --dry-run)"}), file=sys.stderr)
        return 2

    cfg = _load_config()
    matched: list[str] = []
    for section_name in ("shopify_core_fields", "shopify_metafields"):
        for owner in ("PRODUCT", "PRODUCTVARIANT"):
            entries = cfg[section_name][owner]
            for key, entry in entries.items():
                if entry.get("bq_targets"):
                    continue
                if entry.get("skip_reason"):
                    continue
                path = f"{section_name}/{owner}/{key}"
                if pattern.lower() not in path.lower():
                    continue
                matched.append(path)
                if not dry_run:
                    entry["skip_reason"] = reason

    if not dry_run and matched:
        _save_config(cfg)

    print(json.dumps({
        "ok": True,
        "dry_run": dry_run,
        "pattern": pattern,
        "reason": reason if not dry_run else None,
        "matched_count": len(matched),
        "matched": matched,
    }, indent=2))
    return 0


def _cmd_list_unmapped() -> int:
    """Emit JSON list of entries that need a decision.

    Output is a flat list; each item has enough context for the orchestrator
    (Claude) to prompt Wayne. Order is stable: core fields first (PRODUCT then
    VARIANT), then metafields (PRODUCT then VARIANT), alphabetical by key.
    """
    cfg = _load_config()
    out: list[dict[str, Any]] = []
    for section_name in ("shopify_core_fields", "shopify_metafields"):
        for owner in ("PRODUCT", "PRODUCTVARIANT"):
            entries = cfg[section_name][owner]
            for key in sorted(entries.keys()):
                entry = entries[key]
                if entry.get("bq_targets"):
                    continue
                if entry.get("skip_reason"):
                    continue
                out.append({
                    "section": section_name,
                    "owner_type": owner,
                    "key": key,
                    "type": entry.get("type"),
                    "name": entry.get("name"),
                    "shop_owner_added": entry.get("shop_owner_added", False),
                    "example_value": entry.get("example_value"),
                    "example_from_sku": entry.get("example_from_sku"),
                })
    print(json.dumps(out, indent=2))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bq_shopify_shape")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("refresh", help="Pull definitions, merge, drift report")

    scan = sub.add_parser("scan-skus", help="Walk SKUs to populate examples + catch ad-hoc metafields")
    scan.add_argument("skus", nargs="+", help="One or more Shopify SKUs")

    sub.add_parser("status", help="Report config state")
    sub.add_parser("list-unmapped", help="Emit JSON of entries needing decisions")

    sm = sub.add_parser("set-mapping", help="Apply mapping decision to one entry")
    sm.add_argument("--section", required=True, choices=["shopify_core_fields", "shopify_metafields"])
    sm.add_argument("--owner-type", required=True, choices=["PRODUCT", "PRODUCTVARIANT"])
    sm.add_argument("--key", required=True, help="Entry key, e.g. 'custom.bullet_two' or 'featuredImage.url'")
    sm.add_argument("--bq-targets", help="Comma-separated B&Q column names")
    sm.add_argument("--hint", help="Free-text hint about how to transform at runtime")
    sm.add_argument("--skip-reason", help="Reason this metafield has no B&Q equivalent")
    sm.add_argument("--note", help="Free-text note for future-you")

    bs = sub.add_parser("bulk-skip", help="Blast-skip every unmapped entry matching a substring")
    bs.add_argument("--pattern", required=True, help="Substring to match against <section>/<owner>/<key>")
    bs.add_argument("--reason", default="", help="skip_reason text to write")
    bs.add_argument("--dry-run", action="store_true", help="List matches without writing")

    args = parser.parse_args(argv)
    if args.cmd == "refresh":
        return _cmd_refresh()
    if args.cmd == "scan-skus":
        return _cmd_scan_skus(args.skus)
    if args.cmd == "status":
        return _cmd_status()
    if args.cmd == "list-unmapped":
        return _cmd_list_unmapped()
    if args.cmd == "set-mapping":
        targets = [t.strip() for t in args.bq_targets.split(",") if t.strip()] if args.bq_targets else None
        return _cmd_set_mapping(
            section=args.section,
            owner_type=args.owner_type,
            key=args.key,
            bq_targets=targets,
            hint=args.hint,
            skip_reason=args.skip_reason,
            note=args.note,
        )
    if args.cmd == "bulk-skip":
        return _cmd_bulk_skip(args.pattern, args.reason, args.dry_run)
    parser.error(f"Unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
