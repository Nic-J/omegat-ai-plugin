"""
Preset import configurations for known terminology sources.

Use with `glossary import-terminology --preset <name>` to import Termium or
OQLF data without specifying column mappings manually. Each preset knows the
source's CSV structure and applies any source-specific pre-processing.
"""
import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import structlog

from .terminology import import_rows

log = structlog.get_logger()


@dataclass
class ImportPreset:
    name: str
    description: str
    column_mapping: dict[str, str]
    delimiter: str = ","
    encoding: str = "utf-8-sig"
    preprocess: Callable[[list[dict]], list[dict]] | None = None


def _oqlf_preprocess(rows: list[dict]) -> list[dict]:
    """
    Expand OQLF's semicolon-separated term variants into individual rows and
    strip grammatical annotations like "(n. f.)" from French terms.

    Each row's Termes_francais and Termes_anglais are parallel semicolon-
    separated lists; pairing them positionally gives all variant pairs.
    """
    result = []
    for row in rows:
        en_terms = [t.strip() for t in (row.get("Termes_anglais") or "").split(";")]
        fr_terms = [
            re.sub(r"\s*\(.*?\)\s*", "", t).strip()
            for t in (row.get("Termes_francais") or "").split(";")
        ]
        domain = row.get("Domaines", "")
        for en, fr in zip(en_terms, fr_terms):
            if en and fr:
                result.append({"Termes_anglais": en, "Termes_francais": fr, "Domaines": domain})
    return result


PRESETS: dict[str, ImportPreset] = {
    "termium": ImportPreset(
        name="termium",
        description=(
            "TERMIUM Plus — Government of Canada bilingual terminology (OGL-Canada). "
            "Download subject ZIPs from "
            "open.canada.ca/data/en/dataset/94fc74d6-9b9a-4c2e-9c6c-45a5092453aa, "
            "unzip, then import each CSV."
        ),
        column_mapping={
            "source_term": "TERM_EN",
            "target_term": "TERME_FR",
            "subject": "SUBJECT_EN",
        },
    ),
    "oqlf": ImportPreset(
        name="oqlf",
        description=(
            "OQLF Grand dictionnaire terminologique — officialized Quebec French terms. "
            "Download fiches_recentes_signees_oqlf_*.csv via the CKAN API: "
            "donneesquebec.ca/recherche/api/3/action/package_show?id=donnees-linguistiques"
        ),
        column_mapping={
            "source_term": "Termes_anglais",
            "target_term": "Termes_francais",
            "subject": "Domaines",
        },
        preprocess=_oqlf_preprocess,
    ),
}


def import_preset(
    preset_name: str,
    csv_path: Path,
    source_lang: str = "EN",
    target_lang: str = "FR",
    db_path: Path | None = None,
) -> int:
    """
    Import a CSV file using a named preset (termium, oqlf).
    Applies source-specific pre-processing before inserting into the index.
    Returns the number of rows inserted.
    """
    preset = PRESETS.get(preset_name)
    if preset is None:
        raise ValueError(f"Unknown preset {preset_name!r}. Available: {sorted(PRESETS)}")

    with open(csv_path, encoding=preset.encoding, newline="") as f:
        rows = list(csv.DictReader(f, delimiter=preset.delimiter))

    if preset.preprocess:
        rows = preset.preprocess(rows)

    count = import_rows(
        rows,
        column_mapping=preset.column_mapping,
        source_label=preset.name,
        source_lang=source_lang,
        target_lang=target_lang,
        db_path=db_path,
    )
    log.info("preset_imported", preset=preset_name, path=str(csv_path), count=count)
    return count
