import csv
import json
from dataclasses import dataclass
from pathlib import Path

import httpx2 as httpx
import structlog

log = structlog.get_logger()

RATING_VALUES = {"domain-specific", "common", "uncertain"}

_SYSTEM_PROMPT = """\
You are helping build a translation glossary for a localization project.
Rate each term pair as one of:
- "domain-specific": a specialized or technical term worth including in a glossary
- "common": an everyday word or generic expression not worth including
- "uncertain": you cannot determine

Return ONLY a JSON object in this exact format, with no explanation:
{"ratings": [{"index": 0, "rating": "domain-specific"}, ...]}\
"""


@dataclass
class RatedRow:
    source: str
    target: str
    similarity: str
    frequency: str
    rating: str


def _rate_batch(
    batch: list[tuple[int, str, str]],
    model: str,
    ollama_url: str,
) -> dict[int, str]:
    """Send one batch of terms to Ollama and return {index: rating} mapping."""
    lines = "\n".join(f'{i}: "{src}" → "{tgt}"' for i, src, tgt in batch)
    user_message = f"Rate these terms:\n{lines}"

    try:
        response = httpx.post(
            f"{ollama_url}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                "stream": False,
                "format": "json",
            },
            timeout=60.0,
        )
        response.raise_for_status()
        content = response.json()["message"]["content"]
        data = json.loads(content)
        return {
            r["index"]: r["rating"] if r["rating"] in RATING_VALUES else "uncertain"
            for r in data.get("ratings", [])
        }
    except (httpx.HTTPError, json.JSONDecodeError, KeyError) as exc:
        log.warning("batch_rating_failed", error=str(exc), batch_size=len(batch))
        return {}


def rate_csv(
    input_path: Path,
    output_path: Path,
    model: str = "mistral-nemo",
    ollama_url: str = "http://localhost:11434",
    batch_size: int = 15,
) -> int:
    """Read candidates CSV, rate each term via Ollama, write rated CSV.

    Returns number of rows rated.
    """
    with input_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        log.warning("empty_csv", path=str(input_path))
        return 0

    ratings: dict[int, str] = {}
    total_batches = (len(rows) + batch_size - 1) // batch_size

    for batch_num in range(total_batches):
        start = batch_num * batch_size
        chunk = rows[start: start + batch_size]
        batch = [(start + i, row["source"], row["target"]) for i, row in enumerate(chunk)]

        log.info("rating_batch", batch=batch_num + 1, of=total_batches, terms=len(batch))
        result = _rate_batch(batch, model=model, ollama_url=ollama_url)

        for idx, rating in result.items():
            ratings[idx] = rating

        # Mark any unrated rows in this batch as uncertain
        for i in range(len(batch)):
            if start + i not in ratings:
                ratings[start + i] = "uncertain"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) + ["rating"]

    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for i, row in enumerate(rows):
            writer.writerow({**row, "rating": ratings.get(i, "uncertain")})

    log.info("rating_complete", total=len(rows), output=str(output_path))
    return len(rows)
