import csv
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from glossary.rater import RATING_VALUES, _rate_batch, rate_csv


def _make_response(ratings: list[dict]) -> MagicMock:
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.json.return_value = {"message": {"content": json.dumps({"ratings": ratings})}}
    return mock


def _write_candidates(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["source", "target", "similarity", "frequency"])
        writer.writeheader()
        writer.writerows(rows)


class TestRateBatch:
    def test_returns_rating_map(self):
        batch = [(0, "lymphome", "lymphoma"), (1, "bonjour", "hello")]
        resp = _make_response([
            {"index": 0, "rating": "domain-specific"},
            {"index": 1, "rating": "common"},
        ])
        with patch("glossary.rater.httpx.post", return_value=resp):
            result = _rate_batch(batch, model="mistral-nemo", ollama_url="http://localhost:11434")
        assert result == {0: "domain-specific", 1: "common"}

    def test_unknown_rating_becomes_uncertain(self):
        batch = [(0, "foo", "bar")]
        resp = _make_response([{"index": 0, "rating": "nonsense"}])
        with patch("glossary.rater.httpx.post", return_value=resp):
            result = _rate_batch(batch, model="mistral-nemo", ollama_url="http://localhost:11434")
        assert result[0] == "uncertain"

    def test_http_error_returns_empty(self):
        import httpx2 as httpx
        batch = [(0, "term", "terme")]
        with patch("glossary.rater.httpx.post", side_effect=httpx.HTTPError("connection refused")):
            result = _rate_batch(batch, model="mistral-nemo", ollama_url="http://localhost:11434")
        assert result == {}

    def test_bad_json_returns_empty(self):
        mock = MagicMock()
        mock.raise_for_status = MagicMock()
        mock.json.return_value = {"message": {"content": "not json {"}}
        with patch("glossary.rater.httpx.post", return_value=mock):
            result = _rate_batch([(0, "a", "b")], model="mistral-nemo", ollama_url="http://localhost:11434")
        assert result == {}


class TestRateCsv:
    def test_writes_rating_column(self, tmp_path):
        input_csv = tmp_path / "candidates.csv"
        output_csv = tmp_path / "rated.csv"
        _write_candidates(input_csv, [
            {"source": "lymphome", "target": "lymphoma", "similarity": "0.92", "frequency": "3"},
        ])
        resp = _make_response([{"index": 0, "rating": "domain-specific"}])
        with patch("glossary.rater.httpx.post", return_value=resp):
            count = rate_csv(input_csv, output_csv)

        assert count == 1
        with output_csv.open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["rating"] == "domain-specific"
        assert rows[0]["source"] == "lymphome"

    def test_missing_rows_get_uncertain(self, tmp_path):
        input_csv = tmp_path / "candidates.csv"
        output_csv = tmp_path / "rated.csv"
        _write_candidates(input_csv, [
            {"source": "a", "target": "b", "similarity": "0.9", "frequency": "2"},
            {"source": "c", "target": "d", "similarity": "0.9", "frequency": "2"},
        ])
        resp = _make_response([{"index": 0, "rating": "common"}])
        with patch("glossary.rater.httpx.post", return_value=resp):
            rate_csv(input_csv, output_csv)

        with output_csv.open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["rating"] == "common"
        assert rows[1]["rating"] == "uncertain"

    def test_empty_csv_returns_zero(self, tmp_path):
        input_csv = tmp_path / "empty.csv"
        output_csv = tmp_path / "rated.csv"
        with input_csv.open("w") as f:
            f.write("source,target,similarity,frequency\n")
        count = rate_csv(input_csv, output_csv)
        assert count == 0

    def test_batching_makes_multiple_calls(self, tmp_path):
        input_csv = tmp_path / "candidates.csv"
        output_csv = tmp_path / "rated.csv"
        rows = [
            {"source": f"term{i}", "target": f"terme{i}", "similarity": "0.9", "frequency": "2"}
            for i in range(5)
        ]
        _write_candidates(input_csv, rows)

        call_count = 0

        def fake_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            payload = kwargs.get("json", {})
            user_msg = payload["messages"][1]["content"]
            ratings = []
            for line in user_msg.splitlines()[1:]:
                if ":" in line:
                    idx = int(line.split(":")[0].strip())
                    ratings.append({"index": idx, "rating": "common"})
            return _make_response(ratings)

        with patch("glossary.rater.httpx.post", side_effect=fake_post):
            rate_csv(input_csv, output_csv, batch_size=3)

        assert call_count == 2  # ceil(5/3) = 2 batches

    def test_rating_values_set(self):
        assert RATING_VALUES == {"domain-specific", "common", "uncertain"}
