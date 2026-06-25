"""Tests for the generic terminology CSV importer and local lookup."""
import csv
from pathlib import Path

import pytest

from glossary.terminology import TermHit, import_csv, lookup_term, normalize


# ── normalize ─────────────────────────────────────────────────────────────────

def test_normalize_casefolds():
    assert normalize("Building Model") == "building model"

def test_normalize_strips_whitespace():
    assert normalize("  term  ") == "term"

def test_normalize_unicode_casefold():
    assert normalize("STRASSE") == "strasse"


# ── fixtures ──────────────────────────────────────────────────────────────────

SAMPLE_ROWS = [
    {"EN_TERM": "building model",  "FR_TERM": "immeuble type", "DOMAIN": "Construction"},
    {"EN_TERM": "cover system",    "FR_TERM": "couverture",     "DOMAIN": "Construction"},
    {"EN_TERM": "software",        "FR_TERM": "logiciel",       "DOMAIN": "IT"},
    {"EN_TERM": "software",        "FR_TERM": "progiciel",      "DOMAIN": "IT"},  # second hit, same source term
    {"EN_TERM": "",                "FR_TERM": "vide",           "DOMAIN": ""},    # skipped: empty source
    {"EN_TERM": "orphan",          "FR_TERM": "",               "DOMAIN": ""},    # skipped: empty target
]


@pytest.fixture
def sample_csv(tmp_path: Path) -> Path:
    path = tmp_path / "terms.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["EN_TERM", "FR_TERM", "DOMAIN"])
        writer.writeheader()
        writer.writerows(SAMPLE_ROWS)
    return path


@pytest.fixture
def seeded_db(tmp_path: Path, sample_csv: Path) -> Path:
    db = tmp_path / "test.db"
    import_csv(
        sample_csv,
        column_mapping={"source_term": "EN_TERM", "target_term": "FR_TERM", "subject": "DOMAIN"},
        source_label="test",
        source_lang="EN",
        target_lang="FR",
        db_path=db,
    )
    return db


# ── import_csv ────────────────────────────────────────────────────────────────

def test_import_returns_count(tmp_path, sample_csv):
    db = tmp_path / "test.db"
    count = import_csv(
        sample_csv,
        column_mapping={"source_term": "EN_TERM", "target_term": "FR_TERM"},
        source_label="test",
        source_lang="EN",
        target_lang="FR",
        db_path=db,
    )
    assert count == 4  # 2 rows skipped (empty source or target)


def test_import_skips_empty_source(tmp_path, sample_csv):
    db = tmp_path / "test.db"
    import_csv(
        sample_csv,
        column_mapping={"source_term": "EN_TERM", "target_term": "FR_TERM"},
        source_label="test", source_lang="EN", target_lang="FR", db_path=db,
    )
    hits = lookup_term("", "EN", "FR", db_path=db)
    assert hits == []


def test_import_without_subject_col(tmp_path, sample_csv):
    db = tmp_path / "test.db"
    import_csv(
        sample_csv,
        column_mapping={"source_term": "EN_TERM", "target_term": "FR_TERM"},
        source_label="test", source_lang="EN", target_lang="FR", db_path=db,
    )
    hits = lookup_term("software", "EN", "FR", db_path=db)
    assert all(h.subject is None for h in hits)


def test_import_accumulates_across_calls(tmp_path, sample_csv):
    db = tmp_path / "test.db"
    mapping = {"source_term": "EN_TERM", "target_term": "FR_TERM"}
    import_csv(sample_csv, column_mapping=mapping, source_label="src_a",
               source_lang="EN", target_lang="FR", db_path=db)
    import_csv(sample_csv, column_mapping=mapping, source_label="src_b",
               source_lang="EN", target_lang="FR", db_path=db)
    hits = lookup_term("software", "EN", "FR", db_path=db)
    sources = {h.source for h in hits}
    assert sources == {"src_a", "src_b"}


# ── lookup_term ───────────────────────────────────────────────────────────────

def test_lookup_exact_hit(seeded_db):
    hits = lookup_term("building model", "EN", "FR", db_path=seeded_db)
    assert len(hits) == 1
    assert hits[0].target_term == "immeuble type"
    assert hits[0].subject == "Construction"
    assert hits[0].source == "test"


def test_lookup_case_insensitive(seeded_db):
    hits = lookup_term("Building Model", "EN", "FR", db_path=seeded_db)
    assert len(hits) == 1
    assert hits[0].target_term == "immeuble type"


def test_lookup_miss(seeded_db):
    assert lookup_term("nonexistent term", "EN", "FR", db_path=seeded_db) == []


def test_lookup_multiple_hits_same_term(seeded_db):
    hits = lookup_term("software", "EN", "FR", db_path=seeded_db)
    assert len(hits) == 2
    assert {h.target_term for h in hits} == {"logiciel", "progiciel"}


def test_lookup_wrong_language_direction(seeded_db):
    assert lookup_term("building model", "FR", "EN", db_path=seeded_db) == []


def test_lookup_empty_db(tmp_path):
    db = tmp_path / "empty.db"
    assert lookup_term("software", "EN", "FR", db_path=db) == []


def test_lookup_result_type(seeded_db):
    hits = lookup_term("cover system", "EN", "FR", db_path=seeded_db)
    assert isinstance(hits[0], TermHit)
