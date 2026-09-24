"""R7 through the REAL composition, not through stubs (B4c).

⚠ **THE HARNESS PROPERTY IS ALREADY TESTED. THE COMPOSITION IS NOT**, and the
difference is last week's shape exactly: every piece correct, the assembly
wrong. `test_local_harness` proves `run_subtask` prefers the verify using a
`FakeAgent`, a `FakeVerifier` and a `FakeCommitter`. A test that builds its
own inputs cannot discover that the real producers build different ones.

So this drives the real `GooseAgent`, the real `SubprocessVerifier` and the
real `GitCommitter` through `run_subtask`, against a verify that cannot pass.

⚠ **AND IT IS CAREFUL ABOUT WHAT "THE AGENT CLAIMED SUCCESS" MEANS HERE.**
`goose run` has no `--json` — `-q` prints the model's prose and
`session export` dumps messages, neither of which is a machine-readable
claim. So for this engine `claimed_success=True` means only **"a turn ran and
nothing visibly failed"**, which is weaker than Claude's or opencode's
structured claim. `claim_disagreed_with_verify` is the signal RL-T0 measures
honesty with, so the weakness is named rather than papered over: the
strong-claim case is exercised with an explicit stub, and the live case is
labelled for what it is.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from rite_ai.coordination.state_layer import Absent, Present, Written
from rite_ai.local.decomposition import (
    ACCEPTED,
    APPROVED,
    FAILED,
    Decomposition,
    Subtask,
)
from rite_ai.local.harness import AgentReport, run_subtask
from rite_ai.local.runners import GitCommitter, SubprocessVerifier


class _State:
    """The state-layer interface, minimally. Reused in shape from
    `test_local_harness.FakeState` rather than re-guessed — a heartbeat is
    published through it and RL-35 says every coordination read and write
    goes through this interface."""

    def __init__(self):
        self.values: dict[str, tuple[bytes, str]] = {}
        self.n = 0

    def read_state(self, key: str):
        if key not in self.values:
            return Absent()
        value, version = self.values[key]
        return Present(value=value, version=version)

    def write_state(self, key: str, value: bytes, expected_version: str):
        self.n += 1
        self.values[key] = (value, f"v{self.n}")
        return Written(version=f"v{self.n}")

    def append_message(self, content: str):  # pragma: no cover
        raise NotImplementedError

    def read_messages(self, since=None, limit=None):  # pragma: no cover
        raise NotImplementedError


class _Claims:
    def __init__(self):
        self.released = []

    def take(self, paths, worker):
        return True

    def release(self, paths, worker):
        self.released.append(paths)


class _ClaimsSuccess:
    """An agent that makes the STRONG claim: it says, in so many words, that
    it did the work. This is what Claude and opencode can supply and Goose
    cannot."""

    def run(self, context, workspace):
        return AgentReport(claimed_success=True, summary="I completed the subtask.")


def _repo() -> str:
    d = tempfile.mkdtemp(prefix="b4c-")
    subprocess.run(["git", "init", "-q", d], check=True)
    Path(d, "seed.txt").write_text("seed\n")
    subprocess.run(["git", "-C", d, "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            d,
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            "init",
        ],
        check=True,
        capture_output=True,
    )
    return d


def _plan():
    return Decomposition(ticket="RT-14", subtasks=(), approval=APPROVED)


def _subtask(verify: str):
    return Subtask(
        id="s1",
        intent="Create a file named done.txt containing OK.",
        scope=("done.txt",),
        verify=verify,
    )


def _run(agent, verify: str):
    workspace = _repo()
    claims = _Claims()
    try:
        return (
            run_subtask(
                state=_State(),
                manager="planner",
                worker="alpha",
                plan=_plan(),
                subtask=_subtask(verify),
                spec_slice="done.txt must exist and contain OK.",
                workspace=workspace,
                agent=agent,
                verifier=SubprocessVerifier(),
                committer=GitCommitter(scope=("done.txt",)),
                claims=claims,
            ),
            claims,
            workspace,
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


class TestTheVerifyDecidesThroughTheRealPieces:
    """⚠ A real verifier and a real committer. Only the agent is a stub, and
    only so its claim is unambiguous — see the module docstring."""

    def test_a_claim_of_success_against_an_impossible_verify_is_not_accepted(self):
        """**THE R7 OBSERVATION.** The agent says it did the work; the verify
        cannot pass; rite does not accept it."""
        outcome, _, _ = _run(_ClaimsSuccess(), "test -f a_file_nothing_creates.txt")
        assert outcome.agent_claimed is True, "the agent did claim success"
        assert outcome.verified is False, "the verify could not have passed"
        assert outcome.accepted is False, (
            "rite accepted work its own verify rejected — the agent decided, "
            "which is precisely what R7 forbids"
        )
        assert outcome.status == FAILED

    def test_the_disagreement_is_recorded_not_just_the_verdict(self):
        """It is the honesty signal RL-T0 measures, so it has to survive into
        the outcome rather than being collapsed into a pass/fail."""
        outcome, _, _ = _run(_ClaimsSuccess(), "test -f a_file_nothing_creates.txt")
        assert outcome.claim_disagreed_with_verify is True
        assert any("verify disagreed" in n for n in outcome.notes), outcome.notes

    def test_the_claim_is_released_even_though_the_subtask_failed(self):
        """A claim held by a finished subtask blocks the fleet until somebody
        notices."""
        _, claims, _ = _run(_ClaimsSuccess(), "test -f a_file_nothing_creates.txt")
        assert claims.released == [("done.txt",)]

    def test_the_real_verifier_can_still_accept_real_work(self):
        """⚠ The control. Without it, a harness that rejected EVERYTHING
        would pass every test above — which is the vacuous-check shape this
        project keeps finding."""

        class _DoesTheWork:
            def run(self, context, workspace):
                Path(workspace, "done.txt").write_text("OK\n")
                return AgentReport(claimed_success=True, summary="done")

        outcome, _, _ = _run(_DoesTheWork(), "test -f done.txt")
        assert outcome.verified is True
        assert outcome.accepted is True
        assert outcome.status == ACCEPTED
        assert outcome.commit, "accepted work with no commit is not on a branch"
        assert outcome.claim_disagreed_with_verify is False


class TestWhatGoosesClaimActuallyMeans:
    """⚠ Named rather than assumed, because a weak signal standing in for a
    strong one is how `claim_disagreed_with_verify` would quietly stop
    measuring anything."""

    def test_goose_reports_success_for_a_turn_that_merely_ran(self):
        """`GooseAgent.claimed_success` is "a turn ran and nothing visibly
        failed" — NOT "the agent said it did the work". `goose run` has no
        `--json`, so there is no claim to read."""
        from rite_ai.local.goose_agent import GooseAgent

        agent = GooseAgent(
            model="m",
            endpoint="http://x/v1",
            probe=lambda: type("P", (), {"problems": []})(),
            launch=lambda *a: subprocess.CompletedProcess(
                [], 0, "I could not find the file, so I did nothing.", ""
            ),
        )
        report = agent.run(
            type(
                "C",
                (),
                {"subtask": _subtask("true"), "spec_slice": "", "ticket": "RT-14"},
            )(),
            tempfile.mkdtemp(),
        )
        assert report.claimed_success is True, (
            "this is the documented weakness, not a bug: the model said it "
            "did nothing and the adapter still reports a claim, because "
            "goose publishes no machine-readable one"
        )

    @pytest.mark.skipif(shutil.which("goose") is None, reason="goose is not installed")
    def test_the_weakness_is_visible_in_the_signal_not_hidden_by_it(self):
        """So a step review reading `claim_disagreed_with_verify` for a
        Goose-backed subtask must know it is reading a weaker claim. The
        summary carries the model's own words, which is what a human can
        actually judge."""
        from rite_ai.local.goose_agent import GooseAgent

        agent = GooseAgent(
            model="m",
            endpoint="http://x/v1",
            probe=lambda: type("P", (), {"problems": []})(),
            launch=lambda *a: subprocess.CompletedProcess(
                [], 0, "I could not find the file, so I did nothing.", ""
            ),
        )
        report = agent.run(
            type(
                "C",
                (),
                {"subtask": _subtask("true"), "spec_slice": "", "ticket": "RT-14"},
            )(),
            tempfile.mkdtemp(),
        )
        assert "did nothing" in report.summary, (
            "the model's own words are the only judgeable evidence for this "
            "engine, so they must survive into the outcome"
        )
