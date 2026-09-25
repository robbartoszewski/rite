"""The benchmark's own gate, run before any model sees it (RL-T0).

A benchmark is an instrument, and an uncalibrated instrument produces numbers
that look like measurements. Two ways this one could lie:

- a verify that passes on the UNTOUCHED task measures nothing, and every agent
  "completes" it;
- a verify that fails on the KNOWN-GOOD solution measures the verify's own
  bugs, and every agent fails it however well it works.

Both are checked here by running the real verify command in a scratch copy, not
by reading the task definitions. `sort-stable` is the one task whose verify
passes untouched, and it is in the set on purpose — the test asserts that it is
declared as such rather than letting it hide among the others.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from rite_local_bench.tasks import ALREADY_PASSING, TASKS  # noqa: E402


def _bare_python_problem() -> str:
    """Why the verify commands cannot run in THIS shell, or "".

    Every task's `verify` is a literal command for the Worker's environment —
    `python -m pytest -q …` — and runs through the shell here as written. A
    machine with no bare `python` on PATH, or one whose `python` has no
    pytest, cannot run it at all. Measured: macOS ships `python3` and no
    `python`, and `.venv/bin/python -m pytest` does not put the venv's `bin`
    on PATH, so all eleven verify tests failed with `python: command not
    found` on a fresh checkout. That is the shell, not the benchmark, so it
    is a skip with the reason — `uv run pytest` and CI put the venv's
    `python` first on PATH and run them.
    """
    try:
        done = subprocess.run(
            ["python", "-c", "import pytest"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        return "no bare `python` on PATH (run the suite with `uv run pytest`)"
    except subprocess.TimeoutExpired:
        return "`python -c 'import pytest'` did not finish"
    if done.returncode != 0:
        return "the `python` on PATH cannot import pytest (use `uv run pytest`)"
    return ""


_NO_PYTHON = _bare_python_problem()
needs_bare_python = pytest.mark.skipif(bool(_NO_PYTHON), reason=_NO_PYTHON)


def _run_verify(
    root: Path, files: dict[str, str], verify: str
) -> subprocess.CompletedProcess:
    for name, body in files.items():
        (root / name).write_text(body)
    return subprocess.run(
        verify, shell=True, cwd=root, capture_output=True, text=True, timeout=120
    )


@needs_bare_python
@pytest.mark.parametrize("task", TASKS, ids=[t.id for t in TASKS])
def test_the_verify_passes_on_the_known_good_solution(task, tmp_path: Path):
    """Otherwise the task measures the verify's bugs, and no agent can pass."""
    result = _run_verify(tmp_path, task.solution, task.verify)
    assert result.returncode == 0, (
        f"{task.id}: the known-good solution does not pass its own verify\n"
        f"{result.stdout}\n{result.stderr}"
    )


@needs_bare_python
@pytest.mark.parametrize("task", TASKS, ids=[t.id for t in TASKS])
def test_the_verify_fails_before_the_work(task, tmp_path: Path):
    """Otherwise every agent 'completes' it by doing nothing at all."""
    result = _run_verify(tmp_path, task.before, task.verify)
    if task.id in ALREADY_PASSING:
        assert result.returncode == 0, (
            f"{task.id} is declared as already passing and does not — either "
            "the task changed or the declaration is stale"
        )
        return
    assert result.returncode != 0, (
        f"{task.id}: its verify passes untouched, so it measures nothing\n"
        f"{result.stdout}"
    )


def test_every_task_names_the_file_it_is_scoped_to():
    """Scope is what 'stayed in scope' is scored against, so a task whose scope
    does not name the file it edits scores every agent as out of scope."""
    for task in TASKS:
        edited = [
            name
            for name, body in task.solution.items()
            if task.before.get(name) != body
        ]
        if task.id in ALREADY_PASSING:
            assert not edited, f"{task.id} should need no edit"
            continue
        assert edited, f"{task.id} has a solution identical to its before state"
        for name in edited:
            assert name in task.scope, f"{task.id} edits {name}, outside its own scope"


def test_the_set_is_ten_tasks_with_distinct_ids():
    """The threshold in the design is '7 of 10'. A set of a different size
    silently changes what that threshold means."""
    assert len(TASKS) == 10
    assert len({t.id for t in TASKS}) == 10


def test_each_task_says_what_it_probes():
    """A result table is unreadable without it: 'executor failed task 6' says
    nothing, 'failed the one with two edits in one file' says where to look."""
    for task in TASKS:
        assert task.probes.strip(), f"{task.id} does not say what it is for"
