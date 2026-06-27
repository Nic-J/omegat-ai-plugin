from functools import lru_cache
from pathlib import Path

from platformdirs import user_data_path
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_STATE_DB_PATH = user_data_path("omegat-ai-service") / "state.db"


class Settings(BaseSettings):
    # ── API keys ──────────────────────────────────────────────────────────────
    # Required when using an Anthropic model (e.g. "anthropic:claude-haiku-4-5-20251001").
    # Leave empty when using Ollama only.
    anthropic_api_key: str = ""

    # ── AI model selection ────────────────────────────────────────────────────
    # Full PydanticAI model string used for translation.
    # Examples: "ollama:mistral-nemo", "anthropic:claude-haiku-4-5-20251001",
    #           "google-gla:gemini-2.0-flash"
    ai_model: str = "ollama:mistral-nemo"

    # Model used for glossary web research (terminology source lookups).
    # Falls back to ai_model if empty. Web research benefits from a stronger
    # model — set this to a cloud model while keeping ai_model on local Ollama.
    glossary_model: str = ""

    # ── Ollama ────────────────────────────────────────────────────────────────
    # Base URL for the local Ollama instance. Used both for translation and
    # for the glossary rater CLI.
    ollama_base_url: str = "http://localhost:11434"

    # ── File paths ────────────────────────────────────────────────────────────
    # Path to a GLOBAL plain-text file of style rules injected into the translation
    # prompt (one rule per line; see ai_style_rules.example.txt). Unset by default
    # (opt-in) — not injected until you set STYLE_RULES_PATH in .env to an absolute
    # path. A per-project file named exactly "ai_style_rules.txt" in an OmegaT
    # project root overrides this for that project (handled plugin-side).
    style_rules_path: Path | None = None

    # Shared SQLite database for all plugin state (glossary + file summaries).
    # Defaults to a platform-appropriate user data dir so the service works
    # correctly regardless of the directory it's launched from.
    state_db_path: Path | None = _DEFAULT_STATE_DB_PATH

    # ── Feature toggles ───────────────────────────────────────────────────────
    # Server-side translation memory cache. One flag gates both read and write:
    # disabling only one is incoherent (read-only serves stale entries forever;
    # write-only never produces a hit). On by default — turn off to force a fresh
    # LLM call for every segment.
    tm_cache_enabled: bool = True

    # QA self-critique pass: a second LLM call that verifies each new translation
    # against the glossary + style rules and auto-corrects violations. Off by
    # default — it doubles the per-segment cost on cache misses. Skipped when a
    # segment has neither glossary terms nor style rules (nothing to check).
    qa_enabled: bool = False

    # ── QA model selection ────────────────────────────────────────────────────
    # Model used for the QA self-critique pass. Falls back to ai_model if empty,
    # mirroring glossary_model — set a stronger model here while keeping ai_model
    # on local Ollama.
    qa_model: str = ""

    # ── Glossary agent tuning ─────────────────────────────────────────────────
    # Maximum number of candidate terms sent to terminology sources for lookup.
    # Increase for broader coverage; decrease to reduce API calls and latency.
    glossary_max_terms: int = 20

    # Maximum characters of fetched page text passed to the LLM per tool call.
    # Higher values give more context but cost more tokens.
    glossary_max_page_chars: int = 3000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # silently ignore unknown env vars
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
