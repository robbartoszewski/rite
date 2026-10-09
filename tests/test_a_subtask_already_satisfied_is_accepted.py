"""SCRUM-96 — a subtask whose outcome already holds is a PASS, not a wedge.

🔴 **Measured on the v0.7.0 gate run, and it stopped the gate.** Ticket 2's
plan had two subtasks: s1 implemented `tally.format_amount`, and s2 said
"ensure the agreed test exists in tests/test_format.py and passes". The app
ships that test FAILING on purpose — and s1's work is what made it pass. So by
the time s2 ran, its required outcome already held.

The agent correctly changed nothing. The verify passed (`2 passed in 0.01s`).
`GitCommitter` found nothing staged in scope and returned an error, and
`run_subtask` read any commit error as `VERIFIED` — a status with no
successor. The local tier then reported, every cycle for over an hour:

    not advanced — nothing is planned and s2 did not reach accepted, so the
    ticket is neither runnable nor finished

…with the cause recorded nowhere. `Outcome.notes` held the one sentence that
explained it and `loop` dropped it, so it had to be reconstructed from the
Worker's branch and working tree.

RL-7's rule is that the verify ALONE decides, and the verify said yes. Three
things are pinned here, each the defect it was:

1. a satisfied subtask reaches ACCEPTED, with no commit and saying so;
2. a commit that genuinely FAILED still does not — that is the guard against
   an ACCEPTED subtask carrying an empty reference composition would apply;
3. the reason reaches the log either way.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from rite_ai.local import decomposition as dec
from rite_ai.local.harness import (
    AgentReport,
    Commit,
    Context,
    VerifyResult,
    run_subtask,
)
from rite_ai.local.runners import GitCommitter


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "ws"
    repo.mkdir()
    for argv in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "T"],
    ):
        subprocess.run(argv, cwd=repo, check=True, capture_output=True)
    (repo / "parser.py").write_text("already right\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "the state s2 asks for already holds"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return repo


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
    """Changes nothing, which is the correct thing to do here."""

    def run(self, context: Context, workspace: str) -> AgentReport:
        return AgentReport(claimed_success=True, summary="it already held")


class _Claims:
    def take(self, paths, worker):
        return True

    def release(self, paths, worker):
        pass


class _Passes:
    def run(self, command, workspace):
        return VerifyResult(passed=True, output="2 passed")


class _BrokenCommitter:
    """A commit that genuinely failed — `nothing_to_commit` is NOT set."""

    def commit_to_branch(self, workspace, branch, message) -> Commit:
        return Commit(error="commit failed: disk is full")


SUBTASK = dec.Subtask(
    id="s2",
    intent="ensure the agreed test exists and passes",
    scope=("parser.py",),
    verify="pytest -q",
    cites=("5.1",),
)
PLAN = dec.Decomposition(
    ticket="ABC-1",
    subtasks=(SUBTASK,),
    decomposed_by="planner",
    approval=dec.APPROVED,
    approved_by="lead",
)


def _run(committer, tmp_path: Path):
    return run_subtask(
        state=_State(),
        manager="planner",
        worker="alpha",
        plan=PLAN,
        subtask=SUBTASK,
        spec_slice="## 5.1",
        workspace=str(_repo(tmp_path)),
        agent=_Agent(),
        verifier=_Passes(),
        committer=committer,
        claims=_Claims(),
    )


def test_a_passing_verify_with_nothing_to_commit_is_accepted(tmp_path):
    """🔴 The wedge. The REAL committer, against a tree that already holds the
    required state, so nothing is staged."""
    outcome = _run(GitCommitter(scope=("parser.py",)), tmp_path)
    assert outcome.status == dec.ACCEPTED, outcome.notes
    assert outcome.accepted is True
    assert outcome.commit == "", "there was nothing to commit, and it says so"
    assert any("accepted with no commit" in n for n in outcome.notes), outcome.notes
    assert any("nothing for composition to apply" in n for n in outcome.notes)


def test_a_commit_that_actually_failed_is_still_not_accepted(tmp_path):
    """CONTROL, and the guard this must not weaken: an ACCEPTED subtask with an
    empty sha would have composition apply a branch nothing names."""
    outcome = _run(_BrokenCommitter(), tmp_path)
    assert outcome.status == dec.VERIFIED
    assert outcome.accepted is False
    assert any("verified but not committed" in n for n in outcome.notes), outcome.notes


def test_the_loop_says_why_a_subtask_stopped_where_it_did(tmp_path):
    """Defect 3: the step's own lines were dropped, so the stall had no
    recorded cause. Asserted on the Advance a REAL `drive_local_tier` builds."""
    from rite_ai.local import loop
    from rite_ai.local import stage as st

    class _Step:
        """Stands in for `take_one_step`, returning what it really returns."""

        def __call__(self, root, manager, ticket):
            return type(
                "Step",
                (),
                {
                    "problem": "",
                    "ran": True,
                    "subtask": "s2",
                    "status": dec.VERIFIED,
                    "accepted": False,
                    "lines": [
                        "verified but not committed: commit failed: disk is full"
                    ],
                },
            )()

    from rite_ai.local import plan_state

    (tmp_path / ".rite").mkdir(parents=True, exist_ok=True)
    state = plan_state.layer(tmp_path)
    dec.write(state, PLAN, dec.read(state, "ABC-1").version)
    got = state.read_state(st.key_for("ABC-1"))
    state.write_state(
        st.key_for("ABC-1"),
        st.render(
            st.Record(
                ticket="ABC-1",
                stage=st.STEPPING,
                log=(st.Transition(frm=st.UNSTARTED, to=st.STEPPING, at=1.0, why="x"),),
            )
        ),
        got.version,
    )
    advance = loop.advance_ticket(
        tmp_path, "planner", "ABC-1", state=state, step=_Step()
    )
    assert "s2 verified" in advance.line()
    assert "verified but not committed" in advance.line(), advance.line()
