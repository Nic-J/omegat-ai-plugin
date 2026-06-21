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

    # Model used for glossary web research (Termium / OQLF lookups).
    # Falls back to ai_model if empty. Web research benefits from a stronger
    # model — set this to a cloud model while keeping ai_model on local Ollama.
    glossary_model: str = ""

    # ── Ollama ────────────────────────────────────────────────────────────────
    # Base URL for the local Ollama instance. Used both for translation and
    # for the glossary rater CLI.
    ollama_base_url: str = "http://localhost:11434"

    # ── File paths ────────────────────────────────────────────────────────────
    # Path to a plain-text file of style rules injected into the translation
    # prompt (one rule per line). Unset by default (opt-in) — style rules are
    # simply not injected until you set STYLE_RULES_PATH in .env to an absolute path.
    style_rules_path: Path | None = None

    # Shared SQLite database for all plugin state (glossary + file summaries).
    # Defaults to a platform-appropriate user data dir so the service works
    # correctly regardless of the directory it's launched from.
    state_db_path: Path | None = _DEFAULT_STATE_DB_PATH

    # ── Glossary agent tuning ─────────────────────────────────────────────────
    # Maximum number of candidate terms sent to Termium / OQLF for lookup.
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
