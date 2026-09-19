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
from rite_ai.gate.gate import EXIT_CLEAN, EXIT_ERROR, EXIT_FAIL, run_gate
from rite_ai.gate.suppression import append as append_suppression
from tests.gate_helpers import commit_all, init_repo, write

requires_gitleaks = pytest.mark.skipif(
    gitleaks_runner.find_gitleaks_binary() is None, reason="gitleaks not installed"
)

GITHUB_TOKEN = "ghp_1234567890abcdefghij1234567890ABCDEF"

# Split so this file does not itself trip the built-in rule it is testing:
# the scan reads the source, and a contiguous `/Users/<name>/` here is a real
# finding against rite's own repo — which is how it was noticed.
A_HARDCODED_HOME = "cp build /Users/" + "robert/Sites/www\n"


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

    # Same defect, second symptom, verified rather than assumed to fall out
    # of the other fix: ONE stale entry — a fingerprint matching nothing —
    # stopped every push and every release run until somebody deleted it.
    assert len(report.stale_suppressions) == 1
    assert report.findings == []
    assert report.exit_code == 0, "a stale suppression must not block a push"
    assert report.outcome == "warn", "and must still be reported"
    assert report.exit_code_for(strict=True) != 0, "--strict must block"


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


class TestWhatStillRunsWithoutGitleaks:
    """rite does not reimplement secret detection, so without gitleaks it
    cannot call a tree safe — that stays EXIT_ERROR. But the built-in
    hardcoded-path rules (§11.3 calls them never optional), the user's own
    declared patterns and the kb/ cross-reference need no gitleaks at all,
    and returning early ran none of them: someone on a machine without
    gitleaks got no answer rather than a partial one, then a second round of
    failures once they installed it."""

    @staticmethod
    def _without_gitleaks(monkeypatch) -> None:
        import rite_ai.gate.gate as gate_mod

        monkeypatch.setattr(
            gate_mod.gitleaks_runner, "find_gitleaks_binary", lambda: None
        )

    def _repo_with_a_hardcoded_path(self, root: Path) -> None:
        init_repo(root)
        write(root, "deploy.sh", A_HARDCODED_HOME)
        commit_all(root, "deploy script")

    def test_the_checks_that_need_no_gitleaks_still_run(self, tmp_path, monkeypatch):
        self._repo_with_a_hardcoded_path(tmp_path)
        self._without_gitleaks(monkeypatch)

        report = run_gate(tmp_path)

        assert report.exit_code == EXIT_ERROR, "a missing scanner is still an error"
        assert report.findings == [], "a partial scan must never become the verdict"
        assert [f.file for f in report.partial_findings] == ["deploy.sh"], (
            "rite's own path rules did not run without gitleaks"
        )

    def test_nothing_found_still_is_not_clean(self, tmp_path, monkeypatch):
        """The dangerous reading of an empty list."""
        init_repo(tmp_path)
        write(tmp_path, "readme.md", "# hello\n")
        commit_all(tmp_path, "clean commit")
        self._without_gitleaks(monkeypatch)

        report = run_gate(tmp_path)

        assert report.partial_findings == []
        assert report.exit_code == EXIT_ERROR
        assert report.status == "error"

    def test_a_decision_already_made_is_not_raised_again(self, tmp_path, monkeypatch):
        self._repo_with_a_hardcoded_path(tmp_path)
        self._without_gitleaks(monkeypatch)
        found = run_gate(tmp_path).partial_findings
        assert found, "nothing to suppress — the fixture stopped working"
        append_suppression(
            tmp_path / ".rite" / "gitleaksignore",
            found[0].content_fingerprint or found[0].fingerprint,
            "the deploy box is mine and the path is public",
        )

        report = run_gate(tmp_path)

        assert report.partial_findings == [], "a suppressed finding came back"
        assert report.exit_code == EXIT_ERROR

    def test_an_unparsable_suppression_file_shows_everything(
        self, tmp_path, monkeypatch
    ):
        """Best effort, and it fails towards showing more, not less."""
        self._repo_with_a_hardcoded_path(tmp_path)
        write(tmp_path, ".rite/gitleaksignore", "-:a.py:r1:1\n")  # no reason
        self._without_gitleaks(monkeypatch)

        report = run_gate(tmp_path)

        assert report.partial_findings, "findings were hidden by a broken file"

    def test_the_report_refuses_to_read_as_a_verdict(self, tmp_path, monkeypatch):
        from rite_ai.gate.gate import format_report

        self._repo_with_a_hardcoded_path(tmp_path)
        self._without_gitleaks(monkeypatch)

        text = format_report(run_gate(tmp_path))

        assert "ERROR — the gate could not complete" in text
        assert "not a verdict" in text
        assert "deploy.sh" in text


class TestThePartialPathCannotLie:
    """Round 2 of the review of the partial-scan path. Every one of these
    failed before the fix it pins."""

    @staticmethod
    def _without_gitleaks(monkeypatch) -> None:
        import rite_ai.gate.gate as gate_mod

        monkeypatch.setattr(
            gate_mod.gitleaks_runner, "find_gitleaks_binary", lambda: None
        )

    def test_findings_the_exit_code_cannot_see_are_impossible(self):
        """`partial_findings` is rendered only beside an error, so a report
        carrying them without one would print nothing and exit clean — a
        silent sink for real findings."""
        from rite_ai.gate.findings import Finding
        from rite_ai.gate.gate import GateReport

        report = GateReport(
            partial_findings=[
                Finding(
                    rule_id="rite-path",
                    description="Hardcoded home directory",
                    file="deploy.sh",
                    line=1,
                    commit=None,
                    match_preview="[redacted]",
                    source="rite-path",
                    digest="abc123",
                )
            ]
        )

        assert report.exit_code == EXIT_ERROR
        assert report.status != "clean"

    def test_the_banner_does_not_wave_away_a_confirmed_secret(self):
        """The wording used to say the checks that did not run "are the ones
        that detect secrets". Any source can be the one that failed, so that
        sentence printed a gitleaks-confirmed token under a line telling the
        reader to discount it."""
        from rite_ai.gate.findings import Finding
        from rite_ai.gate.gate import GateReport, format_report

        report = GateReport(
            errors=["gitleaks commit-message scan failed: simulated"],
            partial_findings=[
                Finding(
                    rule_id="github-pat",
                    description="Uncovered a GitHub Personal Access Token",
                    file="src/config.py",
                    line=1,
                    commit=None,
                    match_preview="[redacted]",
                    source="gitleaks",
                    digest="abc123",
                )
            ],
        )

        text = format_report(report)

        assert "detect secrets" not in text, text
        assert "not a verdict" in text
        assert "has not been fully scanned" in text

    def test_the_missing_scanner_is_still_named_when_something_else_fails_too(
        self, tmp_path, monkeypatch
    ):
        """The `install gitleaks` line was dropped by the next early return,
        so the user read about the second problem and never the first."""
        self._without_gitleaks(monkeypatch)

        report = run_gate(tmp_path)  # not a git repository

        joined = " ".join(report.errors)
        assert "gitleaks is not installed" in joined, report.errors
        assert len(report.errors) >= 2, report.errors

    def test_a_suppression_file_that_cannot_be_decoded_hides_nothing(
        self, tmp_path, monkeypatch
    ):
        """`parse` reads the file with no decoding guarantee, so this raised
        out of the best-effort helper and took the findings AND the install
        instructions with it."""
        init_repo(tmp_path)
        write(tmp_path, "deploy.sh", A_HARDCODED_HOME)
        commit_all(tmp_path, "deploy script")
        (tmp_path / ".rite").mkdir(exist_ok=True)
        (tmp_path / ".rite" / "gitleaksignore").write_bytes(
            b"-:a.py:r1:1  # caf\xe9 reason\n"
        )
        self._without_gitleaks(monkeypatch)

        report = run_gate(tmp_path)

        assert report.partial_findings, "findings were lost to a decode error"
        assert any("gitleaks is not installed" in e for e in report.errors)

    def test_one_finding_is_not_listed_twice(self, tmp_path, monkeypatch):
        """A declared pattern duplicating a built-in rule produced the same
        finding twice; the complete path has always deduped."""
        init_repo(tmp_path)
        write(
            tmp_path,
            ".rite/config.yaml",
            "publish_gate:\n"
            "  scan_patterns:\n"
            "    - type: path\n"
            '      pattern: "/Users/[A-Za-z0-9._-]+/"\n'
            "      description: Hardcoded macOS home directory path\n",
        )
        write(tmp_path, "deploy.sh", A_HARDCODED_HOME)
        commit_all(tmp_path, "deploy script")
        self._without_gitleaks(monkeypatch)

        report = run_gate(tmp_path)

        keys = [(f.fingerprint, f.digest) for f in report.partial_findings]
        assert len(keys) == len(set(keys)), keys

    def test_a_long_list_is_capped_like_every_other_advisory_list(self):
        from rite_ai.gate.findings import Finding
        from rite_ai.gate.gate import GateReport, format_report

        report = GateReport(
            errors=["gitleaks is not installed"],
            partial_findings=[
                Finding(
                    rule_id="rite-path",
                    description="Hardcoded home directory",
                    file=f"file{n}.sh",
                    line=1,
                    commit=None,
                    match_preview="[redacted]",
                    source="rite-path",
                    digest=f"d{n}",
                )
                for n in range(25)
            ],
        )

        text = format_report(report)

        assert "… and 15 more" in text
        assert "file24.sh" not in text

    def test_a_partial_finding_can_be_suppressed_from_what_is_printed(self):
        """Without a fingerprint in the output there is no way to act on one."""
        from rite_ai.gate.findings import Finding
        from rite_ai.gate.gate import GateReport, format_report

        report = GateReport(
            errors=["gitleaks is not installed"],
            partial_findings=[
                Finding(
                    rule_id="rite-path",
                    description="Hardcoded home directory",
                    file="deploy.sh",
                    line=1,
                    commit=None,
                    match_preview="[redacted]",
                    source="rite-path",
                    digest="abc123",
                )
            ],
        )

        assert "fingerprint:" in format_report(report)


class TestNothingIsLostToTheSuppressionFile:
    """Round 2. A broken `.rite/gitleaksignore` used to take every real
    finding in the tree down with it — the same information loss the missing
    gitleaks path had, on the path that is far commoner: a typo'd line."""

    def _repo_with_a_finding(self, root: Path, monkeypatch) -> None:
        import rite_ai.gate.gate as gate_mod

        init_repo(root)
        write(root, "deploy.sh", A_HARDCODED_HOME)
        commit_all(root, "deploy script")
        monkeypatch.setattr(
            gate_mod.gitleaks_runner, "find_gitleaks_binary", lambda: None
        )

    def test_a_typod_entry_does_not_hide_the_tree(self, tmp_path, monkeypatch):
        self._repo_with_a_finding(tmp_path, monkeypatch)
        write(tmp_path, ".rite/gitleaksignore", "-:a.py:r1:1\n")  # no reason

        report = run_gate(tmp_path)

        assert report.exit_code == EXIT_ERROR
        assert report.partial_findings, "a parse error swallowed the findings"

    def test_what_a_suppression_hid_is_counted_in_the_report(
        self, tmp_path, monkeypatch
    ):
        from rite_ai.gate.gate import format_report

        self._repo_with_a_finding(tmp_path, monkeypatch)
        found = run_gate(tmp_path).partial_findings
        append_suppression(
            tmp_path / ".rite" / "gitleaksignore",
            found[0].content_fingerprint or found[0].fingerprint,
            "the deploy box is mine",
        )

        text = format_report(run_gate(tmp_path))

        assert "not listed" in text, "a filtered list read as the whole one"
        assert "1 finding(s) matched" in text
        assert ".rite/gitleaksignore" in text, "no clue where the decision lives"

    def test_an_invalid_config_still_names_the_missing_scanner(
        self, tmp_path, monkeypatch
    ):
        """The sibling early return was pinned; this one was not."""
        self._repo_with_a_finding(tmp_path, monkeypatch)
        write(tmp_path, ".rite/config.yaml", "publish_gate: [not a mapping\n")

        report = run_gate(tmp_path)

        joined = " ".join(report.errors)
        assert "gitleaks is not installed" in joined, report.errors
        assert "config.yaml" in joined, report.errors

    def test_findings_are_printed_wherever_the_exit_code_can_see_them(self):
        """`exit_code` reads `partial_findings`; so must both renderers, or a
        report can exit 3 while printing nothing at all."""
        from rite_ai.gate.findings import Finding
        from rite_ai.gate.gate import GateReport, format_report

        report = GateReport(
            partial_findings=[
                Finding(
                    rule_id="rite-path",
                    description="Hardcoded home directory",
                    file="deploy.sh",
                    line=1,
                    commit=None,
                    match_preview="[redacted]",
                    source="rite-path",
                    digest="abc123",
                )
            ]
        )

        text = format_report(report)

        assert "deploy.sh" in text, text
        assert report.exit_code == EXIT_ERROR
