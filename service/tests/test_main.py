from unittest.mock import patch

from fastapi.testclient import TestClient

import translation.agent as translation_agent
from config import Settings, get_settings
from main import app

client = TestClient(app)


def test_translate_no_context_uses_ai():
    """No glossary and no TM match → still uses AI provider."""
    response = client.post(
        "/translate",
        json={
            "source_text": "Hello world",
            "source_lang": "EN",
            "target_lang": "FR-CA",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["translated_text"] == "(mocked ai translation)"
    assert data["source_lang"] == "EN"
    assert data["target_lang"] == "FR-CA"
    assert data["fuzzy_match_used"] is False
    assert data["glossary_applied"] is False


def test_translate_fuzzy_match_used():
    response = client.post(
        "/translate",
        json={
            "source_text": "Hello",
            "source_lang": "EN",
            "target_lang": "FR-CA",
            "fuzzy_matches": [{"source": "Hello", "target": "Bonjour", "score": 95}],
        },
    )
    assert response.status_code == 200
    assert response.json()["fuzzy_match_used"] is True


def test_translate_no_fuzzy_match():
    response = client.post(
        "/translate",
        json={
            "source_text": "Hello",
            "source_lang": "EN",
            "target_lang": "FR-CA",
        },
    )
    assert response.status_code == 200
    assert response.json()["fuzzy_match_used"] is False


def test_translate_glossary_applied():
    response = client.post(
        "/translate",
        json={
            "source_text": "Hello",
            "source_lang": "EN",
            "target_lang": "FR-CA",
            "glossary": [{"source": "Hello", "target": "Bonjour"}],
        },
    )
    assert response.status_code == 200
    assert response.json()["glossary_applied"] is True


def test_translate_glossary_with_comment():
    response = client.post(
        "/translate",
        json={
            "source_text": "Save",
            "source_lang": "EN",
            "target_lang": "FR-CA",
            "glossary": [
                {
                    "source": "Save",
                    "target": "Enregistrer",
                    "comment": "Use for file save actions",
                }
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["glossary_applied"] is True


def test_translate_passes_style_rules_into_prompt():
    """Contract guard: a style_rules value in the /translate body must reach the prompt the
    model receives (plugin → service → prompt). Catches a regression that silently drops them."""
    captured = {}
    real_build_prompt = translation_agent.build_prompt

    def spy(request, file_summary=None):
        prompt = real_build_prompt(request, file_summary=file_summary)
        captured["prompt"] = prompt
        return prompt

    with patch.object(translation_agent, "build_prompt", side_effect=spy):
        response = client.post(
            "/translate",
            json={
                "source_text": "the directors",
                "source_lang": "EN",
                "target_lang": "FR-CA",
                "style_rules": "Use the median point for gender-inclusive forms, e.g. directeur·trice·s.",
            },
        )

    assert response.status_code == 200
    assert "median point" in captured["prompt"]
    assert "directeur·trice·s" in captured["prompt"]


def test_tm_cache_serves_second_identical_request(tmp_path):
    """With the cache on (default), the second identical request is served from cache."""
    app.dependency_overrides[get_settings] = lambda: Settings(state_db_path=tmp_path / "state.db")
    body = {"source_text": "Repeat me", "source_lang": "EN", "target_lang": "FR-CA"}

    first = client.post("/translate", json=body)
    second = client.post("/translate", json=body)

    assert first.json()["from_cache"] is False
    assert second.json()["from_cache"] is True


def test_tm_cache_disabled_never_serves_cache(tmp_path):
    """With tm_cache_enabled=False, even a repeated request always hits the LLM."""
    app.dependency_overrides[get_settings] = lambda: Settings(
        state_db_path=tmp_path / "state.db", tm_cache_enabled=False
    )
    body = {"source_text": "Repeat me", "source_lang": "EN", "target_lang": "FR-CA"}

    first = client.post("/translate", json=body)
    second = client.post("/translate", json=body)

    assert first.json()["from_cache"] is False
    assert second.json()["from_cache"] is False


def test_translate_fuzzy_match_with_full_fields():
    response = client.post(
        "/translate",
        json={
            "source_text": "Hello",
            "source_lang": "EN",
            "target_lang": "FR-CA",
            "fuzzy_matches": [
                {
                    "source": "Hello world",
                    "target": "Bonjour le monde",
                    "score": 82,
                    "score_no_stem": 80,
                    "adjusted_score": 81,
                    "match_source": "MEMORY",
                    "project": "my-project",
                },
                {
                    "source": "Hello there",
                    "target": "Bonjour là",
                    "score": 75,
                },
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["fuzzy_match_used"] is True
