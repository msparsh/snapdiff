import json
from pathlib import Path
import duckdb
import pytest

from snapdiff.cli import main


@pytest.fixture
def sample_csvs(tmp_path: Path):
    base_file = tmp_path / "base.csv"
    curr_clean = tmp_path / "curr_clean.csv"
    curr_breaking = tmp_path / "curr_breaking.csv"

    # Baseline CSV
    base_file.write_text("id,val,category\n1,10.0,A\n2,20.0,B\n3,30.0,A\n", encoding="utf-8")

    # Clean Current CSV
    curr_clean.write_text("id,val,category\n1,10.0,A\n2,20.0,B\n3,30.0,A\n", encoding="utf-8")

    # Breaking Current CSV (dropped category column, duplicated id)
    curr_breaking.write_text("id,val\n1,10.0\n1,10.0\n", encoding="utf-8")

    return base_file, curr_clean, curr_breaking


def test_cli_pass_identical(sample_csvs, tmp_path: Path):
    base_file, curr_clean, _ = sample_csvs
    report_file = tmp_path / "report.md"

    exit_code = main([
        str(base_file),
        str(curr_clean),
        "--out", str(report_file),
        "--fail-on", "breaking",
    ])

    assert exit_code == 0
    assert report_file.exists()
    content = report_file.read_text(encoding="utf-8")
    assert "🟢 **PASS**" in content


def test_cli_fail_breaking(sample_csvs, tmp_path: Path):
    base_file, _, curr_breaking = sample_csvs
    report_file = tmp_path / "report.md"

    exit_code = main([
        str(base_file),
        str(curr_breaking),
        "--out", str(report_file),
        "--fail-on", "breaking",
    ])

    assert exit_code == 1
    assert report_file.exists()
    content = report_file.read_text(encoding="utf-8")
    assert "🔴 **FAIL**" in content
    assert "Column removed" in content


def test_cli_json_format(sample_csvs, tmp_path: Path):
    base_file, curr_clean, _ = sample_csvs
    report_file = tmp_path / "report.json"

    exit_code = main([
        str(base_file),
        str(curr_clean),
        "--out", str(report_file),
        "--format", "json",
    ])

    assert exit_code == 0
    assert report_file.exists()
    data = json.loads(report_file.read_text(encoding="utf-8"))
    assert data["metadata"]["passed"] is True
    assert "summary" in data


def test_cli_invalid_path(tmp_path: Path):
    bad_base = tmp_path / "non_existent.parquet"
    curr = tmp_path / "curr.parquet"

    exit_code = main([str(bad_base), str(curr)])
    assert exit_code == 2
