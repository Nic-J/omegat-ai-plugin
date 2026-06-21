from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import spacy
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity

_LABSE_MODEL = "sentence-transformers/LaBSE"

# Multi-word terms starting with these words are excluded (articles, possessives,
# demonstratives). Matched against the lowercased first word of each term.
_LEADING_FUNCTION_WORDS: frozenset[str] = frozenset({
    "a", "an", "the",
    "my", "your", "his", "her", "its", "our", "their",
    "this", "that", "these", "those",
})

_SPACY_MODELS: dict[str, str] = {
    "en": "en_core_web_md",
    "fr": "fr_core_news_md",
    "de": "de_core_news_md",
    "es": "es_core_news_md",
    "it": "it_core_news_md",
    "pt": "pt_core_news_md",
}


@dataclass
class TermCandidate:
    source: str
    target: str
    similarity: float
    frequency: int


@lru_cache(maxsize=6)
def _spacy_model(lang: str) -> spacy.Language:
    name = _SPACY_MODELS.get(lang.lower())
    if not name:
        raise ValueError(f"Unsupported language: {lang!r}. Supported: {list(_SPACY_MODELS)}")
    # Disable unused pipeline components to speed up processing
    return spacy.load(name, disable=["ner", "lemmatizer"])


@lru_cache(maxsize=1)
def _labse() -> SentenceTransformer:
    return SentenceTransformer(_LABSE_MODEL)


def _extract_term_counts(
    texts: list[str],
    lang: str,
    span_range: tuple[int, int],
) -> Counter:
    """Extract candidate terms from texts using spaCy noun chunks."""
    nlp = _spacy_model(lang)
    min_words, max_words = span_range
    counts: Counter = Counter()

    for doc in nlp.pipe(texts, batch_size=64):
        seen: set[str] = set()
        for chunk in doc.noun_chunks:
            content_words = [t for t in chunk if not t.is_stop and not t.is_punct and t.is_alpha]
            if min_words <= len(content_words) <= max_words:
                term = chunk.text.lower().strip()
                first_word = term.split()[0]
                if term and term not in seen and first_word not in _LEADING_FUNCTION_WORDS:
                    counts[term] += 1
                    seen.add(term)

    return counts


def extract_biterms(
    bitext: list[tuple[str, str]],
    src_lang: str,
    tgt_lang: str,
    similarity_min: float = 0.85,
    freq_min: int = 2,
    min_words: int = 1,
    max_doc_freq: float | None = None,
) -> list[TermCandidate]:
    """Extract bilingual term candidates from (source, target) sentence pairs.

    Uses spaCy for candidate term extraction and LaBSE for cross-lingual
    similarity scoring to align source terms with their target equivalents.

    max_doc_freq: drop terms appearing in more than this fraction of segments
    (e.g. 0.3 removes terms found in >30% of segments — likely common words).
    """
    src_texts, tgt_texts = zip(*bitext)
    span_range = (min_words, 3)

    src_counts = _extract_term_counts(list(src_texts), src_lang, span_range)
    tgt_counts = _extract_term_counts(list(tgt_texts), tgt_lang, span_range)

    total_src = len(src_texts)
    total_tgt = len(tgt_texts)

    def _keep(count: int, total: int) -> bool:
        if count < freq_min:
            return False
        if max_doc_freq is not None and count / total > max_doc_freq:
            return False
        return True

    # Filter before the expensive vectorization step
    src_terms = [t for t, c in src_counts.items() if _keep(c, total_src)]
    tgt_terms = [t for t, c in tgt_counts.items() if _keep(c, total_tgt)]

    if not src_terms or not tgt_terms:
        return []

    model = _labse()
    src_vecs = model.encode(src_terms, batch_size=128, show_progress_bar=True)
    tgt_vecs = model.encode(tgt_terms, batch_size=128, show_progress_bar=True)

    sim_matrix = cosine_similarity(src_vecs, tgt_vecs)

    candidates: list[TermCandidate] = []
    for i, src_term in enumerate(src_terms):
        best_j = int(np.argmax(sim_matrix[i]))
        best_sim = float(sim_matrix[i, best_j])
        if best_sim >= similarity_min:
            tgt_term = tgt_terms[best_j]
            freq = min(src_counts[src_term], tgt_counts[tgt_term])
            candidates.append(TermCandidate(
                source=src_term,
                target=tgt_term,
                similarity=round(best_sim, 3),
                frequency=freq,
            ))

    return candidates
