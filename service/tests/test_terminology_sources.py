from unittest.mock import patch

from config import Settings
from glossary.sources import TerminologySource, build_url, load_terminology_sources


class TestLoadTerminologySources:
    def test_falls_back_to_empty_when_no_file(self):
        # Termium/OQLF are no longer live-fetched; no built-in sources ship by default.
        # Users populate the local index via CLI import instead (OMP-013/019).
        settings = Settings(terminology_sources_path=None)
        with patch("glossary.sources.get_settings", return_value=settings):
            sources = load_terminology_sources()
        assert sources == []

    def test_loads_custom_toml_file(self, tmp_path):
        toml_file = tmp_path / "terminology_sources.toml"
        toml_file.write_text(
            '[[sources]]\nname = "iate"\nenabled = true\nurl_template = "https://iate.europa.eu/{term}"\n'
        )
        settings = Settings(terminology_sources_path=toml_file)
        with patch("glossary.sources.get_settings", return_value=settings):
            sources = load_terminology_sources()
        assert [s.name for s in sources] == ["iate"]

    def test_filters_disabled_sources(self, tmp_path):
        toml_file = tmp_path / "terminology_sources.toml"
        toml_file.write_text(
            '[[sources]]\nname = "iate"\nenabled = false\nurl_template = "https://iate.europa.eu/{term}"\n'
        )
        settings = Settings(terminology_sources_path=toml_file)
        with patch("glossary.sources.get_settings", return_value=settings):
            sources = load_terminology_sources()
        assert sources == []


class TestBuildUrl:
    def test_substitutes_term(self):
        source = TerminologySource(name="x", url_template="https://example.com/{term}")
        assert build_url(source, "logiciel", "EN") == "https://example.com/logiciel"

    def test_substitutes_lang_when_prefix_matches(self):
        source = TerminologySource(
            name="termium",
            url_template="https://example.com/{lang}/{term}",
            lang_map={"EN": "eng", "FR": "fra"},
        )
        assert build_url(source, "logiciel", "FR-CA") == "https://example.com/fra/logiciel"

    def test_lang_placeholder_left_when_no_lang_map(self):
        source = TerminologySource(name="x", url_template="https://example.com/{lang}/{term}")
        assert build_url(source, "logiciel", "EN") == "https://example.com/{lang}/logiciel"
