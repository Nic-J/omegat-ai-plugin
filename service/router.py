import structlog
from fastapi import APIRouter, Depends

from config import Settings, get_settings
from glossary import agent as glossary_agent
from glossary import state as glossary_state
from summary import agent as summary_agent
from summary import state as summary_state
from translation.pipeline import translate_segment
from models import (
    FileSummaryRequest,
    FileSummaryResponse,
    GlossaryDeferResponse,
    GlossaryPrepRequest,
    GlossaryPrepResponse,
    GlossaryStatusResponse,
    TranslateRequest,
    TranslateResponse,
)

log = structlog.get_logger()

router = APIRouter()


@router.post("/translate", response_model=TranslateResponse)
async def translate(
    request: TranslateRequest,
    settings: Settings = Depends(get_settings),
) -> TranslateResponse:
    result = await translate_segment(request, settings)
    response = TranslateResponse(
        translated_text=result.translated_text,
        source_lang=request.source_lang,
        target_lang=request.target_lang,
        glossary_applied=bool(request.glossary),
        fuzzy_match_used=bool(request.fuzzy_matches),
        from_cache=result.from_cache,
    )
    log.info("translate_response", file_path=request.file_path, **response.model_dump())
    return response


@router.post("/file-summary/generate", response_model=FileSummaryResponse)
async def generate_file_summary(
    request: FileSummaryRequest,
    settings: Settings = Depends(get_settings),
) -> FileSummaryResponse:
    existing = summary_state.get_summary(
        request.file_path, project_id=request.project_id or "", db_path=settings.state_db_path
    )
    if existing:
        log.info("file_summary_cache_hit", file_path=request.file_path)
        return FileSummaryResponse(summary=existing, from_cache=True)

    summary = await summary_agent.generate_summary(
        request.source_strings, request.source_lang, request.target_lang
    )
    summary_state.save_summary(
        request.file_path, summary,
        request.source_lang, request.target_lang,
        settings.ai_model,
        project_id=request.project_id or "",
        db_path=settings.state_db_path,
    )
    log.info("file_summary_generated", file_path=request.file_path)
    return FileSummaryResponse(summary=summary, from_cache=False)


@router.post("/prepare-glossary", response_model=GlossaryPrepResponse)
async def prepare_glossary(
    request: GlossaryPrepRequest,
    settings: Settings = Depends(get_settings),
) -> GlossaryPrepResponse:
    log.info("prepare_glossary_request", string_count=len(request.source_strings),
             source_lang=request.source_lang, target_lang=request.target_lang,
             file_path=request.file_path)
    suggestions = await glossary_agent.extract_glossary(
        source_strings=request.source_strings,
        source_lang=request.source_lang,
        target_lang=request.target_lang,
    )
    if request.existing_terms:
        existing_lower = {t.strip().lower() for t in request.existing_terms}
        before = len(suggestions)
        suggestions = [s for s in suggestions if s.source.strip().lower() not in existing_lower]
        log.info("prepare_glossary_dedup", before=before, after=len(suggestions),
                 skipped=before - len(suggestions))

    content_hash = glossary_state.compute_hash(request.source_strings)
    glossary_state.mark_extracted(
        content_hash,
        project_id=request.project_id or "",
        file_path=request.file_path,
        db_path=settings.state_db_path,
    )

    log.info("prepare_glossary_response", suggestion_count=len(suggestions))
    return GlossaryPrepResponse(suggestions=suggestions)


@router.post("/glossary/defer", response_model=GlossaryDeferResponse)
async def glossary_defer(
    request: GlossaryPrepRequest,
    settings: Settings = Depends(get_settings),
) -> GlossaryDeferResponse:
    content_hash = glossary_state.compute_hash(request.source_strings)
    glossary_state.mark_deferred(
        content_hash,
        project_id=request.project_id or "",
        file_path=request.file_path,
        db_path=settings.state_db_path,
    )
    log.info("glossary_defer", content_hash=content_hash, file_path=request.file_path)
    return GlossaryDeferResponse()


@router.post("/glossary/status", response_model=GlossaryStatusResponse)
async def glossary_status(
    request: GlossaryPrepRequest,
    settings: Settings = Depends(get_settings),
) -> GlossaryStatusResponse:
    content_hash = glossary_state.compute_hash(request.source_strings)
    needs_extraction = not glossary_state.is_extracted(
        content_hash, project_id=request.project_id or "", db_path=settings.state_db_path
    )
    log.info("glossary_status", content_hash=content_hash, needs_extraction=needs_extraction)
    return GlossaryStatusResponse(needs_extraction=needs_extraction, content_hash=content_hash)
