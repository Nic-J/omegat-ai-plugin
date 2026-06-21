"""File summary generation agent."""
from functools import lru_cache
from pathlib import Path

import jinja2
import structlog
from pydantic_ai import Agent

from config import get_settings
from model_utils import resolve_model

log = structlog.get_logger()

_TEMPLATE_DIR = Path(__file__).parent
_MAX_SEGMENTS = 120


@lru_cache
def _get_template() -> jinja2.Template:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATE_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env.get_template("prompt.j2")


def _build_agent() -> Agent:
    settings = get_settings()
    return Agent(resolve_model(settings.ai_model), output_type=str)


_agent = _build_agent()


async def generate_summary(
    source_strings: list[str],
    source_lang: str,
    target_lang: str,
) -> str:
    segments = source_strings[:_MAX_SEGMENTS]
    prompt = _get_template().render(
        source_lang=source_lang,
        target_lang=target_lang,
        segments=segments,
    ).strip()
    log.info("generate_summary", source_lang=source_lang, target_lang=target_lang,
             segment_count=len(segments), model=get_settings().ai_model)
    result = await _agent.run(prompt)
    return result.output.strip()
