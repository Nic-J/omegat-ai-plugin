from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from config import Settings, get_settings
from main import app
from models import GlossaryEntry
from tm import state as tm_state


@pytest.fixture
def test_settings(tmp_path):
    settings = Settings(state_db_path=tmp_path / "test_state.db")
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.clear()


class TestComputeKey:
    def test_deterministic(self):
        key1 = tm_state.compute_key("Hello", "EN", "FR-CA", [], [], "test-model")
        key2 = tm_state.compute_key("Hello", "EN", "FR-CA", [], [], "test-model")
        assert key1 == key2

    def test_glossary_order_does_not_matter(self):
        g1 = [GlossaryEntry(source="A", target="a"), GlossaryEntry(source="B", target="b")]
        g2 = [GlossaryEntry(source="B", target="b"), GlossaryEntry(source="A", target="a")]
        assert tm_state.compute_key("Hello", "EN", "FR-CA", g1, [], "m") == \
            tm_state.compute_key("Hello", "EN", "FR-CA", g2, [], "m")

    def test_glossary_change_busts_key(self):
        key1 = tm_state.compute_key("Hello", "EN", "FR-CA", [], [], "m")
        key2 = tm_state.compute_key(
            "Hello", "EN", "FR-CA", [GlossaryEntry(source="Hello", target="Bonjour")], [], "m"
        )
        assert key1 != key2

    def test_style_rules_change_busts_key(self):
        key1 = tm_state.compute_key("Hello", "EN", "FR-CA", [], ["Use formal tone."], "m")
        key2 = tm_state.compute_key("Hello", "EN", "FR-CA", [], ["Use casual tone."], "m")
        assert key1 != key2

    def test_model_change_busts_key(self):
        key1 = tm_state.compute_key("Hello", "EN", "FR-CA", [], [], "model-a")
        key2 = tm_state.compute_key("Hello", "EN", "FR-CA", [], [], "model-b")
        assert key1 != key2


class TestTmState:
    def test_get_returns_none_when_absent(self, tmp_path):
        db = tmp_path / "state.db"
        assert tm_state.get("missing-key", db_path=db) is None

    def test_save_and_retrieve(self, tmp_path):
        db = tmp_path / "state.db"
        tm_state.save("key1", "Hello", "EN", "FR-CA", "Bonjour", "m", db_path=db)
        assert tm_state.get("key1", db_path=db) == "Bonjour"

    def test_save_is_idempotent(self, tmp_path):
        db = tmp_path / "state.db"
        tm_state.save("key1", "Hello", "EN", "FR-CA", "Bonjour", "m", db_path=db)
        tm_state.save("key1", "Hello", "EN", "FR-CA", "Salut", "m", db_path=db)
        assert tm_state.get("key1", db_path=db) == "Salut"

    def test_same_key_isolated_across_projects(self, tmp_path):
        db = tmp_path / "state.db"
        tm_state.save("key1", "Hello", "EN", "FR-CA", "Bonjour A", "m", project_id="proj-a", db_path=db)
        tm_state.save("key1", "Hello", "EN", "FR-CA", "Bonjour B", "m", project_id="proj-b", db_path=db)
        assert tm_state.get("key1", project_id="proj-a", db_path=db) == "Bonjour A"
        assert tm_state.get("key1", project_id="proj-b", db_path=db) == "Bonjour B"
        assert tm_state.get("key1", db_path=db) is None  # default "" bucket untouched


class TestTranslateEndpointCaching:
    def test_first_call_translates_and_caches(self, test_settings):
        client = TestClient(app)
        with patch("translation.agent.translate", new=AsyncMock(return_value="Bonjour")):
            resp = client.post("/translate", json={
                "source_text": "Hello", "source_lang": "EN", "target_lang": "FR-CA",
            })
        assert resp.status_code == 200
        assert resp.json()["translated_text"] == "Bonjour"
        assert resp.json()["from_cache"] is False

    def test_second_identical_call_hits_cache(self, test_settings):
        client = TestClient(app)
        with patch("translation.agent.translate", new=AsyncMock(return_value="Bonjour")):
            client.post("/translate", json={
                "source_text": "Hello", "source_lang": "EN", "target_lang": "FR-CA",
            })

        with patch("translation.agent.translate", new=AsyncMock()) as mock_translate:
            resp = client.post("/translate", json={
                "source_text": "Hello", "source_lang": "EN", "target_lang": "FR-CA",
            })
            mock_translate.assert_not_called()

        assert resp.json()["from_cache"] is True
        assert resp.json()["translated_text"] == "Bonjour"

    def test_glossary_change_busts_cache(self, test_settings):
        client = TestClient(app)
        with patch("translation.agent.translate", new=AsyncMock(return_value="Bonjour")):
            client.post("/translate", json={
                "source_text": "Hello", "source_lang": "EN", "target_lang": "FR-CA",
            })

        with patch("translation.agent.translate", new=AsyncMock(return_value="Salut")) as mock_translate:
            resp = client.post("/translate", json={
                "source_text": "Hello", "source_lang": "EN", "target_lang": "FR-CA",
                "glossary": [{"source": "Hello", "target": "Salut"}],
            })
            mock_translate.assert_called_once()

        assert resp.json()["from_cache"] is False
        assert resp.json()["translated_text"] == "Salut"

    def test_style_rules_change_busts_cache(self, test_settings):
        client = TestClient(app)
        with patch("translation.agent.translate", new=AsyncMock(return_value="Bonjour")):
            client.post("/translate", json={
                "source_text": "Hello", "source_lang": "EN", "target_lang": "FR-CA",
                "style_rules": "Use formal tone.",
            })

        with patch("translation.agent.translate", new=AsyncMock(return_value="Salut")) as mock_translate:
            resp = client.post("/translate", json={
                "source_text": "Hello", "source_lang": "EN", "target_lang": "FR-CA",
                "style_rules": "Use casual tone.",
            })
            mock_translate.assert_called_once()

        assert resp.json()["from_cache"] is False

    def test_project_partitioning(self, test_settings):
        client = TestClient(app)
        with patch("translation.agent.translate", new=AsyncMock(return_value="Bonjour A")):
            client.post("/translate", json={
                "source_text": "Hello", "source_lang": "EN", "target_lang": "FR-CA",
                "project_id": "proj-a",
            })

        with patch("translation.agent.translate", new=AsyncMock(return_value="Bonjour B")) as mock_translate:
            resp = client.post("/translate", json={
                "source_text": "Hello", "source_lang": "EN", "target_lang": "FR-CA",
                "project_id": "proj-b",
            })
            mock_translate.assert_called_once()

        assert resp.json()["from_cache"] is False
        assert resp.json()["translated_text"] == "Bonjour B"
