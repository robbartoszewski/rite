"""Tests against the real gitleaks binary — no mocking of the tool itself.

SPEC's own instruction: "Prove it against a known-bad input before you trust
it." These tests are that proof for the gitleaks-wrapping layer specifically.
Skipped (not faked) if gitleaks isn't installed, so a missing dependency
shows up as a visible skip, not a silently-passing green suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rite_ai.gate import gitleaks_runner
from tests.gate_helpers import commit_all, init_repo, write

BINARY = gitleaks_runner.find_gitleaks_binary()
requires_gitleaks = pytest.mark.skipif(BINARY is None, reason="gitleaks not installed")

GITHUB_TOKEN = "ghp_1234567890abcdefghij1234567890ABCDEF"


@requires_gitleaks
def test_scan_history_detects_real_secret(tmp_path: Path):
    init_repo(tmp_path)
    write(tmp_path, "secret.py", f'github_token = "{GITHUB_TOKEN}"\n')
    commit_all(tmp_path, "add token")
    result = gitleaks_runner.scan_history(tmp_path, BINARY)
    assert isinstance(result, list), result
    assert len(result) == 1
    assert result[0].rule_id == "github-pat"
    assert result[0].file == "secret.py"
    assert GITHUB_TOKEN not in result[0].match_preview


@requires_gitleaks
def test_scan_history_clean_repo_no_findings(tmp_path: Path):
    init_repo(tmp_path)
    write(tmp_path, "readme.md", "# hello\n")
    commit_all(tmp_path, "clean commit")
    result = gitleaks_runner.scan_history(tmp_path, BINARY)
    assert result == []


@requires_gitleaks
def test_scan_history_bad_binary_path_is_scan_error(tmp_path: Path):
    init_repo(tmp_path)
    write(tmp_path, "readme.md", "# hello\n")
    commit_all(tmp_path, "clean commit")
    result = gitleaks_runner.scan_history(tmp_path, "/nonexistent/gitleaks-binary")
    assert isinstance(result, gitleaks_runner.ScanError)


@requires_gitleaks
def test_scan_commit_messages_catches_secret_gitleaks_detect_misses(tmp_path: Path):
    """The load-bearing regression test: `gitleaks detect` alone does NOT see
    this (verified separately, empirically, while designing the module) —
    the secret is only in the commit message, never in file content. If this
    test ever fails because `scan_history` alone would also catch it, that
    means gitleaks changed behaviour upstream and the relay workaround in
    this module may no longer be necessary — investigate before deleting it."""
    init_repo(tmp_path)
    write(tmp_path, "clean.txt", "nothing to see here\n")
    commit_all(tmp_path, f"oops, pasted a token: {GITHUB_TOKEN}")

    from_history = gitleaks_runner.scan_history(tmp_path, BINARY)
    assert isinstance(from_history, list)
    assert from_history == [], (
        "gitleaks detect unexpectedly caught the commit-message secret"
    )

    from_messages = gitleaks_runner.scan_commit_messages(tmp_path, BINARY)
    assert isinstance(from_messages, list), from_messages
    assert len(from_messages) == 1
    assert from_messages[0].source == "gitleaks-commit-msg"
    assert from_messages[0].file == "<commit message>"
    assert from_messages[0].commit is not None


@requires_gitleaks
def test_scan_commit_messages_scoped_to_rev_range(tmp_path: Path):
    init_repo(tmp_path)
    write(tmp_path, "a.txt", "hello\n")
    first = commit_all(tmp_path, "clean first commit")
    write(tmp_path, "a.txt", "hello again\n")
    commit_all(tmp_path, f"leaked here: {GITHUB_TOKEN}")

    full = gitleaks_runner.scan_commit_messages(tmp_path, BINARY)
    assert isinstance(full, list) and len(full) == 1

    scoped = gitleaks_runner.scan_commit_messages(tmp_path, BINARY, rev_range=first)
    assert scoped == []
