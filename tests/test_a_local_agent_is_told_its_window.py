"""A local agent is told its window, or told it will not be (S35, local tier).

S33 settled the rule that refuses: a local Manager declaring no
`context_window` does not start, whatever its agent. This is the other half —
what happens for an agent rite has no env mapping for.

⚠ **AND IT IS A DISPATCH, NOT A REFUSAL. An earlier version of this file
asserted the opposite and was wrong.** It refused such an agent even with a
window declared, on the premise that without the agent-specific env the window
is unenforced. That premise is false, and `context_window.pin_window`'s own
contract says why: it gives "a model that is served with exactly `window`
tokens, whatever the server's default" — the pin goes INTO THE MODEL on the
server, so Ollama serves that window to ANY client. `GOOSE_CONTEXT_LIMIT` only
tells Goose the number so it can compact at 80%.

So the window is enforced for everyone. What an unmapped agent loses is being
TOLD: it cannot compact before the limit and meets it instead. That is a
degradation to say out loud, not a reason to refuse a Manager that works —
refusing it is what S33's tests caught, and they are what this file is now
written against.

Two more pieces of the local tier live here, because the same run found them:

- **RL-47's "only work counts"** was carried in the WORDING of a free-text
  summary ("infrastructure fault before the turn: …"), so the one caller that
  must act on it would have had to match a sentence. Measured: the first real
  run of the wired pipeline was refused before the turn and still counted as
  an attempt. It is a field now, and the rule lives on `Outcome`.
- **`rite spec stamp --all` exited 0 printing nothing** when a project had no
  derived unit text, so somebody who had just run `rite spec index` believed
  their units were stamped and had none. Naming a unit always refused clearly;
  `--all` answers in the same voice now.
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
    def test_the_note_says_what_is_lost_and_what_is_not(self, agent):
        """⚠ The note has to say BOTH halves, because saying only one is how
        this got built wrong the first time: the window IS served, and the
        agent is NOT told it."""
        said = enforcement.not_told(agent, 32768)

        assert "pinned into the model" in said, "it must say the window holds"
        assert "serves it to any client" in said
        assert "not" in said and "TELL" in said, "and that the agent is untold"
        assert "refus" not in said.lower(), "this is not a refusal"
        assert "rite_ai.local.enforcement" in said
        assert KNOWN in said, "it names the agents rite can tell"


class TestTheInvariantOverTheAgentRange:
    """⚠ **The invariant: refused exactly when no window is declared, and told
    exactly when the agent is mapped — across every agent × every window.**

    Two independent facts, so the matrix is the only honest way to assert it.
    The middles are where the earlier version of this file was WRONG: a mapped
    agent with no window is refused (S33's rule), and an UNMAPPED agent with a
    window is **accepted and pinned** — that second cell is the one the first
    version refused, and S33's tests are what caught it.
    """

    @pytest.mark.parametrize(
        ("agent", "window"),
        list(itertools.product((KNOWN, *UNKNOWN), (0, 1, 32768, 40960))),
    )
    def test_refused_only_for_an_undeclared_window(self, agent, window):
        role = _role(agent, window)

        said = effective_model(role)

        # S33's rule, and the ONLY thing that refuses here.
        assert ("refuses it" in said) is (window == 0), said
        if window:
            assert f"{window}-token window pinned into the model" in said
        # Never the old sentence, which described a Manager rite started and
        # could not enforce at all.
        assert "the server's default window" not in said

    @pytest.mark.parametrize(
        ("agent", "window"),
        list(itertools.product((KNOWN, *UNKNOWN), (1, 32768, 40960))),
    )
    def test_told_exactly_when_the_agent_is_mapped(self, agent, window):
        """The second axis: with a window declared, the description says
        whether the agent is told the number."""
        role = _role(agent, window)

        said = effective_model(role)
        mapped = enforcement.for_agent(agent) is not None

        assert ("is not told" in said) is (not mapped), said
        if not mapped:
            assert f"agent {agent!r} is not told" in said

    @pytest.mark.parametrize("agent", UNKNOWN)
    def test_an_unmapped_agent_is_still_pinned_and_not_refused(self, agent):
        """⚠ The cell the first version got wrong, asserted on its own so it
        cannot be lost in a matrix again."""
        said = effective_model(_role(agent, 32768))

        assert "32768-token window pinned into the model" in said
        assert "refuses" not in said


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


class TestTheLaunchPathItself:
    """⚠ **The gap neither S33's tests nor the first version of these had.**

    Both asserted on `effective_model` — the sentence `rite doctor` and `rite
    start` PRINT — and neither on `_engine_model_env`, which is what actually
    decides whether a Manager launches and with what. So two things could be
    true at once, and were:

    - S33 accepted a local Manager on any agent that declared a window;
    - `_engine_model_env` began with `if agent != "goose": return {}, "", ""`,
      the first line of the function, so for any other agent it returned
      before `pin_window` ever ran.

    A Manager declaring `context_window: 32768` on another agent therefore
    passed the check and launched with NOTHING pinned, on the server's
    default — declaring a window it did not get. Reverting the gate is caught
    here and nowhere else, which is the whole reason this class exists.
    """

    @pytest.fixture
    def project(self, tmp_path):
        root = tmp_path / "proj"
        (root / ".rite").mkdir(parents=True)
        return root

    def _config(self, root, agent: str, window: int = 32768) -> None:
        (root / ".rite" / "config.yaml").write_text(
            "coordination:\n"
            "  managers:\n    - small\n"
            "  manager_roles:\n"
            "    - name: small\n"
            "      engine: local:small\n"
            "      endpoint: http://localhost:11434/v1\n"
            "      model: qwen3:8b\n"
            f"      agent: {agent}\n"
            f"      context_window: {window}\n"
            # Required once any Manager declares one: routing cannot guess
            # what a Manager is for. Part of the fixture, not of the subject.
            "      duties: [execute]\n"
        )

    def _call(self, root, agent, monkeypatch, pinned_ok=True):
        """`_engine_model_env` with the pin stubbed, recording whether it ran."""
        import rite_ai.local.context_window as cw
        from rite_ai.managers import supervise as sup

        ran: list[tuple] = []

        class _Pinned:
            problem = "" if pinned_ok else "the endpoint refused"
            model = "rite-ctx32768-qwen3-8b"
            created = True
            detail = "created rite-ctx32768-qwen3-8b"

        def fake_pin(endpoint, model, window, **kw):
            ran.append((endpoint, model, window))
            return _Pinned()

        monkeypatch.setattr(cw, "pin_window", fake_pin)
        env, refusal, note = sup._engine_model_env(root, "small", agent)  # noqa: PLC2701
        return env, refusal, note, ran

    @pytest.mark.parametrize("agent", [KNOWN, *[a for a in UNKNOWN if a]])
    def test_every_local_agent_gets_its_window_pinned(
        self, project, agent, monkeypatch
    ):
        """⚠ The assertion that catches the gate. `ran` is empty exactly when
        `pin_window` was never called — which is what the old `agent !=
        "goose"` first line caused for every other agent."""
        self._config(project, agent)

        _env, refusal, _note, ran = self._call(project, agent, monkeypatch)

        assert ran, f"{agent}: pin_window never ran, so nothing was pinned"
        assert ran[0][2] == 32768
        assert refusal == "", f"{agent} was refused despite declaring a window"

    @pytest.mark.parametrize("agent", [a for a in UNKNOWN if a])
    def test_an_unmapped_agent_is_not_refused_but_is_noted(
        self, project, agent, monkeypatch
    ):
        """The dispatch: no refusal, no agent-specific env, and a note saying
        the agent was not told."""
        self._config(project, agent)

        env, refusal, note, _ran = self._call(project, agent, monkeypatch)

        assert refusal == "", f"{agent} was refused"
        assert env == {}, "an agent that reads no window must get no window env"
        assert "not" in note and "TELL" in note, note

    def test_the_mapped_agent_gets_its_env(self, project, monkeypatch):
        """The control. Without it, returning `{}` for everybody would pass
        every assertion above while telling Goose nothing either."""
        self._config(project, KNOWN)

        env, refusal, _note, _ran = self._call(project, KNOWN, monkeypatch)

        assert refusal == ""
        assert env.get("GOOSE_CONTEXT_LIMIT") == "32768", env
        assert env.get("GOOSE_PROVIDER") == "ollama"

    def test_a_claude_manager_is_untouched(self, project, monkeypatch):
        """An empty agent is a Claude Manager; its path must not change, and
        nothing may be pinned for it."""
        self._config(project, KNOWN)

        env, refusal, note, ran = self._call(project, "", monkeypatch)

        assert (env, refusal, note) == ({}, "", "")
        assert ran == [], "a Claude Manager had a model pinned for it"

    def test_a_pin_that_fails_refuses_whatever_the_agent(self, project, monkeypatch):
        """A declared window that could not be pinned is a refusal, not a
        note — the Manager would otherwise run on the default believing it
        had 32768."""
        self._config(project, "cursor")

        _env, refusal, _note, _ran = self._call(
            project, "cursor", monkeypatch, pinned_ok=False
        )

        assert "could not" in refusal and "pinned" in refusal, refusal
