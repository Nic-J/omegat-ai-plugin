# AI Translation Assistant for OmegaT

An OmegaT machine-translation plugin that forwards segments to a local AI service
for translation, with full glossary enforcement, TM fuzzy-match context, and
document-level summarization. Works with local models via Ollama or cloud models
via Anthropic/Google APIs.

## Features

- AI translation with glossary enforcement, fuzzy-match context, and surrounding-segment context
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
| `STYLE_RULES_PATH` | _(unset)_ | Path to a style-rules file injected into the translation prompt — copy `service/style_rules.example.txt` to get started |
| `STATE_DB_PATH` | platform user data dir | SQLite DB for glossary/summary state |
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

You can also override the global style rules per OmegaT project: drop an
`ai_style_rules.txt` file (same format as `style_rules.example.txt`) in the
project's root folder and it takes priority over `STYLE_RULES_PATH` for
translations done in that project.

## Usage

1. In OmegaT: **Options → Machine Translate** → enable "AI Translation Assistant"
2. Translate a segment as usual — the plugin calls the local service for each one
3. When you open a file, a popup offers to extract glossary candidates from the active translation memory

## Architecture

```
OmegaT  ──▶  Plugin (Java, JAR)  ──▶  Service (Python, FastAPI)  ──▶  LLM (Ollama / Anthropic / Google)
                                            │
                                            ▼
                                  SQLite (glossary + summary state)
```

The plugin never reads files directly from the service's perspective — all
content is sent in the request payload, not as filesystem paths.

## Limitations

- Built-in terminology sources (Termium, OQLF) are Canadian EN↔FR-focused — add your own via `terminology_sources.toml` for other languages

## Contributing

Issues and pull requests welcome.

## License

MIT — see [LICENSE](LICENSE).
