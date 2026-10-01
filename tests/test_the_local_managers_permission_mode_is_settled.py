"""A local Manager's permission mode is `auto`, and `auto` is not a boundary.

Settled 2026-10-01 (SPEC §5.4.9, v0.7.0 local tier 1c). **This pins a ruling
over behaviour that already existed.** `supervise` has placed
`GOOSE_MODE=auto` on a local Manager's pane since the permission destination
landed, and `test_the_permission_reaches_the_engine.py` asserts the placement.
What was open was the record: `GooseAgent.mode`'s docstring said B4d "is
measuring" the question and it "is not yet settled", while the code had
already chosen — so a reader of the adapter was told the opposite of what ran.

⚠ **The reason `auto` is supported is that `approve` CANNOT WORK HEADLESS, not
that `auto` is safe.** B4d measured both: `auto` exits 0 and ran `rm` on a
file unattended; `approve` exits 1 on the first tool call with "Tool approval
required in non-interactive mode". `GOOSE_MODE` is whole-session, so there is
no per-command mode in between. Asserting the posture without asserting that
reason would pin a number and lose the only thing that justifies it, so
§5.4.9's non-boundary statement is checked here too.

**What this file is for: the posture is stated in four places that can
drift** — the agent's default, what the agent puts in the environment, the
engine spelling's destination, and what the real supervisor computes. A test
on one of them passes while another is wrong, which is the two-places-that-
did-not-know-about-each-other shape S35 was.
"""

from __future__ import annotations

import inspect
import subprocess

from rite_ai.local import goose_agent
from rite_ai.local.decomposition import Subtask
from rite_ai.local.goose_agent import GooseAgent
from rite_ai.local.harness import Context
from rite_ai.managers.engines import GOOSE, permission_placement
from rite_ai.managers.supervise import UNATTENDED_MODE_FOR_ENV_ENGINES

AUTO = "auto"


class TestThePostureIsAutoAtEveryLayer:
    """One assertion per place the value lives. Each is a separate test so a
    failure names the layer that drifted rather than "the mode is wrong"."""

    def test_the_agents_default_is_auto(self):
        assert GooseAgent.mode == AUTO

    def test_the_agent_puts_auto_in_the_environment(self):
        """Not the dataclass default — what a real turn hands the binary.
        The default could be right while `run` wrote something else."""
        seen: dict = {}
        agent = GooseAgent(
            model="qwen3.8:latest",
            endpoint="http://localhost:11434/v1",
            probe=lambda: _ProbeOK(),
            launch=lambda argv, ws, env: (
                seen.update(env=env)
                or subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="done", stderr=""
                )
            ),
        )
        agent.run(
            Context(
                subtask=Subtask(
                    id="s1",
                    intent="add f()",
                    scope=("a.py",),
                    verify="pytest a_test.py",
                ),
                spec_slice="a.py must have f()",
                ticket="RT-14",
            ),
            "/tmp",
        )
        assert seen["env"]["GOOSE_MODE"] == AUTO

    def test_the_engine_spelling_names_the_destination(self):
        """⚠ The destination, not a boolean. An earlier version recorded only
        that permission was not argv for Goose, which refused writing a flag
        and left the value nowhere to go."""
        assert GOOSE.permission_env == "GOOSE_MODE"

    def test_the_supervisor_places_auto(self):
        assert permission_placement("local:small", "goose", AUTO) == (
            "env",
            "GOOSE_MODE",
            AUTO,
        )

    def test_the_supervisors_own_constant_is_auto(self):
        assert UNATTENDED_MODE_FOR_ENV_ENGINES == AUTO

    def test_an_operator_export_does_not_decide_it(self, monkeypatch):
        """The placement is computed, so an ambient `approve` — which would
        kill the session on its first tool call — cannot reach the pane."""
        monkeypatch.setenv("GOOSE_MODE", "approve")
        assert permission_placement(
            "local:small", "goose", UNATTENDED_MODE_FOR_ENV_ENGINES
        ) == ("env", "GOOSE_MODE", AUTO)


class TestTheRecordMatchesTheCode:
    """The failure this closes was documentary, so it gets a documentary
    assertion. ⚠ A docstring test is weak on its own — it is here because the
    defect WAS the docstring, and the layers above cover the behaviour."""

    def test_the_adapter_no_longer_calls_the_question_unsettled(self):
        """⚠ Reads the SOURCE, deliberately. A first version asked
        `inspect.getdoc(GooseAgent.__dict__["mode"])` — which returns the
        default VALUE `"auto"`, so it read `str`'s own docstring and passed
        over a planted "not yet settled". An attribute docstring is not
        reachable at runtime; only the source has it."""
        assert "not yet settled" not in _mode_docstring().lower(), (
            "the adapter still says the mode is unsettled while the code has "
            "chosen it; a reader is told the opposite of what runs"
        )

    def test_the_adapter_says_auto_is_not_the_boundary(self):
        """⚠ The one that keeps this honest. Settling `auto` reads like a
        safety property; it is the opposite, and containment comes from the
        profile the pane runs inside."""
        assert "boundary" in _mode_docstring().lower(), (
            "the ruling is recorded without the limit that makes it honest: "
            "auto is not containment"
        )


def _mode_docstring() -> str:
    """The text under `mode:` in the adapter's SOURCE. See the test above for
    why this cannot be `inspect.getdoc`."""
    source = inspect.getsource(goose_agent)
    start = source.index("mode: str")
    rest = source[start:]
    end = rest.index('"""', rest.index('"""') + 3)
    return rest[:end]


class _ProbeOK:
    ok = True
    problem = ""
    detail = ""
    served_model = "qwen3.8:latest"
