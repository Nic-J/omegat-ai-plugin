from functools import lru_cache
from pathlib import Path

import jinja2

from config import get_settings
from models import TranslateRequest

_TEMPLATE_DIR = Path(__file__).parent


@lru_cache
def _get_template() -> jinja2.Template:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATE_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env.get_template("prompt.j2")


@lru_cache
def _load_style_rules() -> list[str]:
    """Load style rules from the configured file, one rule per line."""
    path = get_settings().style_rules_path
    if not path or not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def build_prompt(request: TranslateRequest, file_summary: str | None = None) -> str:
    return _get_template().render(
        source_lang=request.source_lang,
        target_lang=request.target_lang,
        source_text=request.source_text,
        glossary=request.glossary,
        fuzzy_matches=request.fuzzy_matches,
        style_rules=_load_style_rules(),
        context_before=request.context_before,
        context_after=request.context_after,
        file_summary=file_summary,
    ).strip()
