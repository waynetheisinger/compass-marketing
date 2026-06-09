#!/usr/bin/env python3
"""
Maintain config/tesco_overrides.json — the per-product Tesco listing overrides
+ constants that scripts/mirakl_bq_to_tesco.py reads when generating CSVs.

Driven by the /tesco-shopify-shape skill. Tesco's category system IS the Shopify
product taxonomy and its attribute schema is API-discoverable, so this also
reads the live schema (/products/attributes) to show which required attributes
a category needs and whether each is already covered (auto-filled / default /
colour / extra) or still UNCOVERED.

Auto-filled columns (never need a mapping): shopifyHierarchyId (from Shopify
taxonomy), sku, barcode (EAN), description (title), marketingText (body),
brand (offer/vendor), image1..N (re-hosted JPEGs).

Run from the repo root. Subcommands below; all writes are to
config/tesco_overrides.json (git-committed — decisions are reviewable).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts.mirakl_client import MiraklClient  # noqa: E402
from scripts.shopify_client import ShopifyClient  # noqa: E402
from scripts.mirakl_bq_to_tesco import _SHOPIFY_Q, shopify_sources  # noqa: E402

_CFG = os.path.join(_REPO_ROOT, "config", "tesco_overrides.json")
_PRE = "gid://shopify/TaxonomyCategory/"

# Columns the generator always fills itself — never need a config entry.
_AUTOFILLED = {"shopifyHierarchyId", "sku", "barcode", "description",
               "marketingText", "brand", "baseColour"} | {f"image{i}" for i in range(1, 11)}


def _load() -> dict:
    with open(_CFG) as fh:
        return json.load(fh)


def _save(cfg: dict) -> None:
    with open(_CFG, "w") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _emit(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _gid(s: str) -> str:
    return s if s.startswith("gid://") else _PRE + s.lstrip("/")


# ---------------------------------------------------------------------------

def cmd_status(args) -> None:
    c = _load()
    _emit({
        "defaults": c.get("defaults", {}),
        "brand_colour": c.get("brand_colour", {}),
        "sku_colour_count": len(c.get("sku_colour", {})),
        "sku_category_overrides": len(c.get("sku_category", {})),
        "excluded_skus": list(c.get("exclude_skus", {})),
        "unsellable_gids": c.get("unsellable_gids", []),
        "attribute_extras": {k: v for k, v in c.get("attribute_extras", {}).items()
                             if not k.startswith("_")},
    })


def cmd_list_attributes(args) -> None:
    """Show a category's live Tesco attribute schema + coverage."""
    t = MiraklClient("TESCO")
    gid = _gid(args.category)
    attrs = t.get("/products/attributes", params={"hierarchy": gid})["attributes"]
    c = _load()
    defaults = set(c.get("defaults", {}))
    extras = {k for k in c.get("attribute_extras", {}) if not k.startswith("_")}
    maps = c.get("attribute_mappings", {})
    mapped = set(maps.get("*", {})) | set(maps.get(gid, {}))
    mapped.discard("_doc")
    out = []
    for a in attrs:
        code = a.get("code")
        covered = ("auto" if code in _AUTOFILLED else
                   "mapped" if code in mapped else
                   "default" if code in defaults else
                   "extra" if code in extras else
                   "UNCOVERED")
        out.append({
            "code": code, "label": a.get("label"), "type": a.get("type"),
            "required": bool(a.get("required")),
            "value_list": a.get("type_parameter") or None,
            "coverage": covered,
        })
    req_uncovered = [o for o in out if o["required"] and o["coverage"] == "UNCOVERED"]
    _emit({"category": gid, "total": len(out),
           "required_uncovered": req_uncovered,
           "attributes": sorted(out, key=lambda x: (not x["required"], x["code"]))})


def cmd_resolve_category(args) -> None:
    """Search the Tesco taxonomy for leaf categories matching a label substring."""
    t = MiraklClient("TESCO")
    hier = t.get("/hierarchies")["hierarchies"]
    kids = set()
    for h in hier:
        if h.get("parent_code"):
            kids.add(h["parent_code"])
    q = args.query.lower()
    hits = [{"gid": h["code"], "label": h["label"],
             "leaf": h["code"] not in kids}
            for h in hier if q in (h.get("label") or "").lower()]
    _emit({"query": args.query, "matches": hits[:40],
           "note": "Only leaf=true categories are accepted by Tesco (else error 1005)."})


def cmd_set_colour(args) -> None:
    c = _load()
    if args.sku:
        c.setdefault("sku_colour", {})[args.sku] = args.colour
        where = f"sku_colour[{args.sku}]"
    else:
        c.setdefault("brand_colour", {})[args.brand.lower()] = args.colour
        where = f"brand_colour[{args.brand.lower()}]"
    _save(c)
    _emit({"set": where, "colour": args.colour})


def cmd_set_category(args) -> None:
    c = _load()
    c.setdefault("sku_category", {})[args.sku] = _gid(args.gid)
    _save(c)
    _emit({"set": f"sku_category[{args.sku}]", "gid": _gid(args.gid)})


def cmd_exclude(args) -> None:
    c = _load()
    if args.remove:
        c.get("exclude_skus", {}).pop(args.sku, None)
        _save(c)
        _emit({"unexcluded": args.sku})
    else:
        c.setdefault("exclude_skus", {})[args.sku] = args.reason or "excluded"
        _save(c)
        _emit({"excluded": args.sku, "reason": c["exclude_skus"][args.sku]})


def cmd_set_default(args) -> None:
    c = _load()
    c.setdefault("defaults", {})[args.key] = args.value
    _save(c)
    _emit({"set_default": args.key, "value": args.value})


def cmd_set_extra(args) -> None:
    c = _load()
    extras = c.setdefault("attribute_extras", {})
    if args.remove:
        extras.get(args.attr, {}).pop(args.sku, None)
        if args.attr in extras and not extras[args.attr]:
            extras.pop(args.attr)
        _save(c)
        _emit({"removed_extra": f"{args.attr}[{args.sku}]"})
    else:
        extras.setdefault(args.attr, {})[args.sku] = args.value
        _save(c)
        _emit({"set_extra": f"{args.attr}[{args.sku}]", "value": args.value})


def cmd_set_unsellable(args) -> None:
    c = _load()
    lst = c.setdefault("unsellable_gids", [])
    gid = _gid(args.gid)
    if args.remove:
        if gid in lst:
            lst.remove(gid)
        _emit({"removed_unsellable": gid})
    elif gid not in lst:
        lst.append(gid)
        _emit({"added_unsellable": gid})
    else:
        _emit({"already_present": gid})
    _save(c)


def cmd_shopify_sources(args) -> None:
    """Dump the mappable Shopify data for a SKU so Wayne can pick sources."""
    with ShopifyClient() as sc:
        edges = sc.execute(_SHOPIFY_Q, {"q": f"sku:{args.sku}"})["products"]["edges"]
        if not edges:
            edges = sc.execute(_SHOPIFY_Q, {"q": f"barcode:{args.sku}"})["products"]["edges"]
    if not edges:
        _emit({"error": f"{args.sku} not found in Shopify"})
        return
    s = shopify_sources(edges[0]["node"])
    mfs = {k: (v["value"][:80] if isinstance(v["value"], str) else v["value"])
           for k, v in s["metafields"].items()}
    _emit({
        "sku": args.sku,
        "title": s["title"], "vendor": s["vendor"], "weight": s["weight"],
        "display_attributes": s["display_attributes"],
        "metafields": mfs,
        "hint": "source forms: metafield:<ns.key>, display_attribute:<code>, "
                "field:title|vendor, weight, const:<v>. transforms: dim_value, "
                "dim_unit, weight_value, weight_unit, list_first, list_join, "
                "strip_html, bullet:N.",
    })


def cmd_set_mapping(args) -> None:
    c = _load()
    scope = "*" if args.category in (None, "*") else _gid(args.category)
    maps = c.setdefault("attribute_mappings", {})
    entry = {"source": args.source}
    if args.transform:
        entry["transform"] = args.transform
    maps.setdefault(scope, {})[args.attr] = entry
    _save(c)
    _emit({"set_mapping": f"{scope} / {args.attr}", **entry})


def cmd_del_mapping(args) -> None:
    c = _load()
    scope = "*" if args.category in (None, "*") else _gid(args.category)
    maps = c.get("attribute_mappings", {})
    removed = maps.get(scope, {}).pop(args.attr, None) is not None
    _save(c)
    _emit({"removed": f"{scope} / {args.attr}" if removed else None})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Maintain config/tesco_overrides.json")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status").set_defaults(fn=cmd_status)

    p = sub.add_parser("list-attributes"); p.add_argument("--category", required=True)
    p.set_defaults(fn=cmd_list_attributes)

    p = sub.add_parser("resolve-category"); p.add_argument("query")
    p.set_defaults(fn=cmd_resolve_category)

    p = sub.add_parser("set-colour")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--brand"); g.add_argument("--sku")
    p.add_argument("--colour", required=True)
    p.set_defaults(fn=cmd_set_colour)

    p = sub.add_parser("set-category")
    p.add_argument("--sku", required=True); p.add_argument("--gid", required=True)
    p.set_defaults(fn=cmd_set_category)

    p = sub.add_parser("exclude")
    p.add_argument("--sku", required=True); p.add_argument("--reason")
    p.add_argument("--remove", action="store_true")
    p.set_defaults(fn=cmd_exclude)

    p = sub.add_parser("set-default")
    p.add_argument("--key", required=True); p.add_argument("--value", required=True)
    p.set_defaults(fn=cmd_set_default)

    p = sub.add_parser("set-extra")
    p.add_argument("--attr", required=True)
    p.add_argument("--sku", required=True, help="SKU or '*' for all products")
    p.add_argument("--value"); p.add_argument("--remove", action="store_true")
    p.set_defaults(fn=cmd_set_extra)

    p = sub.add_parser("set-unsellable")
    p.add_argument("--gid", required=True); p.add_argument("--remove", action="store_true")
    p.set_defaults(fn=cmd_set_unsellable)

    p = sub.add_parser("shopify-sources"); p.add_argument("--sku", required=True)
    p.set_defaults(fn=cmd_shopify_sources)

    p = sub.add_parser("set-mapping")
    p.add_argument("--attr", required=True)
    p.add_argument("--source", required=True,
                   help="metafield:<ns.key> | display_attribute:<code> | field:title|vendor | weight | const:<v>")
    p.add_argument("--transform", help="dim_value|dim_unit|weight_value|weight_unit|list_first|list_join|strip_html|bullet:N")
    p.add_argument("--category", help="Tesco category gid/code to scope to; omit for all ('*')")
    p.set_defaults(fn=cmd_set_mapping)

    p = sub.add_parser("del-mapping")
    p.add_argument("--attr", required=True); p.add_argument("--category")
    p.set_defaults(fn=cmd_del_mapping)

    args = ap.parse_args(argv)
    args.fn(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
