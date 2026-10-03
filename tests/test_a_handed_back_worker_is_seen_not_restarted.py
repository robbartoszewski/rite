"""A finished Worker's handback reaches its Manager, and stops a restart.

**Observed** in the dogfood of 2026-10-03, two defects with one cause.

A sandboxed Worker finished its ticket and did what its instructions said:
"Tell your Manager you are free." The command for that is `rite message
<manager>`, which from inside a sandbox fails with `PermissionError:
Operation not permitted` — correctly, because a Worker must not write a
Manager's inbox. It fell back to writing `rw/files/<TICKET>-handback.md`,
which nothing reads. The Manager never learned it had finished (SCRUM-57).

Then, because the Worker had stopped beating, the watchdog reported it
"stalled" and the Manager restarted a Worker that was already done
(SCRUM-58).

**What is pinned here:**

* `rite done` records a handback a sandboxed Worker can write — in `.rite/`,
  the tree its heartbeat already writes, with no sandbox profile change;
* the supervisor, on the host, turns it into the Manager's next instruction,
  once;
* the watchdog and `rite status` stop calling that Worker stalled, and say
  not to restart it;
* a handback is NOT permanent across tasks: starting new work clears it, so
  it cannot hide the next stall. That is the one window where "done" and
  "hung" could otherwise be confused, and it is closed by a clear, not by
  inference.
"""

from __future__ import annotations

import json
import time

import pytest
from click.testing import CliRunner

from rite_ai import handback
from rite_ai.cli.main import cli
from rite_ai.managers import mailbox
from rite_ai.managers import worker_handbacks as wh
from rite_ai.reporting.heartbeat import write_heartbeat
from rite_ai.watchdog import run_watchdog_check

OWNER = "lead"
WORKER = "alpha"


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A REAL project with one registered Worker, alpha.

    Built from files rather than by patching `load_project`: the whole
    subject here is whether the watchdog, `rite status` and the supervisor
    agree about one Worker, and a patched loader is a place where they could
    agree with each other and not with a project on disk.
    """
    root = tmp_path
    rite_dir = root / ".rite"
    rite_dir.mkdir()
    (rite_dir / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    (rite_dir / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "heartbeat:\n  interval_minutes: 10\n  stall_threshold: 3\n"
    )
    worker_dir = root / "workers" / WORKER
    worker_dir.mkdir(parents=True)
    (worker_dir / "worker.yml").write_text(
        f'worker:\n  name: "{WORKER}"\n  modules: []\n'
    )
    monkeypatch.setenv("RITE_PROJECT_ROOT", str(root))
    return root


def _stale_beat(root, seconds_ago=4000.0):
    """A heartbeat old enough to be a stall, as a finished Worker leaves."""
    write_heartbeat(root, WORKER, ticket="KAN-7")
    path = root / ".rite" / "heartbeats" / f"{WORKER}.json"
    record = json.loads(path.read_text())
    record["timestamp"] = time.time() - seconds_ago
    path.write_text(json.dumps(record))


class TestTheWorkerCanSignalDone:
    """SCRUM-57: a sandbox-safe way to say "done", and hand the work back."""

    def test_rite_done_records_a_handback(self, project):
        root = project
        result = CliRunner().invoke(
            cli,
            [
                "done",
                "--worker",
                WORKER,
                "--ticket",
                "KAN-7",
                "--branch",
                "KAN-7-timeout",
            ],
        )
        assert result.exit_code == 0, result.output
        record = handback.read(root, WORKER)
        assert record is not None
        assert record.ticket == "KAN-7"
        assert record.branch == "KAN-7-timeout"
        assert record.done

    def test_it_is_written_under_dot_rite_where_a_sandbox_can_write(self, project):
        """⚠ The transport, pinned as a PATH.

        A Worker's sandbox is given `.rite/` as a read-write mount
        (`sandbox.start_worker`), which is how `rite heartbeat` already works
        from inside one. Moving this record anywhere else — a Manager's
        directory, yoloAI's exchange directory — breaks the handback for a
        sandboxed Worker, which is the only kind the dogfood had, so the
        location is asserted rather than left to a helper.
        """
        root = project
        CliRunner().invoke(cli, ["done", "--worker", WORKER, "--ticket", "KAN-7"])
        assert (root / ".rite" / "handback" / f"{WORKER}.json").is_file()

    def test_the_summary_comes_in_on_stdin_never_the_command_line(self, project):
        """F14: a handback quotes its ticket, so the text is not an argv."""
        refused = CliRunner().invoke(
            cli, ["done", "--worker", WORKER, "added the column"]
        )
        assert refused.exit_code == 1
        assert "reads its text from stdin" in refused.output
        assert handback.read(project, WORKER) is None

        ok = CliRunner().invoke(
            cli, ["done", "--worker", WORKER, "-"], input="added the column\n"
        )
        assert ok.exit_code == 0, ok.output
        assert handback.read(project, WORKER).summary == "added the column"

    def test_an_unsafe_worker_name_is_refused(self, project):
        """`--worker` is a free string and the name reaches a path."""
        result = CliRunner().invoke(cli, ["done", "--worker", "../../IMPORTANT"])
        assert result.exit_code == 1
        assert "refusing" in result.output


class TestTheManagerObservesIt:
    """SCRUM-57: the completion is observed by the Manager."""

    def test_the_manager_is_told_the_worker_finished(self, project):
        root = project
        handback.write(
            root, WORKER, ticket="KAN-7", branch="KAN-7-timeout", summary="done it"
        )
        said = []
        assert wh.surface(root, OWNER, said.append) == 1
        [note] = mailbox.read(root, OWNER, mailbox.INBOX)
        first = note.text.splitlines()[0]
        assert first.startswith("[from rite · about Worker 'alpha'")
        assert "HANDED BACK" in first
        assert "READY TO INTEGRATE" in first
        assert "KAN-7" in note.text
        assert "KAN-7-timeout" in note.text
        assert "done it" in note.text
        # The two things the dogfood's Manager got wrong, said in the note.
        assert "do not restart it" in note.text
        assert "not something rite checked" in note.text
        assert any("handed back" in s for s in said)

    def test_told_once(self, project):
        root = project
        handback.write(root, WORKER, ticket="KAN-7")
        wh.surface(root, OWNER, lambda s: None)
        assert wh.surface(root, OWNER, lambda s: None) == 0
        assert len(mailbox.read(root, OWNER, mailbox.INBOX)) == 1

    def test_a_second_task_handed_back_is_told_again(self, project):
        """Cleared and re-handed-back is a new completion, not the old one.

        Keyed on the handback's identity rather than on its clock, so this
        does not depend on two writes landing on different floats.
        """
        root = project
        handback.write(root, WORKER, ticket="KAN-7", now=1000.0)
        wh.surface(root, OWNER, lambda s: None)
        handback.clear(root, WORKER)
        handback.write(root, WORKER, ticket="KAN-8", now=1000.0)
        assert wh.surface(root, OWNER, lambda s: None) == 1
        assert len(mailbox.read(root, OWNER, mailbox.INBOX)) == 2

    def test_no_handback_tells_nobody(self, project):
        root = project
        assert wh.surface(root, OWNER, lambda s: None) == 0
        assert mailbox.read(root, OWNER, mailbox.INBOX) == []

    def test_an_unreadable_handback_is_told_as_one_rite_could_not_read(self, project):
        """⚠ Not dropped, and not reported as a clean completion."""
        root = project
        path = root / ".rite" / "handback" / f"{WORKER}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{truncated")
        assert wh.surface(root, OWNER, lambda s: None) == 1
        [note] = mailbox.read(root, OWNER, mailbox.INBOX)
        assert "RECORD UNREADABLE" in note.text
        assert "do NOT" in note.text and "hung" in note.text


class TestTheWatchdogDoesNotRestartIt:
    """SCRUM-58: a handed-back Worker is not reported as a stall."""

    def test_without_a_handback_a_silent_worker_is_stalled(self, project):
        """The control. Without this, the test below proves nothing: it
        would pass on a watchdog that never reports a stall at all."""
        root = project
        _stale_beat(root)
        result = run_watchdog_check(root)
        assert [s.worker for s in result.stalled] == [WORKER]
        assert any("stalled" in r for r in result.reasons)

    def test_a_handed_back_worker_is_not_stalled(self, project):
        root = project
        _stale_beat(root)
        handback.write(root, WORKER, ticket="KAN-7")
        result = run_watchdog_check(root)
        assert result.stalled == [], (
            "a Worker that said it finished was still reported as a stall — "
            "the report the dogfood's Manager acted on by restarting it"
        )
        assert [h.worker for h in result.handed_back] == [WORKER]
        assert not any("stalled" in r for r in result.reasons)
        assert any("do NOT restart it" in r for r in result.reasons)

    def test_the_watchdog_calls_it_waiting_work_not_a_fault(self, project):
        """Exit 1 is documented as "something may be WRONG — investigate",
        and that reading is what got a finished Worker restarted."""
        root = project
        _stale_beat(root)
        handback.write(root, WORKER, ticket="KAN-7")
        result = CliRunner().invoke(cli, ["watchdog"])
        assert result.exit_code == 2, result.output
        assert "do NOT restart it" in result.output

    def test_status_says_handed_back_and_not_stalled(self, project):
        root = project
        _stale_beat(root)
        handback.write(root, WORKER, ticket="KAN-7", branch="KAN-7-timeout")
        result = CliRunner().invoke(cli, ["status"])
        assert "STALLED" not in result.output, result.output
        assert "HANDED BACK" in result.output
        assert "do NOT restart" in result.output

    def test_an_unreadable_handback_also_stops_the_restart(self, project):
        """The judgement recorded in `run_watchdog_check`: the file existing
        is evidence the Worker reached the step where it says it is done."""
        root = project
        _stale_beat(root)
        path = root / ".rite" / "handback" / f"{WORKER}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{truncated")
        result = run_watchdog_check(root)
        assert result.stalled == []
        assert any("UNREADABLE" in r for r in result.reasons)


class TestAStaleHandbackCannotHideTheNextStall:
    """The one race: permanence is what makes "done" unambiguous, and a
    clear at the start of new work is what bounds it."""

    def test_starting_new_work_clears_it(self, project):
        root = project
        handback.write(root, WORKER, ticket="KAN-7")
        result = CliRunner().invoke(cli, ["prepare", "--worker", WORKER])
        assert handback.read(root, WORKER) is None
        assert "cleared" in result.output
        assert "KAN-7" in result.output

    def test_and_then_a_stall_is_reported_again(self, project):
        root = project
        _stale_beat(root)
        handback.write(root, WORKER, ticket="KAN-7")
        assert run_watchdog_check(root).stalled == []
        handback.clear(root, WORKER)
        assert [s.worker for s in run_watchdog_check(root).stalled] == [WORKER]

    def test_clearing_says_what_it_dropped(self, project):
        """A handback dropped silently is the failure `rite done` removes."""
        root = project
        handback.write(root, WORKER, ticket="KAN-7")
        removed = handback.clear(root, WORKER)
        assert removed is not None and removed.ticket == "KAN-7"
        assert handback.clear(root, WORKER) is None
