from pydantic import BaseModel


class GlossaryEntry(BaseModel):
    source: str
    target: str | None = None
    comment: str | None = None  # usage note from the glossary file


class FuzzyMatch(BaseModel):
    source: str
    target: str
    score: int  # 0-100 similarity
    score_no_stem: int | None = None  # score ignoring stemming
    adjusted_score: int | None = None  # OmegaT's final adjusted score
    match_source: str | None = None  # "MEMORY" (active TM) or "FILES" (reference TM)
    project: str | None = None  # project the match originates from


class ContextSegment(BaseModel):
    source: str
    translation: str | None = None  # present when the segment already has a translation


class TranslateRequest(BaseModel):
    source_text: str
    source_lang: str
    target_lang: str
    file_path: str | None = None             # looked up server-side to inject file summary
    style_rules: str | None = None           # project-local rules content; takes priority over the global setting
    glossary: list[GlossaryEntry] = []
    fuzzy_matches: list[FuzzyMatch] = []
    context_before: list[ContextSegment] = []
    context_after: list[ContextSegment] = []


class TranslateResponse(BaseModel):
    translated_text: str
    source_lang: str
    target_lang: str
    glossary_applied: bool
    fuzzy_match_used: bool


class GlossaryPrepRequest(BaseModel):
    source_strings: list[str]
    source_lang: str
    target_lang: str
    existing_terms: list[str] = []  # source terms already in the project glossary; used to skip duplicates
    file_path: str | None = None    # OmegaT project-relative path, stored for human readability


class GlossarySuggestion(BaseModel):
    source: str
    target: str
    comment: str | None = None
    source_url: str | None = None  # which database the suggestion came from


class GlossaryPrepResponse(BaseModel):
    suggestions: list[GlossarySuggestion]


class GlossaryStatusResponse(BaseModel):
    needs_extraction: bool  # True if this content has not yet been extracted
    content_hash: str       # Hash of the source strings used for the lookup


class GlossaryDeferResponse(BaseModel):
    deferred: bool = True


class FileSummaryRequest(BaseModel):
    file_path: str
    source_strings: list[str]
    source_lang: str
    target_lang: str


class FileSummaryResponse(BaseModel):
    summary: str
    from_cache: bool = False
