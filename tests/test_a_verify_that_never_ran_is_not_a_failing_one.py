"""SCRUM-95 — RL-47 at the verify: not running is not failing.

🔴 **Measured on the v0.7.0 gate run, not imagined.** Ticket 2's agreed
definition of done was `python -m pytest -q tests/test_format.py` — the
natural phrasing — and there is no bare `python` on a uv-managed host at all.
`runners.SubprocessVerifier` catches that deliberately ("the tool is not
installed on this machine… saying the verify failed would spend an attempt
proving a machine was set up wrong") and then had nowhere to put the fact:
`VerifyResult` was `(passed, output)`, so "never ran" was prose in `output`
and every consumer read a failing verify.

What that cost, in the two gates that consume it:

* RL-8 recorded "the composed work FAILS its agreed verify" and returned the
  plan to plan review, spending one of RL-10's bounded returns — on something
  no plan change can fix. The module's own docstring claims the opposite,
  which was true only for an exception the launcher raises, and the launcher
  catches the common one.
* RL-7 counted it as an attempt against `MAX_ATTEMPTS=2` (DD-3.4), so two
  missing interpreters retire a subtask nobody tried.

Every test here drives a REAL caller, and the pair that matters is the
non-run beside a genuine failure: a fix that classified both the same way
would be a fix that stopped reporting failures.
"""

from __future__ import annotations

from pathlib import Path

from rite_ai.local import decomposition as dec
from rite_ai.local import level2, plan_state
from rite_ai.local import recompose as rc
from rite_ai.local.harness import AgentReport, Commit, Context, run_subtask
from rite_ai.local.runners import SubprocessVerifier

TICKET = "T-1"
NOT_A_PROGRAM = "rite-no-such-program-95 --version"
REALLY_FAILS = "git rev-parse --verify no-such-ref-95"


# --- the runner says which ---------------------------------------------------


def test_the_runner_distinguishes_a_missing_program_from_a_failing_command(tmp_path):
    v = SubprocessVerifier()

    never = v.run(NOT_A_PROGRAM, str(tmp_path))
    assert never.passed is False
    assert never.ran is False, "a program that is not installed did not run"
    assert "not installed on this machine" in never.output

    # CONTROL: a program that IS installed, exiting non-zero. This is the
    # verdict the fix must not touch — it is evidence about the work.
    (tmp_path / ".git").mkdir(exist_ok=True)
    failed = v.run(REALLY_FAILS, str(tmp_path))
    assert failed.passed is False
    assert failed.ran is True, "it ran; it failed"

    # CONTROL: and a command that passes.
    ok = v.run("git --version", str(tmp_path))
    assert ok.passed is True and ok.ran is True


def test_an_unreadable_or_empty_command_never_ran_either(tmp_path):
    v = SubprocessVerifier()
    assert v.run('pytest "unclosed', str(tmp_path)).ran is False
    assert v.run("   ", str(tmp_path)).ran is False


# --- RL-8 ---------------------------------------------------------------------


def _approved(tmp_path):
    (tmp_path / ".rite").mkdir(parents=True, exist_ok=True)
    state = plan_state.layer(tmp_path)
    plan = dec.Decomposition(
        ticket=TICKET,
        subtasks=(
            dec.Subtask(
                id="s1",
                intent="first",
                scope=("a.txt",),
                verify="pytest -q",
                cites=("5.1",),
                status=dec.ACCEPTED,
            ),
            dec.Subtask(
                id="s2",
                intent="second",
                scope=("b.txt",),
                verify="pytest -q",
                cites=("5.2",),
                status=dec.ACCEPTED,
            ),
        ),
        decomposed_by="planner",
        approval=dec.APPROVED,
        approved_by="lead",
    )
    dec.write(state, plan, dec.read(state, TICKET).version)
    level2.record_approval(state, TICKET, "lead", plan.subtasks)
    digests = level2.approved_digests(state, TICKET)
    assert not isinstance(digests, str), digests
    return state, digests


def test_rl8_reports_an_agreed_verify_that_cannot_run_as_unverified(tmp_path):
    """🔴 The gate run's own case: `python -m pytest …` on a uv host."""
    state, digests = _approved(tmp_path)
    got = rc.run_recomposition(
        tmp_path,
        "planner",
        TICKET,
        digests,
        snapshot={"verify": [NOT_A_PROGRAM]},
        workspace=str(tmp_path),
        verifier=SubprocessVerifier(),
    )
    assert got.ok is False, "a non-run is never a pass"
    assert got.problem, "and it is a PROBLEM, not the work failing"
    assert "never ran" in got.problem
    assert "unverified rather than wrong" in got.problem
    # The failure fields stay empty: nothing about the work was learned.
    assert not got.failed

    # Still blocked, which is the half SCRUM-68 settles: a check that did not
    # run and a check that passed must not read alike.
    rc.write(state, got)
    assert isinstance(rc.cleared_to_deliver(state, TICKET, digests), rc.Blocked)


def test_rl8_still_reports_a_real_failure_as_one(tmp_path):
    """CONTROL. Without this the test above is satisfied by calling everything
    a problem, which would stop RL-8 ever failing a plan."""
    state, digests = _approved(tmp_path)
    (tmp_path / ".git").mkdir(exist_ok=True)
    got = rc.run_recomposition(
        tmp_path,
        "planner",
        TICKET,
        digests,
        snapshot={"verify": [REALLY_FAILS]},
        workspace=str(tmp_path),
        verifier=SubprocessVerifier(),
    )
    assert got.ok is False
    assert not got.problem, f"it ran and failed, so it is not a problem: {got.problem}"
    assert got.failed == REALLY_FAILS


# --- RL-7 ---------------------------------------------------------------------


class _State:
    def __init__(self):
        self.values: dict = {}
        self.n = 0

    def read_state(self, key):
        from rite_ai.coordination.state_layer import Absent, Present

        if key not in self.values:
            return Absent()
        value, version = self.values[key]
        return Present(value=value, version=version)

    def write_state(self, key, value, expected_version):
        from rite_ai.coordination.state_layer import Written

        self.n += 1
        self.values[key] = (value, f"v{self.n}")
        return Written(version=f"v{self.n}")


class _Agent:
    def run(self, context: Context, workspace: str) -> AgentReport:
        # Claims success, which is the interesting case: the agent must not be
        # recorded as contradicted by a verify that did not happen.
        return AgentReport(claimed_success=True, summary="did the thing")


class _Committer:
    def commit_to_branch(self, workspace, branch, message) -> Commit:
        return Commit(sha="abc1234")


class _Claims:
    def __init__(self):
        self.released = []

    def take(self, paths, worker):
        return True

    def release(self, paths, worker):
        self.released.append(paths)


def _step(verify: str, tmp_path: Path):
    subtask = dec.Subtask(
        id="s1", intent="add it", scope=("a.py",), verify=verify, cites=("5.1",)
    )
    plan = dec.Decomposition(
        ticket="ABC-1",
        subtasks=(subtask,),
        decomposed_by="planner",
        approval=dec.APPROVED,
        approved_by="lead",
    )
    return run_subtask(
        state=_State(),
        manager="planner",
        worker="alpha",
        plan=plan,
        subtask=subtask,
        spec_slice="## 5.1",
        workspace=str(tmp_path),
        agent=_Agent(),
        verifier=SubprocessVerifier(),
        committer=_Committer(),
        claims=_Claims(),
    )


def test_rl7_does_not_spend_an_attempt_on_a_verify_that_never_ran(tmp_path):
    outcome = _step(NOT_A_PROGRAM, tmp_path)
    assert outcome.accepted is False
    assert outcome.infrastructure_fault is True
    assert outcome.counts_as_attempt is False, (
        "two missing interpreters would retire a subtask nobody tried"
    )
    # The agent claimed success and is NOT recorded as contradicted: a verify
    # that did not happen contradicts nobody.
    assert outcome.claim_disagreed_with_verify is False
    assert any("the verify never ran" in n for n in outcome.notes)


def test_rl7_still_spends_an_attempt_on_a_verify_that_ran_and_failed(tmp_path):
    """CONTROL, and it also pins the honesty signal: here the agent IS
    contradicted, and that must still be recorded."""
    (tmp_path / ".git").mkdir(exist_ok=True)
    outcome = _step(REALLY_FAILS, tmp_path)
    assert outcome.accepted is False
    assert outcome.infrastructure_fault is False
    assert outcome.counts_as_attempt is True
    assert outcome.claim_disagreed_with_verify is True
