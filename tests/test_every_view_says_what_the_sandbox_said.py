"""`rite status` never says "not started", and every view says one thing (S1).

**Observed** in the v0.6.0 dogfood (2026-09-28, macOS): about Worker alpha, at
one moment, `rite status` said `modules=[all] — not started (no heartbeat or
claims yet)`, `rite sandbox status` said `idle`, and `rite loop run` said
`busy — a sandbox is running for it`. alpha's sandbox had read its ticket and
was waiting at its prompt; alpha had been created with 0 modules. A Manager
relayed the first to Robert as "no progress".

**Pre-registered in the dogfood write-up (#20):** `rite status` for a Worker
that has run and is blocked says so (not "not started"); `modules=[]` prints
as none. **Added by the coordinator:** the views agree on a property rather
than each inferring one, and where rite cannot tell it says so.

Part A (#68) fixed the case where the Worker left a question file. These are
the rest: a sandbox with no question file, one yoloAI cannot be asked about,
one in a project with `sandbox.enabled` off, and no sandbox at all.

Every test here runs a real `yoloai` executable (a script on PATH that
answers as yoloAI 0.11.0 does), through the real subprocess calls, so the
three views are fed the same observation the way they are in use.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.sandbox import sandbox_name

IDLE = "sandbox idle: its agent is waiting at its prompt"


def _project(tmp_path: Path, *, sandbox_enabled: bool = True) -> Path:
    """alpha as in the dogfood: created with 0 modules, one module
    registered afterwards (pingr), no heartbeat, no claims."""
    root = tmp_path / "proj"
    rite = root / ".rite"
    rite.mkdir(parents=True)
    (rite / "brief.yaml").write_text(
        "project:\n  name: pingr\n  role: owner\nwhat:\n  kind: app\n"
        "technology:\n  languages:\n    - python\n"
    )
    (root / "pingr").mkdir()
    (rite / "modules.yaml").write_text("modules:\n  pingr:\n    path: pingr\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "heartbeat:\n  interval_minutes: 10\n  stall_threshold: 3\n"
        "schedule:\n  timezone: UTC\n  windows:\n    - hours: '00:00-23:59'\n"
        "      workers: 2\n"
        f"sandbox:\n  enabled: {'true' if sandbox_enabled else 'false'}\n"
    )
    worker = root / "workers" / "alpha"
    worker.mkdir(parents=True)
    (worker / "worker.yml").write_text(
        "worker:\n  name: alpha\n  manager: ''\n  modules: []\n"
    )
    return root


def _yoloai(tmp_path: Path, monkeypatch, *, ls: str, rc: int = 0) -> Path:
    """A `yoloai` on PATH. `ls --json` prints `ls` and exits `rc`; `files
    <name> path` names an exchange directory with no question in it."""
    exchange = tmp_path / "exchange"
    exchange.mkdir(exist_ok=True)
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    script = bindir / "yoloai"
    (tmp_path / "ls.out").write_text(ls)
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = ls ]; then\n'
        f'  cat "{tmp_path / "ls.out"}"; exit {rc}\n'
        "fi\n"
        'if [ "$1" = files ] && [ "$3" = path ]; then\n'
        f'  echo "{exchange}"; exit 0\n'
        "fi\n"
        'echo "unexpected: $*" >&2; exit 64\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
    return exchange


def _sandbox(root: Path, status: str) -> str:
    return json.dumps(
        {
            "sandboxes": [
                {
                    "environment": {"name": sandbox_name("alpha", root)},
                    "status": status,
                    "has_changes": False,
                }
            ]
        }
    )


def _status_line(root: Path, monkeypatch) -> str:
    monkeypatch.chdir(root)
    result = CliRunner().invoke(cli, ["status", "--no-board"])
    assert result.exit_code == 0, result.output
    [line] = [x for x in result.output.splitlines() if x.startswith("  alpha:")]
    return line


def _sandbox_status(root: Path, monkeypatch):
    monkeypatch.chdir(root)
    return CliRunner().invoke(cli, ["sandbox", "status", "alpha"])


def _loop_view(root: Path):
    import time

    from rite_ai.loop import _look_at_worker
    from rite_ai.sandbox import worker_sandbox_status

    return _look_at_worker(root, "alpha", time.time(), worker_sandbox_status)


# --- the pre-registered test -------------------------------------------------


def test_a_worker_whose_sandbox_ran_and_waits_is_said_so(tmp_path, monkeypatch):
    """The dogfood's state exactly, minus the question file part A covers:
    the sandbox is up and its agent is at its prompt."""
    root = _project(tmp_path)
    _yoloai(tmp_path, monkeypatch, ls=_sandbox(root, "idle"))
    line = _status_line(root, monkeypatch)
    assert line == f"  alpha: modules=[none] — no heartbeat or claims yet; {IDLE}"


def test_a_worker_created_with_no_modules_holds_none(tmp_path, monkeypatch):
    """alpha was created with 0 modules and `pingr` registered; status said
    `[all]`. A Worker's modules are what was cloned into it."""
    root = _project(tmp_path)
    _yoloai(tmp_path, monkeypatch, ls=_sandbox(root, "idle"))
    line = _status_line(root, monkeypatch)
    assert line.startswith("  alpha: modules=[none] — ")


# --- the views agree ---------------------------------------------------------


@pytest.mark.parametrize(
    "yoloai_says, sentence",
    [
        ("idle", IDLE),
        ("active", "sandbox active: its agent is working"),
        ("done", "sandbox done: its agent finished and exited"),
        ("failed", "sandbox failed: its agent exited with an error"),
        ("stopped", "sandbox stopped: the sandbox is stopped"),
        ("paused", "sandbox paused: a yoloAI state rite does not recognise"),
    ],
)
def test_all_three_views_print_the_same_sentence(
    tmp_path, monkeypatch, yoloai_says, sentence
):
    root = _project(tmp_path)
    _yoloai(tmp_path, monkeypatch, ls=_sandbox(root, yoloai_says))

    assert _status_line(root, monkeypatch).endswith(f"claims yet; {sentence}")
    result = _sandbox_status(root, monkeypatch)
    assert result.exit_code == 0
    assert result.output.strip() == sentence
    view = _loop_view(root)
    assert sentence in view.evidence
    assert not view.free


def test_the_loop_calls_only_a_working_agent_busy(tmp_path, monkeypatch):
    """ "busy — a sandbox is running" was said of an agent at its prompt."""
    root = _project(tmp_path)
    _yoloai(tmp_path, monkeypatch, ls=_sandbox(root, "active"))
    assert _loop_view(root).verdict.startswith("busy")
    (tmp_path / "ls.out").write_text(_sandbox(root, "idle"))
    assert not _loop_view(root).verdict.startswith("busy")


def test_saturation_is_not_claimed_of_a_worker_nobody_saw_working(
    tmp_path, monkeypatch
):
    """The dogfood's loop counted alpha, at its prompt eight hours, as a
    full queue: "a queue, not a fault"."""
    from datetime import UTC, datetime

    from rite_ai.loop import SATURATED, plan_cycle
    from rite_ai.sandbox import worker_sandbox_status

    class Board:
        def list_tickets(self, _filter):
            class T:
                id = "KAN-7"
                labels = ["scheduled"]

            return [T()]

    root = _project(tmp_path)
    _yoloai(tmp_path, monkeypatch, ls=_sandbox(root, "idle"))
    cycle = plan_cycle(
        root,
        board=Board(),
        sandbox_status=worker_sandbox_status,
        now=datetime.now(UTC).replace(hour=12),
    )
    assert cycle.verdict == SATURATED
    assert "a queue, not a fault" not in cycle.detail
    assert "alpha not seen working" in cycle.detail


# --- where rite cannot tell, it says so --------------------------------------


def test_a_yoloai_that_cannot_be_asked_is_unknown_everywhere(tmp_path, monkeypatch):
    root = _project(tmp_path)
    _yoloai(tmp_path, monkeypatch, ls="lock held", rc=2)

    line = _status_line(root, monkeypatch)
    assert line == (
        "  alpha: modules=[none] — no heartbeat or claims yet; sandbox state "
        "unknown (`yoloai ls --json` exited 2: lock held); a session opened "
        "by hand shows only once it beats or claims"
    )
    result = _sandbox_status(root, monkeypatch)
    assert result.exit_code == 1
    assert result.output.startswith("sandbox state unknown")
    view = _loop_view(root)
    assert view.verdict.startswith("cannot tell")
    assert not view.free


def test_no_sandbox_is_said_and_is_not_not_started(tmp_path, monkeypatch):
    """No sandbox, no heartbeat, no claim: rite has not started it, but a
    session opened by hand in `workers/alpha/` is invisible here until it
    beats or claims, so "not started" would still be a guess."""
    root = _project(tmp_path)
    _yoloai(tmp_path, monkeypatch, ls='{"sandboxes": []}')
    line = _status_line(root, monkeypatch)
    assert line == (
        "  alpha: modules=[none] — no heartbeat or claims yet; no sandbox; a "
        "session opened by hand shows only once it beats or claims"
    )
    assert _sandbox_status(root, monkeypatch).output.strip() == "no sandbox"


def test_a_sandbox_is_seen_with_sandbox_enabled_off(tmp_path, monkeypatch):
    """`sandbox.enabled` governs setup; `rite sandbox start` runs without it.
    status used to skip the sandbox when it was off."""
    root = _project(tmp_path, sandbox_enabled=False)
    _yoloai(tmp_path, monkeypatch, ls=_sandbox(root, "idle"))
    assert _status_line(root, monkeypatch).endswith(f"claims yet; {IDLE}")


def test_no_yoloai_with_sandbox_off_is_unchecked_not_absent(tmp_path, monkeypatch):
    root = _project(tmp_path, sandbox_enabled=False)
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    line = _status_line(root, monkeypatch)
    assert line == (
        "  alpha: modules=[none] — no heartbeat or claims yet; no sandbox "
        "checked (yoloai is not installed); a session opened by hand shows "
        "only once it beats or claims"
    )
