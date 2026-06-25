"""
Generic CSV importer and local lookup for the terminology index.

Import once via the CLI (`glossary import-terminology`); all subsequent lookups
are fast local SQLite queries — no network call, no LLM call at lookup time.
"""
import csv
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import structlog

from config import get_settings

log = structlog.get_logger()


@dataclass
class TermHit:
    source_term: str
    target_term: str
    subject: str | None
    source: str  # dataset label, e.g. "termium" or "oqlf"


def normalize(term: str) -> str:
    """Case-fold and strip for uniform matching across sources."""
    return term.casefold().strip()


def _default_db_path() -> Path:
    path = get_settings().state_db_path
    return path if path is not None else Path("state.db")


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS terminology (
            source            TEXT NOT NULL,
            source_lang       TEXT NOT NULL,
            target_lang       TEXT NOT NULL,
            source_term       TEXT NOT NULL,
            target_term       TEXT NOT NULL,
            subject           TEXT,
            normalized_source TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_terminology_lookup
        ON terminology (source_lang, target_lang, normalized_source)
    """)
    conn.commit()
    return conn


def import_rows(
    parsed_rows: list[dict],
    column_mapping: dict[str, str],
    source_label: str,
    source_lang: str,
    target_lang: str,
    db_path: Path | None = None,
) -> int:
    """
    Insert pre-parsed rows (list of dicts, e.g. from csv.DictReader) into the
    terminology table. Rows with an empty source or target term are skipped.
    Returns the number of rows inserted.

    Callers that need pre-processing before insert (e.g. expanding semicolon-
    separated OQLF variants) should transform their rows first, then call this.
    """
    src_col  = column_mapping["source_term"]
    tgt_col  = column_mapping["target_term"]
    subj_col = column_mapping.get("subject")

    rows: list[tuple] = []
    for row in parsed_rows:
        source_term = (row.get(src_col) or "").strip()
        target_term = (row.get(tgt_col) or "").strip()
        if not source_term or not target_term:
            continue
        subject = (row.get(subj_col) or "").strip() if subj_col else None
        rows.append((
            source_label,
            source_lang.upper(),
            target_lang.upper(),
            source_term,
            target_term,
            subject or None,
            normalize(source_term),
        ))

    db = db_path or _default_db_path()
    with _connect(db) as conn:
        conn.executemany(
            "INSERT INTO terminology "
            "(source, source_lang, target_lang, source_term, target_term, subject, normalized_source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()

    return len(rows)


def import_csv(
    csv_path: Path,
    column_mapping: dict[str, str],
    source_label: str,
    source_lang: str,
    target_lang: str,
    delimiter: str = ",",
    encoding: str = "utf-8-sig",  # utf-8-sig strips BOM transparently (needed for Termium CSVs)
    db_path: Path | None = None,
) -> int:
    """
    Import a CSV file into the terminology table.

    column_mapping keys:
      source_term (required) — CSV column for the source-language term
      target_term (required) — CSV column for the target-language term
      subject     (optional) — CSV column for subject/domain label

    Rows with an empty source or target term are skipped.
    Returns the number of rows inserted.
    """
    with open(csv_path, encoding=encoding, newline="") as f:
        parsed_rows = list(csv.DictReader(f, delimiter=delimiter))
    count = import_rows(parsed_rows, column_mapping, source_label, source_lang, target_lang, db_path)
    log.info("terminology_imported", source=source_label, path=str(csv_path), count=count)
    return count


def lookup_term(
    term: str,
    source_lang: str,
    target_lang: str,
    db_path: Path | None = None,
) -> list[TermHit]:
    """Look up a term in the local index. Returns all hits across all sources."""
    db = db_path or _default_db_path()
    norm = normalize(term)
    with _connect(db) as conn:
        rows = conn.execute(
            "SELECT source_term, target_term, subject, source "
            "FROM terminology "
            "WHERE source_lang = ? AND target_lang = ? AND normalized_source = ? "
            "ORDER BY source",
            (source_lang.upper(), target_lang.upper(), norm),
        ).fetchall()
    return [TermHit(source_term=r[0], target_term=r[1], subject=r[2], source=r[3]) for r in rows]
