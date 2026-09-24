"""`rite start X` continues; `rite start X --fresh` starts over.

Continuity existed WITHIN one `rite start` — the supervisor passes
`--resume` on cycles after the first — and was dropped BETWEEN
invocations, so cycle one always began with no memory. This makes
continuation the default and starting over explicit
(`docs/design/V060_SESSION_CONTINUITY.md`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from known_session import designate_known
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


class TestTheDesignationIsNeverListedAsARunningManager:
    """C11. Both files live in `.rite/user/` and `running_instances` globs
    `*.json`, which `<name>.designated.json` matches.

    ⚠ It used to stay out of the listing only because of two rules about
    something else: the stem `lead.designated` fails the tmux naming rule, and
    a designation has no `name` key. So this test RELAXES BOTH and asserts the
    separation still holds — a test that leaves them in place would pass
    against the old code and prove nothing about the new line. Measured with
    both relaxed and no skip: `rite status` said `lead: running as <session
    id> — tmux attach -t <session id>`.
    """

    def test_a_designation_is_skipped_even_when_nothing_else_would(
        self, tmp_path, monkeypatch
    ):
        import json
        import os

        import rite_ai.managers as managers
        from rite_ai.reporting.status import _manager_lines

        root = _project(tmp_path)
        # The positive control: a real record beside it must still be listed,
        # since a listing that skips everything passes the negative half.
        record_instance(
            root,
            ManagerInstance(name="real", pid=os.getpid(), session="s", engine="claude"),
        )
        designate(root, "lead", "SESSION-ABC")
        designation_path(root, "lead").write_text(
            json.dumps({"session": "SESSION-ABC", "name": "lead", "pid": os.getpid()})
        )
        monkeypatch.setattr(managers, "name_problem", lambda *a, **k: "")

        assert [i.name for i in managers.running_instances(root)] == ["real"]
        joined = "\n".join(_manager_lines(root))
        assert "SESSION-ABC" not in joined, joined
        assert designated(root, "lead") == "SESSION-ABC"


@dataclass(frozen=True)
class Launch:
    """Every argument a launch was handed — not just the one a test looked at.

    ⚠ **THIS RECORDER USED TO KEEP ONLY `resume_id`** and swallow the rest
    into `**kw`, and that is how a real defect survived a green suite: the
    fresh fallback in `supervise` launched with no prompt, the harness could
    not see the argument, so no assertion built on it could either. The
    permission mode was invisible the same way and was missing from the same
    call.

    **A recorder is a measuring instrument, and one that cannot see an
    argument reports every value of it as correct.** `extra` catches anything
    added to the launch that this class does not name yet, so the next
    argument to appear is visible on the day it appears rather than on the
    day it goes missing.
    """

    resume_id: str
    prompt: str
    permission: str
    engine: str
    max_sessions: int
    window_seconds: float
    extra: dict


def _starter(calls: list, fail_on_resume: bool = False):
    def starter(
        root,
        manager,
        *,
        engine,
        resume_id,
        max_sessions,
        window_seconds,
        prompt="",
        permission="",
        **kw,
    ):
        calls.append(
            Launch(
                resume_id=resume_id,
                prompt=prompt,
                permission=permission,
                engine=engine,
                max_sessions=max_sessions,
                window_seconds=window_seconds,
                extra=dict(kw),
            )
        )
        if fail_on_resume and resume_id:
            # ⚠ The engine refusing a resume. Detected by the START FAILING,
            # never by matching its words: a bad id and a well-formed unknown
            # id produce DIFFERENT messages, and code matching one silently
            # misses the other.
            return StartResult(False, "engine exited immediately")
        return StartResult(True, "ok", session="s1", attach="a", pane="%1")

    return starter


def _quiet(monkeypatch, mod):
    monkeypatch.setattr(
        mod, "liveness", lambda n: type("L", (), {"alive": False, "known": True})()
    )
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
        designate_known(root, "lead", "YESTERDAY")
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
        assert [c.resume_id for c in calls] == ["YESTERDAY"], (
            f"cycle one did not continue: {calls}"
        )

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
        assert [c.resume_id for c in calls] == [""]
        assert any("no previous session" in m.lower() for m in said), said


class TestAGoneSessionFallsBackLOUDLY:
    """⚠ The existence check is on the SESSION, not the file. A designation
    can be present and perfectly readable while the provider has forgotten
    that conversation — checking the file is a proxy for the property."""

    def test_it_retries_fresh_when_the_resume_is_refused(self, tmp_path, monkeypatch):
        import rite_ai.managers.supervise as sup

        root = _project(tmp_path)
        designate_known(root, "lead", "PRUNED")
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
        assert [c.resume_id for c in calls] == ["PRUNED", ""], (
            f"it did not fall back to a fresh start: {calls}"
        )
        assert result.ok

    def test_the_fallback_announces_itself(self, tmp_path, monkeypatch):
        """Silence makes it identical, from the user's side, to the
        continuation they asked for."""
        import rite_ai.managers.supervise as sup

        root = _project(tmp_path)
        designate_known(root, "lead", "PRUNED")
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
        designate_known(root_b, "lead", "PRUNED")
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
        designate_known(root, "lead", "YESTERDAY")
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
        assert [c.resume_id for c in calls] == [""], (
            f"--fresh continued anyway: {calls}"
        )

    def test_fresh_REWRITES_the_designation(self, tmp_path, monkeypatch):
        import rite_ai.managers.supervise as sup

        root = _project(tmp_path)
        designate_known(root, "lead", "YESTERDAY")
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


class TestCtrlCBeforeTheFirstCycleIsTheStatedException:
    """C17. "Designated whatever the ending" has one exception: an interrupt
    before the first cycle is appended designates nothing, and whatever was
    designated before stays. These pin both halves, so the exception is a
    stated behaviour rather than a surprise."""

    @staticmethod
    def _interrupted_in_the_launch(*_a, **_k):
        raise KeyboardInterrupt

    def _run(self, tmp_path, monkeypatch, *, fresh):
        import rite_ai.managers.supervise as sup

        root = _project(tmp_path)
        _quiet(monkeypatch, sup)
        designate_known(root, "lead", "YESTERDAY")
        said: list[str] = []
        supervise(
            root,
            "lead",
            engine="claude",
            prompt="go",
            max_sessions=3,
            window_seconds=0,
            fresh=fresh,
            verdict=lambda _r: "ready",
            starter=self._interrupted_in_the_launch,
            # If this were consulted the test would see "NEW" designated.
            resume_id_for=lambda r, m, since: "NEW",
            note=said.append,
        )
        return designated(root, "lead"), " ".join(said)

    def test_a_bare_start_keeps_what_it_set_out_to_continue(
        self, tmp_path, monkeypatch
    ):
        kept, said = self._run(tmp_path, monkeypatch, fresh=False)
        assert kept == "YESTERDAY"
        assert "PREVIOUS conversation" not in said, (
            "a bare start was warned about continuing — which is what it asked for"
        )

    def test_a_fresh_start_says_the_old_conversation_is_still_designated(
        self, tmp_path, monkeypatch
    ):
        """⚠ The half that contradicts `--fresh` REWRITES the designation.
        Measured before this was said: 'YESTERDAY' before and after, and
        nothing printed — so tomorrow's bare `rite start` would silently
        return to the conversation the user chose to abandon."""
        kept, said = self._run(tmp_path, monkeypatch, fresh=True)
        assert kept == "YESTERDAY", "the old id was dropped — that is a design change"
        assert "PREVIOUS conversation" in said and "--fresh" in said, said


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

    # ⚠ This class used to carry its own private recorder, because the
    # shared `_starter` could not see `prompt` or `permission`. C3 fixed the
    # shared one, so the workaround is gone: two recorders drifting apart is
    # the same hazard as one that cannot see, arriving more slowly.

    def test_the_relaunch_is_given_the_opening_prompt(self, tmp_path, monkeypatch):
        import rite_ai.managers.supervise as sup

        root = _project(tmp_path)
        designate_known(root, "lead", "PRUNED")
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
            starter=_starter(seen, fail_on_resume=True),
            note=lambda _m: None,
        )
        assert len(seen) == 2, f"expected a resumed try then a fresh one: {seen}"
        assert seen[1].resume_id == "", "the fallback must not resume"
        assert seen[1].prompt, (
            "the fresh relaunch was handed NO prompt. `claude -p` with empty "
            "stdin exits 1, so the run rite just announced as starting fresh "
            "cannot start at all — and the operator's instruction is gone"
        )
        assert "OPENING INSTRUCTION" in seen[1].prompt, (
            f"the relaunch got something other than the opening prompt: "
            f"{seen[1].prompt!r}"
        )
        # ⚠ THE SAME CALL LOST THE PERMISSION MODE TOO, and for the same
        # reason: `permission=` was added to the FIRST launch when the
        # permission work landed and not to this one. `launch_command` only
        # adds the flag `if permission`, so the fallback ran `claude -p`
        # with none — which the permission design records as "a working loop
        # around a Manager that cannot act".
        assert seen[1].permission == seen[0].permission, (
            f"the fresh relaunch was given a different permission mode from "
            f"the resumed attempt: {seen[0].permission!r} then "
            f"{seen[1].permission!r}. A Manager with no permission mode "
            "starts and can do nothing"
        )

    def test_the_resumed_attempt_still_gets_the_continuation(
        self, tmp_path, monkeypatch
    ):
        """So the fix cannot be "send the opening prompt to both"."""
        import rite_ai.managers.supervise as sup

        root = _project(tmp_path)
        designate_known(root, "lead", "PRUNED")
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
            starter=_starter(seen, fail_on_resume=True),
            note=lambda _m: None,
        )
        assert "OPENING INSTRUCTION" not in seen[0].prompt, (
            "a continuation was handed the opening instruction — the failure "
            "D-90 was amended to prevent"
        )


class TestTheRecorderCanSeeWhatItIsRecording:
    """C3. The recorder is the instrument; these are its calibration.

    ⚠ **A green suite with a blind recorder proves nothing**, which is the
    whole point of this ticket. `supervise`'s fresh fallback shipped without
    a prompt and without a permission mode, through a harness that kept only
    `resume_id` — every assertion built on that harness passed, because none
    of them could see the arguments that were missing.

    So these do not test `supervise`. They test that the harness would NOTICE
    if `supervise` stopped passing something — which is exactly what the two
    mutations recorded in the commit message demonstrate.
    """

    def test_a_launch_records_the_prompt_it_was_handed(self, tmp_path, monkeypatch):
        import rite_ai.managers.supervise as sup

        root = _project(tmp_path)
        _quiet(monkeypatch, sup)
        calls: list = []
        supervise(
            root,
            "lead",
            engine="claude",
            max_sessions=1,
            window_seconds=0,
            prompt="ORIENT-ME",
            verdict=lambda _r: "ready",
            starter=_starter(calls),
        )
        assert calls, "nothing launched, so this measures nothing"
        # The cycle instruction is the operator's prompt with the mailbox
        # reply instructions appended, so this checks the operator's text
        # LEADS it rather than equalling it — appending is the behaviour, and
        # a test asserting equality would break on any addition to that text
        # while proving nothing more.
        assert calls[0].prompt.startswith("ORIENT-ME"), (
            "the launch did not carry the operator's instruction, or the "
            f"recorder cannot see it: {calls[0]}"
        )

    def test_a_launch_records_the_permission_mode_it_was_handed(
        self, tmp_path, monkeypatch
    ):
        """⚠ The argument whose absence produced a Manager that ran three
        cycles and could not act. Invisible to the old recorder."""
        import rite_ai.managers.supervise as sup
        from rite_ai.managers.permissions import PERMISSION_FLAG

        root = _project(tmp_path)
        _quiet(monkeypatch, sup)
        calls: list = []
        supervise(
            root,
            "lead",
            engine="claude",
            max_sessions=1,
            window_seconds=0,
            prompt="go",
            verdict=lambda _r: "ready",
            starter=_starter(calls),
        )
        assert calls, "nothing launched, so this measures nothing"
        assert calls[0].permission == PERMISSION_FLAG, (
            "the launch carried no permission mode, so the Manager would "
            f"start and be unable to act: {calls[0]}"
        )

    def test_an_argument_the_recorder_does_not_name_is_still_captured(self):
        """`extra` is what stops this fix from being a one-time patch. An
        argument added to the launch tomorrow is visible the day it appears,
        not the day somebody notices it went missing."""
        calls: list = []
        starter = _starter(calls)
        starter(
            Path("/tmp"),
            "lead",
            engine="claude",
            resume_id="",
            max_sessions=1,
            window_seconds=0,
            prompt="p",
            permission="--flag",
            something_new="SEEN",
        )
        assert calls[0].extra == {"something_new": "SEEN"}, calls[0]


class TestADesignationMustBeThisProjects:
    """C8. `supervise` passed a designated id to `--resume` unexamined, so a
    file naming another project's session was continued and announced as a
    continuation. Measured through `rite start`: the engine was launched with
    `--resume <another project's session id>`."""

    def _run(self, tmp_path, monkeypatch, sid, *, own):
        import json

        import rite_ai.managers.supervise as sup
        from rite_ai.managers.transcripts import project_transcript_dir

        root = _project(tmp_path / "proj")
        _quiet(monkeypatch, sup)
        where = project_transcript_dir(root if own else Path("/somewhere/else"))
        where.mkdir(parents=True, exist_ok=True)
        (where / f"{sid}.jsonl").write_text(json.dumps({"sessionId": sid}) + "\n")
        designate(root, "lead", sid)
        calls: list = []
        said: list[str] = []
        supervise(
            root,
            "lead",
            engine="claude",
            prompt="go",
            max_sessions=1,
            window_seconds=0,
            verdict=lambda _r: "ready",
            starter=_starter(calls),
            note=said.append,
        )
        return calls, " ".join(said)

    def test_another_projects_session_is_not_continued(self, tmp_path, monkeypatch):
        calls, said = self._run(tmp_path, monkeypatch, "FOREIGN-1", own=False)
        assert calls[0].resume_id == "", f"resumed another project's session: {calls}"
        assert "not one of this project's conversations" in said, said
        # Its own sentence, not the provider-forgot-it one — different facts.
        assert "could not be continued" not in said, said

    def test_this_projects_session_still_is(self, tmp_path, monkeypatch):
        """The control: a check that refused everything would pass above."""
        calls, said = self._run(tmp_path, monkeypatch, "MINE-1", own=True)
        assert calls[0].resume_id == "MINE-1", calls
        assert "not one of this project's" not in said, said
