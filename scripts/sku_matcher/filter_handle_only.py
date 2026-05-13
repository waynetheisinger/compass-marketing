#!/usr/bin/env python3
"""Filter out rows that only have data in the Handle column."""

import argparse
import pandas as pd
from pathlib import Path


def filter_Handle_only_rows(input_file: str, output_file: str):
    """
    Remove rows that only have data in the 'Handle' column.

    Args:
        input_file: Path to input CSV file
        output_file: Path to output CSV file
    """
    # Read the CSV
    df = pd.read_csv(input_file)

    if 'Handle' not in df.columns:
        raise ValueError(
            f"Column 'Handle' not found in CSV. "
            f"Available columns: {list(df.columns)}"
        )

    # Get all columns except 'Handle'
    other_columns = [col for col in df.columns if col != 'Handle']

    # Create a mask: True if at least one non-Handle column has data
    # We check if all other columns are null/empty for each row
    has_other_data = df[other_columns].notna().any(axis=1)

    # Filter: keep rows that have data in at least one non-Handle column
    df_filtered = df[has_other_data].copy()

    # Save to output file
    df_filtered.to_csv(output_file, index=False)

    rows_removed = len(df) - len(df_filtered)
    print(f"Input file: {input_file}")
    print(f"  Total rows: {len(df)}")
    print(f"  Rows with only Handle data: {rows_removed}")
    print(f"  Rows kept: {len(df_filtered)}")
    print(f"\nOutput written to: {output_file}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Filter out rows that only have data in the Handle column.'
    )

    parser.add_argument('input', help='Input CSV file')
    parser.add_argument('output', help='Output CSV file')

    args = parser.parse_args()

    # Check if input file exists
    if not Path(args.input).exists():
        print(f"Error: Input file '{args.input}' not found")
        return 1

    # Check if output file already exists
    if Path(args.output).exists():
        response = input(f"Warning: '{args.output}' already exists. Overwrite? (y/n): ")
        if response.lower() != 'y':
            print("Cancelled")
            return 0

    filter_Handle_only_rows(args.input, args.output)
    return 0


if __name__ == '__main__':
    exit(main())
