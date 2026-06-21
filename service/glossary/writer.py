import csv
from pathlib import Path

import structlog

from .extractor import TermCandidate

log = structlog.get_logger()


def write_glossary(candidates: list[TermCandidate], output_path: Path) -> int:
    """Write candidates to OmegaT tab-separated glossary format.

    Format: source<TAB>target<TAB>comment
    Comment encodes extraction metadata so you can filter during review.
    Returns number of entries written.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as f:
        for c in candidates:
            comment = f"similarity={c.similarity:.0%}, freq={c.frequency}"
            f.write(f"{c.source}\t{c.target}\t{comment}\n")

    log.info("wrote_glossary", format="txt", entries=len(candidates), path=str(output_path))
    return len(candidates)


def write_csv(candidates: list[TermCandidate], output_path: Path) -> int:
    """Write candidates to CSV with headers, suitable for review in a spreadsheet.

    Returns number of entries written.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["source", "target", "similarity", "frequency"])
        writer.writeheader()
        for c in candidates:
            writer.writerow({
                "source": c.source,
                "target": c.target,
                "similarity": f"{c.similarity:.0%}",
                "frequency": c.frequency,
            })

    log.info("wrote_glossary", format="csv", entries=len(candidates), path=str(output_path))
    return len(candidates)
