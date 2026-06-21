from unittest.mock import patch

from models import ContextSegment, FuzzyMatch, GlossaryEntry, TranslateRequest
from translation.prompt import build_prompt


def make_request(**kwargs) -> TranslateRequest:
    base = {"source_text": "Save", "source_lang": "EN", "target_lang": "FR-CA"}
    return TranslateRequest(**(base | kwargs))


class TestBuildPrompt:
    def test_includes_languages(self):
        prompt = build_prompt(make_request())
        assert "EN" in prompt and "FR-CA" in prompt

    def test_includes_source_text(self):
        assert "Hello" in build_prompt(make_request(source_text="Hello"))

    def test_includes_glossary_term(self):
        request = make_request(glossary=[GlossaryEntry(source="Save", target="Enregistrer")])
        assert '"Save" → "Enregistrer"' in build_prompt(request)

    def test_includes_glossary_comment(self):
        request = make_request(
            glossary=[GlossaryEntry(source="Save", target="Enregistrer", comment="file actions")]
        )
        assert "file actions" in build_prompt(request)

    def test_includes_fuzzy_match_score_and_text(self):
        request = make_request(
            fuzzy_matches=[FuzzyMatch(source="Hello world", target="Bonjour le monde", score=82, adjusted_score=65)]
        )
        prompt = build_prompt(request)
        assert "65% match" in prompt  # adjusted_score takes precedence
        assert "Hello world" in prompt
        assert "Bonjour le monde" in prompt

    def test_fuzzy_match_falls_back_to_score_when_no_adjusted(self):
        request = make_request(
            fuzzy_matches=[FuzzyMatch(source="Hello world", target="Bonjour le monde", score=82)]
        )
        prompt = build_prompt(request)
        assert "82% match" in prompt

    def test_no_context_sections_when_empty(self):
        prompt = build_prompt(make_request())
        assert "Reference translation" not in prompt
        assert "Approved term" not in prompt

    def test_includes_style_rules_when_configured(self):
        with patch("translation.prompt._load_style_rules", return_value=["Use inclusive gender forms."]):
            prompt = build_prompt(make_request())
        assert "Style rules" in prompt
        assert "Use inclusive gender forms." in prompt

    def test_no_style_rules_section_when_empty(self):
        with patch("translation.prompt._load_style_rules", return_value=[]):
            prompt = build_prompt(make_request())
        assert "Style rules" not in prompt

    def test_context_before_with_translation_appears_in_prompt(self):
        request = make_request(
            context_before=[ContextSegment(source="Open the file.", translation="Ouvrez le fichier.")]
        )
        prompt = build_prompt(request)
        assert "Open the file." in prompt
        assert "Ouvrez le fichier." in prompt
        assert "[before]" in prompt

    def test_context_after_without_translation_appears_in_prompt(self):
        request = make_request(context_after=[ContextSegment(source="Close the dialog.")])
        prompt = build_prompt(request)
        assert "Close the dialog." in prompt
        assert "[after]" in prompt

    def test_no_context_section_when_lists_empty(self):
        prompt = build_prompt(make_request())
        assert "[before]" not in prompt
        assert "[after]" not in prompt
        assert "Surrounding segments" not in prompt
