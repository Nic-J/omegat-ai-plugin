from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from config import Settings, get_settings
from main import app
from summary import state as summary_state


@pytest.fixture
def test_settings(tmp_path):
    settings = Settings(state_db_path=tmp_path / "test_state.db")
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.clear()


class TestFileSummaryState:
    def test_get_summary_returns_none_when_absent(self, tmp_path):
        db = tmp_path / "state.db"
        assert summary_state.get_summary("docs/guide.docx", db_path=db) is None

    def test_save_and_retrieve(self, tmp_path):
        db = tmp_path / "state.db"
        summary_state.save_summary(
            "docs/guide.docx", "A technical manual.", "EN", "FR-CA", "test-model", db_path=db
        )
        assert summary_state.get_summary("docs/guide.docx", db_path=db) == "A technical manual."

    def test_save_is_idempotent(self, tmp_path):
        db = tmp_path / "state.db"
        summary_state.save_summary("f.docx", "First.", "EN", "FR-CA", "m", db_path=db)
        summary_state.save_summary("f.docx", "Updated.", "EN", "FR-CA", "m", db_path=db)
        assert summary_state.get_summary("f.docx", db_path=db) == "Updated."

    def test_different_files_are_independent(self, tmp_path):
        db = tmp_path / "state.db"
        summary_state.save_summary("a.docx", "Summary A.", "EN", "FR-CA", "m", db_path=db)
        summary_state.save_summary("b.docx", "Summary B.", "EN", "FR-CA", "m", db_path=db)
        assert summary_state.get_summary("a.docx", db_path=db) == "Summary A."
        assert summary_state.get_summary("b.docx", db_path=db) == "Summary B."


class TestFileSummaryEndpoint:
    def test_generates_and_caches(self, test_settings):
        client = TestClient(app)
        with patch("summary.agent.generate_summary", new=AsyncMock(return_value="A donor newsletter.")):
            resp = client.post("/file-summary/generate", json={
                "file_path": "docs/newsletter.docx",
                "source_strings": ["Open the file", "Save"],
                "source_lang": "EN",
                "target_lang": "FR-CA",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] == "A donor newsletter."
        assert data["from_cache"] is False

    def test_returns_cached_on_second_call(self, test_settings):
        client = TestClient(app)
        with patch("summary.agent.generate_summary", new=AsyncMock(return_value="Cached summary.")):
            client.post("/file-summary/generate", json={
                "file_path": "docs/guide.docx",
                "source_strings": ["Open"],
                "source_lang": "EN",
                "target_lang": "FR-CA",
            })

        with patch("summary.agent.generate_summary", new=AsyncMock()) as mock_gen:
            resp = client.post("/file-summary/generate", json={
                "file_path": "docs/guide.docx",
                "source_strings": ["Open"],
                "source_lang": "EN",
                "target_lang": "FR-CA",
            })
            mock_gen.assert_not_called()

        assert resp.json()["from_cache"] is True
        assert resp.json()["summary"] == "Cached summary."


class TestTranslateInjectsSummary:
    def test_summary_injected_when_file_path_known(self, test_settings):
        summary_state.save_summary(
            "docs/letter.docx", "A formal letter.",
            "EN", "FR-CA", "test-model",
            db_path=test_settings.state_db_path,
        )
        client = TestClient(app)
        captured_summary = {}

        async def fake_translate(request, file_summary=None):
            captured_summary["value"] = file_summary
            return "Bonjour"

        with patch("translation.agent.translate", new=fake_translate):
            resp = client.post("/translate", json={
                "source_text": "Hello",
                "source_lang": "EN",
                "target_lang": "FR-CA",
                "file_path": "docs/letter.docx",
            })

        assert resp.status_code == 200
        assert captured_summary["value"] == "A formal letter."

    def test_no_summary_when_file_path_absent(self, test_settings):
        client = TestClient(app)
        captured_summary = {}

        async def fake_translate(request, file_summary=None):
            captured_summary["value"] = file_summary
            return "Bonjour"

        with patch("translation.agent.translate", new=fake_translate):
            client.post("/translate", json={
                "source_text": "Hello",
                "source_lang": "EN",
                "target_lang": "FR-CA",
            })

        assert captured_summary["value"] is None
