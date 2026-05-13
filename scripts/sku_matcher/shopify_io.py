"""I/O utilities for Shopify updater: state management and logging."""

import json
from pathlib import Path
from typing import Dict, List, Optional, Set
from datetime import datetime
import pandas as pd


def load_matches_csv(
    filepath: str,
    shopify_sku_col: str,
    target_sku_col: str
) -> pd.DataFrame:
    """
    Load matches CSV and validate required columns.

    Args:
        filepath: Path to matches.csv
        shopify_sku_col: Column name for current Shopify SKU
        target_sku_col: Column name for target/new SKU

    Returns:
        DataFrame with validated columns

    Raises:
        ValueError: If required columns are missing
    """
    if not Path(filepath).exists():
        raise FileNotFoundError(f"Matches file not found: {filepath}")

    df = pd.read_csv(filepath)

    # Validate required columns exist
    required_cols = [shopify_sku_col, target_sku_col]
    missing_cols = [col for col in required_cols if col not in df.columns]

    if missing_cols:
        raise ValueError(
            f"Required columns missing from CSV: {missing_cols}\n"
            f"Available columns: {list(df.columns)}"
        )

    # Optional columns that we'll use if available
    optional_cols = ['score', 'method']
    for col in optional_cols:
        if col not in df.columns:
            df[col] = None

    # Drop rows with null SKUs
    df = df.dropna(subset=[shopify_sku_col, target_sku_col])

    return df


def load_state(state_file: str) -> Dict:
    """
    Load resume state from JSON file.

    Returns:
        State dict with keys: current_index, updated_skus, skipped_skus, failed_skus, config
    """
    if not Path(state_file).exists():
        return {
            'current_index': 0,
            'updated_skus': [],
            'skipped_skus': [],
            'failed_skus': [],
            'config': {}
        }

    try:
        with open(state_file, 'r', encoding='utf-8') as f:
            state = json.load(f)

        # Ensure all required keys exist
        state.setdefault('current_index', 0)
        state.setdefault('updated_skus', [])
        state.setdefault('skipped_skus', [])
        state.setdefault('failed_skus', [])
        state.setdefault('config', {})

        return state

    except json.JSONDecodeError as e:
        print(f"Warning: Could not parse state file {state_file}: {e}")
        print("Starting fresh...")
        return {
            'current_index': 0,
            'updated_skus': [],
            'skipped_skus': [],
            'failed_skus': [],
            'config': {}
        }


def save_state(
    state_file: str,
    current_index: int,
    updated_skus: List[str],
    skipped_skus: List[str],
    failed_skus: List[Dict],
    config: Dict
):
    """
    Save current state for resumption.

    Args:
        state_file: Path to state JSON file
        current_index: Current row index in matches.csv
        updated_skus: List of successfully updated Shopify SKUs
        skipped_skus: List of user-skipped Shopify SKUs
        failed_skus: List of failed updates with error info
        config: Config dict (matches_file, shopify_sku_col, etc.)
    """
    state = {
        'current_index': current_index,
        'updated_skus': updated_skus,
        'skipped_skus': skipped_skus,
        'failed_skus': failed_skus,
        'config': config
    }

    with open(state_file, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2)


def append_log(
    log_file: str,
    entry: Dict
):
    """
    Append a log entry to JSONL file.

    Args:
        log_file: Path to log file (JSONL format)
        entry: Log entry dict
    """
    # Add timestamp if not present
    if 'timestamp' not in entry:
        entry['timestamp'] = datetime.utcnow().isoformat() + 'Z'

    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry) + '\n')


def log_update_success(
    log_file: str,
    index: int,
    shopify_sku: str,
    target_sku: str,
    product_id: int,
    product_title: str,
    product_handle: str,
    variant_updates: List[Dict],
    match_score: Optional[float] = None,
    match_method: Optional[str] = None,
    dry_run: bool = False
):
    """
    Log a successful update.

    Args:
        log_file: Path to log file
        index: Row index in matches.csv
        shopify_sku: Original Shopify SKU
        target_sku: New target SKU
        product_id: Shopify product ID
        product_title: Product title
        product_handle: Product handle
        variant_updates: List of variant update dicts with keys:
            variant_id, variant_title, old_sku, new_sku
        match_score: Match confidence score (optional)
        match_method: Matching method (optional)
        dry_run: Whether this was a dry-run
    """
    entry = {
        'status': 'success',
        'index': index,
        'shopify_sku': shopify_sku,
        'target_sku': target_sku,
        'product_id': product_id,
        'product_title': product_title,
        'product_handle': product_handle,
        'variant_updates': variant_updates,
        'match_score': match_score,
        'match_method': match_method,
        'dry_run': dry_run,
        'error': None
    }

    append_log(log_file, entry)


def log_update_failure(
    log_file: str,
    index: int,
    shopify_sku: str,
    target_sku: str,
    error: str,
    product_id: Optional[int] = None,
    product_title: Optional[str] = None,
    match_score: Optional[float] = None,
    match_method: Optional[str] = None
):
    """
    Log a failed update.

    Args:
        log_file: Path to log file
        index: Row index in matches.csv
        shopify_sku: Original Shopify SKU
        target_sku: Target SKU
        error: Error message
        product_id: Shopify product ID (if found)
        product_title: Product title (if found)
        match_score: Match confidence score (optional)
        match_method: Matching method (optional)
    """
    entry = {
        'status': 'failed',
        'index': index,
        'shopify_sku': shopify_sku,
        'target_sku': target_sku,
        'product_id': product_id,
        'product_title': product_title,
        'error': error,
        'match_score': match_score,
        'match_method': match_method,
        'dry_run': False
    }

    append_log(log_file, entry)


def log_update_skipped(
    log_file: str,
    index: int,
    shopify_sku: str,
    target_sku: str,
    reason: str,
    product_id: Optional[int] = None,
    product_title: Optional[str] = None,
    match_score: Optional[float] = None,
    match_method: Optional[str] = None
):
    """
    Log a skipped update.

    Args:
        log_file: Path to log file
        index: Row index in matches.csv
        shopify_sku: Original Shopify SKU
        target_sku: Target SKU
        reason: Reason for skipping
        product_id: Shopify product ID (if found)
        product_title: Product title (if found)
        match_score: Match confidence score (optional)
        match_method: Matching method (optional)
    """
    entry = {
        'status': 'skipped',
        'index': index,
        'shopify_sku': shopify_sku,
        'target_sku': target_sku,
        'product_id': product_id,
        'product_title': product_title,
        'reason': reason,
        'match_score': match_score,
        'match_method': match_method,
        'dry_run': False
    }

    append_log(log_file, entry)


def get_processed_skus(state_file: str) -> Set[str]:
    """
    Get set of already processed SKUs from state file.

    Returns:
        Set of SKU strings (updated + skipped)
    """
    state = load_state(state_file)

    processed = set(state.get('updated_skus', []))
    processed.update(state.get('skipped_skus', []))

    return processed


def read_log_file(log_file: str) -> List[Dict]:
    """
    Read all entries from a JSONL log file.

    Args:
        log_file: Path to log file

    Returns:
        List of log entry dicts
    """
    if not Path(log_file).exists():
        return []

    entries = []
    with open(log_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    return entries


def get_log_summary(log_file: str) -> Dict:
    """
    Generate a summary from the log file.

    Returns:
        Dict with keys: total, success, failed, skipped
    """
    entries = read_log_file(log_file)

    summary = {
        'total': len(entries),
        'success': 0,
        'failed': 0,
        'skipped': 0,
        'failed_skus': []
    }

    for entry in entries:
        status = entry.get('status', 'unknown')
        if status == 'success':
            summary['success'] += 1
        elif status == 'failed':
            summary['failed'] += 1
            summary['failed_skus'].append({
                'sku': entry.get('shopify_sku'),
                'error': entry.get('error')
            })
        elif status == 'skipped':
            summary['skipped'] += 1

    return summary
