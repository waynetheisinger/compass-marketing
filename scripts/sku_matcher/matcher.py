#!/usr/bin/env python3
"""Main CLI for SKU matching."""

import argparse
import sys
from pathlib import Path
from tqdm import tqdm

from scripts.sku_matcher.io_utils import (
    load_csv, append_match, append_jsonl,
    load_state, save_state, get_matched_skus
)
from scripts.sku_matcher.matching import Matcher
from scripts.sku_matcher.normalize import load_stopwords
from scripts.sku_matcher.tui import MatchSelector


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Interactive SKU matcher with TF-IDF, RapidFuzz, and Claude semantic scoring.'
    )

    parser.add_argument('file_a', help='CSV file A (to match)')
    parser.add_argument('file_b', help='CSV file B (catalog)')
    parser.add_argument('--out', default='matches.csv', help='Output file (default: matches.csv)')

    parser.add_argument('--col-a-sku', default='sku', help='SKU column name in file A')
    parser.add_argument('--col-a-title', default='title', help='Title column name in file A')
    parser.add_argument('--col-b-sku', default='sku', help='SKU column name in file B')
    parser.add_argument('--col-b-title', default='title', help='Title column name in file B')

    parser.add_argument('--k', type=int, default=50, help='Candidate pool size (default: 50)')
    parser.add_argument('--batch', type=int, default=5, help='Page size for TUI (default: 5)')
    parser.add_argument('--min-score', type=float, default=70.0, help='Minimum score threshold (default: 70)')

    parser.add_argument('--stopwords', help='File with additional stopwords (one per line)')
    parser.add_argument('--claude', action='store_true', help='Enable Claude semantic tie-breaker')
    parser.add_argument('--max-claude', type=int, default=10, help='Max Claude calls per query (default: 10)')

    parser.add_argument('--redo', action='store_true', help='Redo already matched items')
    parser.add_argument('--state-file', default='state.json', help='State file for resume (default: state.json)')

    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()

    print(f"Loading catalog A from {args.file_a}...")
    df_a = load_csv(args.file_a, args.col_a_sku, args.col_a_title)
    print(f"  Loaded {len(df_a)} records")

    print(f"Loading catalog B from {args.file_b}...")
    df_b = load_csv(args.file_b, args.col_b_sku, args.col_b_title)
    print(f"  Loaded {len(df_b)} records")

    stopwords = load_stopwords(args.stopwords)
    print(f"  Using {len(stopwords)} stopwords")

    if args.claude:
        print("  Claude semantic tie-breaker: ENABLED")
    else:
        print("  Claude semantic tie-breaker: DISABLED")

    print("\nInitializing matcher...")
    matcher = Matcher(
        df_b,
        stopwords=stopwords,
        k=args.k,
        use_claude=args.claude,
        max_claude_calls=args.max_claude,
        min_score_threshold=args.min_score
    )

    selector = MatchSelector(batch_size=args.batch)

    state = load_state(args.state_file)
    start_idx = 0 if args.redo else state['current_index']
    matched_skus = set() if args.redo else set(state.get('matched_skus', []))

    if not args.redo:
        already_matched = get_matched_skus(args.out)
        matched_skus.update(already_matched)

    skipped_file = args.out.replace('.csv', '_skipped.jsonl')
    unmatched_file = args.out.replace('.csv', '_unmatched.jsonl')

    # Display progress and resume info
    total_records = len(df_a)
    remaining = total_records - start_idx

    print(f"\n{'='*80}")
    if start_idx > 0:
        print(f"RESUMING from previous session")
        print(f"  Progress: {start_idx}/{total_records} records already processed")
        print(f"  Remaining: {remaining} records to match")
        print(f"  Already matched: {len(matched_skus)} SKUs")
    else:
        print(f"Starting fresh matching session")
        print(f"  Total records: {total_records}")

    print(f"\nOutput files:")
    print(f"  Matches: {args.out}")
    print(f"  Skipped: {skipped_file}")
    print(f"  Unmatched: {unmatched_file}")
    print(f"  State: {args.state_file}")

    print(f"\n{'='*80}")
    print("Controls:")
    print("  • Press 'q' during any prompt to quit and save progress")
    print("  • Press Ctrl+C to interrupt and save progress")
    print("  • Re-run the same command to resume from where you left off")
    print(f"{'='*80}\n")

    try:
        for idx in tqdm(range(start_idx, len(df_a)), desc="Matching", initial=start_idx, total=len(df_a)):
            row_a = df_a.iloc[idx]
            sku_a = row_a['sku']
            title_a = row_a['title']

            if sku_a in matched_skus and not args.redo:
                continue

            matches = matcher.match(sku_a, title_a)

            result = selector.select_match(sku_a, title_a, matches)

            if result is None:
                continue

            if isinstance(result, dict) and 'action' in result:
                if result['action'] == 'quit':
                    print(f"\n{'='*80}")
                    print("QUITTING AND SAVING PROGRESS")
                    print(f"{'='*80}")
                    save_state(args.state_file, idx, list(matched_skus))

                    processed = idx - start_idx
                    remaining = len(df_a) - idx

                    print(f"\nProgress saved:")
                    print(f"  • Processed this session: {processed} records")
                    print(f"  • Total progress: {idx}/{len(df_a)} records")
                    print(f"  • Remaining: {remaining} records")
                    print(f"  • Matched SKUs: {len(matched_skus)}")
                    print(f"  • State file: {args.state_file}")

                    print(f"\nTo resume, run the same command:")
                    print(f"  python matcher.py {args.file_a} {args.file_b} --out {args.out}")
                    print(f"\n{'='*80}\n")
                    sys.exit(0)

                elif result['action'] == 'skip':
                    append_jsonl(skipped_file, {
                        'sku_a': sku_a,
                        'title_a': title_a
                    })
                    continue

                elif result['action'] == 'unmatched':
                    append_jsonl(unmatched_file, {
                        'sku_a': sku_a,
                        'title_a': title_a
                    })
                    matched_skus.add(sku_a)
                    save_state(args.state_file, idx + 1, list(matched_skus))
                    continue

            else:
                append_match(
                    args.out,
                    sku_a,
                    title_a,
                    result['sku_b'],
                    result['title_b'],
                    result['score'],
                    result['method']
                )
                matched_skus.add(sku_a)
                save_state(args.state_file, idx + 1, list(matched_skus))

                if result['score'] < args.min_score:
                    print(f"\n⚠️  Warning: Low score ({result['score']:.0f}) for selected match")
                    input("Press Enter to continue...")

    except KeyboardInterrupt:
        print(f"\n\n{'='*80}")
        print("INTERRUPTED! SAVING PROGRESS")
        print(f"{'='*80}")
        save_state(args.state_file, idx, list(matched_skus))

        processed = idx - start_idx
        remaining = len(df_a) - idx

        print(f"\nProgress saved:")
        print(f"  • Processed this session: {processed} records")
        print(f"  • Total progress: {idx}/{len(df_a)} records")
        print(f"  • Remaining: {remaining} records")
        print(f"  • Matched SKUs: {len(matched_skus)}")
        print(f"  • State file: {args.state_file}")

        print(f"\nTo resume, run the same command:")
        print(f"  python matcher.py {args.file_a} {args.file_b} --out {args.out}")
        print(f"\n{'='*80}\n")
        sys.exit(0)

    print(f"\n{'='*80}")
    print("✓ ALL RECORDS PROCESSED!")
    print(f"{'='*80}")

    print(f"\nResults:")
    print(f"  • Matches: {args.out}")
    print(f"  • Skipped: {skipped_file}")
    print(f"  • Unmatched: {unmatched_file}")
    print(f"  • Total matched: {len(matched_skus)} SKUs")

    Path(args.state_file).unlink(missing_ok=True)
    print(f"\n✓ State file cleared: {args.state_file}")
    print(f"{'='*80}\n")


if __name__ == '__main__':
    main()
