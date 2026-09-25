"""Starting, watching and stopping the loop (L-5).

The surface Robert asked for — "a CLI command that just runs in the
background" — and the three properties that make it safe to leave running:

- **stopping does not kill.** A session killed mid-dispatch leaves a claim
  held by a process that no longer exists, which is the failure §2.6 exists
  for, caused by the stop command;
- **one loop per project**, by two mechanisms, because they fail differently:
  tmux refuses a duplicate name, a pid lock catches everything else;
- **it stops on the one verdict that is a reason to**, and keeps going on the
  three that are not — a loop that stopped on `saturated` would turn a busy
  fleet into a stopped one with a queue nobody can see.
"""

from __future__ import annotations

from pathlib import Path

from rite_ai.loop import IDLE, UNKNOWN, watch
from rite_ai.loop.session import (
    clear_drain,
    draining,
    hold_lock,
    release_lock,
    request_drain,
    session_name,
)

from .test_loop_dry_run import FakeBoard, _claim, _free, project  # noqa: F401

# --- the drain signal --------------------------------------------------------------


def test_stopping_records_a_reason_not_a_flag(tmp_path):
    """It travels into `distribute`'s `draining` argument and out onto the
    board as "shutting down: <reason>" on any ticket handed back, where
    somebody reading the board next week needs it."""
    root = project(tmp_path)
    request_drain(root, "laptop going to sleep")

    assert draining(root) == "laptop going to sleep"


def test_no_drain_file_is_not_draining(tmp_path):
    assert draining(project(tmp_path)) == ""


def test_an_unreadable_drain_file_counts_as_draining(tmp_path):
    """The opposite of the intents file, deliberately: a drain file that
    cannot be read is most likely one somebody just wrote, and taking more
    work on that guess is the expensive mistake. Stopping early costs a
    restart."""
    root = project(tmp_path)
    (root / ".rite" / "loop-drain").mkdir()  # a directory: readable as nothing

    assert draining(root)


def test_start_clears_a_previous_drain_but_the_loop_never_does(tmp_path):
    """A loop clearing its own signal on exit would race the next start. A
    human running `start` after `stop` is the sequence the file records."""
    root = project(tmp_path)
    request_drain(root, "earlier")
    clear_drain(root)

    assert draining(root) == ""


# --- one loop per project ----------------------------------------------------------


def test_the_lock_names_the_holder_rather_than_failing_silently(tmp_path):
    root = project(tmp_path)
    assert hold_lock(root) is None

    import os

    assert hold_lock(root) == os.getpid()
    release_lock(root)


def test_a_lock_from_a_dead_process_is_reclaimed(tmp_path):
    """A loop killed by a reboot must not lock the project out for ever."""
    from rite_ai.loop.session import lock_path

    root = project(tmp_path)
    lock_path(root).write_text("999999 0\n")

    assert hold_lock(root) is None
    release_lock(root)


def test_the_session_name_is_deterministic_and_readable(tmp_path):
    """`pool.slot_name`'s lesson: uniqueness was never the problem,
    readability was — a human scanning `tmux ls` looks for the project."""
    root = project(tmp_path)
    name = session_name(root)

    assert name == session_name(root)
    assert name.startswith("rite-loop-")
    assert "acme" in name


def test_watch_refuses_to_run_beside_another_loop(tmp_path):
    root = project(tmp_path)
    hold_lock(root)
    said: list[str] = []

    why = watch(root, emit=said.append, board=FakeBoard("BEN-1"), clock=1.0)

    assert why == "locked"
    assert any("another loop holds" in line for line in said)
    release_lock(root)


def test_watch_releases_the_lock_when_it_finishes(tmp_path):
    """Including on the way out of an error — a lock a crashed loop kept
    would need a human to delete a file before work could resume."""
    root = project(tmp_path)
    watch(root, emit=lambda _l: None, board=FakeBoard(), sandbox_status=_free)

    assert hold_lock(root) is None
    release_lock(root)


# --- what it stops on, and what it does not ----------------------------------------


def test_it_stops_when_the_queue_is_empty(tmp_path):
    root = project(tmp_path)
    said: list[str] = []

    why = watch(root, emit=said.append, board=FakeBoard(), sandbox_status=_free)

    assert why == IDLE
    assert any("the queue is empty" in line for line in said)


def test_it_does_not_stop_because_everyone_is_busy(tmp_path):
    """The distinction the verdict vocabulary exists for. Stopping here turns
    a busy fleet into a stopped one and leaves a queue nobody can see."""
    root = project(tmp_path)
    _claim(root, "alpha", "engine/parser.py")
    slept: list[float] = []

    why = watch(
        root,
        emit=lambda _l: None,
        sleep=slept.append,
        limit=3,
        board=FakeBoard("BEN-1"),
        clock=1.0,
    )

    # Three cycles, two sleeps: the last one does not sleep because it is
    # already leaving. What matters is that `saturated` did not end it.
    assert why == "limit"
    assert slept == [120.0, 120.0], "it stopped instead of going round"


def test_it_stops_on_an_unknown_rather_than_guessing(tmp_path):
    root = project(tmp_path)
    said: list[str] = []

    why = watch(
        root, emit=said.append, board=FakeBoard(error="503"), sandbox_status=_free
    )

    assert why == UNKNOWN
    assert any("could not be established" in line for line in said)


def test_a_drain_stops_it_before_the_next_cycle(tmp_path):
    root = project(tmp_path)
    request_drain(root, "stop requested")
    said: list[str] = []

    why = watch(root, emit=said.append, board=FakeBoard("BEN-1"), clock=1.0)

    assert why == "drained"
    assert any("Taking no new work" in line for line in said)


def test_a_drain_mid_run_is_noticed_on_the_next_cycle(tmp_path):
    """ "Finishes the cycle it is in" is the promise `stop` makes."""
    root = project(tmp_path)
    _claim(root, "alpha", "engine/parser.py")

    def drain_after_first(_seconds):
        request_drain(root, "asked to stop")

    why = watch(
        root,
        emit=lambda _l: None,
        sleep=drain_after_first,
        board=FakeBoard("BEN-1"),
        clock=1.0,
    )

    assert why == "drained"


# --- the CLI ----------------------------------------------------------------------


def _run(root: Path, monkeypatch, *args):
    from click.testing import CliRunner

    from rite_ai.cli.main import cli

    monkeypatch.chdir(root)
    return CliRunner().invoke(cli, ["loop", *args])


def test_status_says_not_running_rather_than_nothing(tmp_path, monkeypatch):
    """After a reboot this is the honest answer, and the one that makes the
    'does not survive a reboot' cost visible instead of confusing."""
    result = _run(project(tmp_path), monkeypatch, "status")

    assert result.exit_code == 0
    assert "not running" in result.output


def test_stop_without_a_running_loop_still_records_the_signal(tmp_path, monkeypatch):
    """So a loop started a second later stops immediately rather than
    outliving the instruction that was meant for it."""
    root = project(tmp_path)
    result = _run(root, monkeypatch, "stop", "--reason", "going home")

    assert result.exit_code == 0
    assert draining(root) == "going home"


def test_start_refuses_without_tmux_and_says_what_to_do_instead(tmp_path, monkeypatch):
    import rite_ai.loop.session as session

    monkeypatch.setattr(session, "_tmux", lambda: None)
    result = _run(project(tmp_path), monkeypatch, "start")

    assert result.exit_code == 1
    assert "tmux is not installed" in result.output
    assert "run `rite loop run --watch`" in result.output


def test_start_refuses_a_second_loop_for_one_project(tmp_path, monkeypatch):
    """A second loop in one project is not a smaller version of one loop.

    ⚠ `liveness`, not `is_alive`: `start` asks `liveness`, so stubbing
    `is_alive` stubbed nothing. On macOS the test then passed BY ACCIDENT —
    `/usr/bin/tmux` does not exist there, so the refusal was "cannot tell
    whether a loop is already running", which contains the words asserted.
    On Linux, where it exists, the real tmux was asked, no loop was found,
    and a real loop was started in CI's tmux server. The assertion is now
    the refusal's own sentence."""
    import rite_ai.loop.session as session
    from rite_ai.managers.session import Liveness

    monkeypatch.setattr(session, "_tmux", lambda: "/usr/bin/tmux")
    monkeypatch.setattr(
        session, "liveness", lambda _name: Liveness(alive=True, known=True)
    )
    result = _run(project(tmp_path), monkeypatch, "start")

    assert result.exit_code == 1
    assert "a loop is already running for this project as" in result.output


def test_stop_never_kills(tmp_path, monkeypatch):
    """The property, asserted against the source: `pool/` has no kill path
    either, and a kill here would cause the very failure it looks like it is
    preventing."""
    source = (
        Path(__file__).resolve().parent.parent / "src/rite_ai/loop/session.py"
    ).read_text()
    in_docstring = False
    for line in source.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.count('"""') % 2:
            in_docstring = not in_docstring
            continue
        if in_docstring:
            continue
        assert "kill-session" not in line, line
        assert "kill(" not in line, line


# --- the log does not grow for ever ------------------------------------------------


def test_the_loop_log_is_rotated_rather_than_growing(tmp_path):
    """`rite loop run --watch` is the one command meant to run for days, its
    output goes to a file through a shell redirect, and nothing trimmed it."""
    from rite_ai.loop.session import log_path
    from rite_ai.scheduler.logfile import MAX_BYTES

    root = project(tmp_path)
    log = log_path(root)
    log.write_text("x" * (MAX_BYTES + 10))
    said: list[str] = []

    watch(root, emit=said.append, board=FakeBoard(), sandbox_status=_free)

    assert log.stat().st_size < MAX_BYTES
    assert log.with_suffix(log.suffix + ".1").is_file()
    assert any("rotated loop.log" in line for line in said)


def test_rotation_keeps_the_same_file_so_the_redirect_survives(tmp_path):
    """Copy-and-truncate, not rename: the tmux session holds the file open
    through `>>`, and a renamed file leaves it writing to an inode nobody
    can find."""
    from rite_ai.loop.session import log_path
    from rite_ai.scheduler.logfile import MAX_BYTES

    root = project(tmp_path)
    log = log_path(root)
    log.write_text("y" * (MAX_BYTES + 10))
    before = log.stat().st_ino

    watch(root, emit=lambda _l: None, board=FakeBoard(), sandbox_status=_free)

    assert log.stat().st_ino == before


def test_a_small_log_is_left_alone(tmp_path):
    """Almost always the case, and rotation that fired every cycle would be
    its own kind of noise."""
    from rite_ai.loop.session import log_path

    root = project(tmp_path)
    log_path(root).write_text("a few lines\n")
    said: list[str] = []

    watch(root, emit=said.append, board=FakeBoard(), sandbox_status=_free)

    assert not any("rotated" in line for line in said)
    assert not log_path(root).with_suffix(".log.1").exists()
