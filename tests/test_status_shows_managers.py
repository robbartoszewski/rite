"""`rite status` says which Managers are running.

`rite start <manager>` could start one and nothing could tell you it was
there. `running_instances` and `pid_alive` both existed with zero callers —
the built-and-never-wired shape this release keeps finding — and for a
release whose criterion is unsupervised operation, being unable to ask what
is running is the disqualifying half.

The acceptance test this file makes durable, run against a real project:

    A1. Manager running   -> "planner: running as rite-mgr-… — tmux attach -t …"
    A2. its session killed -> "recorded but not running: planner"
"""

from __future__ import annotations

import os
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


def test_a_running_manager_is_named_with_how_to_reach_it(tmp_path):
    root = _project(tmp_path)
    _record(root, "planner", os.getpid())  # this process is certainly alive

    lines = _manager_lines(root)

    assert any("planner" in line and "running as" in line for line in lines)
    assert any("tmux attach -t rite-mgr-x-planner" in line for line in lines)


def test_a_dead_manager_is_not_reported_as_running(tmp_path):
    """THE POINT OF FILTERING HERE. `running_instances` returns every
    RECORDED instance and says so; repeating that without checking invents
    Managers that died with a reboot."""
    root = _project(tmp_path)
    _record(root, "planner", 999_999_999)  # no such process

    lines = _manager_lines(root)

    assert not any("running as" in line for line in lines)
    assert any("recorded but not running" in line for line in lines)


def test_a_stale_record_is_reported_rather_than_dropped(tmp_path):
    """Silently omitting it is how a stale record becomes invisible and then
    permanent. Naming it is what gets it cleaned up."""
    root = _project(tmp_path)
    _record(root, "ghost", 999_999_999)

    assert any("ghost" in line for line in _manager_lines(root))


def test_nothing_recorded_says_nothing(tmp_path):
    """A section printed on every run is one people stop reading."""
    assert _manager_lines(_project(tmp_path)) == []


def test_a_corrupt_record_does_not_invent_a_manager(tmp_path):
    root = _project(tmp_path)
    _record(root, "planner", os.getpid())
    from rite_ai.managers import instance_path

    instance_path(root, "broken").write_text("{ not json")

    lines = _manager_lines(root)

    assert any("planner" in line for line in lines)
    assert not any("broken" in line for line in lines)


def test_it_does_not_spawn_a_process(tmp_path, monkeypatch):
    """`collect_status` is the read-only path and a test pins that it never
    spawns one. Asking tmux here would put a process spawn into the command
    people run most often — the mistake `_loop_line` already made once."""
    import subprocess

    root = _project(tmp_path)
    _record(root, "planner", os.getpid())

    def explode(*a, **k):
        raise AssertionError("status spawned a process")

    monkeypatch.setattr(subprocess, "run", explode)
    monkeypatch.setattr(subprocess, "Popen", explode)

    assert _manager_lines(root)
