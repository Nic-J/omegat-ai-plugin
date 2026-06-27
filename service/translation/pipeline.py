"""Translation orchestration — the domain flow behind the /translate endpoint.

Kept out of the FastAPI route so the sequence (summary lookup → style-rule
resolution → TM cache read → translate → TM cache write) is unit-testable without
the HTTP stack, and so a future batch endpoint can reuse it segment-by-segment.
The route handler stays transport-only: receive request → call translate_segment
→ shape response.
"""
from dataclasses import dataclass

import structlog

from config import Settings
from models import TranslateRequest
from summary import state as summary_state
from tm import state as tm_state
from translation import agent as translator
from translation.prompt import resolve_style_rules

log = structlog.get_logger()


@dataclass
class TranslateResult:
    """Outcome of translating one segment. Grows as the pipeline gains steps
    (e.g. QA findings in OMP-023); the route maps it onto TranslateResponse."""
    translated_text: str
    from_cache: bool


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

    if settings.tm_cache_enabled:
        tm_state.save(
            cache_key, request.source_text, request.source_lang, request.target_lang,
            translated_text, settings.ai_model, project_id=project_id, db_path=settings.state_db_path,
        )

    return TranslateResult(translated_text=translated_text, from_cache=False)
