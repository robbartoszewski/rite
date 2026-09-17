"""`rite init` announces a publish gate that cannot run on this machine.

The gate is built on gitleaks and deliberately does not reimplement secret
detection. `rite init` installs a pre-push hook and a CI workflow that both
run it, prints a `✓` for each, and never checks whether gitleaks is there.

On a machine without it the hook fails closed — exit 3, git aborts the push —
so the reader's first news of the dependency is a push they cannot make, in
the middle of trying to ship. Measured on the first third-party run of rite:
no gitleaks and no Homebrew, and the only instruction rite ever gave her named
Homebrew. She hand-grepped the staged files by hand instead.

Saying it at init costs one line, at the moment when installing a tool is
just another setup step. It is the same defect §11.5.1 records for the hook
itself: a `✓` for a layer that will not run.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from rite_ai.cli.init import run_init
from rite_ai.gate import gitleaks_runner


@pytest.fixture
def project(tmp_path: Path) -> Path:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def _init(root: Path, capsys, *, gitleaks: str | None) -> str:
    with (
        patch.object(gitleaks_runner, "find_gitleaks_binary", return_value=gitleaks),
        patch("rite_ai.sandbox.platform_can_sandbox", return_value=False),
    ):
        assert run_init(root, yes=True).status == "created"
    return capsys.readouterr().out


class TestInitSaysSo:
    def test_it_names_gitleaks_when_it_is_absent(self, project, capsys):
        """THE DEFECT: init reported both gate layers installed and said
        nothing about the tool neither can run without."""
        out = _init(project, capsys, gitleaks=None)

        assert "gitleaks" in out, out

    def test_it_says_pushes_are_blocked_not_merely_unscanned(self, project, capsys):
        """The two outcomes need different actions from the reader, and the
        one that applies with a hook installed is the harsher one. "Nothing
        scans" would send them looking for a scanner; they are about to be
        unable to push at all."""
        out = _init(project, capsys, gitleaks=None)

        assert "blocked" in out

    def test_it_says_ci_is_still_covered_because_it_installs_its_own(
        self, project, capsys
    ):
        """The generated workflow installs its own pinned gitleaks, so the
        remote layer really is unaffected. Stated, not merely not-denied:
        a reader told the gate is dead needs to know which half."""
        out = _init(project, capsys, gitleaks=None)

        assert "installs its own copy" in out
        assert "not covering" not in out

    def test_it_says_ci_is_uncovered_when_the_workflow_is_not_rites(
        self, project, capsys
    ):
        """And when CI genuinely is not running the gate, it must not offer
        the reassurance. Saying "CI has you covered" when it does not is the
        failure this file exists about, one layer over."""
        wf = project / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "publish-gate.yml").write_text("name: someone else's\non: push\n")

        out = _init(project, capsys, gitleaks=None)

        assert "not covering" in out
        assert "installs its own copy" not in out

    def test_it_says_nothing_when_gitleaks_is_installed(
        self, project, tmp_path, capsys
    ):
        """Asserted as a DIFFERENCE. "Does not mention gitleaks" passes on
        any build that mentions nothing at all, including the one this test
        was written against."""
        import subprocess

        other = tmp_path / "other"
        other.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=other, check=True)

        absent = _init(project, capsys, gitleaks=None)
        present = _init(other, capsys, gitleaks="/usr/local/bin/gitleaks")

        assert "gitleaks is not installed" in absent
        assert "gitleaks is not installed" not in present


class TestTheRemedyIsSaidTheSameWayEverywhere:
    """Three places report this: the gate's own error, `rite doctor`, and now
    `rite init`. Two of them had already drifted apart, and both named only
    Homebrew at the point a tester had no Homebrew."""

    def test_it_names_the_release_binaries_before_a_package_manager(self):
        remedy = gitleaks_runner.HOW_TO_INSTALL

        assert remedy.index("release binaries") < remedy.index("package manager")
        assert "github.com/gitleaks/gitleaks" in remedy

    def test_init_and_the_gate_use_the_same_words(self, project, capsys):
        from rite_ai.gate.gate import format_report, run_gate

        out = _init(project, capsys, gitleaks=None)
        with patch.object(gitleaks_runner, "find_gitleaks_binary", return_value=None):
            gate = format_report(run_gate(project))

        assert gitleaks_runner.HOW_TO_INSTALL in out
        assert gitleaks_runner.HOW_TO_INSTALL in gate

    def test_the_gate_error_does_not_argue_with_its_own_history(self):
        """It ended with "naming only the package manager was a dead end for
        a tester who had neither" — a note to rite's authors, printed to a
        stranger who cannot place any of it."""
        import tempfile

        from rite_ai.gate.gate import format_report, run_gate

        with patch.object(gitleaks_runner, "find_gitleaks_binary", return_value=None):
            out = format_report(run_gate(Path(tempfile.mkdtemp())))

        assert "tester" not in out, out
