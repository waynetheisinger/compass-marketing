#!/usr/bin/env python3
"""Shopify SKU Updater — apply a matches.csv to MowDirect's Shopify store.

Reads the output of `matcher.py` and rewrites Shopify variant SKUs via the
GraphQL `productVariantsBulkUpdate` mutation. Auth flows through the project
standard `scripts/shopify_client.py` (client-credentials grant from `.env`)
— no per-run tokens or shop-URL flags.

Run from the repo root:

    PYTHONPATH=. python scripts/sku_matcher/shopify_updater.py matches.csv \\
        --shopify-sku-col sku_b --target-sku-col sku_a --dry-run
"""

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from scripts.shopify_client import ShopifyClient
from scripts.sku_matcher.shopify_api import (
    ShopifyAPI,
    ShopifyAPIError,
    ShopifyProduct,
    ShopifyVariant,
)
from scripts.sku_matcher.shopify_io import (
    get_log_summary,
    load_matches_csv,
    load_state,
    log_update_failure,
    log_update_skipped,
    log_update_success,
    save_state,
)


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Rewrite Shopify variant SKUs from a matches.csv (output of matcher.py).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry-run (always do this first):
  PYTHONPATH=. python scripts/sku_matcher/shopify_updater.py matches.csv \\
    --shopify-sku-col sku_b --target-sku-col sku_a --dry-run

  # Live run, only matches scoring 85+:
  PYTHONPATH=. python scripts/sku_matcher/shopify_updater.py matches.csv \\
    --shopify-sku-col sku_b --target-sku-col sku_a --min-score 85

Credentials come from .env via scripts/shopify_client.py:
  SHOPIFY_STORE_DOMAIN, SHOPIFY_CLIENT_ID, SHOPIFY_CLIENT_SECRET, SHOPIFY_API_VERSION
""",
    )

    parser.add_argument("matches_file", help="Path to matches.csv (output of matcher.py)")
    parser.add_argument(
        "--shopify-sku-col",
        required=True,
        help="Column in matches.csv holding the CURRENT Shopify SKU",
    )
    parser.add_argument(
        "--target-sku-col",
        required=True,
        help="Column in matches.csv holding the NEW (canonical) SKU to write",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        help="Skip rows scoring below this (default: 0 — process all)",
    )
    parser.add_argument(
        "--state-file",
        default="shopify_update_state.json",
        help="Resume state file (default: shopify_update_state.json)",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="JSONL audit log (default: <matches>_shopify_updates.log)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change — make no API writes",
    )

    return parser.parse_args()


# ---------------------------------------------------------------------------
# UI helpers — kept intentionally simple (prompt-based, no curses)
# ---------------------------------------------------------------------------

def display_product_info(product: ShopifyProduct):
    print(f"\n{'=' * 80}")
    print(f"Product: {product.title}")
    print(f"Handle:  {product.handle}")
    print(f"ID:      {product.id}")
    print(f"Variants: {len(product.variants)}")
    print(f"{'=' * 80}")


def prompt_variant_skus(
    base_sku: str,
    variants: List[ShopifyVariant],
) -> Optional[Dict[str, str]]:
    """Ask the user to assign a (possibly suffixed) SKU to each variant.

    Returns a `{variant_gid: new_sku}` dict, or `None` if the user cancelled.
    Variants the user typed 'skip' for are omitted from the returned dict.
    """
    print(f"\nThis product has {len(variants)} variants.")
    print(f"Base target SKU: {base_sku}\n")
    print("Current variants:")
    for i, v in enumerate(variants, 1):
        print(f'  {i}. "{v.title}" (current SKU: {v.sku})')

    print(
        "\nEnter a suffix for each variant (Enter = use base as-is).\n"
        "  blank   → use base SKU unchanged\n"
        "  skip    → leave this variant alone\n"
        "  cancel  → abandon the entire product"
    )

    variant_skus: Dict[str, str] = {}
    for i, v in enumerate(variants, 1):
        while True:
            suffix = input(f'\nVariant {i} "{v.title}" suffix: ').strip()
            low = suffix.lower()
            if low == "cancel":
                print("Update cancelled.")
                return None
            if low == "skip":
                print(f'  → Skipping variant "{v.title}"')
                break

            if suffix == "":
                new_sku = base_sku
            elif suffix.startswith(("_", "-")):
                new_sku = f"{base_sku}{suffix}"
            else:
                new_sku = f"{base_sku}_{suffix}"

            print(f"  → Will update to: {new_sku}")
            confirm = input("  Confirm? (y/n): ").strip().lower()
            if confirm in ("y", ""):
                variant_skus[v.id] = new_sku
                break
            if confirm == "n":
                print("  Let's try again...")
                continue
            print("  Invalid input. Please enter 'y' or 'n'.")

    return variant_skus


def display_update_preview(
    product: ShopifyProduct,
    variant_updates: Dict[str, str],
    match_score: Optional[float],
    match_method: Optional[str],
    dry_run: bool,
):
    print(f"\n{'[DRY-RUN] ' if dry_run else ''}Proposed update:")
    print("─" * 80)
    for v in product.variants:
        if v.id in variant_updates:
            print(f'Variant: "{v.title}"  ({v.id})')
            print(f"  OLD SKU: {v.sku}")
            print(f"  NEW SKU: {variant_updates[v.id]}")
    if match_score is not None:
        print(f"\nMatch score:  {match_score:.1f}")
    if match_method:
        print(f"Match method: {match_method}")
    print("─" * 80)


def review_and_confirm() -> str:
    """Return one of 'accept' / 'reject' / 'quit'."""
    while True:
        r = input("\n[Enter] Accept | [n] Reject | [q] Quit: ").strip().lower()
        if r in ("", "y", "yes"):
            return "accept"
        if r in ("n", "no"):
            return "reject"
        if r in ("q", "quit"):
            return "quit"
        print("Invalid input. Press Enter to accept, 'n' to reject, or 'q' to quit.")


def handle_api_error(error: str) -> str:
    """Return one of 'continue' / 'retry' / 'quit'."""
    print(f"\n❌ Update failed: {error}")
    while True:
        r = input("\n[c] Continue | [r] Retry | [q] Quit: ").strip().lower()
        if r in ("c", "continue"):
            return "continue"
        if r in ("r", "retry"):
            return "retry"
        if r in ("q", "quit"):
            return "quit"
        print("Invalid input. Enter 'c', 'r', or 'q'.")


def handle_not_found(shopify_sku: str) -> str:
    """Return one of 'continue' / 'skip' / 'quit'."""
    print(f"\n⚠️  SKU not found in Shopify: {shopify_sku}")
    while True:
        r = input("\n[c] Continue | [s] Skip | [q] Quit: ").strip().lower()
        if r in ("c", "continue"):
            return "continue"
        if r in ("s", "skip"):
            return "skip"
        if r in ("q", "quit"):
            return "quit"
        print("Invalid input. Enter 'c', 's', or 'q'.")


# ---------------------------------------------------------------------------
# Per-row processing
# ---------------------------------------------------------------------------

def process_match(
    api: ShopifyAPI,
    row: Dict,
    shopify_sku_col: str,
    target_sku_col: str,
    log_file: str,
    index: int,
    dry_run: bool,
) -> Tuple[str, Optional[str]]:
    """Handle one matches.csv row end-to-end.

    Returns `(status, error)` where status is one of:
      success / skipped / failed / quit
    """
    shopify_sku = str(row[shopify_sku_col]).strip()
    target_sku = str(row[target_sku_col]).strip()
    match_score = row.get("score")
    match_method = row.get("method")

    print(f"\n{'=' * 80}")
    print(f"Processing: {shopify_sku}  →  {target_sku}")
    print(f"{'=' * 80}")

    # ---- Look up the product ----
    try:
        products = api.find_products_by_sku(shopify_sku)
    except ShopifyAPIError as e:
        action = handle_api_error(str(e))
        if action == "quit":
            return "quit", None
        if action == "retry":
            try:
                products = api.find_products_by_sku(shopify_sku)
            except ShopifyAPIError as e2:
                log_update_failure(
                    log_file, index, shopify_sku, target_sku,
                    f"API error: {e2}", None, None, match_score, match_method,
                )
                return "failed", str(e2)
        else:
            log_update_failure(
                log_file, index, shopify_sku, target_sku,
                f"API error: {e}", None, None, match_score, match_method,
            )
            return "failed", str(e)

    if not products:
        action = handle_not_found(shopify_sku)
        if action == "quit":
            return "quit", None
        log_update_failure(
            log_file, index, shopify_sku, target_sku,
            "SKU not found in Shopify", None, None, match_score, match_method,
        )
        return ("skipped" if action == "skip" else "failed"), "SKU not found"

    if len(products) > 1:
        print(f"\n⚠️  {len(products)} products share SKU {shopify_sku}. Using the first.")
    product = products[0]
    display_product_info(product)

    # ---- Decide which variants to rewrite ----
    if len(product.variants) == 1:
        variant_updates = {product.variants[0].id: target_sku}
        print("\nSingle variant.")
        print(f"  Current SKU: {product.variants[0].sku}")
        print(f"  New SKU:     {target_sku}")
    else:
        chosen = prompt_variant_skus(target_sku, product.variants)
        if chosen is None:
            log_update_skipped(
                log_file, index, shopify_sku, target_sku,
                "User cancelled multi-variant update",
                product.id, product.title, match_score, match_method,
            )
            return "skipped", "User cancelled"
        variant_updates = chosen

    if not variant_updates:
        log_update_skipped(
            log_file, index, shopify_sku, target_sku,
            "All variants skipped by user",
            product.id, product.title, match_score, match_method,
        )
        return "skipped", "All variants skipped"

    # ---- Preview and confirm ----
    display_update_preview(
        product, variant_updates, match_score, match_method, dry_run
    )
    decision = review_and_confirm()
    if decision == "quit":
        return "quit", None
    if decision == "reject":
        log_update_skipped(
            log_file, index, shopify_sku, target_sku,
            "Rejected by user",
            product.id, product.title, match_score, match_method,
        )
        return "skipped", "User rejected"

    # ---- Apply (or dry-run) ----
    variant_update_log = [
        {
            "variant_id": vid,
            "variant_title": next(v.title for v in product.variants if v.id == vid),
            "old_sku": next(v.sku for v in product.variants if v.id == vid),
            "new_sku": new_sku,
        }
        for vid, new_sku in variant_updates.items()
    ]

    if dry_run:
        print(f"\n[DRY-RUN] Would update {len(variant_updates)} variant(s)")
        log_update_success(
            log_file, index, shopify_sku, target_sku,
            product.id, product.title, product.handle,
            variant_update_log, match_score, match_method, dry_run=True,
        )
        print("✓ Dry-run logged")
        return "success", None

    print(f"\nUpdating {len(variant_updates)} variant(s) via productVariantsBulkUpdate...")
    results = api.update_multiple_variant_skus(
        product.id,
        [(vid, new_sku) for vid, new_sku in variant_updates.items()],
    )
    all_success = all(success for _, success, _ in results)

    if all_success:
        print(f"✓ Updated {len(variant_updates)} variant(s)")
        log_update_success(
            log_file, index, shopify_sku, target_sku,
            product.id, product.title, product.handle,
            variant_update_log, match_score, match_method, dry_run=False,
        )
        return "success", None

    failed = [(vid, err) for vid, ok, err in results if not ok]
    error_msg = "Failed to update variant(s): " + "; ".join(
        f"{vid}: {err}" for vid, err in failed
    )
    log_update_failure(
        log_file, index, shopify_sku, target_sku,
        error_msg, product.id, product.title, match_score, match_method,
    )
    return "failed", error_msg


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    if not Path(args.matches_file).exists():
        print(f"Error: Matches file not found: {args.matches_file}")
        sys.exit(1)

    log_file = args.log_file or args.matches_file.replace(".csv", "_shopify_updates.log")

    print(f"{'=' * 80}")
    print("Shopify SKU Updater")
    print(f"{'=' * 80}")
    print(f"Matches file:        {args.matches_file}")
    print(f"Shopify SKU column:  {args.shopify_sku_col}")
    print(f"Target SKU column:   {args.target_sku_col}")
    print(f"Min score:           {args.min_score}")
    print(f"State file:          {args.state_file}")
    print(f"Log file:            {log_file}")
    print(f"Mode:                {'DRY-RUN' if args.dry_run else 'LIVE'}")
    print("Auth:                scripts/shopify_client.py (client-credentials, GraphQL)")
    print(f"{'=' * 80}\n")

    # Load matches up-front so we fail fast on bad columns
    try:
        df = load_matches_csv(args.matches_file, args.shopify_sku_col, args.target_sku_col)
        print(f"✓ Loaded {len(df)} match(es)\n")
    except Exception as e:
        print(f"❌ Failed to load matches: {e}")
        sys.exit(1)

    if args.min_score > 0 and "score" in df.columns and df["score"].notna().any():
        before = len(df)
        df = df[df["score"] >= args.min_score]
        print(f"Filtered by score ≥ {args.min_score}: {len(df)}/{before} matches remain\n")

    # Resume state
    state = load_state(args.state_file)
    start_idx = state["current_index"]
    updated_skus = state["updated_skus"]
    skipped_skus = state["skipped_skus"]
    failed_skus = state["failed_skus"]
    processed_skus = set(updated_skus + skipped_skus)

    if start_idx > 0:
        print(f"Resuming from index {start_idx} ({len(processed_skus)} SKU(s) already done)\n")

    config = {
        "matches_file": args.matches_file,
        "shopify_sku_col": args.shopify_sku_col,
        "target_sku_col": args.target_sku_col,
        "min_score": args.min_score,
    }

    success_count = skipped_count = failed_count = 0
    idx = start_idx  # In case the loop body never executes

    # One ShopifyClient session for the whole run — token refresh is automatic.
    with ShopifyClient() as client:
        api = ShopifyAPI(client=client)

        print("Validating credentials...")
        try:
            api.validate_credentials()
            print("✓ Credentials validated\n")
        except ShopifyAPIError as e:
            print(f"❌ {e}")
            sys.exit(1)

        print("Starting updates...  (Ctrl+C to quit safely)\n")

        try:
            for idx in range(start_idx, len(df)):
                row = df.iloc[idx]
                shopify_sku = str(row[args.shopify_sku_col]).strip()

                if shopify_sku in processed_skus:
                    print(f"\nSkipping already processed: {shopify_sku}")
                    continue

                status, error = process_match(
                    api, row,
                    args.shopify_sku_col, args.target_sku_col,
                    log_file, idx, args.dry_run,
                )

                if status == "quit":
                    print("\n\nQuitting and saving state...")
                    save_state(args.state_file, idx, updated_skus, skipped_skus, failed_skus, config)
                    print(f"State saved to {args.state_file}")
                    break

                if status == "success":
                    success_count += 1
                    updated_skus.append(shopify_sku)
                    processed_skus.add(shopify_sku)
                elif status == "skipped":
                    skipped_count += 1
                    skipped_skus.append(shopify_sku)
                    processed_skus.add(shopify_sku)
                elif status == "failed":
                    failed_count += 1
                    failed_skus.append({"sku": shopify_sku, "error": error})

                save_state(args.state_file, idx + 1, updated_skus, skipped_skus, failed_skus, config)

        except KeyboardInterrupt:
            print("\n\nInterrupted! Saving state...")
            save_state(args.state_file, idx, updated_skus, skipped_skus, failed_skus, config)
            print(f"State saved to {args.state_file}")
            sys.exit(0)

    # Summary
    print(f"\n{'=' * 80}")
    print("Update Complete")
    print(f"{'=' * 80}")
    print(f"Updated: {success_count}")
    print(f"Skipped: {skipped_count}")
    print(f"Failed:  {failed_count}")
    print(f"\nLog file: {log_file}")

    if failed_count:
        print("\nLast failures:")
        for fail in failed_skus[-10:]:
            print(f"  - {fail['sku']}: {fail['error']}")
        if len(failed_skus) > 10:
            print(f"  ... and {len(failed_skus) - 10} more (see log file)")

    # Tidy up the state file once we've drained the CSV
    if idx == len(df) - 1:
        Path(args.state_file).unlink(missing_ok=True)
        print("\n✓ All records processed. State file removed.")

    # Audit summary from the log (a nice cross-check)
    summary = get_log_summary(log_file)
    print(
        f"\nLog tally → success={summary['success']}  "
        f"skipped={summary['skipped']}  failed={summary['failed']}"
    )

    sys.exit(0 if failed_count == 0 else 1)


if __name__ == "__main__":
    main()
