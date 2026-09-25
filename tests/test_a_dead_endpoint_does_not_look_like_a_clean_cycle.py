"""A local Manager must not spend its window on an endpoint that is down.

⚠ **WHY THIS EXISTS, and it is the worst of the four engine breaks.**
`ending` classifies a cycle by the engine's exit status. That is correct for
`claude -p` — its exit IS the cycle boundary, which is why `-p` was chosen.
**It is not correct for every engine.** Measured 2026-09-24:

    OLLAMA_HOST=http://localhost:1  goose run -n ... -i instr.txt
    exit=0   secs=109   file created: NO

`goose run` returns 0 for an unreachable provider, and separately 0 for a
model that does not exist. So a Goose Manager pointed at a dead endpoint
reads as `finished` every cycle, and the supervisor keeps starting sessions
until `--sessions` or `--minutes` runs out: **the whole window spent, nothing
done, every cycle reported clean.**

⚠ **And the check has to come before the mail is taken.** `take_mail`
deletes what it reads, so a cycle abandoned after it would lose the message
it was about to deliver.
"""

from __future__ import annotations

from rite_ai.managers.mailbox import INBOX, read, send
from rite_ai.managers.supervise import supervise


def _project(tmp_path):
    (tmp_path / ".rite").mkdir(exist_ok=True)
    return tmp_path


def _never_called_starter(calls):
    def starter(root, manager, **kw):
        calls.append(kw)
        raise AssertionError("a session was started against a dead engine")

    return starter


class TestItStopsInsteadOfSpending:
    def test_a_dead_engine_starts_no_session_at_all(self, tmp_path):
        calls = []
        outcome = supervise(
            _project(tmp_path),
            "small",
            engine="local:small",
            agent="goose",
            max_sessions=5,
            window_seconds=600,
            verdict=lambda _r: "ready",
            starter=_never_called_starter(calls),
            engine_ready=lambda: [
                "manager small: its engine endpoint is not answering."
            ],
        )
        assert calls == [], "it started a session against an engine known to be down"
        assert not outcome.ok
        assert outcome.cycles == []

    def test_it_says_which_engine_problem_stopped_it(self, tmp_path):
        outcome = supervise(
            _project(tmp_path),
            "small",
            engine="local:small",
            agent="goose",
            max_sessions=5,
            window_seconds=600,
            verdict=lambda _r: "ready",
            starter=_never_called_starter([]),
            engine_ready=lambda: [
                "manager small: its engine endpoint is not answering."
            ],
        )
        assert "not answering" in outcome.reason, outcome.reason
        assert "Nothing was spent" in outcome.reason, (
            "the operator is not told the bound was untouched, which is the "
            f"fact that distinguishes this from a real run: {outcome.reason}"
        )

    def test_it_does_not_burn_the_ceiling_one_cycle_at_a_time(self, tmp_path):
        """⚠ The failure being prevented, stated as a count. Without the
        check the loop starts a session, reads exit 0 as `finished`, and goes
        round again — five sessions, five clean reports, no work."""
        outcome = supervise(
            _project(tmp_path),
            "small",
            engine="local:small",
            agent="goose",
            max_sessions=5,
            window_seconds=600,
            verdict=lambda _r: "ready",
            starter=_never_called_starter([]),
            engine_ready=lambda: ["endpoint down."],
        )
        assert len(outcome.cycles) == 0, (
            f"it spent {len(outcome.cycles)} of 5 sessions on a dead engine"
        )


class TestItDoesNotEatTheMail:
    def test_a_message_survives_a_cycle_the_engine_could_not_run(self, tmp_path):
        """⚠ `take_mail` DELETES what it reads. A check placed after it would
        lose the message the abandoned cycle was carrying, and the sender
        would never learn their message went nowhere."""
        root = _project(tmp_path)
        send(root, "small", INBOX, "stop after this ticket and report")
        supervise(
            root,
            "small",
            engine="local:small",
            agent="goose",
            max_sessions=5,
            window_seconds=600,
            verdict=lambda _r: "ready",
            starter=_never_called_starter([]),
            engine_ready=lambda: ["endpoint down."],
        )
        still_there = [m.text for m in read(root, "small", INBOX)]
        assert still_there == ["stop after this ticket and report"], (
            "the message was consumed by a cycle that never ran"
        )


class TestAClaudeManagerIsUnchanged:
    def test_no_check_means_no_behaviour_change(self, tmp_path):
        """⚠ The control. `engine_ready=None` is the Claude path, and a
        change that altered the one engine rite actually launches would be a
        refactor with a behaviour change hidden in it."""
        started = []

        def starter(root, manager, **kw):
            started.append(kw)
            from rite_ai.managers.session import StartResult

            return StartResult(True, "ok", session="s1", attach="a", pane="%1")

        outcome = supervise(
            _project(tmp_path),
            "lead",
            engine="claude",
            max_sessions=1,
            window_seconds=0,
            verdict=lambda _r: "ready",
            starter=starter,
            note=lambda _m: None,
        )
        assert started, "a Claude Manager did not start a session"
        assert outcome is not None
