# AI Translation Assistant for OmegaT

An OmegaT machine-translation plugin that forwards segments to a local AI service
for translation, with full glossary enforcement, TM fuzzy-match context, and
document-level summarization. Works with local models via Ollama or cloud models
via Anthropic/Google APIs.

## Features

- AI translation with glossary enforcement, fuzzy-match context, and surrounding-segment context
- Server-side translation memory cache — repeat segments return instantly with no extra LLM call
- Automatic document summarization, injected into each translation request for better context
- Glossary extraction from configurable terminology databases (Termium, OQLF — extensible to your own)
- Works with any model via Ollama (local, free) or Anthropic/Google APIs (cloud)

## Prerequisites

- OmegaT 6+
- Java 11+ and Maven (to build the plugin)
- Python 3.13+ and [uv](https://docs.astral.sh/uv/)
- [Ollama](https://ollama.com/) (optional, for local models)
- An Anthropic or Google API key (optional, for cloud models)

## Installation

### 1. Plugin (JAR)

Build it:

```sh
cd plugin
mvn package
```

This produces `target/ai-translate-plugin-0.1.0.jar`. Copy it to your OmegaT plugins folder:

| OS | Path |
|---|---|
| macOS | `~/Library/Preferences/OmegaT/plugins/` |
| Windows | `%APPDATA%\OmegaT\plugins\` |
| Linux | `~/.omegat/plugins/` |

### 2. Service

```sh
cd service
uv sync
cp .env.example .env   # edit .env with your model/API key choices
./start.sh
```

The service listens on `http://localhost:8000` by default.

## Configuration

All service settings live in `service/.env` (see `service/.env.example` for the full list with descriptions):

| Setting | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | _(empty)_ | Required only if using an `anthropic:...` model |
| `AI_MODEL` | `ollama:mistral-nemo` | Model used for translation |
| `GLOSSARY_MODEL` | _(falls back to `AI_MODEL`)_ | Model used for glossary web research — benefits from a stronger model |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local Ollama instance |
| `STYLE_RULES_PATH` | _(unset)_ | Path to a global style-rules file injected into the translation prompt — copy `service/ai_style_rules.example.txt` to get started |
| `STATE_DB_PATH` | platform user data dir | SQLite DB for glossary/summary/translation-memory state |
| `GLOSSARY_MAX_TERMS` | `20` | Max candidate terms sent for terminology lookup |
| `GLOSSARY_MAX_PAGE_CHARS` | `3000` | Max characters of fetched page text passed to the LLM per lookup |
| `TERMINOLOGY_SOURCES_PATH` | `terminology_sources.toml` | TOML file listing terminology lookup sources — copy `service/terminology_sources.toml.example` to customize |

The OmegaT plugin's service URL is configurable via the OmegaT preferences key
`ai_translation_service_url` (default `http://localhost:8000`) — set it in
`omegat.prefs` if you run the service on a different host or port.

Glossary lookup ships with Termium and OQLF (Canadian EN↔FR terminology
databases) as built-in defaults. To add your own sources (IATE, Microsoft
Terminology, a corporate glossary API, etc.) or disable the defaults, copy
`service/terminology_sources.toml.example` to `service/terminology_sources.toml`
and edit it — no code changes needed. Each entry becomes a tool the glossary
agent can call.

### Style rules: global default and per-project override

Style rules are resolved in two layers, with the per-project file taking priority:

- **Global default** — set `STYLE_RULES_PATH` in `service/.env` to a file (copy
  `service/ai_style_rules.example.txt`). Applies to every project.
- **Per-project override** — place a file named **exactly `ai_style_rules.txt`**
  in the OmegaT **project's root folder** (next to `omegat.project`). When present
  it replaces the global rules for translations in that project.

The per-project file must be named exactly `ai_style_rules.txt` — any other name
(`style_rules.txt`, `ai_style_rules.md`, …) is ignored. The plugin logs which path
it checked and whether a file was loaded, so check OmegaT's log if rules don't
seem to apply. Both files use the same format: one rule per line, `#` for comments.

## Translation memory cache

`/translate` caches each translation in SQLite, keyed by an exact-match hash of
`source_text` + `source_lang` + `target_lang` + `glossary` + resolved
`style_rules` + the model. OmegaT's MT pane re-queries on every revisit (with
repeat-suppression disabled), so without this, the same segment would trigger
a fresh, billable LLM call each time — the cache returns the stored
translation instantly instead, and only calls the LLM for genuinely new or
changed input. Editing the glossary, changing style rules, or switching model
busts the cache automatically. The cache is scoped per OmegaT project
(`project_id`) and excludes surrounding context (fuzzy matches, file summary)
from the key — same source text in different context returns one cached
translation, matching how OmegaT's own TM behaves. There's no eviction; a
changed key just orphans the old row, which is fine at single-user scale. The
`/translate` response includes `from_cache: true` when served from the cache.

## Adding your own research tool

There's no bundled web-search tool — that would mean shipping an extra
dependency, a per-provider API quirk, and a key to manage for something most
users won't need. Instead, the glossary agent (`service/glossary/agent.py`) is
structured so adding your own [PydanticAI tool](https://ai.pydantic.dev/tools/)
is ~15 lines. This works the same whether `AI_MODEL`/`GLOSSARY_MODEL` is Ollama
or a cloud model.

Worked example — a DuckDuckGo web-search tool (no API key required), added
right after `_TOOL_NAMES = _register_terminology_tools(_glossary_agent)`:

```python
@_glossary_agent.tool
async def fetch_duckduckgo(ctx: RunContext[GlossaryDeps], term: str) -> str:
    """Web-search a term on DuckDuckGo and return stripped result text."""
    url = f"https://html.duckduckgo.com/html/?q={term}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, follow_redirects=True)
        return _strip_html(resp.text) if resp.status_code == 200 else f"HTTP {resp.status_code}"
    except Exception as e:
        return f"Error: {e}"


_TOOL_NAMES.append("fetch_duckduckgo")
```

The `_TOOL_NAMES.append(...)` line matters: Phase 2's prompt tells the LLM
which tools it can call by joining `_TOOL_NAMES`, so a tool the LLM doesn't
know exists will never get called. Same idea applies to a corporate glossary
API, Tavily, or anything else with an HTTP endpoint — fetch, return text, and
register the name.

## Usage

1. In OmegaT: **Options → Machine Translate** → enable "AI Translation Assistant"
2. Translate a segment as usual — the plugin calls the local service for each one
3. When you open a file, a popup offers to extract glossary candidates from the active translation memory

## Architecture

```
OmegaT  ──▶  Plugin (Java, JAR)  ──▶  Service (Python, FastAPI)  ──▶  LLM (Ollama / Anthropic / Google)
                                            │
                                            ▼
                            SQLite (glossary + summary + translation memory)
```

The plugin never reads files directly from the service's perspective — all
content is sent in the request payload, not as filesystem paths.

## Limitations

- Built-in terminology sources (Termium, OQLF) are Canadian EN↔FR-focused — add your own via `terminology_sources.toml` for other languages

## Contributing

Issues and pull requests welcome.

## License

MIT — see [LICENSE](LICENSE).
