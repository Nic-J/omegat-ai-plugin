"""SQLite-backed state for glossary extraction.

State is keyed by (project_id, content_hash) — a content hash of the source
strings so that changes to the file trigger re-extraction automatically, scoped
per OmegaT project so two projects never share or overwrite each other's state.
project_id is an opaque string supplied by the caller; "" is used when absent
(e.g. callers that don't send one share one global bucket).

All public functions accept an optional db_path parameter. When omitted,
the path from settings is used. Pass db_path explicitly in tests to avoid
touching global config.
"""
import hashlib
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
        CREATE TABLE IF NOT EXISTS glossary_state (
            project_id   TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL,
            extracted_at TEXT NOT NULL,
            file_path    TEXT,
            status       TEXT NOT NULL DEFAULT 'extracted',
            PRIMARY KEY (project_id, content_hash)
        )
    """)
    conn.commit()
    return conn


def compute_hash(source_strings: list[str]) -> str:
    """SHA-256 (first 16 hex chars) of sorted source strings."""
    content = "\n".join(sorted(source_strings))
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def is_extracted(content_hash: str, project_id: str = "", db_path: Path | None = None) -> bool:
    """Return True only when extraction has completed (status='extracted').

    Deferred rows (user declined) are excluded so the popup reappears next session.
    """
    with _connect(db_path or _default_db_path()) as conn:
        row = conn.execute(
            "SELECT 1 FROM glossary_state WHERE project_id = ? AND content_hash = ? AND status = 'extracted'",
            (project_id, content_hash),
        ).fetchone()
        return row is not None


def mark_extracted(
    content_hash: str,
    project_id: str = "",
    file_path: str | None = None,
    db_path: Path | None = None,
) -> None:
    extracted_at = datetime.now(timezone.utc).isoformat()
    with _connect(db_path or _default_db_path()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO glossary_state (project_id, content_hash, extracted_at, file_path, status) "
            "VALUES (?, ?, ?, ?, 'extracted')",
            (project_id, content_hash, extracted_at, file_path),
        )
        conn.commit()


def mark_deferred(
    content_hash: str,
    project_id: str = "",
    file_path: str | None = None,
    db_path: Path | None = None,
) -> None:
    """Record that the user declined extraction. Popup reappears next session."""
    deferred_at = datetime.now(timezone.utc).isoformat()
    with _connect(db_path or _default_db_path()) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO glossary_state (project_id, content_hash, extracted_at, file_path, status) "
            "VALUES (?, ?, ?, ?, 'deferred')",
            (project_id, content_hash, deferred_at, file_path),
        )
        conn.commit()
