from fastapi.testclient import TestClient

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
