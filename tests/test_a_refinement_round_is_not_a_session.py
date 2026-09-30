"""A refinement round does not spend a session slot (v0.7.0a4 dogfood S26).

`--sessions` is a ceiling on how many ENGINE sessions a Manager's run starts.
A refinement round is not one: it is rite composing and sending text from the
supervisor, outside any session, at a tick that was going to happen anyway.
If a round were charged a slot, a project whose board needs refining would
spend its whole budget refining and never start the work.

⚠ **What `--sessions` is, said here because the finding that produced this
test said something else.** S26 was filed as "`--sessions` caps concurrent
Workers (machine-thrash protection)". It does not, and the two caps it
conflates are different in kind:

- **`--sessions`** bounds the COUNT of engine sessions one Manager run
  starts (D-69, §9.14.5). Not concurrency — a Manager runs one session at a
  time — and not a spend limit, which is what the window is for. A session
  may run for any length of time inside it.
- **`sandbox.max_concurrent_workers`** bounds how many Workers run AT ONCE,
  which is the machine-thrash protection. It is enforced by
  `schedule.check_worker_cap`, at schedule-design time and again at spawn,
  and refused rather than clamped in both.

Neither is the other, and a round is charged against neither.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import rite_ai.managers.supervise as supervise_mod
from rite_ai.managers.session import FINISHED, Ending, Liveness, StartResult
from rite_ai.managers.supervise import supervise


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".rite").mkdir()
    return tmp_path


@pytest.fixture
def instant(monkeypatch):
    """Sessions that finish the moment they start; no provider is ever run."""
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
        agent="",
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


def _run(project, starter, *, max_sessions, refine=None, rounds=None):
    return supervise(
        project,
        "lead",
        engine="claude",
        max_sessions=max_sessions,
        window_seconds=0,
        starter=starter,
        resume_id_for=lambda _r, _m, _s=0.0: "sess-abc",
        poll=0,
        refine=refine,
        note=None,
    )


class TestARoundIsNotCharged:
    """⚠ **The invariant, across the whole range of ceilings and round
    counts** rather than one example of it: for every ceiling 1..4 crossed
    with a refinement that runs 0..5 times per tick, the number of sessions
    started is the ceiling — never fewer because rounds ate into it.

    The middle is where a miscount shows. At a ceiling of 1 almost any bug
    still yields 1, and with 0 rounds there is nothing to charge; it is 3
    sessions against 4 rounds that separates "rounds are free" from "rounds
    are charged".
    """

    @pytest.mark.parametrize(
        ("ceiling", "per_tick"),
        [(c, n) for c in (1, 2, 3, 4) for n in (0, 1, 2, 5)],
    )
    def test_the_ceiling_is_spent_on_sessions_only(
        self, project, instant, ceiling, per_tick
    ):
        starter, started = instant
        rounds: list[int] = []

        def refine(say, messages=()):
            for _ in range(per_tick):
                rounds.append(1)
            return []

        result = _run(project, starter, max_sessions=ceiling, refine=refine)

        assert result.sessions_started == ceiling, (
            f"ceiling {ceiling} with {per_tick} round(s) per tick started "
            f"{result.sessions_started} session(s)"
        )
        assert len(started) == ceiling
        assert "ceiling reached" in result.reason

    def test_a_round_that_raises_does_not_cost_a_session_either(self, project, instant):
        """A refinement that fails is said and the cycle goes on — it must not
        also consume the budget it never used."""
        starter, _ = instant

        def refine(say, messages=()):
            raise RuntimeError("the board could not be read")

        result = _run(project, starter, max_sessions=2, refine=refine)

        assert result.sessions_started == 2

    def test_with_no_refinement_at_all_the_count_is_the_same(self, project, instant):
        """The control: refinement is what changed, so the same run without it
        must reach the same number. Without this, a ceiling that bound for an
        unrelated reason would read as proof."""
        starter, _ = instant

        result = _run(project, starter, max_sessions=3, refine=None)

        assert result.sessions_started == 3


class TestTheTwoCapsAreDifferentThings:
    def test_the_worker_cap_is_about_concurrency_and_refuses(self):
        """`sandbox.max_concurrent_workers` is the machine-thrash protection
        S26's wording reached for. It is a different number, enforced
        elsewhere, and refused rather than clamped."""
        from rite_ai.schedule import check_worker_cap

        assert check_worker_cap(2, 3) is None
        problem = check_worker_cap(4, 3)
        assert problem and "max_concurrent_workers" in problem
        assert "refused, not clamped" in problem

    def test_the_session_ceiling_says_it_is_a_count_not_a_spend_limit(
        self, project, instant
    ):
        starter, _ = instant

        result = _run(project, starter, max_sessions=1)

        assert "COUNT, not a spend limit" in result.reason
