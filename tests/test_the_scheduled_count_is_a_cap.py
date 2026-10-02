"""A window's `workers: N` caps how many Workers run, not only whether any do
(SCRUM-28).

`_schedule_refusal` refused only at 0, so `workers: 1` let a second and a third
Worker start, held back only by `sandbox.max_concurrent_workers` (5). §2.7.5
calls the schedule "a cap the user sets". And because the cap is
count-then-start, two starts at once must not both count the same number:
`start_worker` holds the project's start lock across both.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import rite_ai.schedule as sched
from rite_ai import sandbox
from rite_ai.config.models import SandboxConfig
from rite_ai.schedule import Moment, ResolvedZone

TUE = 1
YOLOAI = "/usr/local/bin/yoloai"


@pytest.fixture
def ten_on_a_tuesday(monkeypatch):
    zone = ResolvedZone("Europe/Warsaw", machine_local=False)
    monkeypatch.setattr(
        sched, "current_moment", lambda _tz, now=None: Moment(10 * 60, TUE, zone)
    )


def _project(tmp_path: Path, workers: int, names=("alpha", "beta")) -> Path:
    (tmp_path / ".rite").mkdir()
    (tmp_path / ".rite" / "brief.yaml").write_text(
        "project:\n  name: t\n  role: owner\n"
    )
    (tmp_path / ".rite" / "modules.yaml").write_text("modules: {}\n")
    (tmp_path / ".rite" / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "sandbox:\n  max_concurrent_workers: 5\n"
        "schedule:\n  timezone: Europe/Warsaw\n  windows:\n"
        f'    - {{days: "Mon-Fri", hours: "09:00-17:00", workers: {workers}}}\n'
    )
    for name in names:
        (tmp_path / "workers" / name).mkdir(parents=True)
    return tmp_path


class TestTheCheck:
    def test_one_running_under_a_window_of_one_refuses_the_next(
        self, tmp_path, ten_on_a_tuesday
    ):
        root = _project(tmp_path, workers=1)
        refusal = sandbox._schedule_refusal(root, running=1)
        assert refusal is not None, (
            "a second Worker was allowed under `workers: 1` — the window's "
            "count is only a switch again"
        )
        assert "allows 1 Worker(s)" in refusal
        assert "1 of this project's are already running" in refusal

    def test_room_under_the_count_does_not_refuse(self, tmp_path, ten_on_a_tuesday):
        root = _project(tmp_path, workers=3)
        assert sandbox._schedule_refusal(root, running=0) is None
        assert sandbox._schedule_refusal(root, running=2) is None
        assert sandbox._schedule_refusal(root, running=3) is not None

    def test_zero_still_says_when_the_window_opens(self, tmp_path, ten_on_a_tuesday):
        root = _project(tmp_path, workers=0)
        refusal = sandbox._schedule_refusal(root, running=0)
        assert refusal is not None and "allows 0 Workers" in refusal


def _yoloai(root: Path, running: list[str], new_takes: float = 0.0):
    """`yoloai ls` lists `running`; `yoloai new` adds its sandbox to it, after
    `new_takes` seconds, as a real create would only show once made."""

    def run(args, *a, **kw):
        if "ls" in args:
            listed = [{"environment": {"name": n}} for n in list(running)]
            return MagicMock(
                returncode=0, stdout=json.dumps({"sandboxes": listed}), stderr=""
            )
        if "new" in args:
            time.sleep(new_takes)
            running.append(args[-2] if ":copy" in args[-1] else args[-1])
        return MagicMock(returncode=0, stdout="", stderr="")

    return run


@patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
def test_a_start_over_the_windows_count_is_refused_under_the_flat_cap(
    mock_which, tmp_path, ten_on_a_tuesday
):
    """The dogfood's shape: `workers: 1`, one running, flat cap 5."""
    root = _project(tmp_path, workers=1)
    running = [sandbox.sandbox_name("alpha", root)]
    with patch("rite_ai.sandbox.subprocess.run", side_effect=_yoloai(root, running)):
        result = sandbox.start_worker(
            root, "beta", SandboxConfig(max_concurrent_workers=5)
        )
    assert not result.ok, "a second Worker started under a window of one"
    assert "allows 1 Worker(s)" in result.message


@patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
def test_two_starts_at_once_do_not_both_fit_in_one_slot(
    mock_which, tmp_path, ten_on_a_tuesday
):
    """Both start together; `yoloai new` takes long enough that, uncounted,
    each would see zero running. Under the lock the second counts the first.

    ⚠ `worker_home` is pinned, stated rather than asked of the machine: built
    unserialised it is itself a check-then-symlink, and two starts at once
    crash one of them with `FileExistsError` — which the lock also prevents,
    but which made the first version of this test fail on a crashed thread
    rather than on two Workers started. The property here is the count."""
    root = _project(tmp_path, workers=1)
    home = tmp_path / "home"
    home.mkdir()
    running: list[str] = []
    results: dict[str, object] = {}
    gate = threading.Barrier(2)

    def start(name: str) -> None:
        gate.wait()
        results[name] = sandbox.start_worker(
            root, name, SandboxConfig(max_concurrent_workers=5)
        )

    with (
        patch("rite_ai.sandbox.worker_home", return_value=home),
        patch(
            "rite_ai.sandbox.subprocess.run",
            side_effect=_yoloai(root, running, new_takes=0.5),
        ),
    ):
        threads = [threading.Thread(target=start, args=(n,)) for n in ("alpha", "beta")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

    started = [n for n, r in results.items() if r.ok]
    assert len(results) == 2
    assert len(started) == 1, (
        f"{len(started)} Workers started at once under `workers: 1`: {started}"
    )
    refused = next(r for r in results.values() if not r.ok)
    assert "allows 1 Worker(s)" in refused.message


@patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
def test_where_locking_does_not_exclude_nothing_starts(
    mock_which, tmp_path, ten_on_a_tuesday
):
    root = _project(tmp_path, workers=1)
    calls: list = []
    with (
        patch("rite_ai.state.exclusion_holds", return_value=False),
        patch("rite_ai.sandbox.subprocess.run", side_effect=calls.append),
    ):
        result = sandbox.start_worker(root, "alpha", SandboxConfig())
    assert not result.ok
    assert "file locking does not work" in result.message
    assert calls == [], "it shelled out to yoloAI after deciding not to start"


@patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
def test_a_start_lock_held_too_long_refuses_and_names_the_lock(
    mock_which, tmp_path, ten_on_a_tuesday, monkeypatch
):
    import fcntl

    from rite_ai.state import lock_path_for

    root = _project(tmp_path, workers=1)
    monkeypatch.setattr(sandbox, "START_LOCK_WAIT", 0.3)
    lock_file = lock_path_for(root / ".rite" / sandbox.START_LOCK)
    with open(lock_file, "a+") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        calls: list = []
        with patch("rite_ai.sandbox.subprocess.run", side_effect=calls.append):
            result = sandbox.start_worker(root, "alpha", SandboxConfig())
    assert not result.ok
    assert str(lock_file) in result.message
    assert calls == []


@patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
def test_two_starts_that_both_fit_both_start(mock_which, tmp_path, ten_on_a_tuesday):
    """Found while writing the test above: unserialised, two starts at once
    raced in `worker_home` (check, then symlink) and one died with
    `FileExistsError` — a Worker that should have started, did not, with a
    traceback. Serialised, both start. `worker_home` is NOT pinned here; it is
    the thing under test."""
    root = _project(tmp_path, workers=2)
    running: list[str] = []
    results: dict[str, object] = {}
    crashed: list[BaseException] = []
    gate = threading.Barrier(2)

    def start(name: str) -> None:
        gate.wait()
        try:
            results[name] = sandbox.start_worker(
                root, name, SandboxConfig(max_concurrent_workers=5)
            )
        except BaseException as e:  # noqa: BLE001 - the crash IS the finding
            crashed.append(e)

    with patch(
        "rite_ai.sandbox.subprocess.run",
        side_effect=_yoloai(root, running, new_takes=0.2),
    ):
        threads = [threading.Thread(target=start, args=(n,)) for n in ("alpha", "beta")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

    assert crashed == [], f"a concurrent start crashed: {crashed!r}"
    assert sorted(n for n, r in results.items() if r.ok) == ["alpha", "beta"]
