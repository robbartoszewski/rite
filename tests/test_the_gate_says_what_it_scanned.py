"""The gate must not report "clean" about work it never looked at.

🔴 SCRUM-63. Every gate run reported `commits_scanned=0` with no error, and
the configured `publish_gate.gitleaks_config` did not exist — which read as a
hollow gate: a secret scanner that silently checks nothing and passes. Three
separate things were true, and only the last two are defects:

1. The history scan is REAL. A secret committed and then deleted is still
   caught from history, with the exact revision range `rite deliver` uses.
   `test_a_secret_only_in_history_is_caught` is that proof, standing because
   nothing in the suite asserted it and the question had to be answered by
   hand.
2. `commits_scanned` was declared on `GateReport` and never once assigned,
   so every report published a zero that no code had computed. An observable
   a tool publishes and does not compute is worse than none, because it is
   believed — it was, and it cost an investigation.
3. A `gitleaks_config` that does not exist fell back to gitleaks' defaults in
   silence. For the DEFAULT path that is correct and harmless; for a path
   someone deliberately typed it is the hole, because the rules they asked
   for vanish and the gate reports clean against weaker ones.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rite_ai.gate import gitleaks_runner
from rite_ai.gate.gate import (
    EXIT_CLEAN,
    EXIT_ERROR,
    EXIT_FAIL,
    format_report,
    run_gate,
)
from tests.gate_helpers import commit_all, init_repo, write

requires_gitleaks = pytest.mark.skipif(
    gitleaks_runner.find_gitleaks_binary() is None, reason="gitleaks not installed"
)

A_PLANTED_TOKEN = "ghp_" + "7Qk2mZr9VtXbN4pLs8DwH3JcY6FaE1RuG0Oi"


def _repo_with_a_buried_secret(root: Path) -> str:
    """A branch that ADDS a secret and then DELETES it, so the working tree is
    clean and only history is not. Returns the branch name."""
    init_repo(root)
    write(root, "README.md", "hello\n")
    commit_all(root, "base")
    subprocess.run(["git", "branch", "-M", "main"], cwd=root, check=True)
    subprocess.run(["git", "checkout", "-q", "-b", "work"], cwd=root, check=True)
    write(root, "creds.py", f'TOKEN = "{A_PLANTED_TOKEN}"\n')
    commit_all(root, "add creds")
    subprocess.run(["git", "rm", "-q", "creds.py"], cwd=root, check=True)
    write(root, "feature.txt", "feature\n")
    commit_all(root, "remove creds, add feature")
    assert not (root / "creds.py").exists()
    return "work"


@requires_gitleaks
def test_a_secret_only_in_history_is_caught(tmp_path):
    """The question SCRUM-63 asked, answered as a test: plant a secret in a
    historical commit, scan the range `rite deliver` scans
    (`<module.branch>..<branch>`), and the gate must BLOCK."""
    _repo_with_a_buried_secret(tmp_path)

    report = run_gate(tmp_path, rev_range="main..work")

    assert report.errors == []
    assert report.exit_code == EXIT_FAIL
    rules = {f.rule_id for f in report.findings}
    assert "github-pat" in rules
    # From history, not the tree — the tree does not contain the file.
    assert all(f.commit for f in report.findings if f.rule_id == "github-pat")


@requires_gitleaks
def test_the_report_counts_the_commits_it_walked(tmp_path):
    """`commits_scanned` is computed, not defaulted."""
    _repo_with_a_buried_secret(tmp_path)

    report = run_gate(tmp_path, rev_range="main..work")

    assert report.commits_scanned == 2
    assert "2 commit(s)" in format_report(report)


@requires_gitleaks
def test_an_empty_range_does_not_report_a_bare_clean(tmp_path):
    """The run that produced the original misreading: a pre-push range
    containing no commits. "clean" is the right verdict — nothing is being
    published — but it must not be offered as a statement about history the
    scan never walked."""
    init_repo(tmp_path)
    write(tmp_path, "README.md", "hello\n")
    commit_all(tmp_path, "base")

    report = run_gate(tmp_path, rev_range="HEAD..HEAD")

    assert report.commits_scanned == 0
    assert report.exit_code == EXIT_CLEAN
    text = format_report(report)
    assert "0 commit(s)" in text
    assert "NO history was in range" in text


@requires_gitleaks
def test_a_missing_default_gitleaks_config_is_not_an_error_but_is_said(tmp_path):
    """`rite init` writes `gitleaks_config: .rite/gitleaks.toml` into every
    config.yaml and does not create the file, so this is the ordinary state
    of a healthy project. It must keep working — and must say which ruleset
    it actually applied, which is the part that was silent."""
    init_repo(tmp_path)
    write(tmp_path, "README.md", "hello\n")
    commit_all(tmp_path, "base")
    assert not (tmp_path / ".rite" / "gitleaks.toml").exists()

    report = run_gate(tmp_path)

    assert report.errors == []
    assert report.exit_code == EXIT_CLEAN
    assert "gitleaks' defaults" in format_report(report)


@requires_gitleaks
def test_a_missing_chosen_gitleaks_config_is_a_hard_error(tmp_path):
    """The hole. Pointed at rules that are not there, the gate must refuse —
    not quietly enforce gitleaks' defaults and report clean, which tells
    someone their stricter rules passed when they never ran."""
    init_repo(tmp_path)
    write(
        tmp_path,
        ".rite/config.yaml",
        "publish_gate:\n  gitleaks_config: .rite/strict.toml\n",
    )
    write(tmp_path, "README.md", "hello\n")
    commit_all(tmp_path, "base")

    report = run_gate(tmp_path)

    assert report.exit_code == EXIT_ERROR
    assert any(".rite/strict.toml" in e for e in report.errors)


@requires_gitleaks
def test_a_full_audit_covers_commit_messages_on_every_ref(tmp_path):
    """🔴 SCRUM-63. `rite publish check` means "safe for the whole history to
    become public". gitleaks' own `detect` walks every ref, but the
    commit-message relay ran a bare `git log` and saw HEAD's ancestry only —
    so an unmerged branch was audited for secrets in file content and not for
    secrets in its commit messages. Measured and reported clean.
    """
    init_repo(tmp_path)
    write(tmp_path, "README.md", "hello\n")
    commit_all(tmp_path, "base")
    subprocess.run(["git", "checkout", "-q", "-b", "side"], cwd=tmp_path, check=True)
    write(tmp_path, "b.txt", "b\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", f"deploy key {A_PLANTED_TOKEN}"],
        cwd=tmp_path,
        check=True,
    )
    # Back to a branch the secret-bearing commit is NOT reachable from.
    subprocess.run(["git", "checkout", "-q", "-"], cwd=tmp_path, check=True)

    report = run_gate(tmp_path)

    assert report.errors == []
    assert report.exit_code == EXIT_FAIL
    assert any("github-pat" == f.rule_id for f in report.findings)


@requires_gitleaks
def test_a_range_scan_stays_inside_its_range(tmp_path):
    """The other side of that widening: a `rev_range` is the pre-push hook
    naming exactly what it is about to publish, and it must NOT grow to refs
    the push does not touch."""
    init_repo(tmp_path)
    write(tmp_path, "README.md", "hello\n")
    commit_all(tmp_path, "base")
    subprocess.run(["git", "branch", "-M", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "checkout", "-q", "-b", "side"], cwd=tmp_path, check=True)
    write(tmp_path, "b.txt", "b\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", f"deploy key {A_PLANTED_TOKEN}"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "checkout", "-q", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "checkout", "-q", "-b", "work"], cwd=tmp_path, check=True)
    write(tmp_path, "feature.txt", "feature\n")
    commit_all(tmp_path, "an innocent commit")

    report = run_gate(tmp_path, rev_range="main..work")

    assert report.errors == []
    assert report.exit_code == EXIT_CLEAN, [f.rule_id for f in report.findings]
