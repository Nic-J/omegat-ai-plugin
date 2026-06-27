from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import translation.qa as qa
from config import Settings, get_settings
from main import app
from models import GlossaryEntry, TranslateRequest
from translation.qa import QAReview

client = TestClient(app)


def _make_request(**kwargs) -> TranslateRequest:
    base = {"source_text": "Save", "source_lang": "EN", "target_lang": "FR-CA"}
    return TranslateRequest(**(base | kwargs))


class TestQAPrompt:
    """The QA prompt must carry the glossary terms and style rules it's meant to enforce."""

    def test_includes_glossary_term(self):
        request = _make_request(glossary=[GlossaryEntry(source="Save", target="Enregistrer")])
        prompt = qa._render_prompt(request, "Sauvegarder", style_rules=[])
        assert '"Save" → "Enregistrer"' in prompt
        assert "Sauvegarder" in prompt  # the candidate translation under review

    def test_includes_style_rule(self):
        prompt = qa._render_prompt(_make_request(), "Bonjour", style_rules=["Use formal register."])
        assert "Use formal register." in prompt

    def test_no_sections_when_nothing_to_check(self):
        prompt = qa._render_prompt(_make_request(), "Bonjour", style_rules=[])
        assert "Required terminology" not in prompt
        assert "Style rules the target text" not in prompt


class TestQAInTranslateFlow:
    def _enable_qa(self, tmp_path, **extra):
        settings = Settings(state_db_path=tmp_path / "state.db", qa_enabled=True, **extra)
        app.dependency_overrides[get_settings] = lambda: settings

    def test_qa_corrects_and_returns_findings(self, tmp_path):
        self._enable_qa(tmp_path)
        review = QAReview(corrected_text="Enregistrer", findings=["Used approved term 'Enregistrer' for 'Save'."])
        with patch.object(qa, "review", new=AsyncMock(return_value=review)):
            resp = client.post("/translate", json={
                "source_text": "Save", "source_lang": "EN", "target_lang": "FR-CA",
                "glossary": [{"source": "Save", "target": "Enregistrer"}],
            })
        data = resp.json()
        assert data["translated_text"] == "Enregistrer"  # corrected text replaces the MT output
        assert data["qa_findings"] == ["Used approved term 'Enregistrer' for 'Save'."]

    def test_qa_clean_leaves_translation_and_findings_empty(self, tmp_path):
        self._enable_qa(tmp_path)
        review = QAReview(corrected_text="(mocked ai translation)", findings=[])
        with patch.object(qa, "review", new=AsyncMock(return_value=review)):
            resp = client.post("/translate", json={
                "source_text": "Save", "source_lang": "EN", "target_lang": "FR-CA",
                "glossary": [{"source": "Save", "target": "Enregistrer"}],
            })
        data = resp.json()
        assert data["translated_text"] == "(mocked ai translation)"
        assert data["qa_findings"] == []

    def test_qa_disabled_skips_review(self, tmp_path):
        app.dependency_overrides[get_settings] = lambda: Settings(state_db_path=tmp_path / "state.db", qa_enabled=False)
        with patch.object(qa, "review", new=AsyncMock()) as mock_review:
            resp = client.post("/translate", json={
                "source_text": "Save", "source_lang": "EN", "target_lang": "FR-CA",
                "glossary": [{"source": "Save", "target": "Enregistrer"}],
            })
            mock_review.assert_not_called()
        assert resp.json()["translated_text"] == "(mocked ai translation)"
        assert resp.json()["qa_findings"] == []

    def test_qa_skipped_when_no_glossary_or_style(self, tmp_path):
        """Nothing to verify → don't spend a QA call (style rules stubbed empty by conftest)."""
        self._enable_qa(tmp_path)
        with patch.object(qa, "review", new=AsyncMock()) as mock_review:
            resp = client.post("/translate", json={
                "source_text": "Save", "source_lang": "EN", "target_lang": "FR-CA",
            })
            mock_review.assert_not_called()
        assert resp.json()["qa_findings"] == []

    def test_corrected_text_is_cached(self, tmp_path):
        """The QA'd output is what gets cached: a second identical request returns it from cache."""
        self._enable_qa(tmp_path)
        review = QAReview(corrected_text="Enregistrer", findings=["fix"])
        body = {
            "source_text": "Save", "source_lang": "EN", "target_lang": "FR-CA",
            "glossary": [{"source": "Save", "target": "Enregistrer"}],
        }
        with patch.object(qa, "review", new=AsyncMock(return_value=review)) as mock_review:
            first = client.post("/translate", json=body)
            second = client.post("/translate", json=body)

        assert first.json()["from_cache"] is False
        assert second.json()["from_cache"] is True
        assert second.json()["translated_text"] == "Enregistrer"
        mock_review.assert_called_once()  # QA ran only for the cache miss
