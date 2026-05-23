"""
One-shot discovery: walk Mirakl /values_lists for Kingfisher and dump every
value-list's code+label pairs to config/bq_value_list_codes.json.

The output is consumed by scripts/bq_csv_batch.py's write-row step to
translate human-readable labels (e.g. "Spectrum", "240V") into the codes
Mirakl actually wants (e.g. "4592", "16"). The Mirakl product import
rejects labels with error 2006 ("not in the possible values set in the
value list") — codes are required.

Output JSON shape:
    {
      "discovered_at": "2026-05-20T11:30:00Z",
      "by_pim": {
        "PIM_13946": {
          "Core_Product_type": {"Battery charger": "27202"},
          "Charger_type":      {"Fast": "...", "Standard": "..."},
          ...
        }
      },
      "global": {
        "Acquisition_brand":      {"Spectrum": "4592", ...},
        "Power_voltage_supply":   {"240V": "16", ...},
        "Guarantee":              {"5 years": "81", ...},
        ...
      }
    }

Per-PIM lists have a `_PIM_<code>` suffix on their `code` field; globals do
not. This matches the convention documented in project memory
`mirakl_kingfisher_attribute_quirks` ("Core_Product type codes are
per-hierarchy", "Battery_chemistry differs between tool and battery
hierarchies").

Usage:
    PYTHONPATH=. .venv/bin/python scripts/bq_discover_value_list_codes.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

_OUT_PATH = os.path.join(_REPO_ROOT, "config", "bq_value_list_codes.json")

_PIM_SUFFIX_RE = re.compile(r"_PIM_\d+$")


def main() -> int:
    from mirakl_client import MiraklClient

    client = MiraklClient("KINGFISHER")
    print("Querying Mirakl /values_lists ...", file=sys.stderr)
    data = client.get("/values_lists")
    all_lists = data.get("values_lists") or []
    print(f"  fetched {len(all_lists)} value lists", file=sys.stderr)

    by_pim: dict[str, dict[str, dict[str, str]]] = {}
    global_lists: dict[str, dict[str, str]] = {}

    for vl in all_lists:
        code = vl.get("code") or ""
        if not code:
            continue
        pim_match = _PIM_SUFFIX_RE.search(code)
        if pim_match:
            pim = pim_match.group(0)[1:]  # drop the leading "_"
            attr = code[: pim_match.start()]
            target = by_pim.setdefault(pim, {}).setdefault(attr, {})
        else:
            target = global_lists.setdefault(code, {})

        for v in (vl.get("values") or []):
            v_code = v.get("code")
            v_label = v.get("label")
            if v_code is None or v_label is None:
                continue
            target[str(v_label)] = str(v_code)

    os.makedirs(os.path.dirname(_OUT_PATH), exist_ok=True)
    out = {
        "_comment": (
            "Value-list code maps for Kingfisher (B&Q) Mirakl. Discovered by "
            "scripts/bq_discover_value_list_codes.py. Used by "
            "scripts/bq_csv_batch.py to translate labels into the codes "
            "Mirakl actually wants (avoids error 2006). Per-PIM lists override "
            "globals for the same attribute name."
        ),
        "discovered_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "by_pim": by_pim,
        "global": global_lists,
    }
    with open(_OUT_PATH, "w") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)

    # Summary to stderr
    print(f"\nWrote {_OUT_PATH}", file=sys.stderr)
    print(f"  per-PIM hierarchies: {len(by_pim)}", file=sys.stderr)
    for pim, attrs in sorted(by_pim.items()):
        print(f"    {pim}: {len(attrs)} attributes, "
              f"{sum(len(v) for v in attrs.values())} total label->code pairs",
              file=sys.stderr)
    print(f"  global lists: {len(global_lists)} attributes, "
          f"{sum(len(v) for v in global_lists.values())} total label->code pairs",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
