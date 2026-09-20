"""A Manager can read its own name out of its environment — on EVERY session.

WHY THIS FILE EXISTS, and why the resume case is the whole of it. The
identity used to be carried by the prompt text `rite start` types into the
pane: "You are the Manager 'planner'". `supervise` sends that on the FIRST
session only, and deliberately (D-90) — re-issuing an opening instruction
into a conversation that is mid-task is the error it exists to avoid. So the
name reached the MODEL once, as conversation, and reached a PROCESS never.
After a resume there was nothing on the machine that could answer "which
Manager is this"; `rite journal observe --manager` was `required=True`
because nothing could fill it.

⚠ **A test that checks only the first session reproduces the defect it is
meant to close.** The first session is exactly the one the prompt already
covered. The property is that the SECOND cycle — the resumed one, started
with `--resume <id>` and given no prompt at all — can still answer. Every
test here that matters runs against a real tmux and reads the answer out of
a real process's own output.

⚠ `claude` is never a command here — the engine is a shell script that
prints its environment and its arguments. Spent quota is the one damage no
cleanup reverses.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from rite_ai.managers import MANAGER_ENV, current_manager, forget_instance
from rite_ai.managers.session import session_name, start, stop
from rite_ai.managers.supervise import launch_command

# The same CI guard its siblings carry: a skip that goes quiet in CI turns
# "proved against real tmux" into "proved against nothing" without a red run.
_IN_CI = os.environ.get("CI") == "true"
if _IN_CI and shutil.which("tmux") is None:  # pragma: no cover - CI-only guard
    raise RuntimeError(
        "tmux is missing in CI, so the only real coverage of a Manager's "
        "identity would skip silently — install it in the workflow rather "
        "than letting these tests disappear"
    )

needs_tmux = pytest.mark.skipif(
    shutil.which("tmux") is None, reason="needs real tmux; mocking it is the bug"
)


class TestTheAnswerWithoutASession:
    """`current_manager()` on its own. Cheap, and the common case is "no"."""

    def test_an_ordinary_shell_is_not_a_manager(self, monkeypatch):
        monkeypatch.delenv(MANAGER_ENV, raising=False)
        assert current_manager() == ""

    def test_a_set_name_is_returned(self, monkeypatch):
        monkeypatch.setenv(MANAGER_ENV, "planner")
        assert current_manager() == "planner"

    def test_surrounding_whitespace_is_not_part_of_the_name(self, monkeypatch):
        monkeypatch.setenv(MANAGER_ENV, "  planner\n")
        assert current_manager() == "planner"

    @pytest.mark.parametrize(
        "hostile",
        [
            "../escape",
            "a/b",
            "",
            "   ",
            ".",
            # ⚠ Both are SAFE AS A PATH and broken as a tmux target: `.` is
            # tmux's pane separator and `:` its window separator, so a
            # session under either is created and then addressable by
            # nothing. `manager_dir` raises on them, so returning one here
            # would turn a missing default into a traceback.
            "v2.0",
            "eu:west",
        ],
    )
    def test_a_name_that_could_not_be_a_managers_reads_as_absent(
        self, monkeypatch, hostile
    ):
        """⚠ An environment is inherited by everything a session starts, and
        this value reaches `manager_dir` as a path segment. It is validated
        here rather than trusted because nothing upstream of an environment
        variable is under rite's control."""
        monkeypatch.setenv(MANAGER_ENV, hostile)
        assert current_manager() == ""


@needs_tmux
class TestARunningProcessCanRead:
    """The point of the whole exercise: not that an argv was built, but that
    a process running inside the session can answer the question."""

    @pytest.fixture
    def engine(self, tmp_path: Path) -> Path:
        """A stand-in for `claude` that says what it was given.

        It prints its environment and its arguments, then sleeps so the pane
        stays readable — the same shape as a session waiting for work.
        """
        script = tmp_path / "engine.sh"
        # ⚠ A SHORT sleep, and that is not arbitrary. This module sorts
        # first in the suite, so whatever it leaves running is alive for
        # every test after it — and three real-tmux tests in
        # `test_loop_start_really_starts.py` went red, on timing, when an
        # earlier draft slept 300. Long enough for the pane to be read;
        # short enough that a missed cleanup expires rather than haunting
        # the rest of the run.
        script.write_text(
            f'#!/bin/sh\necho "MGR=${{{MANAGER_ENV}:-NONE}} ARGS=$*"\nsleep 20\n'
        )
        script.chmod(0o755)
        return script

    @pytest.fixture
    def project(self, tmp_path: Path):
        """⚠ Tears down EVERY session for this project, not a guessed list.

        A draft named two Managers here, so a test that started a third
        would leak it for the rest of the run — which is the failure this
        teardown exists to prevent. It asks tmux what is there instead.
        """
        (tmp_path / ".rite").mkdir(exist_ok=True)
        yield tmp_path
        mine = session_name(tmp_path, "")
        listed = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
            errors="replace",
        )
        for line in (listed.stdout or "").splitlines():
            if line.startswith(mine):
                subprocess.run(
                    ["tmux", "kill-session", "-t", line], capture_output=True
                )

    def _pane(self, session: str) -> str:
        """What the session has printed, waited for rather than assumed.

        A `capture-pane` taken the instant after `new-session` returns is
        routinely empty — tmux reports that the session was created, not
        that the command in it has run, which is the same distinction
        `settled_alive` exists for.
        """
        import time

        for _ in range(40):
            time.sleep(0.1)
            seen = subprocess.run(
                ["tmux", "capture-pane", "-p", "-t", session],
                capture_output=True,
                text=True,
                errors="replace",
            )
            if "MGR=" in (seen.stdout or ""):
                return seen.stdout
        return seen.stdout or ""

    def test_the_first_session_can_read_its_name(self, project, engine):
        result = start(
            project, "planner", command=launch_command(str(engine)), max_sessions=2
        )
        assert result.ok, result.message
        assert "MGR=planner" in self._pane(result.session)

    def test_A_RESUMED_SESSION_CAN_STILL_READ_ITS_NAME(self, project, engine):
        """⚠ THE ONE THAT MATTERS.

        Cycle 2 as `supervise` builds it: the same `start`, a command from
        `launch_command` carrying `--resume <id>`, and NO prompt — the
        supervisor does not re-prompt a resume. Before `RITE_MANAGER` this
        session had no way to know what it was; the assertion below was the
        gap.
        """
        first = start(
            project, "planner", command=launch_command(str(engine)), max_sessions=2
        )
        assert first.ok, first.message
        # End cycle 1 the way the real loop does — the session finishes and
        # the supervisor moves on — so cycle 2 is a fresh tmux session under
        # the same name rather than a second one beside it.
        stop(first.session)
        forget_instance(project, "planner")

        resumed = start(
            project,
            "planner",
            command=launch_command(str(engine), "sid-abc123"),
            max_sessions=2,
        )
        assert resumed.ok, resumed.message
        pane = self._pane(resumed.session)
        assert "--resume sid-abc123" in pane, (
            "this is not the resume path — the test proves nothing about it"
        )
        assert "MGR=planner" in pane, (
            "a RESUMED Manager session could not read its own name. The "
            "prompt is sent on the first session only, so this session has "
            "no other way to know what it is."
        )

    def test_two_managers_do_not_get_each_others_names(self, project, engine):
        """The name is per SESSION, not per machine — an environment set
        anywhere broader would make every Manager on the laptop agree."""
        one = start(
            project, "planner", command=launch_command(str(engine)), max_sessions=2
        )
        two = start(
            project, "lead", command=launch_command(str(engine)), max_sessions=2
        )
        assert one.ok and two.ok
        assert "MGR=planner" in self._pane(one.session)
        assert "MGR=lead" in self._pane(two.session)


class TestTheJournalIsWhatUsesIt:
    """⚠ The wiring, and the reason this is not another symbol nobody calls.

    `--manager` was `required=True` on both journal commands because nothing
    inside a Manager session could answer the question — so the tool asked
    the agent being audited to retype the key its own entries are filed
    under. With `RITE_MANAGER` set on the session, the command can read it.
    """

    @pytest.fixture
    def project(self, tmp_path: Path, monkeypatch):
        rite = tmp_path / ".rite"
        rite.mkdir()
        (rite / "brief.yaml").write_text(
            "project:\n  name: acme\n  role: owner\n"
            "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
        )
        (rite / "modules.yaml").write_text("modules: {}\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("RITE_PROJECT_ROOT", raising=False)
        return tmp_path

    def _observe(self, *extra: str):
        from click.testing import CliRunner

        from rite_ai.cli.main import cli

        return CliRunner().invoke(
            cli,
            [
                "journal",
                "observe",
                "--anchor",
                "6a8a5b2",
                "--observed",
                "the gate exited 0 on an unreadable file",
                "--expected",
                "a gate that cannot read a file does not report clean",
                *extra,
            ],
        )

    def test_a_manager_session_does_not_have_to_name_itself(self, project, monkeypatch):
        monkeypatch.setenv(MANAGER_ENV, "planner")
        result = self._observe()
        assert result.exit_code == 0, result.output
        assert "recorded:" in result.output
        assert "planner" in result.output, (
            "it recorded the entry somewhere that does not name the Manager"
        )

    def test_an_explicit_manager_still_wins(self, project, monkeypatch):
        """A human filing on a Manager's behalf is a real case, and the flag
        is how `rite journal` is driven from outside a session at all."""
        monkeypatch.setenv(MANAGER_ENV, "planner")
        result = self._observe("--manager", "lead")
        assert result.exit_code == 0, result.output
        assert "lead" in result.output and "planner" not in result.output

    def test_outside_a_session_it_refuses_rather_than_inventing_one(
        self, project, monkeypatch
    ):
        """⚠ It must not fall back to a name. An entry filed under the wrong
        Manager is worse than one that was refused, because it reads as
        evidence about a Manager that did not write it."""
        monkeypatch.delenv(MANAGER_ENV, raising=False)
        result = self._observe()
        assert result.exit_code == 1
        assert "refusing to record" in result.output
        assert "RITE_MANAGER" in result.output, (
            "the refusal did not say what would have answered it"
        )

    def test_a_hostile_environment_value_refuses_too(self, project, monkeypatch):
        monkeypatch.setenv(MANAGER_ENV, "../../etc")
        result = self._observe()
        assert result.exit_code == 1
        assert "refusing to record" in result.output


@needs_tmux
class TestAnInheritedNameDoesNotTravel:
    """⚠ **A WRONG name is worse than no name, and the fallback used to
    produce one.**

    `rite start` is routinely run from inside another Manager's session, so
    this process's environment already carries that Manager's name. When
    `-e` is refused (tmux < 3.2) the session starts without it — and a
    `new-session` that has to START the tmux server forks one that inherits
    this environment, after which every pane on that server reads the
    INHERITED name.

    Measured before the fix: starting Manager `builder` from a process
    holding `RITE_MANAGER=planner` produced a session that read `planner`,
    while the message told the user the session "cannot read
    RITE_MANAGER" — false in both halves, and `rite journal observe` inside
    `builder` filed under `planner`. An audit record attributed to the
    wrong Manager is the one outcome this feature exists to prevent.

    The narrower true statement, also measured: a pane takes its
    environment from the tmux SERVER, not from the client that asked, so a
    nested `new-session` against an ALREADY-RUNNING server does not leak.
    The exposure is exactly the invocation that starts the server.
    """

    @pytest.fixture
    def engine(self, tmp_path: Path) -> Path:
        script = tmp_path / "engine.sh"
        script.write_text(f'#!/bin/sh\necho "MGR=${{{MANAGER_ENV}:-NONE}}"\nsleep 20\n')
        script.chmod(0o755)
        return script

    @pytest.fixture
    def project(self, tmp_path: Path):
        """Asks tmux what this project left behind rather than guessing."""
        (tmp_path / ".rite").mkdir(exist_ok=True)
        yield tmp_path
        mine = session_name(tmp_path, "")
        listed = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
            errors="replace",
        )
        for line in (listed.stdout or "").splitlines():
            if line.startswith(mine):
                subprocess.run(
                    ["tmux", "kill-session", "-t", line], capture_output=True
                )

    def _pane(self, session: str) -> str:
        import time

        for _ in range(40):
            time.sleep(0.1)
            seen = subprocess.run(
                ["tmux", "capture-pane", "-p", "-t", session],
                capture_output=True,
                text=True,
                errors="replace",
            )
            if "MGR=" in (seen.stdout or ""):
                return seen.stdout
        return seen.stdout or ""

    def test_the_fallback_does_not_hand_on_another_managers_name(
        self, project, engine, monkeypatch
    ):
        """Force the `-e`-refused path from inside Manager `planner`."""
        import rite_ai.managers.session as session_mod

        monkeypatch.setenv(MANAGER_ENV, "planner")
        monkeypatch.setattr(session_mod, "_rejected_the_flag", lambda _done: True)

        real_run = subprocess.run

        def refuses_dash_e(argv, **kwargs):
            if isinstance(argv, list) and "new-session" in argv and "-e" in argv:

                class Refused:
                    returncode = 1
                    stderr = "command new-session: unknown flag -e"
                    stdout = ""

                return Refused()
            return real_run(argv, **kwargs)

        monkeypatch.setattr(session_mod.subprocess, "run", refuses_dash_e)

        result = start(project, "builder", command=str(engine), max_sessions=2)
        assert result.ok, result.message
        pane = self._pane(result.session)
        assert "MGR=planner" not in pane, (
            "the session inherited the STARTING Manager's name and would "
            "file journal entries under it"
        )
        assert "MGR=NONE" in pane, pane
        # And the message must describe what actually happened, rather than
        # offering a default that an inherited name would have defeated.
        assert "will REFUSE there" in result.message, result.message

    def test_the_ordinary_path_still_sets_the_right_name(
        self, project, engine, monkeypatch
    ):
        """Stripping the inherited value must not strip the real one."""
        monkeypatch.setenv(MANAGER_ENV, "planner")
        result = start(project, "builder", command=str(engine), max_sessions=2)
        assert result.ok, result.message
        assert "MGR=builder" in self._pane(result.session)
