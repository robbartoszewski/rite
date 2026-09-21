"""A designation that cannot be used is treated as one that is not there.

⚠ **MEASURED: it wedged `rite start` with a Python traceback.** A
designation file holding well-formed JSON whose `session` is a non-empty
but badly-shaped string — `{"session": "abc\\nrm -rf /"}` — was returned by
`designated()` unvalidated, reached `launch_command`, and raised:

    ValueError: refusing to build a launch command with a resume id that
    'abc\\nrm -rf /' is not shaped like a session id

`cli/main.py` calls `supervise` bare, so that reached the user as a
traceback. And because the file is re-read every run, it wedged EVERY
subsequent `rite start` for that Manager until somebody deleted a file they
would have had to guess at.

**The write side accepted what the read-and-use side rejected.**
`designate()` validates only non-emptiness; `launch_command` refuses
anything not shaped like a session id — correctly, since that string is run
by a shell.

⚠ **Fixed on the READ side on purpose.** Validating on write is also right,
but it only protects files written after the fix; an already-poisoned file
would still need manual repair. Reading is what makes it self-heal.

`designated()`'s own docstring already said the rule this restores:
"Unreadable is the same as absent, deliberately. There is no error state
here." An id that cannot be used is unreadable in every sense that matters.

⚠ **It is announced as a session that could not be continued, not as one
that was never there.** That is what happened, and the two messages are
deliberately distinct — the timezone precedent, where an unset zone and a
rejected one do not print the same line. No fourth message: this is the
existing "could not be continued" case arriving by a new route.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rite_ai.managers import designate, designated, designation_path, user_dir
from rite_ai.managers.supervise import StartResult, supervise

POISON = "abc\nrm -rf /"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".rite").mkdir()
    user_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    return tmp_path


def _poison(root: Path, manager: str, value: object) -> None:
    designation_path(root, manager).write_text(json.dumps({"session": value}) + "\n")


class TestReadingIt:
    @pytest.mark.parametrize(
        "value",
        [
            POISON,
            "   ",
            "a" * 100_000,
            "abc$(touch /tmp/PWNED)",
            "-starts-with-a-dash",
            "has spaces",
        ],
    )
    def test_an_unusable_id_reads_as_absent(self, project, value):
        _poison(project, "planner", value)
        assert designated(project, "planner") == "", (
            "an id `launch_command` will refuse was handed back as if usable"
        )

    def test_a_usable_id_is_still_returned(self, project):
        designate(project, "planner", "sid-abc123")
        assert designated(project, "planner") == "sid-abc123"


class TestItDoesNotWedgeTheRun:
    """⚠ The regression. Through `supervise`, because a unit-level fix that
    left the caller raising would be the same defect one layer down."""

    def _run(self, root: Path, said: list[str]):
        started: list[str] = []

        def starter(r, m, *, engine, resume_id, max_sessions, window_seconds, **kw):
            started.append(resume_id)
            return StartResult(False, "engine did not start")

        result = supervise(
            root,
            "planner",
            engine="fake",
            max_sessions=1,
            window_seconds=0,
            verdict=lambda _r: "ready",
            starter=starter,
            resume_id_for=lambda _r, _m, _s=0.0: "",
            note=said.append,
            poll=0,
        )
        return result, started

    def test_it_does_not_raise(self, project):
        """⚠ The starter BUILDS THE REAL LAUNCH COMMAND, because that is
        where the traceback came from. A stub starter that ignores its
        `resume_id` would pass this on the unfixed code and prove nothing —
        an earlier draft of this test did exactly that."""
        from rite_ai.managers.supervise import launch_command

        _poison(project, "planner", POISON)
        said: list[str] = []

        def starter(r, m, *, engine, resume_id, max_sessions, window_seconds, **kw):
            launch_command(engine, resume_id)  # raised ValueError before the fix
            return StartResult(False, "engine did not start")

        supervise(
            project,
            "planner",
            engine="fake",
            max_sessions=1,
            window_seconds=0,
            verdict=lambda _r: "ready",
            starter=starter,
            resume_id_for=lambda _r, _m, _s=0.0: "",
            note=said.append,
            poll=0,
        )

    def test_the_poisoned_id_never_reaches_the_launch_command(self, project):
        _poison(project, "planner", POISON)
        said: list[str] = []
        _, started = self._run(project, said)
        assert started == [""], f"a refused id was passed to the starter: {started}"

    def test_it_says_the_session_could_not_be_continued(self, project):
        """Not "no previous session recorded" — there WAS one, and it is the
        reason this run starts fresh."""
        _poison(project, "planner", POISON)
        said: list[str] = []
        self._run(project, said)
        joined = "\n".join(said)
        assert "could not be continued" in joined, joined
        assert "no previous session recorded" not in joined, (
            "a designation that existed and failed was reported as one that "
            "was never written"
        )

    def test_a_genuinely_absent_designation_still_says_so(self, project):
        """The complement, so the fix cannot be "always print the other one"."""
        said: list[str] = []
        self._run(project, said)
        joined = "\n".join(said)
        assert "no previous session recorded" in joined, joined
        assert "could not be continued" not in joined


def test_the_next_run_self_heals_rather_than_needing_manual_repair(project):
    """⚠ Why the fix is on the read side. A user who has already been
    wedged must not have to find and delete a file to get working again."""
    _poison(project, "planner", POISON)
    assert designated(project, "planner") == ""
    designate(project, "planner", "sid-later")
    assert designated(project, "planner") == "sid-later"
