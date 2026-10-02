"""A session that changed nothing is not repeated on an unchanged board (F22).

**Observed** in the v0.6.0 dogfood (2026-09-28, macOS, a Claude Owner on a
Jira board it could not read): after its last useful turn at 04:37, `rite
start lead --sessions 10` started eight more sessions in eighty seconds.
Each ran `rite status`, said "No change — stopping here" and ended cleanly;
the loop still said `ready`, because tickets were labelled `scheduled`; so
the supervisor started the next. The run ended on `ceiling reached`.

**The pass condition, pre-registered in the dogfood write-up and restated
there as measurable:** with a queue the Owner cannot act on, AT MOST ONE
session starts after the last session that changed anything, until mail
arrives or the board changes. On the code before this fix that number was
the ceiling minus the useful sessions: 8 of 10 in the dogfood, 9 of 10 here.

The harness is `test_mail_causes_a_cycle`'s: no tmux, a fake engine, and a
virtual clock the supervisor's own pause advances.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import rite_ai.managers.supervise as sup
from rite_ai.cli.main import LoopAnswer
from rite_ai.managers import mailbox
from rite_ai.managers.supervise import StartResult, supervise

OWNER = "lead"


class _Ending:
    kind = "finished"
    resume = True
    status = 0
    detail = ""


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path
    (root / ".rite").mkdir()
    state = {"t": 0.0, "root": root, "between": []}

    def pause(_seconds):
        state["t"] += 2.0
        for step in state["between"]:
            step()

    monkeypatch.setattr(
        sup, "liveness", lambda n: type("L", (), {"alive": False, "known": True})()
    )
    monkeypatch.setattr(sup, "was_attached", lambda n: False)
    monkeypatch.setattr(sup, "ending", lambda n, human_was_present, pane="": _Ending())
    monkeypatch.setattr(sup, "stop_session", lambda s: None)
    monkeypatch.setattr(sup, "forget_instance", lambda r, m: None)
    monkeypatch.setattr(sup, "_sleep", pause)
    return state


def _board(ready=("KAN-6", "KAN-7", "KAN-8", "KAN-9"), verdict="ready"):
    """What `_loop_verdict` returns for a board: the same tickets every read."""
    return LoopAnswer.of(
        SimpleNamespace(verdict=verdict, ready=list(ready), blocked={})
    )


def _run(world, *, sessions, minutes, useful=1, cycle_secs=10.0, board=None):
    """An Owner whose first `useful` sessions each write a reply (progress),
    and whose later sessions do nothing at all, as the dogfood's did."""
    root = world["root"]
    starts: list[float] = []
    prompts: list[str] = []
    said: list[str] = []
    board = board or (lambda: _board())

    def starter(r, m, *, engine, resume_id, max_sessions, window_seconds, **kw):
        starts.append(world["t"])
        prompts.append(kw.get("prompt") or "")
        if len(starts) <= useful:
            mailbox.send(root, OWNER, mailbox.OUTBOX, f"did something {len(starts)}")
        world["t"] += cycle_secs
        return StartResult(True, "ok", session=f"s{len(starts)}", attach="a")

    result = supervise(
        root,
        OWNER,
        engine="claude",
        max_sessions=sessions,
        window_seconds=minutes * 60,
        prompt="OPEN",
        starter=starter,
        verdict=lambda r: board(),
        resume_id_for=lambda r, m, since=0.0: "sess-1",
        note=said.append,
        poll=0,
        now=lambda: world["t"],
    )
    return result, starts, prompts, said


class TestTheDogfoodShape:
    def test_at_most_one_session_after_the_last_useful_one(self, world):
        result, starts, _, said = _run(world, sessions=10, minutes=10)
        after_useful = len(starts) - 1
        assert after_useful <= 1, (
            f"{after_useful} sessions after the last useful one; the dogfood "
            f"saw 8 of 10. Sessions started at {starts}"
        )
        # It did not end on the ceiling: the run waited, and the window ended it.
        assert "ceiling reached" not in result.reason
        assert "window elapsed" in result.reason
        assert any("is waiting, spending nothing: session 2" in s for s in said)

    def test_no_session_at_all_is_repeated_when_the_first_does_nothing(self, world):
        result, starts, _, _ = _run(world, sessions=10, minutes=10, useful=0)
        assert len(starts) == 1


class TestWhatWakesIt:
    def test_mail_starts_one_session_and_it_carries_the_mail(self, world):
        root = world["root"]
        sent = {"done": False}

        def dm_arrives():
            if not sent["done"] and world["t"] >= 200:
                mailbox.send(root, OWNER, mailbox.INBOX, "kan-8 too")
                sent["done"] = True

        world["between"].append(dm_arrives)
        result, starts, prompts, _ = _run(world, sessions=10, minutes=10)
        carrying = [i for i, p in enumerate(prompts) if "kan-8 too" in p]
        assert len(carrying) == 1
        # Started because of the mail, after it arrived, and it is the only
        # session since the guard engaged.
        assert starts[carrying[0]] >= 200
        assert len(starts) == 3

    def test_a_changed_board_starts_one_session(self, world):
        tickets = {"ready": ("KAN-6", "KAN-7")}

        def file_one():
            if world["t"] >= 300:
                tickets["ready"] = ("KAN-6", "KAN-7", "KAN-10")

        world["between"].append(file_one)
        result, starts, _, said = _run(
            world, sessions=10, minutes=10, board=lambda: _board(tickets["ready"])
        )
        assert any("the board changed" in s for s in said)
        assert len(starts) == 3
        assert starts[2] >= 300

    def test_a_change_in_the_project_starts_one_session(self, world):
        root = world["root"]
        claimed = {"done": False}

        def someone_claims():
            if not claimed["done"] and world["t"] >= 150:
                (root / ".rite" / "claims.json").write_text('[{"worker": "w1"}]')
                claimed["done"] = True

        world["between"].append(someone_claims)
        _, starts, _, said = _run(world, sessions=10, minutes=10)
        assert any("the project changed (claims)" in s for s in said)
        assert len(starts) == 3


class TestNotEngaged:
    def test_sessions_that_each_change_something_are_not_held(self, world):
        result, starts, _, _ = _run(world, sessions=6, minutes=10, useful=99)
        assert len(starts) == 6
        assert "ceiling reached" in result.reason

    def test_a_verdict_without_a_basis_never_engages_it(self, world):
        """A caller that returns a bare string cannot tell "unchanged", so the
        guard stays out of the way rather than guessing."""
        _, starts, _, _ = _run(
            world, sessions=5, minutes=10, useful=0, board=lambda: "ready"
        )
        assert len(starts) == 5


class TestTheJournalIsNotProgress:
    def test_a_session_that_only_journals_is_idle(self, world, monkeypatch):
        """SPEC §9.15.5: nothing rite does may depend on the journal. A
        session whose only act is an entry is therefore judged idle, and the
        entry is really written, so this is not passing on a no-op."""
        from rite_ai.managers import journal

        root = world["root"]
        wrote = []

        def journalling_starter(r, m, **kw):
            result = journal.write_observation(
                root,
                manager=OWNER,
                anchor="`rite status` 2026-09-28",
                observed="nothing changed",
                expected="work",
            )
            wrote.append(result)
            world["t"] += 10.0
            return StartResult(True, "ok", session=f"s{len(wrote)}", attach="a")

        result = supervise(
            root,
            OWNER,
            engine="claude",
            max_sessions=10,
            window_seconds=600,
            prompt="OPEN",
            starter=journalling_starter,
            verdict=lambda r: _board(),
            resume_id_for=lambda r, m, since=0.0: "sess-1",
            note=lambda s: None,
            poll=0,
            now=lambda: world["t"],
        )
        assert len(wrote) == 1
        assert wrote[0].ok, wrote[0].message
        assert wrote[0].path is not None and wrote[0].path.is_file()
        assert "window elapsed" in result.reason


def _mail_every(world, seconds: float, start: float = 100.0):
    """A message every `seconds` from `start`: what wakes a waiting Manager.
    The session it starts changes nothing (the starter writes no reply)."""
    root = world["root"]
    sent = {"next": start, "n": 0}

    def arrives():
        if world["t"] >= sent["next"]:
            sent["n"] += 1
            mailbox.send(root, OWNER, mailbox.INBOX, f"any news? ({sent['n']})")
            sent["next"] += seconds

    world["between"].append(arrives)
    return sent


class TestAWaitingManagerDoesNotSpendItsCeiling:
    """🔴 SCRUM-24. A Manager waiting on an answer was woken, found nothing
    to do, and each of those sessions counted toward --sessions, so the run
    stopped on "ceiling reached" while it was only waiting. A session counts
    when it changed something rite can see (`progress.footprint`), or when
    rite could not judge it (`test_a_verdict_without_a_basis_never_engages_it`
    pins that half)."""

    def test_sessions_that_changed_nothing_do_not_reach_the_ceiling(self, world):
        _mail_every(world, 120.0, start=100.0)
        result, starts, prompts, _ = _run(world, sessions=2, minutes=10, useful=1)
        # One useful session, then idle ones woken by mail: before SCRUM-24
        # the second (idle) session spent the ceiling of 2, and the next
        # message ended the run unread.
        assert "ceiling reached" not in result.reason, result.reason
        assert len(starts) >= 3, starts
        assert sum("any news? (1)" in p for p in prompts) == 1
        assert sum(not c.idle for c in result.cycles) == 1
        assert result.sessions_started == len(starts)

    def test_idle_sessions_have_an_allowance_of_their_own(self, world):
        """Not counted toward the ceiling is not unbounded: a session that
        changes nothing still spends tokens."""
        _mail_every(world, 30.0, start=20.0)
        result, starts, _, _ = _run(world, sessions=2, minutes=60, useful=0)
        assert len(starts) == 2, starts
        assert all(c.idle for c in result.cycles)
        assert "changed nothing" in result.reason
        assert "allowance" in result.reason
        assert "window elapsed" not in result.reason

    def test_the_wait_line_says_it_does_not_count(self, world):
        _, _, _, said = _run(world, sessions=10, minutes=10)
        assert any("does not count toward --sessions" in s for s in said)
