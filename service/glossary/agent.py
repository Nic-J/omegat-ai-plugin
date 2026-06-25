"""
Glossary extraction agent.

Two-phase approach:
1. LLM identifies candidate terms worth looking up from the source strings.
2. For each candidate, the agent queries the local terminology index via
   lookup_terminology — a fast local SQLite call, no network, no per-lookup
   LLM call. Import data first with:
     uv run python -m glossary.cli import-terminology <file> --preset termium/oqlf
"""
import re

import structlog
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import UnexpectedModelBehavior

from config import get_settings
from glossary.terminology import lookup_term
from models import GlossarySuggestion

log = structlog.get_logger()


class _CandidateTerms(BaseModel):
    terms: list[str]


def _strip_html(html: str) -> str:
    """Strip HTML tags and collapse whitespace. Utility for user-added HTTP fetch tools."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return text.strip()[:get_settings().glossary_max_page_chars]


class GlossaryDeps:
    def __init__(self, source_lang: str, target_lang: str) -> None:
        self.source_lang = source_lang
        self.target_lang = target_lang


def _make_agent(output_type) -> Agent:
    """Build a PydanticAI agent using the glossary_model setting (falls back to ai_model)."""
    from model_utils import resolve_model
    settings = get_settings()
    model_str = settings.glossary_model or settings.ai_model
    return Agent(resolve_model(model_str), output_type=output_type, deps_type=GlossaryDeps)


_term_extractor: Agent = _make_agent(_CandidateTerms)
_glossary_agent: Agent = _make_agent(list[GlossarySuggestion])


@_glossary_agent.tool
async def lookup_terminology(ctx: RunContext[GlossaryDeps], term: str) -> str:
    """Search the local terminology index for authoritative source→target translations."""
    hits = lookup_term(term, ctx.deps.source_lang, ctx.deps.target_lang)
    if not hits:
        return f"No results found for '{term}'"
    lines = [
        f"- {h.source_term} → {h.target_term} [{h.source}]"
        + (f" ({h.subject})" if h.subject else "")
        for h in hits
    ]
    return "\n".join(lines)


async def extract_glossary(
    source_strings: list[str],
    source_lang: str,
    target_lang: str,
) -> list[GlossarySuggestion]:
    """
    Given source strings from an OmegaT file, identify candidate terms and
    look them up in the local terminology index to produce glossary suggestions.
    """
    from glossary.state import compute_hash, mark_extracted

    settings = get_settings()
    max_terms = settings.glossary_max_terms
    deps = GlossaryDeps(source_lang=source_lang, target_lang=target_lang)
    content_hash = compute_hash(source_strings)

    log.info(
        "extract_glossary_start",
        source_lang=source_lang,
        target_lang=target_lang,
        string_count=len(source_strings),
        content_hash=content_hash,
        model=settings.glossary_model or settings.ai_model,
    )

    # Phase 1 — identify candidate terms
    extract_prompt = (
        f"You are a terminology specialist for {source_lang} to {target_lang} translation.\n"
        f"From the source strings below, identify up to {max_terms} domain-specific or technical "
        f"terms (nouns or noun phrases) that would benefit from authoritative terminology research.\n"
        f"Rules:\n"
        f"- Include: specialised vocabulary, technical concepts, domain jargon\n"
        f"- Exclude: people's names, place names, organisation names, common UI labels (OK, Cancel, File, Edit)\n\n"
        + "\n".join(f"- {s}" for s in source_strings[:200])
    )
    term_result = await _term_extractor.run(extract_prompt, deps=deps)
    terms = term_result.output.terms[:max_terms]
    log.info("glossary_candidate_terms", terms=terms, count=len(terms))

    if not terms:
        return []

    # Phase 2 — look up each term in the local terminology index
    lookup_prompt = (
        f"You are researching authoritative {source_lang}→{target_lang} terminology.\n"
        f"For each of these terms, call lookup_terminology to search the local index "
        f"for authoritative translations, then return glossary suggestions with the source term, "
        f"its authoritative {target_lang} translation, and an optional brief usage comment.\n"
        f"Only include entries where you found a clear authoritative translation.\n"
        f"Terms to look up: {', '.join(terms)}"
    )
    try:
        result = await _glossary_agent.run(lookup_prompt, deps=deps)
        suggestions = result.output
    except UnexpectedModelBehavior as e:
        log.warning(
            "glossary_agent_failed",
            error=str(e),
            hint="model could not produce structured output — set GLOSSARY_MODEL to a stronger model",
        )
        return []

    if not suggestions:
        log.info(
            "glossary_no_suggestions",
            hint="if you expected results, run: uv run python -m glossary.cli import-terminology --preset termium/oqlf",
        )

    log.info(
        "glossary_suggestions",
        count=len(suggestions),
        entries=[(s.source, s.target) for s in suggestions],
    )

    mark_extracted(content_hash)
    log.info("extract_glossary_complete", content_hash=content_hash)
    return suggestions
