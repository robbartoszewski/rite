"""Whether rite may merge a pull request it published (PB1, auto_merge).

🔴 **A green counts only on the exact head being merged, and only when that
head contains the base branch's CURRENT tip.** "No failures" is not a green,
and a green on any other tree is not this one's. The rule fired four ways on
2026-09-26 (`V070_RELEASE_PLAN.md` § Auto-merge); each shape has a refusal
here that names it:

1. the PR is merged or closed, so no checks will ever fire;
2. the PR is CONFLICTING, and GitHub computes no merge ref and runs nothing;
3. a check read from an older run, on an older head;
4. a green read while `main` had already moved, so what was tested is not
   what a merge would make.

⚠ **Shape 4 is measured against the base branch's live ref, never the PR's
`base.sha`.** That field is the base as of the PR's last update. Measured on
rite#122: `base.sha` 1527503 while `main` was 6c97075. Comparing against it
would pass exactly the stale green this exists to refuse.

⚠ **The residual race, and why the target branch must be strict.** Between
this check and the merge call, the head can move and so can the base.
`gh pr merge --match-head-commit` makes GitHub refuse a moved head. Nothing
in the merge call pins the base. Only the branch's own "require branches to
be up to date" (`strict_required_status_checks_policy`) makes GitHub refuse
a merge whose head is behind at merge time. So auto-merge also requires that
setting to be ON for the base branch, read from the branch's effective
rules, and an unreadable setting refuses. Without it the race is tolerated,
and a tolerated race is refused.

Every fact is read from GitHub's JSON (`gh api`, parsed as JSON, never
grepped) and decided by `refusal`, which is pure so each shape is tested
without a network.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field

GATE_CHECK = "publish-gate"
"""The publish gate's job name, as `rite publish install-ci` writes it. It is
checked BY NAME and on its own: the test workflow passing says nothing about
the gate, and a missing gate run is not a pass."""


@dataclass(frozen=True)
class Check:
    name: str
    head_sha: str
    status: str  # "completed" | "in_progress" | "queued" | ...
    conclusion: str  # "success" | "failure" | ... | "" while not completed


@dataclass(frozen=True)
class Facts:
    """What GitHub says about one PR, read in one pass."""

    number: int
    state: str  # "open" | "closed"
    merged: bool
    head_sha: str
    base_ref: str
    base_tip: str  # the base BRANCH's current tip, not the PR's base.sha
    behind_by: int | None  # base_tip...head; None when it could not be read
    mergeable: bool | None  # None: GitHub has not computed it
    mergeable_state: str  # "clean" | "behind" | "dirty" | "unstable" | ...
    strict: bool | None  # base branch requires up-to-date; None: unreadable
    checks: list[Check] = field(default_factory=list)


def _short(sha: str) -> str:
    return sha[:7]


def refusal(facts: Facts, published_head: str) -> str | None:
    """Why rite may not merge this PR, or None when it may.

    ⚠ **Every branch refuses; only the end allows.** A validator written as
    "allow unless something looks wrong" fails open on the input nobody
    thought of (the broker's rule, `managers/broker.decide`)."""
    n = facts.number
    if facts.merged or facts.state != "open":
        return (
            f"PR #{n} is {'merged' if facts.merged else facts.state}: nothing "
            "to merge, and no checks will run on it (stale-green shape 1)"
        )
    if facts.head_sha != published_head:
        return (
            f"PR #{n}'s head is {_short(facts.head_sha)}, not "
            f"{_short(published_head)} which rite published: something else "
            "pushed to it. Not merged; a person decides"
        )
    if facts.mergeable is None:
        return (
            f"PR #{n}: GitHub has not computed whether it can merge. Not an "
            "answer, so not merged; rite asks again next cycle"
        )
    if facts.mergeable is False:
        return (
            f"PR #{n} conflicts with {facts.base_ref}: no merge ref, so no "
            "checks ran (stale-green shape 2). Resolve the conflict"
        )
    if facts.behind_by is None:
        return (
            f"PR #{n}: rite could not read whether it contains "
            f"{facts.base_ref}'s tip {_short(facts.base_tip)}, so not merged"
        )
    if facts.behind_by > 0:
        return (
            f"PR #{n}'s head {_short(facts.head_sha)} does not contain "
            f"{facts.base_ref}'s tip {_short(facts.base_tip)} "
            f"({facts.behind_by} behind): its green tested another tree "
            f"(stale-green shape 4). Update the branch from {facts.base_ref}"
        )
    if facts.strict is not True:
        said = "could not be read" if facts.strict is None else "is off"
        return (
            f"PR #{n}: {facts.base_ref}'s 'require branches to be up to date' "
            f"{said}, so {facts.base_ref} could move between this check and "
            "the merge unrefused. Turn it on in the branch's ruleset"
        )
    on_head = [c for c in facts.checks if c.head_sha == facts.head_sha]
    stale = [c for c in facts.checks if c.head_sha != facts.head_sha]
    if stale:
        # Checks are read BY the head SHA, so this cannot happen unless the
        # read is wrong. It is refused rather than filtered out, because a
        # read that returns another commit's checks cannot be trusted for
        # this commit's either (stale-green shape 3).
        return (
            f"PR #{n}: a check read for {_short(facts.head_sha)} belongs to "
            f"{_short(stale[0].head_sha)} (stale-green shape 3). Not merged"
        )
    gate = [c for c in on_head if c.name == GATE_CHECK]
    if not gate:
        return (
            f"PR #{n}: no {GATE_CHECK} run on {_short(facts.head_sha)}. A "
            "missing gate is not a passed one. Not merged"
        )
    for c in on_head:
        if c.status != "completed":
            return f"PR #{n}: {c.name} is {c.status} on {_short(facts.head_sha)}"
        if c.conclusion != "success":
            return (
                f"PR #{n}: {c.name} concluded {c.conclusion or 'nothing'} on "
                f"{_short(facts.head_sha)}. Not merged"
            )
    if not [c for c in on_head if c.name != GATE_CHECK]:
        return (
            f"PR #{n}: no check other than {GATE_CHECK} ran on "
            f"{_short(facts.head_sha)}. Zero tests is no answer, not a green"
        )
    if facts.mergeable_state != "clean":
        return (
            f"PR #{n}: GitHub says its merge state is "
            f"{facts.mergeable_state!r}, not 'clean'. Not merged"
        )
    return None


def gh_api(path: str) -> object:
    """`gh api <path>`, parsed as JSON. Raises on failure: a read that failed
    is never a read that found nothing."""
    done = subprocess.run(
        ["gh", "api", path],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=60,
    )
    if done.returncode != 0:
        raise RuntimeError(
            f"gh api {path}: exit {done.returncode}: {done.stderr.strip()}"
        )
    return json.loads(done.stdout)


def _latest_per_name(runs: list[dict]) -> list[Check]:
    """A re-run adds a check run with the same name. The newest one counts."""
    newest: dict[str, dict] = {}
    for run in runs:
        name = str(run.get("name", ""))
        if name not in newest or int(run.get("id", 0)) > int(newest[name].get("id", 0)):
            newest[name] = run
    return [
        Check(
            name=name,
            head_sha=str(run.get("head_sha", "")),
            status=str(run.get("status", "")),
            conclusion=str(run.get("conclusion") or ""),
        )
        for name, run in sorted(newest.items())
    ]


def read_facts(repo: str, number: int, api=gh_api) -> Facts:
    """Everything `refusal` needs, from GitHub. `repo` is `owner/name`."""
    pr = api(f"repos/{repo}/pulls/{number}")
    head = str(pr["head"]["sha"])
    base_ref = str(pr["base"]["ref"])
    tip = str(api(f"repos/{repo}/git/ref/heads/{base_ref}")["object"]["sha"])
    try:
        compared = api(f"repos/{repo}/compare/{tip}...{head}")
        behind: int | None = int(compared["behind_by"])
    except (RuntimeError, KeyError, TypeError, ValueError):
        behind = None
    try:
        strict: bool | None = False
        for rule in api(f"repos/{repo}/rules/branches/{base_ref}"):
            if rule.get("type") == "required_status_checks":
                params = rule.get("parameters", {})
                if params.get("strict_required_status_checks_policy") is True:
                    strict = True
    except (RuntimeError, AttributeError, TypeError):
        strict = None
    runs = api(f"repos/{repo}/commits/{head}/check-runs?per_page=100")
    checks = _latest_per_name(list(runs.get("check_runs", [])))
    status = api(f"repos/{repo}/commits/{head}/status")
    for s in status.get("statuses", []):
        state = str(s.get("state", ""))
        checks.append(
            Check(
                name=str(s.get("context", "")),
                head_sha=str(status.get("sha", "")),
                status="completed" if state != "pending" else "in_progress",
                conclusion="success" if state == "success" else state,
            )
        )
    return Facts(
        number=number,
        state=str(pr["state"]),
        merged=bool(pr.get("merged")),
        head_sha=head,
        base_ref=base_ref,
        base_tip=tip,
        behind_by=behind,
        mergeable=pr.get("mergeable"),
        mergeable_state=str(pr.get("mergeable_state", "")),
        strict=strict,
        checks=checks,
    )
