"""A test file pytest does not collect is indistinguishable from one that passed.

Measured, and not theoretically: a file of seven tests shipped as
`tests/nt.py` — a name left behind by a stash-and-copy while checking that
they failed first. `testpaths` plus pytest's default `python_files` means
only `test_*.py` and `*_test.py` are collected, so those seven never ran.
The suite went green, the commit said the behaviour was covered, and a
remedy naming a command that does not exist (`rite credential adopt`)
reached user-facing output because the test that would have caught it was
never executed.

It is the same shape as the other four this repository has met — a gate
reporting itself installed while inert, a hook git never reads, a release
history nobody regenerated, a conformance assertion counting failures
without naming them. The report of a check that did not run looks exactly
like the report of a check that passed.

Prose cannot hold this one. The rule is mechanisable, so it is mechanised:
every file under `tests/` that defines tests must be a file pytest
collects, and a module that is imported rather than collected has to say
so out loud by being named below.
"""

from __future__ import annotations

import fnmatch
import re
import tomllib
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent

# Imported by test modules, never collected on their own. Each name is a
# deliberate exception, which is the point: adding one is an act, and
# forgetting to rename a test file is not.
HELPERS = {
    "conftest.py",
    "burst.py",
    "kv_backend.py",
    "kv_server.py",
    "state_layer_conformance.py",
    "gate_helpers.py",
}

DEFINES_TESTS = re.compile(r"^\s*(def test_|class Test)", re.MULTILINE)


def _collected_patterns() -> list[str]:
    """What pytest will actually collect here, read from the project's own
    configuration rather than assumed — if someone sets `python_files`,
    this test must follow them, not a copy of the default."""
    config = tomllib.loads((REPO / "pyproject.toml").read_text())
    pytest_config = config.get("tool", {}).get("pytest", {}).get("ini_options", {})
    patterns = pytest_config.get("python_files")
    if isinstance(patterns, str):
        return patterns.split()
    return list(patterns) if patterns else ["test_*.py", "*_test.py"]


def test_every_file_that_defines_tests_is_one_pytest_collects():
    patterns = _collected_patterns()
    uncollected = []
    for path in sorted(TESTS.rglob("*.py")):
        if path.name in HELPERS or "__pycache__" in path.parts:
            continue
        if not DEFINES_TESTS.search(path.read_text(encoding="utf-8", errors="replace")):
            continue
        if not any(fnmatch.fnmatch(path.name, p) for p in patterns):
            uncollected.append(str(path.relative_to(REPO)))

    assert not uncollected, (
        f"these define tests and pytest collects none of them: {uncollected}. "
        f"Collected names match {patterns}. Rename them, or — if a file is a "
        f"helper that is imported rather than run — add it to HELPERS here, "
        f"which is a decision rather than an accident."
    )


def test_the_guard_would_notice(tmp_path):
    """The guard's own failing case, so it cannot quietly stop checking.
    A file named the way `nt.py` was, defining a test, must be caught."""
    stray = tmp_path / "nt.py"
    stray.write_text("def test_something():\n    assert True\n")

    patterns = _collected_patterns()

    assert DEFINES_TESTS.search(stray.read_text())
    assert not any(fnmatch.fnmatch(stray.name, p) for p in patterns)


def test_every_named_helper_still_exists():
    """A HELPERS entry for a file that is gone is an exemption nobody is
    using, and the next file to take that name inherits it silently."""
    missing = [name for name in HELPERS if not (TESTS / name).is_file()]

    assert not missing, f"HELPERS names files that do not exist: {missing}"


def test_every_file_that_defines_tests_actually_yields_some():
    """Naming is the visible form; silently collecting NOTHING is the same
    defect. A conftest that skips a directory, an import guard, a
    `collect_ignore` — each leaves a correctly named file producing zero
    items, and the run stays green because nothing ran to go red.

    Asserted per FILE rather than against a total, because a total is a
    number that rots: it has to be edited every time a test is added, and
    the edit that keeps it passing is indistinguishable from the edit that
    hides a loss.
    """
    import subprocess
    import sys

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, (
        f"collection itself failed ({proc.returncode}):\n{proc.stdout[-2000:]}"
    )

    collected = {
        line.split("::", 1)[0]
        for line in proc.stdout.splitlines()
        if "::" in line and line.startswith("tests/")
    }
    defines = {
        str(path.relative_to(REPO))
        for path in sorted(TESTS.rglob("*.py"))
        if path.name not in HELPERS
        and "__pycache__" not in path.parts
        and DEFINES_TESTS.search(path.read_text(encoding="utf-8", errors="replace"))
    }

    silent = sorted(defines - collected)
    assert not silent, (
        f"these define tests and yielded no test items: {silent}. Something "
        f"is skipping them — a conftest, an import guard, a collect_ignore — "
        f"and a file that runs nothing reports exactly like a file that "
        f"passed."
    )
