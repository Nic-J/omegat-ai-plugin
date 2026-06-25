"""Tests for the terminology preset import configs (Termium, OQLF)."""
import csv
from pathlib import Path

import pytest

from glossary.presets import PRESETS, _oqlf_preprocess, import_preset
from glossary.terminology import lookup_term


# ── _oqlf_preprocess ─────────────────────────────────────────────────────────

class TestOqlfPreprocess:
    def test_strips_grammatical_annotation(self):
        rows = [{"Termes_francais": "logiciel (n. m.)", "Termes_anglais": "software", "Domaines": "IT"}]
        result = _oqlf_preprocess(rows)
        assert result[0]["Termes_francais"] == "logiciel"

    def test_expands_semicolon_separated_variants(self):
        rows = [{
            "Termes_francais": "gravure multisession (n. f.);gravure en multisession (n. f.)",
            "Termes_anglais": "multisession burning;multi-session burning",
            "Domaines": "informatique",
        }]
        result = _oqlf_preprocess(rows)
        assert len(result) == 2
        assert result[0] == {"Termes_anglais": "multisession burning", "Termes_francais": "gravure multisession", "Domaines": "informatique"}
        assert result[1] == {"Termes_anglais": "multi-session burning", "Termes_francais": "gravure en multisession", "Domaines": "informatique"}

    def test_skips_rows_with_empty_en_after_split(self):
        rows = [{"Termes_francais": "terme (n. m.)", "Termes_anglais": "", "Domaines": ""}]
        assert _oqlf_preprocess(rows) == []

    def test_skips_rows_with_empty_fr_after_strip(self):
        rows = [{"Termes_francais": "(n. m.)", "Termes_anglais": "term", "Domaines": ""}]
        assert _oqlf_preprocess(rows) == []

    def test_preserves_domain(self):
        rows = [{"Termes_francais": "couverture (n. f.)", "Termes_anglais": "cover system", "Domaines": "construction"}]
        result = _oqlf_preprocess(rows)
        assert result[0]["Domaines"] == "construction"


# ── import_preset ─────────────────────────────────────────────────────────────

@pytest.fixture
def termium_csv(tmp_path: Path) -> Path:
    path = tmp_path / "termium_sample.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["SUBJECT_EN", "TERM_EN", "TERME_FR"])
        writer.writeheader()
        writer.writerows([
            {"SUBJECT_EN": "Construction", "TERM_EN": "cover system", "TERME_FR": "couverture"},
            {"SUBJECT_EN": "Construction", "TERM_EN": "building model", "TERME_FR": "immeuble type"},
            {"SUBJECT_EN": "IT", "TERM_EN": "", "TERME_FR": "vide"},  # skipped
        ])
    return path


@pytest.fixture
def oqlf_csv(tmp_path: Path) -> Path:
    path = tmp_path / "oqlf_sample.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["Termes_francais", "Termes_anglais", "Domaines"])
        writer.writeheader()
        writer.writerows([
            {
                "Termes_francais": "logiciel (n. m.);progiciel (n. m.)",
                "Termes_anglais": "software;software package",
                "Domaines": "informatique",
            },
            {
                "Termes_francais": "couverture (n. f.)",
                "Termes_anglais": "cover system",
                "Domaines": "construction",
            },
        ])
    return path


class TestImportPreset:
    def test_termium_preset_imports_rows(self, tmp_path, termium_csv):
        db = tmp_path / "test.db"
        count = import_preset("termium", termium_csv, db_path=db)
        assert count == 2  # 1 row skipped (empty source)

    def test_termium_lookup_after_import(self, tmp_path, termium_csv):
        db = tmp_path / "test.db"
        import_preset("termium", termium_csv, db_path=db)
        hits = lookup_term("cover system", "EN", "FR", db_path=db)
        assert len(hits) == 1
        assert hits[0].target_term == "couverture"
        assert hits[0].source == "termium"
        assert hits[0].subject == "Construction"

    def test_oqlf_preset_expands_variants(self, tmp_path, oqlf_csv):
        db = tmp_path / "test.db"
        count = import_preset("oqlf", oqlf_csv, db_path=db)
        assert count == 3  # software + software package + cover system

    def test_oqlf_lookup_returns_all_variants(self, tmp_path, oqlf_csv):
        db = tmp_path / "test.db"
        import_preset("oqlf", oqlf_csv, db_path=db)
        hits = lookup_term("software", "EN", "FR", db_path=db)
        assert len(hits) == 1
        assert hits[0].target_term == "logiciel"

    def test_oqlf_lookup_variant_term(self, tmp_path, oqlf_csv):
        db = tmp_path / "test.db"
        import_preset("oqlf", oqlf_csv, db_path=db)
        hits = lookup_term("software package", "EN", "FR", db_path=db)
        assert len(hits) == 1
        assert hits[0].target_term == "progiciel"

    def test_unknown_preset_raises(self, tmp_path, termium_csv):
        with pytest.raises(ValueError, match="Unknown preset"):
            import_preset("nonexistent", termium_csv, db_path=tmp_path / "test.db")

    def test_presets_dict_has_expected_keys(self):
        assert "termium" in PRESETS
        assert "oqlf" in PRESETS
