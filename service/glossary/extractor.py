from pathlib import Path

import structlog

from .terms import TermCandidate, extract_biterms
from .tmx import parse_tmx

log = structlog.get_logger()


def extract_from_file(
    tmx_path: Path,
    src_lang: str = "en",
    tgt_lang: str = "fr",
    similarity_min: float = 0.85,
    freq_min: int = 2,
    min_words: int = 1,
    max_doc_freq: float | None = None,
) -> list[TermCandidate]:
    """Extract bilingual term candidates from a single TMX file."""
    log.info("processing_file", path=str(tmx_path))

    bitext = parse_tmx(tmx_path, src_lang, tgt_lang)
    if not bitext:
        log.warning("empty_bitext", path=str(tmx_path))
        return []

    log.debug("bitext_loaded", segments=len(bitext))

    candidates = extract_biterms(
        bitext,
        src_lang=src_lang,
        tgt_lang=tgt_lang,
        similarity_min=similarity_min,
        freq_min=freq_min,
        min_words=min_words,
        max_doc_freq=max_doc_freq,
    )

    log.info("file_done", path=str(tmx_path), candidates=len(candidates))
    return candidates


def extract_from_dir(
    tmx_dir: Path,
    src_lang: str = "en",
    tgt_lang: str = "fr",
    similarity_min: float = 0.85,
    freq_min: int = 2,
    min_words: int = 1,
    max_doc_freq: float | None = None,
) -> list[TermCandidate]:
    """Extract and deduplicate candidates from all TMX files in a directory."""
    tmx_files = sorted(tmx_dir.glob("**/*.tmx"))
    log.info("scanning_dir", path=str(tmx_dir), tmx_files=len(tmx_files))

    all_candidates: dict[tuple[str, str], TermCandidate] = {}

    for tmx_file in tmx_files:
        for candidate in extract_from_file(
            tmx_file,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            similarity_min=similarity_min,
            freq_min=freq_min,
            min_words=min_words,
            max_doc_freq=max_doc_freq,
        ):
            key = (candidate.source.lower(), candidate.target.lower())
            existing = all_candidates.get(key)
            if existing is None or candidate.frequency > existing.frequency:
                all_candidates[key] = candidate

    result = sorted(all_candidates.values(), key=lambda c: (-c.frequency, c.source))
    log.info("dir_done", total_candidates=len(result), deduplicated=True)
    return result
