"""Text normalization utilities for SKU matching."""

import re
from typing import Set, Optional


DEFAULT_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "be", "been",
}


def load_stopwords(filepath: Optional[str] = None) -> Set[str]:
    """Load stopwords from file, or return defaults."""
    if not filepath:
        return DEFAULT_STOPWORDS.copy()

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            custom = {line.strip().lower() for line in f if line.strip()}
        return DEFAULT_STOPWORDS | custom
    except Exception as e:
        print(f"Warning: Could not load stopwords from {filepath}: {e}")
        return DEFAULT_STOPWORDS.copy()


def normalize_text(text: str, stopwords: Optional[Set[str]] = None) -> str:
    """
    Normalize text for matching:
    - lowercase
    - replace hyphens/underscores with spaces
    - keep only alphanumeric and spaces
    - remove extra whitespace
    - remove stopwords
    """
    if stopwords is None:
        stopwords = DEFAULT_STOPWORDS

    text = text.lower()
    text = text.replace('-', ' ').replace('_', ' ')
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()

    tokens = text.split()
    tokens = [t for t in tokens if t not in stopwords]

    return ' '.join(tokens)


def normalize_sku(sku: str) -> str:
    """Normalize SKU (lighter normalization, preserve structure)."""
    sku = sku.upper().strip()
    sku = re.sub(r'\s+', '', sku)
    return sku
