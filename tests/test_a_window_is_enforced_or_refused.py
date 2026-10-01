"""A local Manager's window is enforced, or it is not started (S35, 1b).

Window enforcement was Goose-shaped in two places that did not know about
each other: `supervise._local_environment` ended in `goose_environment(...)`
unconditionally, and `config.managers.effective_model` described a non-Goose
local Manager as getting "the server's default window". So a Manager
declaring `agent: opencode` STARTED, was served Ollama's server-wide default,
and nothing refused it.

⚠ **That default is 4,096 tokens on an unconfigured Ollama, and it is smaller
than the agents' own prompts.** RL-T0 measured opencode sending ~31 KB of
system prompt and tool schemas before the task is added and Goose ~19 KB, and
called the default "the finding that reframes every other number". A Manager
running there does bad work and reports success, which is W19. So an agent
rite cannot enforce is refused: §5.1.1's rule is that a safety property may
fail closed and never open.

Also here, because the same run found them:

- **RL-47's "only work counts"** was carried in the WORDING of a free-text
  summary ("infrastructure fault before the turn: …"), so the one caller that
  must act on it would have had to match a sentence. Measured: the first real
  run of the wired pipeline was refused before the turn and still counted as
  an attempt. It is a field now, and the rule lives on `Outcome`.
- **`rite spec stamp --all` exited 0 printing nothing** when a project had no
  derived unit text, so somebody who had just run `rite spec index` believed
  their units were stamped and had none. Naming a unit always refused clearly;
  `--all` now answers in the same voice.
"""

from __future__ import annotations

import itertools

import pytest

from rite_ai.config.managers import ManagerRole, effective_model
from rite_ai.local import enforcement
from rite_ai.local.harness import AgentReport, Outcome

KNOWN = "goose"
UNKNOWN = ("opencode", "aider", "interpreter", "", "GOOSE", "goose-cli")


def _role(agent: str, window: int = 32768) -> ManagerRole:
    return ManagerRole(
        name="small",
        engine="local:small",
        endpoint="http://localhost:11434/v1",
        model="qwen3:8b",
        agent=agent,
        context_window=window,
    )


class TestTheRegistryIsTheOneAnswer:
    def test_goose_is_enforced_and_says_what_measured_it(self):
        """⚠ An entry is a claim that somebody RAN it. Without the
        measurement recorded, the registry is this module lying in a new
        place."""
        got = enforcement.for_agent(KNOWN)

        assert got is not None
        assert "Goose 1.51.0" in got.measured_on and "0.34.2" in got.measured_on

    @pytest.mark.parametrize("agent", UNKNOWN)
    def test_everything_else_is_unenforced(self, agent):
        """Including `GOOSE` and `goose-cli`: the lookup is exact, because a
        near-miss that resolved to Goose's env would tell a different binary
        the window in a vocabulary it does not read."""
        assert enforcement.for_agent(agent) is None

    @pytest.mark.parametrize("agent", UNKNOWN)
    def test_the_refusal_names_what_is_missing(self, agent):
        """A refusal that only says no sends the next person guessing. This
        one says what to measure and where to add it."""
        said = enforcement.refusal(agent)

        assert "4,096" in said, "the number is the reason; it belongs in the text"
        assert "rite_ai.local.enforcement" in said
        assert KNOWN in said, "it names what IS enforced"


class TestTheInvariantOverTheAgentRange:
    """⚠ **Enforced or refused, with no third state, across every agent × every
    window a role can declare.**

    The middles are where the old behaviour hid: an enforced agent with no
    window was already refused, and an UNenforced agent with a perfectly good
    window was started and silently served the server default. That second
    case is the bug, and it is only visible when both axes vary — which is why
    this is a product and not two examples.
    """

    @pytest.mark.parametrize(
        ("agent", "window"),
        list(itertools.product((KNOWN, *UNKNOWN), (0, 1, 32768, 40960))),
    )
    def test_a_role_is_enforceable_exactly_when_both_hold(self, agent, window):
        role = _role(agent, window)
        enforceable = enforcement.for_agent(role.agent) is not None and window > 0

        said = effective_model(role)

        if enforceable:
            assert f"{window}-token window pinned into the model" in said
            assert "refuses" not in said
        else:
            assert "refuses it" in said, said
        # Whatever the verdict, the description never promises the server's
        # default — the sentence that described an unenforceable Manager rite
        # started anyway.
        assert "the server's default window" not in said

    @pytest.mark.parametrize("agent", UNKNOWN)
    def test_an_unenforceable_agent_is_named_in_the_description(self, agent):
        said = effective_model(_role(agent))

        assert f"agent {agent!r}" in said and "cannot enforce" in said


class TestOnlyWorkCountsAsAnAttempt:
    """RL-47, as a field rather than as a sentence to match."""

    def test_an_infrastructure_fault_is_not_an_attempt(self):
        assert not Outcome(subtask_id="s1", infrastructure_fault=True).counts_as_attempt

    def test_work_is(self):
        assert Outcome(subtask_id="s1").counts_as_attempt

    @pytest.mark.parametrize("fault", [True, False])
    def test_the_rule_does_not_depend_on_the_status(self, fault):
        """A FAILED subtask whose agent never ran is not a failing subtask —
        it is a machine that was not ready. So the status must not change the
        answer, or a retired subtask would be one nobody tried."""
        from rite_ai.local.decomposition import ACCEPTED, FAILED, RUNNING, VERIFIED

        for status in (RUNNING, FAILED, VERIFIED, ACCEPTED):
            got = Outcome(subtask_id="s1", status=status, infrastructure_fault=fault)
            assert got.counts_as_attempt is (not fault), status

    def test_the_agent_reports_it_as_a_field(self):
        """The wording is still there for a person; the FIELD is what a caller
        acts on."""
        report = AgentReport(
            claimed_success=False,
            summary="infrastructure fault before the turn: endpoint down",
            infrastructure_fault=True,
        )

        assert report.infrastructure_fault
        assert "infrastructure fault" in report.summary

    def test_a_blocked_goose_turn_sets_it(self):
        """Through the real agent: a preflight that refuses must mark the
        report, because that is the case the first real run got wrong."""
        from rite_ai.local.decomposition import Subtask
        from rite_ai.local.goose_agent import GooseAgent
        from rite_ai.local.harness import Context

        class _Blocked:
            problems = ["the endpoint is not answering"]

        agent = GooseAgent(
            model="qwen3:8b",
            endpoint="http://localhost:11434/v1",
            probe=lambda: _Blocked(),
        )
        report = agent.run(
            Context(subtask=Subtask(id="s1", intent="x"), spec_slice="s", ticket="K-1"),
            workspace=".",
        )

        assert not report.claimed_success
        assert report.infrastructure_fault, (
            "a turn that never happened was reported as an attempt"
        )

    def test_a_turn_that_ran_and_failed_is_an_attempt(self):
        """The control: without it, marking everything a fault would pass
        every test above and retire nothing."""
        report = AgentReport(claimed_success=False, summary="the tests failed")

        assert not report.infrastructure_fault
