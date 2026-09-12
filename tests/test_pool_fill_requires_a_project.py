"""`rite pool fill` outside a rite project.

`_require_project_root`'s own docstring states the rule — "the project
root, or refuse — for every command that WRITES" — and the damage it
describes is a writer that "CREATES `.rite/` wherever it was run, and
reports success", with the phantom then capturing every directory below it
through the walk-up.

`pool fill` is the one writer that never called it. It reaches the root
through `_load_config_for_write`, which — despite the name — resolves with
`_find_project_root`, the variant that falls back to cwd. Its siblings do
guard: `pool archive` one screen below it, `schedule set`, `schedule
set-timezone` and `add worker` all refuse in a directory with no `.rite/`.

Why this one matters more than the phantom ledger that motivated the rule.
`pool fill` does not merely write state in the wrong place — it starts
`config.pool.coordinator_standby` real `claude` sessions first, and that
cost is not recoverable by deleting the directory afterwards. Measured
while auditing: run in a scratch directory with no `.rite/` in any parent,
it printed "pool at 2/2 — 2 session(s) started", started two sessions, and
recorded them in a `.rite/pool.json` it created there — which no project
would ever read.

The spawn is stubbed to raise rather than mocked to succeed: if the guard
regresses, this test must fail rather than quietly start sessions on the
machine running it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli


@pytest.fixture
def never_spawn(monkeypatch):
    """Any attempt to reach tmux is a test failure, not a session.

    Stubbed to RAISE rather than mocked to succeed: a regression here must
    fail the suite, never quietly start `claude` sessions on whatever
    machine is running the tests.
    """
    import rite_ai.pool as pool

    attempts: list[str] = []

    def _explode(*args, **kwargs):
        # Recorded as well as raised: `CliRunner.invoke` catches exceptions
        # by default, so a test that only watched stdout would pass on the
        # broken code for the wrong reason.
        attempts.append("spawn")
        raise AssertionError(
            "pool fill tried to start a session outside a rite project"
        )

    monkeypatch.setattr(pool, "_tmux_binary", _explode)
    return attempts


def _run(monkeypatch, cwd: Path, *args: str):
    monkeypatch.chdir(cwd)
    return CliRunner().invoke(cli, list(args))


def test_pool_fill_refuses_outside_a_project(tmp_path, monkeypatch, never_spawn):
    """The symptom, exactly as measured: it started sessions and reported
    success in a directory with no `.rite/` in any parent."""
    result = _run(monkeypatch, tmp_path, "pool", "fill")

    assert result.exit_code != 0, (
        f"pool fill succeeded outside a project: {result.output!r}"
    )
    assert "not a rite project" in result.output


def test_it_creates_no_phantom_rite_directory(tmp_path, monkeypatch, never_spawn):
    """The other half of `_require_project_root`'s rationale: the phantom
    `.rite/` captures every directory below it through the walk-up, so an
    unrelated later command adopts it."""
    _run(monkeypatch, tmp_path, "pool", "fill")

    assert not (tmp_path / ".rite").exists(), (
        "pool fill created a .rite/ outside any project"
    )


def test_it_refuses_before_starting_anything(tmp_path, monkeypatch, never_spawn):
    """Order matters, and this is the whole reason the defect is worse than
    a misplaced state file. Sessions cost quota that deleting the directory
    afterwards does not refund, so the guard has to run BEFORE the spawn —
    `never_spawn` turns any reversal of that order into a failure."""
    result = _run(monkeypatch, tmp_path, "pool", "fill")

    assert never_spawn == [], "the spawn was reached before the guard"
    assert "session(s) started" not in result.output, result.output


def test_the_guarded_sibling_still_refuses(tmp_path, monkeypatch):
    """`pool archive` already had the guard. Pinned so a future refactor
    that unifies these two cannot fix one by loosening the other."""
    result = _run(monkeypatch, tmp_path, "pool", "archive", "--dry-run")

    assert result.exit_code != 0
    assert "not a rite project" in result.output


def test_pool_fill_still_works_inside_a_real_project(tmp_path, monkeypatch):
    """The guard must not cost the command its actual job. Stops here at
    the point of spawning — reaching the spawn at all is the assertion,
    since that is past every check the guard added."""
    import rite_ai.pool as pool

    (tmp_path / ".rite").mkdir()
    (tmp_path / ".rite" / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
    )

    reached = []

    def _stop(*args, **kwargs):
        reached.append(True)
        return None  # "no tmux" — fill reports, starts nothing

    monkeypatch.setattr(pool, "_tmux_binary", _stop)
    result = _run(monkeypatch, tmp_path, "pool", "fill")

    assert reached, f"guard rejected a real project: {result.output!r}"
    assert "not a rite project" not in result.output
