"""End-to-end proof against known-bad input — SPEC requirement, verbatim:
"Prove it against a known-bad input before you trust it." A gate only tested
on clean input is untested.

This repo intentionally carries every leak class the gate claims to catch,
in one shot, then asserts the gate fails loudly and specifically — not just
"exit code != 0", but that each expected finding is actually present, so a
future refactor that silently drops one detector shows up here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rite_ai.gate import gitleaks_runner
from rite_ai.gate.gate import EXIT_CLEAN, EXIT_ERROR, EXIT_FAIL, EXIT_WARN, run_gate
from rite_ai.gate.suppression import append as append_suppression
from tests.gate_helpers import commit_all, init_repo, write

requires_gitleaks = pytest.mark.skipif(
    gitleaks_runner.find_gitleaks_binary() is None, reason="gitleaks not installed"
)

GITHUB_TOKEN = "ghp_1234567890abcdefghij1234567890ABCDEF"


def _known_bad_repo(root: Path) -> None:
    init_repo(root)
    write(
        root,
        ".rite/config.yaml",
        "publish_gate:\n"
        "  scan_patterns:\n"
        "    - type: regex\n"
        '      pattern: "ACME-CORP"\n'
        '      description: "customer name"\n',
    )
    # 1. a real secret gitleaks' own ruleset catches, in file content
    write(root, "src/config.py", f'github_token = "{GITHUB_TOKEN}"\n')
    # 2. a hardcoded home path (rite built-in rule)
    write(root, "src/paths.py", 'HOME = "/Users/exampleuser/dev/client-x"\n')
    # 3. a project-declared marker (customer name) in a filename
    write(root, "notes/ACME-CORP-strategy.md", "internal notes\n")
    # 4. kb/ cross-reference: a proprietary line copied from kb/ into source
    long_line = "our proprietary valuation algorithm multiplies risk by exposure"
    write(root, ".rite/kb/algorithm.md", f"# Algorithm\n\n{long_line}\n")
    write(root, "src/valuation.py", f"# {long_line}\ndef f(): pass\n")
    commit_all(root, "initial commit with several leaks")
    # 5. a secret only in a commit message (gitleaks' own blind spot)
    write(root, "clean.txt", "nothing here\n")
    commit_all(root, f"oops pasted: {GITHUB_TOKEN}")


@requires_gitleaks
def test_known_bad_repo_fails_with_every_expected_finding(tmp_path: Path):
    _known_bad_repo(tmp_path)
    report = run_gate(tmp_path)

    assert not report.errors, f"gate errored instead of scanning: {report.errors}"
    assert report.exit_code == EXIT_FAIL
    assert report.files_scanned > 0

    sources = {f.source for f in report.findings}
    rule_ids = {f.rule_id for f in report.findings}

    assert "gitleaks" in sources, "gitleaks content scan did not fire"
    assert "gitleaks-commit-msg" in sources, "commit-message relay did not fire"
    assert "rite-pattern" in sources, "built-in hardcoded-path rule did not fire"
    assert "rite-kb" in sources, "kb/ cross-reference rule did not fire"
    assert any("customer name" in f.description for f in report.findings) or any(
        "ACME" in f.match_preview for f in report.findings
    ), "project-declared marker did not fire anywhere (filename or content)"
    assert "github-pat" in rule_ids


@requires_gitleaks
def test_known_bad_repo_never_leaks_raw_secret_in_report(tmp_path: Path):
    _known_bad_repo(tmp_path)
    report = run_gate(tmp_path)
    for f in report.findings:
        assert GITHUB_TOKEN not in f.match_preview
        assert "exampleuser" not in f.match_preview


@requires_gitleaks
def test_clean_repo_is_exit_clean(tmp_path: Path):
    init_repo(tmp_path)
    write(tmp_path, "readme.md", "# hello world\n")
    commit_all(tmp_path, "clean initial commit")
    report = run_gate(tmp_path)
    assert not report.errors
    assert report.exit_code == EXIT_CLEAN
    assert report.findings == []


@requires_gitleaks
def test_suppression_removes_finding_and_requires_reason(tmp_path: Path):
    init_repo(tmp_path)
    write(tmp_path, "src/config.py", f'github_token = "{GITHUB_TOKEN}"\n')
    commit_all(tmp_path, "add token")

    first = run_gate(tmp_path)
    assert first.exit_code == EXIT_FAIL
    assert len(first.findings) == 1
    fp = first.findings[0].fingerprint

    append_suppression(
        tmp_path / ".rite" / "gitleaksignore", fp, "test fixture, not real"
    )
    second = run_gate(tmp_path)
    assert second.exit_code == EXIT_CLEAN
    assert second.findings == []
    assert len(second.suppressed) == 1
    assert second.suppressed[0].fingerprint == fp


@requires_gitleaks
def test_stale_suppression_warns_without_blocking(tmp_path: Path):
    init_repo(tmp_path)
    write(tmp_path, "readme.md", "# hello\n")
    commit_all(tmp_path, "clean commit")
    # suppress a fingerprint that matches nothing in this repo
    append_suppression(
        tmp_path / ".rite" / "gitleaksignore",
        "-:gone.py:some-rule:1",
        "no longer applies",
    )
    report = run_gate(tmp_path)
    assert report.exit_code == EXIT_WARN
    assert len(report.stale_suppressions) == 1
    assert report.findings == []


def test_malformed_suppression_file_is_an_error_not_a_silent_pass(
    tmp_path: Path, monkeypatch
):
    """A suppression file with a fingerprint but no reason must halt the gate
    with EXIT_ERROR, not silently ignore the malformed line and report clean
    — that would be exactly the "errored to stderr, stdout stayed empty,
    read as clean" failure mode this gate must prove it doesn't have."""
    init_repo(tmp_path)
    write(tmp_path, "readme.md", "# hello\n")
    commit_all(tmp_path, "clean commit")
    write(tmp_path, ".rite/gitleaksignore", "-:a.py:r1:1\n")  # no reason — malformed

    if gitleaks_runner.find_gitleaks_binary() is None:
        pytest.skip("gitleaks not installed")

    report = run_gate(tmp_path)
    assert report.exit_code == EXIT_ERROR
    assert report.errors


def test_missing_gitleaks_binary_is_an_error_not_a_silent_pass(
    tmp_path: Path, monkeypatch
):
    """If gitleaks itself is unavailable, the gate must refuse loudly
    (EXIT_ERROR) rather than silently reporting the repo clean because the
    layer that would have found anything never ran."""
    init_repo(tmp_path)
    write(tmp_path, "readme.md", "# hello\n")
    commit_all(tmp_path, "clean commit")

    monkeypatch.setattr(gitleaks_runner, "find_gitleaks_binary", lambda: None)
    # gate.py imported the function directly; patch it there too
    import rite_ai.gate.gate as gate_mod

    monkeypatch.setattr(gate_mod.gitleaks_runner, "find_gitleaks_binary", lambda: None)

    report = run_gate(tmp_path)
    assert report.exit_code == EXIT_ERROR
    assert report.findings == []
    assert report.errors


def test_partial_scan_failure_is_an_error_not_silently_merged(
    tmp_path: Path, monkeypatch
):
    """If one scan source fails mid-run, the gate must not quietly report
    whatever the other sources found as if that were the whole picture."""
    if gitleaks_runner.find_gitleaks_binary() is None:
        pytest.skip("gitleaks not installed")

    init_repo(tmp_path)
    write(tmp_path, "readme.md", "# hello\n")
    commit_all(tmp_path, "clean commit")

    import rite_ai.gate.gate as gate_mod

    def _boom(*a, **kw):
        return gitleaks_runner.ScanError("simulated failure")

    monkeypatch.setattr(gate_mod.gitleaks_runner, "scan_history", _boom)

    report = run_gate(tmp_path)
    assert report.exit_code == EXIT_ERROR
    assert report.errors
