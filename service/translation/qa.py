"""QA self-critique pass.

A second LLM pass that verifies a translation against the approved glossary and
the resolved style rules, returning a minimally-corrected translation plus a list
of the fixes it made. Runs only when QA_ENABLED is set, on a cache miss, before
the result is cached — so the corrected text is what gets stored and the QA cost
is paid once per genuinely new segment. Uses QA_MODEL (falls back to AI_MODEL),
mirroring the glossary agent's model selection.
"""
from functools import lru_cache
from pathlib import Path

import jinja2
import structlog
from pydantic import BaseModel
from pydantic_ai import Agent

from config import get_settings
from model_utils import resolve_model
from models import TranslateRequest

log = structlog.get_logger()

_TEMPLATE_DIR = Path(__file__).parent


class QAReview(BaseModel):
    corrected_text: str
    findings: list[str]  # one short sentence per fix; empty when nothing needed changing


@lru_cache
def _get_template() -> jinja2.Template:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATE_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env.get_template("qa_prompt.j2")


def _build_agent() -> Agent:
    settings = get_settings()
    model_str = settings.qa_model or settings.ai_model
    return Agent(resolve_model(model_str), output_type=QAReview)


_agent = _build_agent()


def _render_prompt(request: TranslateRequest, translation: str, style_rules: list[str]) -> str:
    """Separate from review() so the prompt can be asserted without an LLM call."""
    return _get_template().render(
        source_lang=request.source_lang,
        target_lang=request.target_lang,
        source_text=request.source_text,
        translation=translation,
        glossary=request.glossary,
        style_rules=style_rules,
    ).strip()


async def review(request: TranslateRequest, translation: str, style_rules: list[str]) -> QAReview:
    """Verify `translation` against the glossary + style rules; return corrected text and findings."""
    prompt = _render_prompt(request, translation, style_rules)
    settings = get_settings()
    log.info("qa_review", model=settings.qa_model or settings.ai_model,
             glossary_count=len(request.glossary or []), style_rules_count=len(style_rules))
    result = await _agent.run(prompt)
    return result.output
