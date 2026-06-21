from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from config import Settings, get_settings
from glossary.agent import _strip_html
from glossary.state import compute_hash
from main import app
from models import GlossarySuggestion


class TestStripHtml:
    def test_removes_tags(self):
        assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"

    def test_collapses_whitespace(self):
        assert _strip_html("<p>  a   b  </p>") == "a b"

    def test_truncates_at_max(self):
        html = "<p>" + "x" * 5000 + "</p>"
        assert len(_strip_html(html)) <= 3000


class TestPrepareGlossaryEndpoint:
    @pytest.fixture
    def client(self, test_settings):
        return TestClient(app)

    def test_returns_suggestions(self, client):
        mock_suggestions = [
            GlossarySuggestion(
                source="software",
                target="logiciel",
                comment="standard FR-CA term",
                source_url="https://www.btb.termiumplus.gc.ca/",
            )
        ]
        with patch("glossary.agent.extract_glossary", new=AsyncMock(return_value=mock_suggestions)):
            resp = client.post(
                "/prepare-glossary",
                json={"source_strings": ["Save the software"], "source_lang": "EN", "target_lang": "FR-CA"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["suggestions"]) == 1
        assert data["suggestions"][0]["source"] == "software"
        assert data["suggestions"][0]["target"] == "logiciel"

    def test_empty_strings_returns_empty(self, client):
        with patch("glossary.agent.extract_glossary", new=AsyncMock(return_value=[])):
            resp = client.post(
                "/prepare-glossary",
                json={"source_strings": [], "source_lang": "EN", "target_lang": "FR-CA"},
            )
        assert resp.status_code == 200
        assert resp.json()["suggestions"] == []

    def test_dedup_filters_existing_terms(self, client):
        mock_suggestions = [
            GlossarySuggestion(source="software", target="logiciel"),
            GlossarySuggestion(source="hardware", target="matériel"),
        ]
        with patch("glossary.agent.extract_glossary", new=AsyncMock(return_value=mock_suggestions)):
            resp = client.post(
                "/prepare-glossary",
                json={
                    "source_strings": ["Save the software"],
                    "source_lang": "EN",
                    "target_lang": "FR-CA",
                    "existing_terms": ["Software"],  # case-insensitive match
                },
            )
        assert resp.status_code == 200
        suggestions = resp.json()["suggestions"]
        assert len(suggestions) == 1
        assert suggestions[0]["source"] == "hardware"

    def test_dedup_no_existing_terms_returns_all(self, client):
        mock_suggestions = [GlossarySuggestion(source="software", target="logiciel")]
        with patch("glossary.agent.extract_glossary", new=AsyncMock(return_value=mock_suggestions)):
            resp = client.post(
                "/prepare-glossary",
                json={"source_strings": ["x"], "source_lang": "EN", "target_lang": "FR-CA"},
            )
        assert resp.status_code == 200
        assert len(resp.json()["suggestions"]) == 1


class TestGlossaryStatusEndpoint:
    @pytest.fixture
    def client(self):
        return TestClient(app)

    def test_needs_extraction_when_not_in_db(self, client):
        with patch("glossary.state.is_extracted", return_value=False):
            resp = client.post(
                "/glossary/status",
                json={"source_strings": ["Save the file"], "source_lang": "EN", "target_lang": "FR-CA"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["needs_extraction"] is True
        assert len(data["content_hash"]) == 16

    def test_no_extraction_needed_when_already_in_db(self, client):
        with patch("glossary.state.is_extracted", return_value=True):
            resp = client.post(
                "/glossary/status",
                json={"source_strings": ["Save the file"], "source_lang": "EN", "target_lang": "FR-CA"},
            )
        assert resp.status_code == 200
        assert resp.json()["needs_extraction"] is False

    def test_hash_is_stable_regardless_of_string_order(self):
        strings_a = ["Alpha", "Beta", "Gamma"]
        strings_b = ["Gamma", "Alpha", "Beta"]
        assert compute_hash(strings_a) == compute_hash(strings_b)

    def test_different_content_produces_different_hash(self):
        assert compute_hash(["foo"]) != compute_hash(["bar"])


class TestGlossaryState:
    def test_mark_and_check(self, tmp_path):
        from glossary import state

        db = tmp_path / "test_state.db"
        h = state.compute_hash(["hello world"])
        assert not state.is_extracted(h, db_path=db)
        state.mark_extracted(h, db_path=db)
        assert state.is_extracted(h, db_path=db)

    def test_mark_extracted_is_idempotent(self, tmp_path):
        from glossary import state

        db = tmp_path / "test_state.db"
        h = state.compute_hash(["idempotent"])
        state.mark_extracted(h, db_path=db)
        state.mark_extracted(h, db_path=db)  # must not raise
        assert state.is_extracted(h, db_path=db)

    def test_mark_extracted_stores_file_path(self, tmp_path):
        from glossary import state
        import sqlite3

        db = tmp_path / "test_state.db"
        h = state.compute_hash(["file path test"])
        state.mark_extracted(h, file_path="docs/chapter1.html", db_path=db)

        conn = sqlite3.connect(str(db))
        row = conn.execute(
            "SELECT file_path FROM glossary_state WHERE content_hash = ?", (h,)
        ).fetchone()
        conn.close()
        assert row[0] == "docs/chapter1.html"

    def test_deferred_is_not_extracted(self, tmp_path):
        from glossary import state

        db = tmp_path / "test_state.db"
        h = state.compute_hash(["deferred content"])
        state.mark_deferred(h, db_path=db)
        assert not state.is_extracted(h, db_path=db)

    def test_deferred_stores_status_in_db(self, tmp_path):
        from glossary import state
        import sqlite3

        db = tmp_path / "test_state.db"
        h = state.compute_hash(["deferred content"])
        state.mark_deferred(h, file_path="docs/guide.html", db_path=db)

        conn = sqlite3.connect(str(db))
        row = conn.execute(
            "SELECT status, file_path FROM glossary_state WHERE content_hash = ?", (h,)
        ).fetchone()
        conn.close()
        assert row[0] == "deferred"
        assert row[1] == "docs/guide.html"

    def test_extracted_cannot_be_downgraded_to_deferred(self, tmp_path):
        from glossary import state

        db = tmp_path / "test_state.db"
        h = state.compute_hash(["important content"])
        state.mark_extracted(h, db_path=db)
        state.mark_deferred(h, db_path=db)  # should not overwrite
        assert state.is_extracted(h, db_path=db)

    def test_same_content_hash_isolated_across_projects(self, tmp_path):
        from glossary import state

        db = tmp_path / "test_state.db"
        h = state.compute_hash(["shared content"])
        state.mark_extracted(h, project_id="proj-a", db_path=db)
        assert state.is_extracted(h, project_id="proj-a", db_path=db)
        assert not state.is_extracted(h, project_id="proj-b", db_path=db)
        assert not state.is_extracted(h, db_path=db)  # default "" bucket untouched


@pytest.fixture
def test_settings(tmp_path):
    """Settings pointing at a throwaway DB; injected via FastAPI dependency_overrides."""
    settings = Settings(state_db_path=tmp_path / "flow_test.db")
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.clear()


class TestGlossaryDeferEndpoint:
    def test_returns_deferred_true(self, test_settings):
        client = TestClient(app)
        resp = client.post(
            "/glossary/defer",
            json={"source_strings": ["Save the file"], "source_lang": "EN", "target_lang": "FR-CA"},
        )
        assert resp.status_code == 200
        assert resp.json()["deferred"] is True

    def test_deferred_file_still_needs_extraction(self, test_settings):
        client = TestClient(app)
        payload = {"source_strings": ["Save the file"], "source_lang": "EN", "target_lang": "FR-CA"}
        client.post("/glossary/defer", json=payload)

        status = client.post("/glossary/status", json=payload)
        assert status.json()["needs_extraction"] is True


class TestExtractionFlow:
    """End-to-end flow: status → prepare-glossary → status should flip needs_extraction."""

    SOURCE_STRINGS = ["Open the file", "Save the document"]
    BASE_PAYLOAD = {"source_strings": SOURCE_STRINGS, "source_lang": "EN", "target_lang": "FR-CA"}

    def test_status_needs_extraction_before_prepare(self, test_settings):
        client = TestClient(app)
        resp = client.post("/glossary/status", json=self.BASE_PAYLOAD)
        assert resp.status_code == 200
        assert resp.json()["needs_extraction"] is True

    def test_status_no_extraction_after_prepare(self, test_settings):
        client = TestClient(app)
        with patch("glossary.agent.extract_glossary", new=AsyncMock(return_value=[])):
            prep = client.post("/prepare-glossary", json={**self.BASE_PAYLOAD, "file_path": "docs/test.html"})
        assert prep.status_code == 200

        status = client.post("/glossary/status", json=self.BASE_PAYLOAD)
        assert status.json()["needs_extraction"] is False

    def test_prepare_stores_file_path_in_db(self, test_settings):
        import sqlite3

        client = TestClient(app)
        with patch("glossary.agent.extract_glossary", new=AsyncMock(return_value=[])):
            client.post("/prepare-glossary", json={**self.BASE_PAYLOAD, "file_path": "docs/chapter1.html"})

        conn = sqlite3.connect(str(test_settings.state_db_path))
        row = conn.execute("SELECT file_path FROM glossary_state").fetchone()
        conn.close()
        assert row[0] == "docs/chapter1.html"

    def test_status_isolated_per_project_id(self, test_settings):
        client = TestClient(app)
        payload_a = {**self.BASE_PAYLOAD, "project_id": "proj-a"}
        payload_b = {**self.BASE_PAYLOAD, "project_id": "proj-b"}

        with patch("glossary.agent.extract_glossary", new=AsyncMock(return_value=[])):
            client.post("/prepare-glossary", json=payload_a)

        assert client.post("/glossary/status", json=payload_a).json()["needs_extraction"] is False
        assert client.post("/glossary/status", json=payload_b).json()["needs_extraction"] is True
