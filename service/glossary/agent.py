"""
Glossary extraction agent.

Two-phase approach:
1. LLM identifies candidate terms worth researching from the source strings.
2. For each candidate, the agent fetches each configured terminology source's
   page and extracts authoritative source->target pairs from the stripped text.
"""
import re

import httpx2 as httpx
import structlog
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import UnexpectedModelBehavior

from config import get_settings
from glossary.sources import TerminologySource, build_url, load_terminology_sources
from models import GlossarySuggestion

log = structlog.get_logger()


class _CandidateTerms(BaseModel):
    terms: list[str]


def _strip_html(html: str) -> str:
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


def _make_fetch_tool(source: TerminologySource, tool_name: str):
    """Build a fetch tool closed over one terminology source. A factory (rather than
    a loop body) so each tool's `source`/`tool_name` are bound per-call, not shared."""

    async def fetch(ctx: RunContext[GlossaryDeps], term: str) -> str:
        url = build_url(source, term, ctx.deps.source_lang)
        log.info(tool_name, term=term, url=url)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, follow_redirects=True)
            log.info(f"{tool_name}_done", term=term, status=resp.status_code, chars=len(resp.text))
            return _strip_html(resp.text) if resp.status_code == 200 else f"HTTP {resp.status_code}"
        except Exception as e:
            log.warning(f"{tool_name}_error", term=term, error=str(e))
            return f"Error: {e}"

    return fetch


def _register_terminology_tools(agent: Agent) -> list[str]:
    """Register one fetch tool per enabled terminology source. Returns the tool
    names so Phase 2's prompt can tell the LLM which tools exist."""
    tool_names = []
    for source in load_terminology_sources():
        tool_name = f"fetch_{source.name}"
        fetch = _make_fetch_tool(source, tool_name)
        agent.tool(
            name=tool_name,
            description=f"Fetch the {source.description or source.name} page for a term and return stripped text.",
        )(fetch)
        tool_names.append(tool_name)
    return tool_names


_TOOL_NAMES: list[str] = _register_terminology_tools(_glossary_agent)


async def extract_glossary(
    source_strings: list[str],
    source_lang: str,
    target_lang: str,
) -> list[GlossarySuggestion]:
    """
    Given source strings from an OmegaT file, identify candidate terms and
    look them up via the configured terminology sources to produce authoritative
    glossary suggestions.
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

    # Phase 2 — look up each term via the configured terminology sources
    lookup_prompt = (
        f"You are researching authoritative {source_lang}→{target_lang} terminology.\n"
        f"For each of these terms, call {', '.join(_TOOL_NAMES)} to look them up, "
        f"then return a list of glossary suggestions with the source term, its authoritative "
        f"{target_lang} translation, an optional brief usage comment, and the source_url "
        f"(the database URL you found it in).\n"
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

    log.info(
        "glossary_suggestions",
        count=len(suggestions),
        entries=[(s.source, s.target) for s in suggestions],
    )

    mark_extracted(content_hash)
    log.info("extract_glossary_complete", content_hash=content_hash)
    return suggestions
