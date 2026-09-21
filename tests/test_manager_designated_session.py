"""`rite start X` continues; `rite start X --fresh` starts over.

Continuity existed WITHIN one `rite start` — the supervisor passes
`--resume` on cycles after the first — and was dropped BETWEEN
invocations, so cycle one always began with no memory. This makes
continuation the default and starting over explicit
(`docs/design/V060_SESSION_CONTINUITY.md`).
"""

from __future__ import annotations

from pathlib import Path

from rite_ai.managers import (
    ManagerInstance,
    designate,
    designated,
    designation_path,
    forget_instance,
    record_instance,
)
from rite_ai.managers.session import StartResult
from rite_ai.managers.supervise import supervise


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".rite").mkdir(parents=True, exist_ok=True)
    return tmp_path


class TestTheDesignationOutlivesTheRun:
    """⚠ It cannot live in the instance file. `forget_instance` unlinks
    `<name>.json` and Ctrl-C calls it — so a designation stored there would
    be erased by the ordinary way a user stops a Manager, which is exactly
    the run they most want to continue tomorrow."""

    def test_it_round_trips(self, tmp_path):
        root = _project(tmp_path)
        assert designated(root, "lead") == ""
        designate(root, "lead", "SESSION-ABC")
        assert designated(root, "lead") == "SESSION-ABC"

    def test_it_survives_forget_instance(self, tmp_path):
        root = _project(tmp_path)
        record_instance(
            root,
            ManagerInstance(name="lead", pid=1, session="s", engine="claude"),
        )
        designate(root, "lead", "SESSION-ABC")
        forget_instance(root, "lead")
        assert designated(root, "lead") == "SESSION-ABC", (
            "stopping a Manager erased what tomorrow's `rite start` continues"
        )

    def test_each_manager_designates_independently(self, tmp_path):
        root = _project(tmp_path)
        designate(root, "lead", "A")
        designate(root, "second", "B")
        assert designated(root, "lead") == "A"
        assert designated(root, "second") == "B"
        assert designation_path(root, "lead") != designation_path(root, "second")

    def test_an_unreadable_designation_is_no_designation(self, tmp_path):
        """Fail to a fresh start, never to a crash: there is no error state
        here, and a corrupt file must not stop a user working."""
        root = _project(tmp_path)
        designate(root, "lead", "A")
        designation_path(root, "lead").write_text("{not json")
        assert designated(root, "lead") == ""


def _starter(calls: list, fail_on_resume: bool = False):
    def starter(
        root, manager, *, engine, resume_id, max_sessions, window_seconds, **kw
    ):
        calls.append(resume_id)
        if fail_on_resume and resume_id:
            # ⚠ The engine refusing a resume. Detected by the START FAILING,
            # never by matching its words: a bad id and a well-formed unknown
            # id produce DIFFERENT messages, and code matching one silently
            # misses the other.
            return StartResult(False, "engine exited immediately")
        return StartResult(True, "ok", session="s1", attach="a", pane="%1")

    return starter


def _quiet(monkeypatch, mod):
    monkeypatch.setattr(mod, "liveness", lambda n: type("L", (), {"alive": False})())
    monkeypatch.setattr(mod, "was_attached", lambda n: False)
    monkeypatch.setattr(mod, "stop_session", lambda n: None)
    monkeypatch.setattr(
        mod,
        "ending",
        lambda n, human_was_present, pane="": type(
            "E", (), {"kind": "quit", "resume": False, "status": 0, "detail": ""}
        )(),
    )


class TestABareStartContinues:
    def test_the_designated_id_is_passed_on_the_FIRST_cycle(
        self, tmp_path, monkeypatch
    ):
        """The whole change. Cycle one used to begin with `resume_from = ""`."""
        import rite_ai.managers.supervise as sup

        root = _project(tmp_path)
        designate(root, "lead", "YESTERDAY")
        _quiet(monkeypatch, sup)
        calls: list[str] = []
        supervise(
            root,
            "lead",
            engine="claude",
            max_sessions=1,
            window_seconds=0,
            verdict=lambda _r: "ready",
            starter=_starter(calls),
        )
        assert calls == ["YESTERDAY"], f"cycle one did not continue: {calls}"

    def test_no_designation_starts_fresh_and_says_which(self, tmp_path, monkeypatch):
        import rite_ai.managers.supervise as sup

        root = _project(tmp_path)
        _quiet(monkeypatch, sup)
        calls: list[str] = []
        said: list[str] = []
        supervise(
            root,
            "lead",
            engine="claude",
            max_sessions=1,
            window_seconds=0,
            verdict=lambda _r: "ready",
            starter=_starter(calls),
            note=said.append,
        )
        assert calls == [""]
        assert any("no previous session" in m.lower() for m in said), said


class TestAGoneSessionFallsBackLOUDLY:
    """⚠ The existence check is on the SESSION, not the file. A designation
    can be present and perfectly readable while the provider has forgotten
    that conversation — checking the file is a proxy for the property."""

    def test_it_retries_fresh_when_the_resume_is_refused(self, tmp_path, monkeypatch):
        import rite_ai.managers.supervise as sup

        root = _project(tmp_path)
        designate(root, "lead", "PRUNED")
        _quiet(monkeypatch, sup)
        calls: list[str] = []
        said: list[str] = []
        result = supervise(
            root,
            "lead",
            engine="claude",
            max_sessions=1,
            window_seconds=0,
            verdict=lambda _r: "ready",
            starter=_starter(calls, fail_on_resume=True),
            note=said.append,
        )
        assert calls == ["PRUNED", ""], (
            f"it did not fall back to a fresh start: {calls}"
        )
        assert result.ok

    def test_the_fallback_announces_itself(self, tmp_path, monkeypatch):
        """Silence makes it identical, from the user's side, to the
        continuation they asked for."""
        import rite_ai.managers.supervise as sup

        root = _project(tmp_path)
        designate(root, "lead", "PRUNED")
        _quiet(monkeypatch, sup)
        said: list[str] = []
        supervise(
            root,
            "lead",
            engine="claude",
            max_sessions=1,
            window_seconds=0,
            verdict=lambda _r: "ready",
            starter=_starter([], fail_on_resume=True),
            note=said.append,
        )
        joined = " ".join(said).lower()
        assert "could not be continued" in joined, said
        assert "fresh" in joined, said

    def test_gone_and_never_had_one_do_NOT_read_the_same(self, tmp_path, monkeypatch):
        """ "You never had one" and "the one you had is gone" are different
        facts to the person reading — the timezone precedent."""
        import rite_ai.managers.supervise as sup

        _quiet(monkeypatch, sup)
        never: list[str] = []
        root_a = _project(tmp_path / "a")
        supervise(
            root_a,
            "lead",
            engine="claude",
            max_sessions=1,
            window_seconds=0,
            verdict=lambda _r: "ready",
            starter=_starter([]),
            note=never.append,
        )
        gone: list[str] = []
        root_b = _project(tmp_path / "b")
        designate(root_b, "lead", "PRUNED")
        supervise(
            root_b,
            "lead",
            engine="claude",
            max_sessions=1,
            window_seconds=0,
            verdict=lambda _r: "ready",
            starter=_starter([], fail_on_resume=True),
            note=gone.append,
        )
        assert " ".join(never) != " ".join(gone)


class TestFreshRedesignates:
    """⚠ SETTLED, not emergent. `--fresh` REWRITES the designation.

    The alternative — skip once, leave the old id — orphans the new
    session: the user starts over, works all day, and tomorrow's bare
    `rite start` silently returns to the conversation they deliberately
    abandoned. That is the confident-wrong-answer shape, and the day's work
    is reachable only by whatever else names sessions.
    """

    def test_fresh_ignores_the_designation(self, tmp_path, monkeypatch):
        import rite_ai.managers.supervise as sup

        root = _project(tmp_path)
        designate(root, "lead", "YESTERDAY")
        _quiet(monkeypatch, sup)
        calls: list[str] = []
        supervise(
            root,
            "lead",
            engine="claude",
            max_sessions=1,
            window_seconds=0,
            verdict=lambda _r: "ready",
            starter=_starter(calls),
            fresh=True,
        )
        assert calls == [""], f"--fresh continued anyway: {calls}"

    def test_fresh_REWRITES_the_designation(self, tmp_path, monkeypatch):
        import rite_ai.managers.supervise as sup

        root = _project(tmp_path)
        designate(root, "lead", "YESTERDAY")
        _quiet(monkeypatch, sup)
        supervise(
            root,
            "lead",
            engine="claude",
            max_sessions=1,
            window_seconds=0,
            verdict=lambda _r: "ready",
            starter=_starter([]),
            fresh=True,
            resume_id_for=lambda r, m, since: "TODAY",
        )
        assert designated(root, "lead") == "TODAY", (
            "the fresh session was orphaned — tomorrow's bare `rite start` "
            "would silently return to the abandoned conversation"
        )


class TestTheDesignationIsWrittenFromTheRun:
    def test_a_cycle_that_ends_designates_what_it_ran(self, tmp_path, monkeypatch):
        import rite_ai.managers.supervise as sup

        root = _project(tmp_path)
        _quiet(monkeypatch, sup)
        supervise(
            root,
            "lead",
            engine="claude",
            max_sessions=1,
            window_seconds=0,
            verdict=lambda _r: "ready",
            starter=_starter([]),
            resume_id_for=lambda r, m, since: "OBSERVED",
        )
        assert designated(root, "lead") == "OBSERVED"

    def test_ctrl_c_still_designates(self, tmp_path, monkeypatch):
        """⚠ The case a user most wants to continue. Stopping with Ctrl-C is
        the ordinary way to end a run, not an anomaly."""
        import rite_ai.managers.supervise as sup

        root = _project(tmp_path)
        _quiet(monkeypatch, sup)
        monkeypatch.setattr(
            sup, "liveness", lambda n: (_ for _ in ()).throw(KeyboardInterrupt())
        )
        supervise(
            root,
            "lead",
            engine="claude",
            max_sessions=1,
            window_seconds=0,
            verdict=lambda _r: "ready",
            starter=_starter([]),
            resume_id_for=lambda r, m, since: "INTERRUPTED",
        )
        assert designated(root, "lead") == "INTERRUPTED", (
            "Ctrl-C lost the designation — the ordinary stop erased what "
            "tomorrow continues"
        )


class TestTheFreshFallbackIsGivenSomethingToDo:
    """⚠ The existing fallback test asserts the ANNOUNCEMENT. That is why
    this survived.

    `test_a_dead_designation_falls_back_to_fresh` checks that rite says
    "could not be continued ... fresh". It does, correctly. What nothing
    checked is what the relaunch is HANDED — and `_starter` above records
    only `resume_id`, swallowing everything else into `**kw`, so the prompt
    was invisible to the harness as well as to the assertions.

    Measured on the code before this test: the second `launch(...)` passed
    `resume_id=""` and omitted `prompt`, taking `_default_starter`'s
    `prompt=""` default.

        launch 1: resume_id='11111111-222'  prompt='Continue the work...'
        launch 2: resume_id=''              prompt=''

    `start_session` writes `prompt.txt` "even when empty", and the launch is
    `claude -p < prompt.txt`. This module's own measured note says `-p` with
    no input "exits 1 saying Input must be provided either through stdin or
    as a prompt argument when using --print". So the path that announces a
    fresh start handed the engine an empty file, and the operator's
    instruction was discarded on the one path designed to recover.
    """

    def _recording_starter(self, seen: list):
        def starter(
            root,
            manager,
            *,
            engine,
            resume_id,
            max_sessions,
            window_seconds,
            prompt="",
            **kw,
        ):
            seen.append({"resume_id": resume_id, "prompt": prompt})
            if resume_id:
                return StartResult(False, "engine exited immediately")
            return StartResult(True, "ok", session="s1", attach="a", pane="%1")

        return starter

    def test_the_relaunch_is_given_the_opening_prompt(self, tmp_path, monkeypatch):
        import rite_ai.managers.supervise as sup

        root = _project(tmp_path)
        designate(root, "lead", "PRUNED")
        _quiet(monkeypatch, sup)
        seen: list = []
        supervise(
            root,
            "lead",
            engine="claude",
            max_sessions=1,
            window_seconds=0,
            prompt="OPENING INSTRUCTION: do the work",
            verdict=lambda _r: "ready",
            starter=self._recording_starter(seen),
            note=lambda _m: None,
        )
        assert len(seen) == 2, f"expected a resumed try then a fresh one: {seen}"
        assert seen[1]["resume_id"] == "", "the fallback must not resume"
        assert seen[1]["prompt"], (
            "the fresh relaunch was handed NO prompt. `claude -p` with empty "
            "stdin exits 1, so the run rite just announced as starting fresh "
            "cannot start at all — and the operator's instruction is gone"
        )
        assert "OPENING INSTRUCTION" in seen[1]["prompt"], (
            f"the relaunch got something other than the opening prompt: "
            f"{seen[1]['prompt']!r}"
        )

    def test_the_resumed_attempt_still_gets_the_continuation(
        self, tmp_path, monkeypatch
    ):
        """So the fix cannot be "send the opening prompt to both"."""
        import rite_ai.managers.supervise as sup

        root = _project(tmp_path)
        designate(root, "lead", "PRUNED")
        _quiet(monkeypatch, sup)
        seen: list = []
        supervise(
            root,
            "lead",
            engine="claude",
            max_sessions=1,
            window_seconds=0,
            prompt="OPENING INSTRUCTION: do the work",
            verdict=lambda _r: "ready",
            starter=self._recording_starter(seen),
            note=lambda _m: None,
        )
        assert "OPENING INSTRUCTION" not in seen[0]["prompt"], (
            "a continuation was handed the opening instruction — the failure "
            "D-90 was amended to prevent"
        )
