"""`python -m rite_ai.cli.main` imported the module, found nothing to run, and
exited 0 printing nothing.

Exit 0 with no output is indistinguishable from a command that succeeded —
the first line of this project's own review checklist ("a broken check that
reports 'clean' is worse than no check at all"), in the CLI's own entry point.
The `-m` form is an idiom this codebase already uses (`python -m rite_ai.gate`,
see `rite_ai/gate/__main__.py`), so a reader will reasonably try it here.

These run it as a real subprocess. In-process `CliRunner` cannot reach the
`if __name__ == "__main__"` block at all — it is only executed when the module
is run as `__main__`, which is exactly the path that was broken. A comment on
that block claimed it was "exercised by subprocess in tests" before any such
test existed; this file is what makes the claim true.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "rite_ai.cli.main", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_the_module_form_actually_runs_the_cli():
    proc = _run("--version")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("rite, version"), proc.stdout


def test_it_does_not_exit_zero_printing_nothing():
    """The defect itself, stated as its own assertion: any invocation must
    either produce output or fail loudly. Silence with exit 0 is the thing."""
    proc = _run()  # no arguments — click prints usage
    assert proc.stdout.strip() or proc.stderr.strip(), "exited silently"


def test_an_unknown_subcommand_fails_loudly_rather_than_quietly():
    proc = _run("no-such-command")
    assert proc.returncode != 0
    assert "no-such-command" in (proc.stdout + proc.stderr)


def test_a_real_subcommand_works_through_it():
    """Not just `--version`, which click handles before dispatch."""
    proc = _run("review")
    assert proc.returncode == 0, proc.stderr
    assert "# checklist:" in proc.stdout
