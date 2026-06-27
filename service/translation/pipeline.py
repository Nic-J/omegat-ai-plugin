"""Translation orchestration — the domain flow behind the /translate endpoint.

Kept out of the FastAPI route so the sequence (summary lookup → style-rule
resolution → TM cache read → translate → TM cache write) is unit-testable without
the HTTP stack, and so a future batch endpoint can reuse it segment-by-segment.
The route handler stays transport-only: receive request → call translate_segment
→ shape response.
"""
from dataclasses import dataclass, field

import structlog

from config import Settings
from models import TranslateRequest
from summary import state as summary_state
from tm import state as tm_state
from translation import agent as translator
from translation import qa
from translation.prompt import resolve_style_rules

log = structlog.get_logger()


@dataclass
class TranslateResult:
    """Outcome of translating one segment. The route maps it onto TranslateResponse."""
    translated_text: str
    from_cache: bool
    qa_findings: list[str] = field(default_factory=list)


async def translate_segment(request: TranslateRequest, settings: Settings) -> TranslateResult:
    file_summary: str | None = None
    if request.file_path:
        file_summary = summary_state.get_summary(
            request.file_path, project_id=request.project_id or "", db_path=settings.state_db_path
        )

    project_id = request.project_id or ""
    style_rules = resolve_style_rules(request)
    style_rules_source = (
        "request" if request.style_rules is not None
        else "global" if style_rules
        else "none"
    )
    log.info("translate_request",
             source_lang=request.source_lang,
             target_lang=request.target_lang,
             glossary_count=len(request.glossary or []),
             fuzzy_count=len(request.fuzzy_matches or []),
             style_rules_source=style_rules_source,
             style_rules_count=len(style_rules),
             summary_injected=file_summary is not None)

    # TM cache read — one flag (tm_cache_enabled) gates both read and write.
    cache_key: str | None = None
    if settings.tm_cache_enabled:
        cache_key = tm_state.compute_key(
            request.source_text, request.source_lang, request.target_lang,
            request.glossary, style_rules, settings.ai_model,
        )
        cached = tm_state.get(cache_key, project_id=project_id, db_path=settings.state_db_path)
        if cached is not None:
            log.info("tm_cache_hit", file_path=request.file_path, cache_key=cache_key)
            return TranslateResult(translated_text=cached, from_cache=True)
        log.info("tm_cache_miss", file_path=request.file_path, cache_key=cache_key)

    translated_text = await translator.translate(request, file_summary=file_summary)

    # QA self-critique — verify glossary/style adherence on the fresh translation,
    # before caching, so the corrected text is what gets stored. Skip when there's
    # nothing to check (no glossary terms and no style rules).
    qa_findings: list[str] = []
    if settings.qa_enabled and (request.glossary or style_rules):
        review = await qa.review(request, translated_text, style_rules)
        if review.findings:
            log.info("qa_corrections", file_path=request.file_path,
                     original=translated_text, corrected=review.corrected_text,
                     findings=review.findings)
            translated_text = review.corrected_text
            qa_findings = review.findings
        else:
            log.info("qa_clean", file_path=request.file_path)

    if settings.tm_cache_enabled:
        tm_state.save(
            cache_key, request.source_text, request.source_lang, request.target_lang,
            translated_text, settings.ai_model, project_id=project_id, db_path=settings.state_db_path,
        )

    return TranslateResult(translated_text=translated_text, from_cache=False, qa_findings=qa_findings)
