#!/usr/bin/env python3
"""
Mirakl connectivity / readiness check — operator-agnostic.

Validates that a Mirakl instance's base URL + API key are wired correctly and
the seller account can read the catalogue schema. Use it the moment a new
operator's credentials land in .env (e.g. MIRAKL_TESCO_BASE_URL /
MIRAKL_TESCO_API_KEY) to satisfy the "connectivity check succeeds" gate before
attempting any product/offer import.

Run from the repo root:

    python scripts/mirakl_connectivity.py --operator TESCO
    python scripts/mirakl_connectivity.py --operator KINGFISHER --hierarchies

What it does (read-only; never mutates):
  1. Confirms the env vars resolve and the client constructs.
  2. GET /version           — cheapest authenticated round-trip.
  3. GET /hierarchies       — category tree (the acceptance-criteria check).
  4. GET /values_lists      — enum value lists (optional, --values-lists).
  5. With --hierarchies, prints the first N hierarchy codes + labels so you can
     start mapping product types to category codes.

Exit code 0 = reachable and authenticated; non-zero = a problem to fix.
"""
from __future__ import annotations

import argparse
import os
import sys

# Anchor imports at repo root regardless of CWD.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import requests  # noqa: E402

from scripts.mirakl_client import MiraklClient  # noqa: E402
from scripts.mirakl_operators import OPERATORS  # noqa: E402


def _try(label: str, fn) -> tuple[bool, object]:
    """Run fn(); return (ok, result_or_exception) and print a one-line verdict."""
    try:
        result = fn()
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        print(f"  ✗ {label}: HTTP {code}")
        return False, e
    except Exception as e:  # noqa: BLE001 — surface anything as a failed check
        print(f"  ✗ {label}: {type(e).__name__}: {e}")
        return False, e
    print(f"  ✓ {label}")
    return True, result


def check(operator: str, *, show_hierarchies: bool, show_values_lists: bool,
          limit: int) -> int:
    name = operator.upper()
    prefix = f"MIRAKL_{name}"
    print(f"Mirakl connectivity check — operator {name}")
    print(f"  env: {prefix}_BASE_URL, {prefix}_API_KEY")

    if name not in OPERATORS:
        print(f"  ! {name} is not a known operator in mirakl_operators.OPERATORS "
              f"({', '.join(OPERATORS)}). Connectivity can still be tested via env vars.")

    base = os.environ.get(f"{prefix}_BASE_URL")
    key = os.environ.get(f"{prefix}_API_KEY")
    if not base or not key:
        print(f"\n  ✗ Credentials not set. Add to .env:")
        print(f"      {prefix}_BASE_URL=https://<instance>/api")
        print(f"      {prefix}_API_KEY=<seller api key>")
        return 2
    print(f"  base_url: {base}")

    try:
        client = MiraklClient(name)
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ Client construction failed: {e}")
        return 2

    print("\nReachability:")
    ok_version, _ = _try("GET /version", lambda: client.get("/version"))
    ok_hier, hier = _try("GET /hierarchies", lambda: client.get("/hierarchies"))

    if show_values_lists:
        _try("GET /values_lists", lambda: client.get("/values_lists"))

    if ok_hier and show_hierarchies and isinstance(hier, dict):
        rows = hier.get("hierarchies", hier.get("data", []))
        print(f"\nFirst {min(limit, len(rows))} of {len(rows)} hierarchies:")
        for h in rows[:limit]:
            code = h.get("code") or h.get("hierarchy_code") or "?"
            label = h.get("label") or h.get("name") or ""
            print(f"  {code:16s} {label}")

    # Connectivity verdict: an authenticated round-trip succeeded.
    reachable = ok_version or ok_hier
    print()
    if reachable:
        print(f"✓ {name} reachable and authenticated.")
        return 0
    print(f"✗ {name} not reachable / not authenticated — check base URL and API key.")
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Mirakl connectivity / readiness check")
    ap.add_argument("--operator", "-o", default="KINGFISHER",
                    help="Operator name (KINGFISHER, TESCO, THERANGE, …). Default KINGFISHER.")
    ap.add_argument("--hierarchies", action="store_true",
                    help="Print the first N category hierarchy codes + labels")
    ap.add_argument("--values-lists", action="store_true",
                    help="Also fetch /values_lists (enum value lists)")
    ap.add_argument("--limit", type=int, default=25,
                    help="How many hierarchies to print with --hierarchies (default 25)")
    args = ap.parse_args(argv)
    return check(args.operator, show_hierarchies=args.hierarchies,
                 show_values_lists=args.values_lists, limit=args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
