import structlog
from pydantic_ai import Agent

from config import get_settings
from model_utils import resolve_model
from models import TranslateRequest
from translation.prompt import build_prompt

log = structlog.get_logger()


def _build_agent() -> Agent:
    settings = get_settings()
    return Agent(resolve_model(settings.ai_model), output_type=str)


agent = _build_agent()


async def translate(request: TranslateRequest, file_summary: str | None = None) -> str:
    prompt = build_prompt(request, file_summary=file_summary)
    log.info("translate_request", model=get_settings().ai_model, **request.model_dump())
    result = await agent.run(prompt)
    return result.output.strip()
