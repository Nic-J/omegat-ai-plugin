from enum import Enum
from pathlib import Path

import structlog
import typer

from .extractor import extract_from_dir, extract_from_file
from .rater import rate_csv
from .writer import write_csv, write_glossary

app = typer.Typer(help="Extract glossary term candidates from OmegaT TMX files.")

log = structlog.get_logger()


class OutputFormat(str, Enum):
    txt = "txt"
    csv = "csv"


def _configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )


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
    _configure_logging()

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
    _configure_logging()

    if not input.exists():
        typer.echo(f"Error: input file does not exist: {input}", err=True)
        raise typer.Exit(code=1)

    log.info("rating_start", input=str(input), model=model, batch_size=batch_size)
    count = rate_csv(input, output, model=model, ollama_url=ollama_url, batch_size=batch_size)
    typer.echo(f"Rated {count} candidates. Output: {output}")


if __name__ == "__main__":
    app()
