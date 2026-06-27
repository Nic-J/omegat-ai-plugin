# Changelog

All notable changes to this project will be documented here.

## [Unreleased]

### Added

- **Batch pre-translation** — on file open, a popup offers to pre-translate all untranslated segments in the background. Each segment is processed by the same orchestrator (glossary, style rules, QA, TM cache) as the live MT pane, so the MT pane responds instantly from cache when you navigate to a pre-translated segment. Non-destructive: nothing is inserted into OmegaT's translation fields. (OMP-025, OMP-026, OMP-027)
- `/batch-translate` service endpoint — accepts a list of fully-contexted `TranslateRequest` objects, processes each via `translate_segment()`, and returns per-segment results with per-segment error isolation so one failure does not abort the batch. (OMP-025)
- `QA_ENABLED` / `QA_MODEL` settings — opt-in QA self-critique pass that checks each new translation against the approved glossary and style rules, auto-corrects violations, and logs each fix to the service log and OmegaT's own log. Off by default. (OMP-023, OMP-024)
- `TM_CACHE_ENABLED` setting — server-side translation memory cache (on by default). Set `false` to force a fresh LLM call per segment. (OMP-022)
