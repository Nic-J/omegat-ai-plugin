"""SQLite-backed exact-match translation memory cache.

Mirrors the OmegaT TM mental model: identical (source_text, source_lang,
target_lang, glossary, resolved style_rules, model) inputs return the same
cached translation, even if surrounding context (fuzzy matches, file summary,
context segments) differs. Scoped per OmegaT project via project_id, like
glossary_state / file_summaries; project_id "" is used when absent.

No eviction: changing the glossary, style rules, or model produces a new
cache_key and orphans the old row. Acceptable for a single-user tool.

All public functions accept an optional db_path parameter. When omitted,
the path from settings is used. Pass db_path explicitly in tests to avoid
touching global config.
"""
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from config import get_settings
from models import GlossaryEntry


def _default_db_path() -> Path:
    path = get_settings().state_db_path
    return path if path is not None else Path("state.db")


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS translation_memory (
            project_id      TEXT NOT NULL DEFAULT '',
            cache_key       TEXT NOT NULL,
            source_text     TEXT NOT NULL,
            source_lang     TEXT NOT NULL,
            target_lang     TEXT NOT NULL,
            translated_text TEXT NOT NULL,
            model           TEXT NOT NULL,
            created_at      TEXT NOT NULL,
            PRIMARY KEY (project_id, cache_key)
        )
    """)
    conn.commit()
    return conn


def compute_key(
    source_text: str,
    source_lang: str,
    target_lang: str,
    glossary: list[GlossaryEntry],
    style_rules: list[str],
    model: str,
) -> str:
    """SHA-256 (first 16 hex chars) of the cache-significant inputs.

    Glossary entries are sorted so term order doesn't affect the key.
    """
    glossary_part = "\n".join(
        sorted(f"{g.source}|{g.target or ''}|{g.comment or ''}" for g in glossary)
    )
    style_part = "\n".join(style_rules)
    content = "\x1f".join([source_text, source_lang, target_lang, glossary_part, style_part, model])
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def get(cache_key: str, project_id: str = "", db_path: Path | None = None) -> str | None:
    with _connect(db_path or _default_db_path()) as conn:
        row = conn.execute(
            "SELECT translated_text FROM translation_memory WHERE project_id = ? AND cache_key = ?",
            (project_id, cache_key),
        ).fetchone()
        return row[0] if row else None


def save(
    cache_key: str,
    source_text: str,
    source_lang: str,
    target_lang: str,
    translated_text: str,
    model: str,
    project_id: str = "",
    db_path: Path | None = None,
) -> None:
    created_at = datetime.now(timezone.utc).isoformat()
    with _connect(db_path or _default_db_path()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO translation_memory "
            "(project_id, cache_key, source_text, source_lang, target_lang, translated_text, model, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (project_id, cache_key, source_text, source_lang, target_lang, translated_text, model, created_at),
        )
        conn.commit()
