"""Tests for csv2json CLI."""

import json
import os
import subprocess
import tempfile

CLI = os.path.join(os.path.dirname(__file__), "..", "cli.py")


def run_cli(csv_content):
    """Write csv_content to a temp file and run cli.py, returning parsed JSON."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8"
    ) as f:
        f.write(csv_content)
        tmp = f.name
    try:
        result = subprocess.run(
            ["python3", CLI, tmp], capture_output=True, text=True
        )
        assert result.returncode == 0, f"cli.py failed: {result.stderr}"
        return json.loads(result.stdout), result.stdout
    finally:
        os.unlink(tmp)


def run_cli_pretty(csv_content):
    """Same as run_cli but with --pretty flag."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8"
    ) as f:
        f.write(csv_content)
        tmp = f.name
    try:
        result = subprocess.run(
            ["python3", CLI, "--pretty", tmp], capture_output=True, text=True
        )
        assert result.returncode == 0, f"cli.py failed: {result.stderr}"
        return json.loads(result.stdout), result.stdout
    finally:
        os.unlink(tmp)


def test_valid_csv_exits_zero():
    """VAL-001: CLI runs successfully on a valid CSV file."""
    data, _ = run_cli("name,age\nJohn,30\n")
    assert isinstance(data, list)
    assert len(data) == 1


def test_quoted_fields_with_commas():
    """VAL-002: Quoted fields containing commas are parsed correctly."""
    data, _ = run_cli('name,age\n"Smith, John",30\n')
    assert data[0]["name"] == "Smith, John"


def test_missing_values_become_empty_strings():
    """VAL-003: Missing values are converted to empty strings."""
    data, _ = run_cli("a,b\n1,\n2\n")
    assert data[0]["b"] == ""
    assert data[1]["b"] == ""


def test_pretty_flag_produces_indented_output():
    """VAL-004: --pretty flag produces indented JSON."""
    _, raw = run_cli_pretty("a\n1\n")
    assert "\n" in raw
    assert "  " in raw
