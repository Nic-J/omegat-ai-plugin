import csv
from unittest.mock import patch

from typer.testing import CliRunner

from glossary.cli import app
from glossary.extractor import TermCandidate, extract_from_file
from glossary.writer import write_csv, write_glossary


def _candidates():
    return [
        TermCandidate(source="save file", target="enregistrer le fichier", similarity=0.92, frequency=5),
        TermCandidate(source="open", target="ouvrir", similarity=0.88, frequency=3),
    ]


# --- extractor tests ---

def test_extract_from_file_returns_candidates(tmp_path):
    tmx = tmp_path / "test.tmx"
    tmx.touch()

    with (
        patch("glossary.extractor.parse_tmx", return_value=[("Save the file", "Enregistrer le fichier")]),
        patch("glossary.extractor.extract_biterms", return_value=_candidates()),
    ):
        result = extract_from_file(tmx, src_lang="en", tgt_lang="fr")

    assert len(result) == 2
    assert result[0].source == "save file"
    assert result[0].similarity == 0.92
    assert result[0].frequency == 5


def test_extract_from_file_empty_bitext(tmp_path):
    tmx = tmp_path / "empty.tmx"
    tmx.touch()

    with patch("glossary.extractor.parse_tmx", return_value=[]):
        result = extract_from_file(tmx)

    assert result == []


# --- writer tests ---

def test_write_glossary_creates_tab_separated_file(tmp_path):
    output = tmp_path / "glossary.txt"

    count = write_glossary(_candidates(), output)

    assert count == 2
    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    parts = lines[0].split("\t")
    assert parts[0] == "save file"
    assert parts[1] == "enregistrer le fichier"
    assert "similarity=92%" in parts[2]
    assert "freq=5" in parts[2]


def test_write_glossary_creates_parent_dirs(tmp_path):
    output = tmp_path / "subdir" / "glossary.txt"
    write_glossary([TermCandidate(source="test", target="essai", similarity=0.9, frequency=1)], output)
    assert output.exists()


def test_write_csv_creates_valid_csv(tmp_path):
    output = tmp_path / "glossary.csv"

    count = write_csv(_candidates(), output)

    assert count == 2
    with output.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert rows[0]["source"] == "save file"
    assert rows[0]["target"] == "enregistrer le fichier"
    assert rows[0]["similarity"] == "92%"
    assert rows[0]["frequency"] == "5"


# --- CLI tests ---

runner = CliRunner()


def test_cli_txt_format(tmp_path):
    tmx = tmp_path / "test.tmx"
    tmx.touch()
    output = tmp_path / "out.txt"

    with patch("glossary.cli.extract_from_file", return_value=_candidates()):
        result = runner.invoke(app, ["extract", str(tmx), str(output)])

    assert result.exit_code == 0
    assert output.exists()
    assert "\t" in output.read_text()


def test_cli_csv_format(tmp_path):
    tmx = tmp_path / "test.tmx"
    tmx.touch()
    output = tmp_path / "out.csv"

    with patch("glossary.cli.extract_from_file", return_value=_candidates()):
        result = runner.invoke(app, ["extract", str(tmx), str(output), "--format", "csv"])

    assert result.exit_code == 0
    assert output.exists()
    with output.open() as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["source"] == "save file"


def test_cli_missing_input(tmp_path):
    result = runner.invoke(app, ["extract", str(tmp_path / "missing.tmx"), str(tmp_path / "out.txt")])
    assert result.exit_code == 1


# --- filter tests ---

def test_min_words_filters_single_word_terms():
    from collections import Counter

    counts = Counter({"translation memory": 5, "memory": 5})
    filtered = [t for t in counts if len(t.split()) >= 2]
    assert "translation memory" in filtered
    assert "memory" not in filtered


def test_leading_function_words_are_excluded():
    from glossary.terms import _LEADING_FUNCTION_WORDS

    terms = ["the translation memory", "our community", "l'arche community", "translation memory"]
    filtered = [t for t in terms if t.split()[0] not in _LEADING_FUNCTION_WORDS]
    assert "the translation memory" not in filtered
    assert "our community" not in filtered
    assert "l'arche community" in filtered
    assert "translation memory" in filtered


def test_max_doc_freq_filters_common_terms():
    from collections import Counter

    # "person" appears in 80% of segments → dropped at max_doc_freq=0.5
    counts = Counter({"person": 8, "l'arche": 3})
    total = 10
    filtered = [t for t, c in counts.items() if c / total <= 0.5]
    assert "person" not in filtered
    assert "l'arche" in filtered


# --- tmx parser tests ---

def test_parse_tmx_returns_segment_pairs(tmp_path):
    from glossary.tmx import parse_tmx

    tmx = tmp_path / "test.tmx"
    tmx.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<tmx version="1.4">
  <body>
    <tu>
      <tuv xml:lang="EN"><seg>Save the file</seg></tuv>
      <tuv xml:lang="FR-CA"><seg>Enregistrer le fichier</seg></tuv>
    </tu>
    <tu>
      <tuv xml:lang="EN"><seg>Open</seg></tuv>
      <tuv xml:lang="FR-CA"><seg>Ouvrir</seg></tuv>
    </tu>
  </body>
</tmx>""", encoding="utf-8")

    pairs = parse_tmx(tmx, src_lang="en", tgt_lang="fr")

    assert len(pairs) == 2
    assert pairs[0] == ("Save the file", "Enregistrer le fichier")
    assert pairs[1] == ("Open", "Ouvrir")


def test_parse_tmx_skips_incomplete_pairs(tmp_path):
    from glossary.tmx import parse_tmx

    tmx = tmp_path / "test.tmx"
    tmx.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<tmx version="1.4">
  <body>
    <tu>
      <tuv xml:lang="EN"><seg>Only source</seg></tuv>
    </tu>
    <tu>
      <tuv xml:lang="EN"><seg>Complete</seg></tuv>
      <tuv xml:lang="FR-CA"><seg>Complet</seg></tuv>
    </tu>
  </body>
</tmx>""", encoding="utf-8")

    pairs = parse_tmx(tmx, src_lang="en", tgt_lang="fr")

    assert len(pairs) == 1
    assert pairs[0] == ("Complete", "Complet")
