"""Matching pipeline: TF-IDF → RapidFuzz → Claude semantic tie-breaker."""

import os
from typing import List, Dict, Tuple, Optional, Set
import pandas as pd
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from rapidfuzz import fuzz
from scripts.sku_matcher.normalize import normalize_text


class Matcher:
    """SKU matching pipeline."""

    def __init__(
        self,
        df_b: pd.DataFrame,
        stopwords: Optional[Set[str]] = None,
        k: int = 50,
        use_claude: bool = False,
        max_claude_calls: int = 10,
        min_score_threshold: float = 70.0
    ):
        """
        Initialize matcher with catalog B.

        Args:
            df_b: DataFrame with 'sku' and 'title' columns
            stopwords: Set of stopwords for normalization
            k: Number of candidates to retrieve
            use_claude: Enable Claude semantic tie-breaker
            max_claude_calls: Max Claude API calls per query
            min_score_threshold: Minimum acceptable match score
        """
        self.df_b = df_b.copy()
        self.stopwords = stopwords
        self.k = k
        self.use_claude = use_claude
        self.max_claude_calls = max_claude_calls
        self.min_score_threshold = min_score_threshold

        self.df_b['title_normalized'] = self.df_b['title'].apply(
            lambda x: normalize_text(x, stopwords)
        )

        self.vectorizer = TfidfVectorizer(
            max_features=1000,
            ngram_range=(1, 2),
            min_df=1
        )
        self.tfidf_matrix = self.vectorizer.fit_transform(
            self.df_b['title_normalized']
        )

        if use_claude:
            try:
                from anthropic import Anthropic
                api_key = os.environ.get('ANTHROPIC_API_KEY')
                if not api_key:
                    print("Warning: ANTHROPIC_API_KEY not set. Claude tie-breaker disabled.")
                    self.use_claude = False
                else:
                    self.claude_client = Anthropic(api_key=api_key)
            except ImportError:
                print("Warning: anthropic package not installed. Claude tie-breaker disabled.")
                self.use_claude = False

    def get_candidates(self, sku_a: str, title_a: str) -> pd.DataFrame:
        """Retrieve top K candidates using TF-IDF cosine similarity."""
        title_normalized = normalize_text(title_a, self.stopwords)
        query_vec = self.vectorizer.transform([title_normalized])
        similarities = cosine_similarity(query_vec, self.tfidf_matrix).flatten()

        top_k_indices = np.argsort(similarities)[-self.k:][::-1]

        candidates = self.df_b.iloc[top_k_indices].copy()
        candidates['tfidf_score'] = similarities[top_k_indices]

        # FBA filtering: if sku_a has -AMZ suffix, only return candidates with -AMZ suffix
        has_amz_suffix = sku_a.upper().endswith('-AMZ')
        if has_amz_suffix:
            candidates = candidates[candidates['sku'].str.upper().str.endswith('-AMZ')]

        return candidates

    def rerank_with_rapidfuzz(
        self,
        title_a: str,
        candidates: pd.DataFrame
    ) -> pd.DataFrame:
        """Re-rank candidates using RapidFuzz."""
        title_a_normalized = normalize_text(title_a, self.stopwords)

        scores = []
        for _, row in candidates.iterrows():
            title_b_normalized = row['title_normalized']

            token_set = fuzz.token_set_ratio(title_a_normalized, title_b_normalized)
            wratio = fuzz.WRatio(title_a_normalized, title_b_normalized)
            partial = fuzz.partial_ratio(title_a_normalized, title_b_normalized)

            combined = 0.6 * token_set + 0.3 * wratio + 0.1 * partial
            scores.append(combined)

        candidates = candidates.copy()
        candidates['fuzz_score'] = scores
        candidates = candidates.sort_values('fuzz_score', ascending=False)

        return candidates

    def claude_semantic_score(
        self,
        title_a: str,
        title_b: str
    ) -> float:
        """Use Claude to score semantic similarity (0-100)."""
        if not self.use_claude:
            return 0.0

        try:
            prompt = f"""Compare these two product titles and rate their semantic similarity from 0-100, where:
- 100 = identical products
- 80-99 = same product, minor variations
- 60-79 = similar products, different variants
- 40-59 = related products, different models
- 0-39 = different products

Title A: {title_a}
Title B: {title_b}

Respond with ONLY a number between 0-100."""

            # Haiku 4.5 — fastest model that still handles the nuance needed
            # for the title-vs-title semantic comparison this prompt does.
            message = self.claude_client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=10,
                messages=[{"role": "user", "content": prompt}]
            )

            score_text = message.content[0].text.strip()
            score = float(score_text)
            return max(0.0, min(100.0, score))

        except Exception as e:
            print(f"Warning: Claude API call failed: {e}")
            return 0.0

    def match(
        self,
        sku_a: str,
        title_a: str
    ) -> List[Dict]:
        """
        Full matching pipeline for a single SKU from A.

        Returns list of matches with scores, sorted by relevance.
        """
        candidates = self.get_candidates(sku_a, title_a)

        if len(candidates) == 0:
            return []

        candidates = self.rerank_with_rapidfuzz(title_a, candidates)

        top_scores = candidates['fuzz_score'].head(2).values
        should_use_claude = (
            self.use_claude
            and len(top_scores) >= 2
            and abs(top_scores[0] - top_scores[1]) <= 3
            and top_scores[0] < 90
        )

        if should_use_claude:
            n_to_rerank = min(self.max_claude_calls, len(candidates))
            claude_scores = []

            for _, row in candidates.head(n_to_rerank).iterrows():
                semantic_score = self.claude_semantic_score(title_a, row['title'])
                claude_scores.append(semantic_score)

            candidates_subset = candidates.head(n_to_rerank).copy()
            candidates_subset['claude_score'] = claude_scores
            candidates_subset['final_score'] = (
                0.5 * candidates_subset['fuzz_score'] +
                0.5 * candidates_subset['claude_score']
            )
            candidates_subset = candidates_subset.sort_values('final_score', ascending=False)

            remaining = candidates.iloc[n_to_rerank:].copy()
            remaining['claude_score'] = 0.0
            remaining['final_score'] = remaining['fuzz_score']

            candidates = pd.concat([candidates_subset, remaining], ignore_index=True)
            method = 'tfidf+rapidfuzz+claude'
        else:
            candidates['final_score'] = candidates['fuzz_score']
            candidates['claude_score'] = 0.0
            method = 'tfidf+rapidfuzz'

        results = []
        for _, row in candidates.iterrows():
            results.append({
                'sku_b': row['sku'],
                'title_b': row['title'],
                'score': row['final_score'],
                'method': method,
                'tfidf_score': row['tfidf_score'],
                'fuzz_score': row['fuzz_score'],
                'claude_score': row.get('claude_score', 0.0)
            })

        return results
