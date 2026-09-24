"""The resume loop stops, and both bounds are verified by RUNNING it.

This is the first place rite spends money unattended-ish — inside the
human's foreground process, per §9.14.6, but without them watching every
minute. So the ceiling and the stop condition are not decoration, and
reading them is not evidence that they bound anything.

⚠ **The most useful thing this file established was found by running, not
by reading: NEITHER BOUND SUFFICES ALONE, and the spec said so before the
test proved it.** Driven with instantaneous sessions, a 1-second window
permitted 1000 of them — no time passed, so the window never fired and only
the COUNT stopped it. Driven with sessions of realistic duration, a ceiling
of 1000 permitted three — the window fired first. The count bounds when
sessions are cheap; the window bounds when they are long. §9.14.5 says a
count does not bound cost; this is what that looks like from the outside.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

import rite_ai.managers.supervise as supervise_mod
from rite_ai.managers.session import FINISHED, Ending, Liveness, StartResult
from rite_ai.managers.supervise import STOP_VERDICTS, launch_command, supervise


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".rite").mkdir()
    return tmp_path


@pytest.fixture
def instant(monkeypatch):
    """Sessions that FINISH the moment they start. No provider is ever run.

    `ending` and `was_attached` are stubbed because a fake session name has
    no tmux pane, so the real `ending` answers `unclear` and the supervisor
    correctly stops after one cycle. These tests are about the BOUNDS, and
    a bound that is never reached because the run stopped for another reason
    proves nothing — so the stubs say "each session finished cleanly with
    nobody attached", which is the case where the bounds are what stops it.
    """
    started: list[str] = []

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
    ):
        started.append(resume_id)
        return StartResult(True, "ok", session=f"fake-{len(started)}", attach="")

    monkeypatch.setattr(
        supervise_mod, "liveness", lambda _n: Liveness(False, known=True)
    )
    monkeypatch.setattr(supervise_mod, "was_attached", lambda _n: False)
    monkeypatch.setattr(
        supervise_mod, "ending", lambda _n, human_was_present, pane="": Ending(FINISHED)
    )
    return starter, started


class TestTheCeilingBounds:
    def test_it_stops_at_the_count_and_not_one_later(self, project, instant):
        starter, started = instant
        result = supervise(
            project,
            "lead",
            engine="claude",
            max_sessions=3,
            window_seconds=0,
            starter=starter,
            resume_id_for=lambda _r, _m, _s=0.0: "sess-abc",
            poll=0,
        )
        assert result.sessions_started == 3, "the ceiling did not bound"
        assert "ceiling reached" in result.reason

    def test_the_reason_says_a_count_is_not_a_spend_limit(self, project, instant):
        """§9.14.5's caveat has to reach the person reading the output, not
        only the person reading the spec."""
        starter, _ = instant
        result = supervise(
            project,
            "lead",
            engine="claude",
            max_sessions=1,
            window_seconds=0,
            starter=starter,
            resume_id_for=lambda _r, _m, _s=0.0: "sid",
            poll=0,
        )
        assert "COUNT, not a spend limit" in result.reason

    def test_every_session_after_the_first_is_a_RESUME(self, project, instant):
        starter, started = instant
        supervise(
            project,
            "lead",
            engine="claude",
            max_sessions=3,
            window_seconds=0,
            starter=starter,
            resume_id_for=lambda _r, _m, _s=0.0: "sess-abc",
            poll=0,
        )
        assert started[0] == "", "the first session resumed something"
        assert started[1:] == ["sess-abc", "sess-abc"]


class TestTheWindowBounds:
    def test_a_window_stops_a_run_the_count_would_not(self, project, monkeypatch):
        """With sessions of realistic duration the window fires first — the
        complement of the test below, and the reason both exist."""
        alive_until: dict[str, float] = {}

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
        ):
            name = f"s{len(alive_until) + 1}"
            alive_until[name] = time.time() + 0.3
            return StartResult(True, "ok", session=name, attach="")

        monkeypatch.setattr(
            supervise_mod,
            "liveness",
            lambda n: Liveness(time.time() < alive_until.get(n, 0), known=True),
        )
        monkeypatch.setattr(supervise_mod, "was_attached", lambda _n: False)
        monkeypatch.setattr(
            supervise_mod,
            "ending",
            lambda _n, human_was_present, pane="": Ending(FINISHED),
        )
        result = supervise(
            project,
            "lead",
            engine="claude",
            max_sessions=1000,
            window_seconds=0.9,
            starter=starter,
            resume_id_for=lambda _r, _m, _s=0.0: "sid",
            poll=0.05,
        )
        assert result.sessions_started < 1000, "the window did not bound"
        assert "window elapsed" in result.reason

    def test_an_instant_session_makes_the_window_useless(self, project, instant):
        """⚠ Asserted because it is a limit, not a bug. No wall-clock time
        passes, so only the count stops it — which is why the ceiling is
        mandatory and the window alone would not be enough."""
        starter, _ = instant
        result = supervise(
            project,
            "lead",
            engine="claude",
            max_sessions=5,
            window_seconds=60,
            starter=starter,
            resume_id_for=lambda _r, _m, _s=0.0: "sid",
            poll=0,
        )
        assert result.sessions_started == 5
        assert "ceiling reached" in result.reason


class TestTheStopConditionComesFromTheLoop:
    @pytest.mark.parametrize("verdict", sorted(STOP_VERDICTS))
    def test_every_stop_verdict_stops_before_spending(self, project, instant, verdict):
        starter, _ = instant
        result = supervise(
            project,
            "lead",
            engine="claude",
            max_sessions=99,
            window_seconds=0,
            starter=starter,
            verdict=lambda _r: verdict,
            resume_id_for=lambda _r, _m, _s=0.0: "sid",
            poll=0,
        )
        assert result.sessions_started == 0, (
            f"'{verdict}' is a stop verdict and a session was started anyway"
        )

    @pytest.mark.parametrize("verdict", ["ready", "saturated", "blocked"])
    def test_a_continue_verdict_does_not_stop(self, project, instant, verdict):
        starter, _ = instant
        result = supervise(
            project,
            "lead",
            engine="claude",
            max_sessions=2,
            window_seconds=0,
            starter=starter,
            verdict=lambda _r: verdict,
            resume_id_for=lambda _r, _m, _s=0.0: "sid",
            poll=0,
        )
        assert result.sessions_started == 2

    def test_idle_reads_as_DONE_and_a_fault_does_not(self, project, instant):
        """A lifecycle that exits identically for all of them tells a human
        "finished" when it means "jammed" (§9.14.4)."""
        starter, _ = instant
        done = supervise(
            project,
            "lead",
            engine="claude",
            max_sessions=9,
            window_seconds=0,
            starter=starter,
            verdict=lambda _r: "idle",
            resume_id_for=lambda _r, _m, _s=0.0: "sid",
            poll=0,
        )
        jammed = supervise(
            project,
            "lead",
            engine="claude",
            max_sessions=9,
            window_seconds=0,
            starter=starter,
            verdict=lambda _r: "deadlocked",
            resume_id_for=lambda _r, _m, _s=0.0: "sid",
            poll=0,
        )
        assert done.reason.startswith("done:")
        assert "fault, not a completion" in jammed.reason

    def test_closed_says_nothing_restarts_it(self, project, instant):
        starter, _ = instant
        result = supervise(
            project,
            "lead",
            engine="claude",
            max_sessions=9,
            window_seconds=0,
            starter=starter,
            verdict=lambda _r: "closed",
            resume_id_for=lambda _r, _m, _s=0.0: "sid",
            poll=0,
        )
        assert "Nothing restarts it" in result.reason


class TestAFailedStartEndsTheRun:
    def test_it_does_not_retry_forever(self, project, monkeypatch):
        """A start that refuses is a reason to stop, not to try again — the
        refusals are a ceiling, a stale session or a bad name, none of which
        a retry fixes."""
        attempts = []

        def refuses(root, manager, **kw):
            attempts.append(1)
            return StartResult(False, "nope")

        monkeypatch.setattr(
            supervise_mod, "liveness", lambda _n: Liveness(False, known=True)
        )
        result = supervise(
            project,
            "lead",
            engine="claude",
            max_sessions=9,
            window_seconds=0,
            starter=refuses,
            resume_id_for=lambda _r, _m, _s=0.0: "sid",
            poll=0,
        )
        assert not result.ok
        assert len(attempts) == 1


def test_claude_launches_exactly_as_it_did_before_the_registry():
    """⚠ B3a's first half: a registry that changed the one engine rite
    actually launches would be a refactor with a behaviour change hidden
    inside it."""
    assert launch_command("claude").endswith("claude -p")
    assert launch_command("claude", "abc").endswith("claude -p --resume abc")
    assert launch_command("", "").endswith("claude -p")


def test_a_second_engine_gets_its_own_vocabulary_not_claudes():
    """⚠ THE DEFECT THIS TICKET REMOVES, and the test that used to pin it.

    `launch_command("some-other-engine", "id")` returned
    `some-other-engine -p --resume id` — Claude Code's flags appended to a
    tool that has never heard of them. The old test asserted exactly that,
    with a docstring saying the interface stays unstable "until `local`
    forces it". It is forcing it.

    Goose's spelling is measured, not invented: `run` is a subcommand rather
    than a flag, the handle is a NAME rite chooses rather than an id it must
    discover, and the instruction is a file argument rather than stdin.
    """
    built = launch_command("local:large", "", "/p.txt", agent="goose")
    assert built == "goose run -i /p.txt", built
    resumed = launch_command("local:large", "lead", "/p.txt", agent="goose")
    assert resumed == "goose run -n lead -r -i /p.txt", resumed
    assert "-p" not in resumed and "--resume" not in resumed


def test_a_substituted_binary_still_gets_claudes_spelling():
    """⚠ Not the defect — the opposite. A test stub (`sh`, `agent.sh`) is
    STANDING IN for claude, so it wants Claude's flags. Production cannot
    reach this path: the config validator's engine list is closed to
    `claude`, `human` and `local:<class>`.

    Refusing here was tried and was wrong: it broke two tests whose point is
    that the permission mode reaches the launch, removing real coverage to
    satisfy a rule about a case production cannot produce.
    """
    assert launch_command("sh", "", "/p.txt") == "sh -p < /p.txt"
    assert launch_command("/tmp/agent.sh", "abc", "", "--flag") == (
        "/tmp/agent.sh -p --flag --resume abc"
    )


def test_an_engine_whose_permission_is_environmental_refuses_a_flag():
    """Goose takes GOOSE_MODE in the environment, so "permission" cannot be
    "a flag string". The adapter that owns the engine sets it; this refuses
    rather than writing a flag the tool would reject."""
    with pytest.raises(ValueError, match="environment"):
        launch_command(
            "local:large", "", "", "--dangerously-skip-permissions", agent="goose"
        )


class TestAnUnrecognisedVerdictStops:
    """⚠ Continuing used to be the FALLTHROUGH, not a decision.

    With only `STOP_VERDICTS` and `if answer in STOP_VERDICTS: return`,
    everything else launched a session — including everything that is not a
    verdict at all. Found by rite-dd, measured with a captured starter:
    `None`, `""`, `"Idle"` with the wrong case and `"error: cannot read"`
    each started one. A verdict function that could not answer spent a
    session.

    Unreachable in production today, because `_loop_verdict` coerces with
    `or "unknown"`, wraps in `str()` and catches everything — which is the
    argument for fixing it rather than deferring it. That is the same shape
    as the duplicate guard that failed open while deterministic session
    naming quietly did the work: protection nobody had recorded as
    load-bearing, one refactor from live, in the code that spends money.
    """

    @pytest.mark.parametrize(
        "answer",
        [None, "", "Idle", "IDLE", "error: cannot read the board", "ready ", 0],
        ids=[
            "none",
            "empty",
            "wrong-case",
            "shouting",
            "error-string",
            "trailing-space",
            "zero",
        ],
    )
    def test_it_starts_nothing(self, tmp_path, answer):
        (tmp_path / ".rite").mkdir()
        started: list[str] = []

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
        ):
            started.append(manager)
            return StartResult(True, "started", session="s", attach="a")

        result = supervise(
            tmp_path,
            "lead",
            engine="sh",
            max_sessions=5,
            window_seconds=0,
            verdict=lambda _root: answer,
            starter=starter,
        )
        assert started == [], (
            f"an unrecognised verdict {answer!r} started a session — an "
            f"answer nobody recognises must not spend quota"
        )
        assert result.ok
        assert "not one of its verdicts" in result.reason

    def test_a_real_continue_verdict_still_continues(self, tmp_path):
        """The guard must not have closed the door on the ordinary case."""
        (tmp_path / ".rite").mkdir()
        started: list[str] = []

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
        ):
            started.append(manager)
            return StartResult(False, "stop here", session="", attach="")

        supervise(
            tmp_path,
            "lead",
            engine="sh",
            max_sessions=1,
            window_seconds=0,
            verdict=lambda _root: "ready",
            starter=starter,
        )
        assert started == ["lead"]


def _loop_verdict_constants() -> dict[str, str]:
    """Every verdict `loop/` declares, DISCOVERED rather than listed.

    ⚠ A draft imported the values but named the constants in a literal
    tuple, so a renamed value went red and an EIGHTH verdict simply did not
    appear — the test passed while `supervise` refused the new verdict as
    unrecognised. Found by rite-dd.

    Module-level `NAME = "lowercase"` with an uppercase NAME yields exactly
    the seven verdicts and nothing else, checked before relying on it — no
    other uppercase string constant lives at `loop/__init__.py`'s top
    level. If one ever does, the fix is for `loop` to export a `VERDICTS`
    set and for this to read it, rather than for this to grow a skip list.
    """
    import ast

    source = (
        Path(__file__).resolve().parents[1] / "src" / "rite_ai" / "loop" / "__init__.py"
    ).read_text()
    found = {}
    for node in ast.parse(source).body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or not target.id.isupper():
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            found[target.id] = node.value.value
    return found


def test_the_discovery_finds_the_verdicts_it_is_meant_to():
    """The discovery above is worthless if it silently finds nothing — an
    empty set makes every assertion below vacuously true, which is the
    failure shape that produced a passing check over no data this week."""
    found = _loop_verdict_constants()
    assert len(found) >= 7, f"discovery found {len(found)} verdicts: {found}"
    assert "IDLE" in found and found["IDLE"] == "idle"


def test_the_two_verdict_sets_are_exhaustive_over_the_loop():
    """⚠ So an EIGHTH verdict cannot silently continue.

    This is the guard that makes the split maintainable rather than a
    snapshot: whoever adds a verdict to `loop/` has to classify it here, at
    the point they add it, instead of discovering the behaviour when it is
    produced at 3am.
    """
    from rite_ai.managers.supervise import CONTINUE_VERDICTS, STOP_VERDICTS

    verdicts = set(_loop_verdict_constants().values())
    classified = STOP_VERDICTS | CONTINUE_VERDICTS
    assert not (verdicts - classified), (
        f"these loop verdicts are in neither set, so `supervise` would "
        f"refuse them as unrecognised: {sorted(verdicts - classified)}"
    )
    assert not (classified - verdicts), (
        f"these are classified but are not loop verdicts: "
        f"{sorted(classified - verdicts)}"
    )
    assert not (STOP_VERDICTS & CONTINUE_VERDICTS), "a verdict is in both sets"
