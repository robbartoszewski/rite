"""A Worker waiting on a question is shown as such, and is not destroyed (Q1–Q4).

**Observed** in the v0.6.0 dogfood (2026-09-28, macOS): Worker alpha on KAN-7
wrote three questions to yoloAI's `files/question.json`, as yoloAI's injected
instructions tell it to, and sat for eight hours. At the same moment:

    rite status     alpha: modules=[all] — not started (no heartbeat or claims yet)
    rite sandbox status alpha   ->  idle
    rite loop run   alpha: busy — a sandbox is running for it
    rite doctor     (idle sandboxes) no changes; `yoloai destroy <name>` frees it
    rite sandbox destroy alpha  ->  would delete it: only code blocks a destroy

**Pre-registered in the dogfood write-up (#19):** `rite status` shows the Worker
as waiting on a question, and `rite sandbox destroy` refuses while a question is
unanswered. The coordinator added: every status view stops pointing away from it.
This file checks each view against the same fixture, a real exchange directory.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rite_ai.sandbox import SandboxStatus
from rite_ai.sandbox import questions as q

ALPHA_ASKS = (
    '{"question": "Ticket KAN-7 (\'timout is way too long, make it configurable '
    "or smth') is not workable as written: WHICH timeout; what 'configurable' "
    'means; the new default?", "context": "Worker alpha, ticket KAN-7"}'
)


def _asked(where: Path, text: str = ALPHA_ASKS, at: float | None = None) -> Path:
    where.mkdir(parents=True, exist_ok=True)
    path = where / q.QUESTION_FILE
    path.write_text(text)
    if at is not None:
        os.utime(path, (at, at))
    return path


def _answered(where: Path, at: float) -> None:
    path = where / q.ANSWER_FILE
    path.write_text('{"answer": "the http one"}')
    os.utime(path, (at, at))


class TestWhatCountsAsPending:
    def test_a_question_with_no_answer_is_pending(self, tmp_path):
        _asked(tmp_path)
        found = q.question_in(tmp_path, "rite-p-alpha")
        assert isinstance(found, q.WorkerQuestion)
        assert "WHICH timeout" in found.question
        assert found.context == "Worker alpha, ticket KAN-7"

    def test_an_answer_written_after_it_settles_it(self, tmp_path):
        _asked(tmp_path, at=1000.0)
        _answered(tmp_path, at=2000.0)
        assert q.question_in(tmp_path) is None

    def test_a_newer_question_than_the_answer_is_pending_again(self, tmp_path):
        """alpha rewrote its question twice; the newest is what waits."""
        _answered(tmp_path, at=1000.0)
        _asked(tmp_path, at=2000.0)
        assert isinstance(q.question_in(tmp_path), q.WorkerQuestion)

    def test_no_question_file_is_no_question(self, tmp_path):
        assert q.question_in(tmp_path) is None

    def test_a_question_that_is_not_json_is_still_a_question(self, tmp_path):
        _asked(tmp_path, text="which timeout do you mean?")
        found = q.question_in(tmp_path)
        assert isinstance(found, q.WorkerQuestion)
        assert found.question == "which timeout do you mean?"


def _yoloai(monkeypatch, *, returncode=0, stdout="", stderr=""):
    import rite_ai.sandbox as sandbox

    monkeypatch.setattr(sandbox, "_yoloai_binary", lambda: "/bin/yoloai")
    run = MagicMock(
        return_value=MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)
    )
    monkeypatch.setattr(q.subprocess, "run", run)
    return run


class TestFindingTheExchangeDirectory:
    """Through yoloAI's own `files <name> path`, measured on 0.11.0: a known
    sandbox prints the host path, stopped or running; an unknown one exits 1
    with "sandbox not found"."""

    def test_it_asks_yoloai_where_the_files_are(self, monkeypatch, tmp_path):
        run = _yoloai(monkeypatch, stdout=f"{tmp_path}\n")
        assert q.files_dir("rite-p-alpha") == tmp_path
        assert run.call_args[0][0] == ["/bin/yoloai", "files", "rite-p-alpha", "path"]

    def test_no_such_sandbox_is_no_question(self, monkeypatch):
        _yoloai(monkeypatch, returncode=1, stderr="yoloai: sandbox not found")
        assert q.pending_question("rite-p-gone") is None

    def test_a_yoloai_that_fails_is_unknown_not_none(self, monkeypatch):
        _yoloai(monkeypatch, returncode=2, stderr="yoloai: lock held")
        found = q.pending_question("rite-p-alpha")
        assert isinstance(found, q.Unknown)
        assert "lock held" in found.reason


def _question(at: float | None = None) -> q.WorkerQuestion:
    return q.WorkerQuestion(
        sandbox="rite-p-alpha",
        question="WHICH timeout; what 'configurable' means; the new default?",
        context="",
        raised_at=at if at is not None else time.time(),
        path=Path("/x/rw/files/question.json"),
    )


class TestDestroyDoesNotDeleteAQuestion:
    @pytest.fixture
    def destroying(self, monkeypatch):
        import rite_ai.sandbox as sandbox

        monkeypatch.setattr(sandbox, "_yoloai_binary", lambda: "/bin/yoloai")
        monkeypatch.setattr(
            sandbox, "existing_sandbox_name", lambda w, r=None: "rite-p-alpha"
        )
        monkeypatch.setattr(sandbox, "_work_only_in_sandbox", lambda n, w, r: "")
        run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
        monkeypatch.setattr(sandbox.subprocess, "run", run)
        return monkeypatch, run

    def test_an_unanswered_question_refuses_and_is_quoted(self, destroying, tmp_path):
        monkeypatch, run = destroying
        monkeypatch.setattr(q, "pending_question", lambda name: _question())
        from rite_ai.sandbox import destroy_worker

        result = destroy_worker("alpha", tmp_path)
        assert not result.ok
        assert "waiting on a question" in result.message
        assert "WHICH timeout" in result.message
        assert "--force" in result.message
        run.assert_not_called()

    def test_could_not_check_refuses_too(self, destroying, tmp_path):
        monkeypatch, run = destroying
        monkeypatch.setattr(
            q, "pending_question", lambda name: q.Unknown(name, "lock held")
        )
        from rite_ai.sandbox import destroy_worker

        result = destroy_worker("alpha", tmp_path)
        assert not result.ok
        assert "could not check" in result.message
        run.assert_not_called()

    def test_no_question_destroys(self, destroying, tmp_path):
        monkeypatch, run = destroying
        monkeypatch.setattr(q, "pending_question", lambda name: None)
        from rite_ai.sandbox import destroy_worker

        assert destroy_worker("alpha", tmp_path).ok
        assert run.call_args[0][0][1] == "destroy"

    def test_force_is_the_stated_way_past_it(self, destroying, tmp_path):
        monkeypatch, run = destroying
        monkeypatch.setattr(q, "pending_question", lambda name: _question())
        from rite_ai.sandbox import destroy_worker

        assert destroy_worker("alpha", tmp_path, force=True).ok
        assert run.call_args[0][0][1] == "destroy"


def _seen(status: str, asked=None):
    from rite_ai.sandbox.activity import SandboxActivity

    return SandboxActivity(status=SandboxStatus(status), question=asked)


class TestRiteStatus:
    def _status(self, **facts):
        from rite_ai.config.models import WorkerManifest
        from rite_ai.reporting.status import ProjectStatus

        return ProjectStatus(
            root=Path("/p"),
            name="pingr",
            role="owner",
            workers=[WorkerManifest(name="alpha")],
            not_started_workers=["alpha"],
            **facts,
        )

    def test_a_waiting_worker_is_not_reported_as_not_started(self):
        from rite_ai.reporting.status import format_status

        out = format_status(
            self._status(
                worker_sandboxes={"alpha": _seen("idle", _question())},
            )
        )
        [line] = [x for x in out.splitlines() if x.startswith("  alpha:")]
        assert "WAITING ON A QUESTION since" in line
        assert "WHICH timeout" in line
        assert "not started" not in line
        assert "`rite sandbox destroy` refuses" in out

    def test_a_sandbox_that_ran_is_not_not_started(self):
        """S1: the dogfood said "not started" for a Worker whose sandbox ran."""
        from rite_ai.reporting.status import format_status

        out = format_status(self._status(worker_sandboxes={"alpha": _seen("idle")}))
        [line] = [x for x in out.splitlines() if x.startswith("  alpha:")]
        assert "sandbox idle: its agent is waiting at its prompt" in line
        assert "not started" not in line

    def test_no_modules_is_not_all_modules(self):
        from rite_ai.reporting.status import format_status

        out = format_status(self._status())
        assert "modules=[none — no modules are registered]" in out

    def test_could_not_check_is_said(self):
        from rite_ai.reporting.status import format_status

        out = format_status(
            self._status(
                worker_sandboxes={
                    "alpha": _seen("idle", q.Unknown("rite-p-alpha", "lock held"))
                },
            )
        )
        assert "could not check for a question: lock held" in out


class TestTheLoop:
    def test_a_waiting_worker_is_blocked_not_busy(self, tmp_path, monkeypatch):
        from rite_ai.loop import _look_at_worker

        monkeypatch.setattr(q, "worker_question", lambda name, root=None: _question())
        view = _look_at_worker(
            tmp_path, "alpha", time.time(), lambda n, r: SandboxStatus("idle")
        )
        assert view.verdict.startswith("blocked — waiting on a question since")
        assert not view.free
        assert any("WHICH timeout" in e for e in view.evidence)

    def test_a_running_worker_with_no_question_is_still_busy(
        self, tmp_path, monkeypatch
    ):
        from rite_ai.loop import _look_at_worker

        monkeypatch.setattr(q, "worker_question", lambda name, root=None: None)
        view = _look_at_worker(
            tmp_path, "alpha", time.time(), lambda n, r: SandboxStatus("active")
        )
        assert view.verdict == "busy — its sandbox's agent is working"


class TestSandboxStatus:
    def _run(self, monkeypatch, status, asked):
        from click.testing import CliRunner

        import rite_ai.cli.main as main
        import rite_ai.sandbox as sandbox

        monkeypatch.setattr(main, "_find_project_root", lambda: Path("/p"))
        monkeypatch.setattr(
            sandbox, "worker_sandbox_status", lambda w, r=None: SandboxStatus(status)
        )
        monkeypatch.setattr(q, "worker_question", lambda w, r=None: asked)
        return CliRunner().invoke(main.cli, ["sandbox", "status", "alpha"])

    def test_it_does_not_say_idle_for_a_waiting_worker(self, monkeypatch):
        result = self._run(monkeypatch, "idle", _question())
        assert result.exit_code == 0
        assert result.output.startswith("waiting on a question since")
        assert "(sandbox idle)" in result.output

    def test_a_stopped_sandbox_points_at_the_file_not_the_pane(self, monkeypatch):
        result = self._run(monkeypatch, "stopped", _question())
        assert "/x/rw/files/question.json" in result.output
        assert "sandbox pane" not in result.output

    def test_no_question_prints_the_sentence_every_view_prints(self, monkeypatch):
        result = self._run(monkeypatch, "idle", None)
        said = result.output.strip()
        assert said == "sandbox idle: its agent is waiting at its prompt"


class TestDoctor:
    def _entry(self, has_changes=False):
        entry = MagicMock(has_changes=has_changes)
        entry.name = "rite-o-w1"  # `name=` in the constructor names the mock
        return entry

    def test_a_question_is_never_freed(self):
        from rite_ai.cli.main import _other_sandbox_note

        said = _other_sandbox_note(self._entry(), _question())
        assert "waiting on a question" in said
        assert "do NOT destroy" in said
        assert "frees it" not in said

    def test_could_not_check_is_not_freed(self):
        from rite_ai.cli.main import _other_sandbox_note

        said = _other_sandbox_note(self._entry(), q.Unknown("rite-o-w1", "lock held"))
        assert "frees it" not in said
        assert "could not be checked" in said

    def test_nothing_pending_and_no_changes_still_frees(self):
        from rite_ai.cli.main import _other_sandbox_note

        said = _other_sandbox_note(self._entry(), None)
        assert said == "no changes; `yoloai destroy rite-o-w1` frees it"


def test_it_reads_a_real_exchange_directory_end_to_end(tmp_path, monkeypatch):
    """No fake question object: a file on disk, found through a yoloAI that
    names the directory, reaches `rite sandbox status`'s wording."""
    where = tmp_path / "rw" / "files"
    _asked(where)
    _yoloai(monkeypatch, stdout=f"{where}\n")
    found = q.pending_question("rite-p-alpha")
    assert isinstance(found, q.WorkerQuestion)
    assert found.path == where / q.QUESTION_FILE
