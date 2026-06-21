"""
Terminology lookup sources for the glossary agent — TOML-configurable, with
built-in Termium + OQLF defaults so the service works out of the box.
"""
import tomllib

from pydantic import BaseModel

from config import get_settings

_BUILTIN_SOURCES_TOML = """
[[sources]]
name = "termium"
enabled = true
description = "Canadian government bilingual terminology"
url_template = "https://www.btb.termiumplus.gc.ca/tpv2alpha/alpha-eng.html?lang={lang}&srchtxt={term}&index=enb"
lang_map = { EN = "eng", FR = "fra" }

[[sources]]
name = "oqlf"
enabled = true
description = "Quebec French terminology"
url_template = "https://vitrinelinguistique.oqlf.gouv.qc.ca/resultats-de-recherche?tx_solr[q]={term}&tx_solr[filter][]=type_stringM:gdt"
"""


class TerminologySource(BaseModel):
    name: str
    enabled: bool = True
    description: str = ""
    url_template: str
    lang_map: dict[str, str] | None = None  # optional source_lang prefix -> {lang} substitution


def _parse_sources_toml(content: str) -> list[TerminologySource]:
    data = tomllib.loads(content)
    return [TerminologySource(**entry) for entry in data.get("sources", [])]


def load_terminology_sources() -> list[TerminologySource]:
    """Enabled terminology sources from the configured TOML file, falling back
    to the built-in Termium + OQLF defaults if no file is found."""
    path = get_settings().terminology_sources_path
    content = path.read_text(encoding="utf-8") if path and path.exists() else _BUILTIN_SOURCES_TOML
    return [s for s in _parse_sources_toml(content) if s.enabled]


def build_url(source: TerminologySource, term: str, source_lang: str) -> str:
    """Substitute {term} and (if lang_map matches) {lang} into the source's URL template."""
    url = source.url_template.replace("{term}", term)
    if source.lang_map:
        for prefix, lang in source.lang_map.items():
            if source_lang.upper().startswith(prefix.upper()):
                url = url.replace("{lang}", lang)
                break
    return url
