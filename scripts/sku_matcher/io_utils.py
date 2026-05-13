"""I/O utilities for CSV reading/writing and state management."""

import json
import csv
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import pandas as pd


def load_csv(
    filepath: str,
    sku_col: str = "sku",
    title_col: str = "title"
) -> pd.DataFrame:
    """Load CSV with specified column names."""
    df = pd.read_csv(filepath)

    if sku_col not in df.columns or title_col not in df.columns:
        raise ValueError(
            f"Required columns '{sku_col}' and '{title_col}' not found. "
            f"Available: {list(df.columns)}"
        )

    df = df[[sku_col, title_col]].copy()
    df.columns = ['sku', 'title']
    df = df.dropna()

    return df


def append_match(
    output_file: str,
    sku_a: str,
    title_a: str,
    sku_b: str,
    title_b: str,
    score: float,
    method: str
):
    """Append a match to the output CSV."""
    file_exists = Path(output_file).exists()

    with open(output_file, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['sku_a', 'title_a', 'sku_b', 'title_b', 'score', 'method'])
        writer.writerow([sku_a, title_a, sku_b, title_b, f"{score:.2f}", method])


def append_jsonl(filepath: str, data: Dict):
    """Append a record to a JSONL file."""
    with open(filepath, 'a', encoding='utf-8') as f:
        f.write(json.dumps(data) + '\n')


def load_state(state_file: str) -> Dict:
    """Load resume state from JSON file."""
    if not Path(state_file).exists():
        return {'current_index': 0, 'matched_skus': []}

    with open(state_file, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_state(state_file: str, current_index: int, matched_skus: List[str]):
    """Save current state for resumption."""
    with open(state_file, 'w', encoding='utf-8') as f:
        json.dump({
            'current_index': current_index,
            'matched_skus': matched_skus
        }, f, indent=2)


def get_matched_skus(output_file: str) -> set:
    """Get set of already matched SKUs from output file."""
    if not Path(output_file).exists():
        return set()

    try:
        df = pd.read_csv(output_file)
        return set(df['sku_a'].values)
    except Exception:
        return set()
