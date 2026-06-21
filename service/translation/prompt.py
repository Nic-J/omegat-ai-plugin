from functools import lru_cache
from pathlib import Path

import jinja2
import structlog

from config import get_settings
from models import TranslateRequest

log = structlog.get_logger()

_TEMPLATE_DIR = Path(__file__).parent


@lru_cache
def _get_template() -> jinja2.Template:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATE_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env.get_template("prompt.j2")


def _parse_style_rules(content: str) -> list[str]:
    """One rule per line, '#' for comments — shared by the global file and per-request rules."""
    lines = content.splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


@lru_cache
def _load_style_rules() -> list[str]:
    """Load style rules from the configured global file, one rule per line."""
    path = get_settings().style_rules_path
    if not path or not path.exists():
        log.debug("style_rules_not_configured", path=str(path) if path else None)
        return []
    rules = _parse_style_rules(path.read_text(encoding="utf-8"))
    log.info("style_rules_loaded", path=str(path), rule_count=len(rules))
    return rules


def build_prompt(request: TranslateRequest, file_summary: str | None = None) -> str:
    # Request-level rules (project-local file, sent by the plugin) take priority over the global setting.
    if request.style_rules is not None:
        style_rules = _parse_style_rules(request.style_rules)
    else:
        style_rules = _load_style_rules()

    return _get_template().render(
        source_lang=request.source_lang,
        target_lang=request.target_lang,
        source_text=request.source_text,
        glossary=request.glossary,
        fuzzy_matches=request.fuzzy_matches,
        style_rules=style_rules,
        context_before=request.context_before,
        context_after=request.context_after,
        file_summary=file_summary,
    ).strip()
