"""`rite status` may not say a session is gone when it cannot know that.

⚠ **MEASURED, on a real tmux session.** A Manager whose command had exited
was listed by `tmux ls`, and `rite start` in that identical state called it
*"left over from an earlier run: its command has finished and the session is
held open so its exit status could be read"*. `rite status` called the same
session **gone**. Two commands, one session, contradictory words — and
status is the one an operator reads first.

**The cause is a confident answer to a question that was never asked.**
`_manager_lines` checks `pid_alive(instance.pid)` and nothing else. A dead
pid means the recorded PROCESS is gone; it says nothing about whether the
tmux session still exists, because `remain-on-exit` deliberately keeps a
session after its command ends. This is the `pid=0` defect's class: not a
wrong lookup, a wrong claim about what the lookup proves.

⚠ **The fix is wording, not a better check, and that is deliberate.**
`collect_status` must not spawn a process — `test_it_does_not_spawn_a_process`
pins it, because this is the command people run most often. So status
*cannot* ask tmux whether the session is there, and the honest report is the
uncertain one: say what the pid proves, name the left-over case as possible,
and point at the command that settles it either way.

The vocabulary is taken from `session.start`'s refusal rather than invented,
so a third description of this state cannot drift from the other two.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from rite_ai.managers import ManagerInstance, record_instance
from rite_ai.reporting.status import _manager_lines


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".rite").mkdir(parents=True)
    return root


def _record(root: Path, name: str, pid: int) -> None:
    record_instance(
        root,
        ManagerInstance(
            name=name,
            session=f"rite-mgr-x-{name}",
            pid=pid,
            engine="claude",
            max_sessions=1,
            window_seconds=0.0,
        ),
    )


def _stale_line(root: Path) -> str:
    lines = [ln for ln in _manager_lines(root) if "recorded but not running" in ln]
    assert lines, "the stale record was not reported at all"
    return lines[0]


def test_it_does_not_claim_the_session_is_gone(tmp_path):
    """⚠ The regression. It cannot know this, and it was measured saying it
    about a session `tmux ls` was listing."""
    root = _project(tmp_path)
    _record(root, "planner", 999_999_999)

    assert "session is gone" not in _stale_line(root), (
        "status asserted the tmux session is gone; a dead pid does not "
        "prove that, and `remain-on-exit` makes the opposite common"
    )


def test_it_says_what_the_pid_actually_proves(tmp_path):
    root = _project(tmp_path)
    _record(root, "planner", 999_999_999)

    assert "process is gone" in _stale_line(root)


def test_it_names_the_left_over_case_in_the_words_start_uses(tmp_path):
    """Not a third wording. `session.start` already describes this state to
    an operator; a second description would drift from it."""
    root = _project(tmp_path)
    _record(root, "planner", 999_999_999)
    line = _stale_line(root)

    assert "left over" in line
    assert "held open" in line


def test_it_points_at_the_command_that_settles_it(tmp_path):
    """An operator reading this needs one command, not a diagnosis."""
    root = _project(tmp_path)
    _record(root, "planner", 999_999_999)

    assert "rite manager stop planner" in _stale_line(root)


def test_several_stale_records_do_not_name_one_of_them_as_the_command(tmp_path):
    """⚠ With two names, `rite manager stop planner` would tell the reader
    to clear one and silently leave the other."""
    root = _project(tmp_path)
    _record(root, "planner", 999_999_999)
    _record(root, "lead", 999_999_998)
    line = _stale_line(root)

    assert "planner" in line and "lead" in line
    assert "rite manager stop <name>" in line


def test_a_live_manager_is_unaffected(tmp_path):
    root = _project(tmp_path)
    _record(root, "planner", os.getpid())
    assert any("running as" in ln for ln in _manager_lines(root))


def test_it_still_spawns_no_process(tmp_path, monkeypatch):
    """The constraint that forces the wording fix instead of a better check."""
    root = _project(tmp_path)
    _record(root, "planner", 999_999_999)

    def explode(*a, **k):
        raise AssertionError("status spawned a process")

    monkeypatch.setattr(subprocess, "run", explode)
    monkeypatch.setattr(subprocess, "Popen", explode)
    assert _stale_line(root)
