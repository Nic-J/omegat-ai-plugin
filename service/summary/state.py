"""SQLite-backed cache for file summaries.

Summaries are keyed by file_path. Files rarely change mid-project, so
content-hash invalidation is not needed — if a file changes materially,
delete its row and re-generate.

Uses the shared state DB (state_db_path in settings).
"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from config import get_settings


def _default_db_path() -> Path:
    path = get_settings().state_db_path
    return path if path is not None else Path("state.db")


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS file_summaries (
            file_path   TEXT PRIMARY KEY,
            summary     TEXT NOT NULL,
            source_lang TEXT NOT NULL,
            target_lang TEXT NOT NULL,
            model       TEXT NOT NULL,
            created_at  TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def get_summary(file_path: str, db_path: Path | None = None) -> str | None:
    with _connect(db_path or _default_db_path()) as conn:
        row = conn.execute(
            "SELECT summary FROM file_summaries WHERE file_path = ?",
            (file_path,),
        ).fetchone()
        return row[0] if row else None


def save_summary(
    file_path: str,
    summary: str,
    source_lang: str,
    target_lang: str,
    model: str,
    db_path: Path | None = None,
) -> None:
    created_at = datetime.now(timezone.utc).isoformat()
    with _connect(db_path or _default_db_path()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO file_summaries "
            "(file_path, summary, source_lang, target_lang, model, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (file_path, summary, source_lang, target_lang, model, created_at),
        )
        conn.commit()
