from enum import Enum
from pathlib import Path

import structlog
import typer

from logging_config import configure_logging

from .extractor import extract_from_dir, extract_from_file
from .rater import rate_csv
from .presets import PRESETS, import_preset as _import_preset
from .terminology import import_csv as _import_csv
from .writer import write_csv, write_glossary

app = typer.Typer(help="Extract glossary term candidates from OmegaT TMX files.")

log = structlog.get_logger()


class OutputFormat(str, Enum):
    txt = "txt"
    csv = "csv"


@app.command()
def extract(
    input: Path = typer.Argument(..., help="Path to a .tmx file or a directory of .tmx files"),
    output: Path = typer.Argument(..., help="Output file path"),
    src_lang: str = typer.Option("en", "--src-lang", help="Source language code"),
    tgt_lang: str = typer.Option("fr", "--tgt-lang", help="Target language code"),
    similarity: float = typer.Option(0.85, "--similarity", help="Minimum similarity score (0–1)"),
    freq: int = typer.Option(2, "--freq", help="Minimum term frequency"),
    min_words: int = typer.Option(2, "--min-words", help="Minimum number of words per term (2 filters out single generic words)"),
    max_doc_freq: float | None = typer.Option(None, "--max-doc-freq", help="Drop terms in more than this fraction of segments, e.g. 0.3"),
    format: OutputFormat = typer.Option(OutputFormat.txt, "--format", help="Output format: txt (OmegaT glossary) or csv"),
) -> None:
    configure_logging()

    if not input.exists():
        typer.echo(f"Error: input path does not exist: {input}", err=True)
        raise typer.Exit(code=1)

    log.info("extraction_start", input=str(input), src_lang=src_lang, tgt_lang=tgt_lang,
             similarity=similarity, freq=freq, min_words=min_words,
             max_doc_freq=max_doc_freq, format=format.value)

    if input.is_dir():
        candidates = extract_from_dir(
            input,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            similarity_min=similarity,
            freq_min=freq,
            min_words=min_words,
            max_doc_freq=max_doc_freq,
        )
    else:
        candidates = extract_from_file(
            input,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            similarity_min=similarity,
            freq_min=freq,
            min_words=min_words,
            max_doc_freq=max_doc_freq,
        )

    if not candidates:
        log.warning("no_candidates_found", hint="try lowering --similarity or --freq")
        typer.echo("No candidates found. Try lowering --similarity or --freq.")
        raise typer.Exit(code=0)

    if format == OutputFormat.csv:
        count = write_csv(candidates, output)
    else:
        count = write_glossary(candidates, output)

    log.info("extraction_complete", candidates=count, output=str(output))
    typer.echo(f"Wrote {count} candidates to: {output}")


@app.command()
def rate(
    input: Path = typer.Argument(..., help="Path to candidates CSV (output of extract --format csv)"),
    output: Path = typer.Argument(..., help="Output CSV path (adds a 'rating' column)"),
    model: str = typer.Option("mistral-nemo", "--model", help="Ollama model name"),
    ollama_url: str = typer.Option("http://localhost:11434", "--ollama-url", help="Ollama base URL"),
    batch_size: int = typer.Option(15, "--batch-size", help="Number of terms per Ollama request"),
) -> None:
    """Rate extracted glossary candidates using a local Ollama model."""
    configure_logging()

    if not input.exists():
        typer.echo(f"Error: input file does not exist: {input}", err=True)
        raise typer.Exit(code=1)

    log.info("rating_start", input=str(input), model=model, batch_size=batch_size)
    count = rate_csv(input, output, model=model, ollama_url=ollama_url, batch_size=batch_size)
    typer.echo(f"Rated {count} candidates. Output: {output}")


@app.command("import-terminology")
def import_terminology(
    csv_path: Path = typer.Argument(..., help="CSV file to import"),
    preset: str | None = typer.Option(
        None, "--preset",
        help=f"Use a named preset ({', '.join(sorted(PRESETS))}). Sets column mapping and pre-processing automatically.",
    ),
    source: str | None = typer.Option(None, "--source", help='Dataset label (required without --preset), e.g. "custom"'),
    src_lang: str = typer.Option("EN", "--src-lang", help="Source language code"),
    tgt_lang: str = typer.Option("FR", "--tgt-lang", help="Target language code"),
    source_col: str | None = typer.Option(None, "--source-col", help="CSV column for the source-language term (required without --preset)"),
    target_col: str | None = typer.Option(None, "--target-col", help="CSV column for the target-language term (required without --preset)"),
    subject_col: str | None = typer.Option(None, "--subject-col", help="CSV column for subject/domain (optional, ignored with --preset)"),
    delimiter: str = typer.Option(",", "--delimiter", help="CSV column delimiter (ignored with --preset)"),
    encoding: str = typer.Option("utf-8-sig", "--encoding", help="CSV file encoding (ignored with --preset)"),
) -> None:
    """Import a terminology CSV into the local SQLite index for fast offline lookup.

    With --preset: column mapping and pre-processing are applied automatically.

      uv run python -m glossary.cli import-terminology file.csv --preset termium --src-lang EN --tgt-lang FR

    Without --preset: specify --source, --source-col, and --target-col manually.
    """
    configure_logging()
    if not csv_path.exists():
        typer.echo(f"Error: file not found: {csv_path}", err=True)
        raise typer.Exit(code=1)

    if preset:
        if preset not in PRESETS:
            typer.echo(f"Error: unknown preset '{preset}'. Available: {', '.join(sorted(PRESETS))}", err=True)
            raise typer.Exit(code=1)
        count = _import_preset(preset, csv_path, source_lang=src_lang, target_lang=tgt_lang)
        typer.echo(f"Imported {count} terms from {csv_path.name} (preset: {preset}, {src_lang}→{tgt_lang})")
    else:
        if not source or not source_col or not target_col:
            typer.echo("Error: --source, --source-col, and --target-col are required without --preset.", err=True)
            raise typer.Exit(code=1)
        mapping: dict[str, str] = {"source_term": source_col, "target_term": target_col}
        if subject_col:
            mapping["subject"] = subject_col
        count = _import_csv(
            csv_path=csv_path,
            column_mapping=mapping,
            source_label=source,
            source_lang=src_lang,
            target_lang=tgt_lang,
            delimiter=delimiter,
            encoding=encoding,
        )
        typer.echo(f"Imported {count} terms from {csv_path.name} (source: {source}, {src_lang}→{tgt_lang})")


if __name__ == "__main__":
    app()
