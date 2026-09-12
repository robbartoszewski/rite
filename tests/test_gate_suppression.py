from pathlib import Path

from rite_ai.gate.findings import Finding
from rite_ai.gate.suppression import (
    Suppression,
    SuppressionError,
    append,
    apply,
    find_stale,
    parse,
)


def _finding(fp_file="a.py", fp_rule="r1", fp_line=1) -> Finding:
    return Finding(
        rule_id=fp_rule,
        description="d",
        file=fp_file,
        line=fp_line,
        commit=None,
        match_preview="x",
        source="rite-pattern",
    )


def test_parse_missing_file_returns_empty(tmp_path: Path):
    assert parse(tmp_path / "nope") == []


def test_parse_valid_entries(tmp_path: Path):
    path = tmp_path / "gitleaksignore"
    path.write_text(
        "# header comment\n"
        "-:a.py:r1:1  # test fixture, not a real credential\n"
        "\n"
        "-:b.py:r2:5  # documentation example\n"
    )
    result = parse(path)
    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0].fingerprint == "-:a.py:r1:1"
    assert result[0].reason == "test fixture, not a real credential"


def test_parse_missing_reason_is_an_error(tmp_path: Path):
    path = tmp_path / "gitleaksignore"
    path.write_text("-:a.py:r1:1\n")
    result = parse(path)
    assert isinstance(result, SuppressionError)
    assert "no reason" in result.message


def test_parse_empty_reason_after_hash_is_an_error(tmp_path: Path):
    path = tmp_path / "gitleaksignore"
    path.write_text("-:a.py:r1:1  #   \n")
    result = parse(path)
    assert isinstance(result, SuppressionError)
    assert "empty reason" in result.message


def test_apply_splits_blocking_and_suppressed():
    f1 = _finding(fp_file="a.py")
    f2 = _finding(fp_file="b.py")
    suppressions = [Suppression(fingerprint=f1.fingerprint, reason="ok", line_no=1)]
    blocking, suppressed = apply([f1, f2], suppressions)
    assert blocking == [f2]
    assert suppressed == [f1]


def test_find_stale_detects_dead_suppression():
    live = _finding(fp_file="a.py")
    stale_entry = Suppression(fingerprint="-:gone.py:r9:1", reason="old", line_no=3)
    live_entry = Suppression(fingerprint=live.fingerprint, reason="ok", line_no=1)
    stale = find_stale([stale_entry, live_entry], [live])
    assert stale == [stale_entry]


def test_append_adds_entry_with_reason(tmp_path: Path):
    path = tmp_path / "sub" / "gitleaksignore"
    append(path, "-:a.py:r1:1", "test fixture")
    content = path.read_text()
    assert "-:a.py:r1:1  # test fixture" in content


def test_append_is_additive_not_overwriting(tmp_path: Path):
    path = tmp_path / "gitleaksignore"
    append(path, "-:a.py:r1:1", "first")
    append(path, "-:b.py:r2:2", "second")
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    assert len(lines) == 2
    assert "first" in lines[0]
    assert "second" in lines[1]
