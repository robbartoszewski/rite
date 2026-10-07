"""Which commits a CI run of the publish gate is responsible for (SCRUM-39).

**The failure this exists to prevent.** `gitleaks detect` with no
`--log-opts` scans every commit the repository can reach, and `actions/
checkout` with `fetch-depth: 0` fetches every ref — so the gate asked "does
any fetched ref anywhere contain a leak that main has not suppressed?"
instead of "does THIS pull request introduce one". Measured on gitleaks
8.30.1 in a scratch repository on 2026-10-07: a `ghp_`-shaped fixture
committed only on a side branch fails the default scan (exit 7) and passes a
scan scoped to the checked-out branch's own range (exit 0).

Live, on 2026-10-02, that coupled every pull request to every other open
branch: a fake token in commit `9aa4336` on `fix/credentials-doctor-setup`
failed the gate on #184 and effectively every pull request to main, and the
suppression had to be landed in main (#186) before anything could merge.

**Why this is a module and not three lines of YAML.** The range decides what
the gate looks at, so a range computed wrong is a gate that passes without
having checked anything — strictly worse than the over-strict behaviour it
replaces, and invisible. Shell in a workflow file is the one layer of this
project with no tests at all, so the decision is made here, where it has
them, and the workflow passes one flag.

**It never narrows on a guess.** Every case it cannot establish falls back to
the full history scan and SAYS it did. That is the old behaviour: too strict,
never vacuous. The only thing that narrows the scan is a base ref git
actually resolved.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PULL_REQUEST_EVENTS = frozenset({"pull_request", "pull_request_target"})
"""The events that carry a base ref, so the pull request's own range is
knowable. `pull_request_target` runs against the base repository and still
sets `GITHUB_BASE_REF`."""

DEFAULT_BRANCH_ENV = "RITE_GATE_DEFAULT_BRANCH"
"""Where the trunk's name comes from for a PUSH event, which carries no base
ref of its own.

⚠ **Named by rite and set by the workflow, deliberately.** GitHub documents
`GITHUB_BASE_REF` and `GITHUB_REF_NAME` as environment variables, but the
default branch is only in the event payload
(`github.event.repository.default_branch`) — so the workflow passes it under
this name rather than this module reading a variable GitHub may not define.
Unset simply means the push cannot be scoped, and the full scan runs."""


@dataclass(frozen=True)
class CiRange:
    """What to scan, and why that was chosen.

    `rev_range` None means full history. `why` is always said out loud by the
    caller: a narrowed scan is a claim about what was checked, and a reader
    of a green gate is entitled to know which commits earned it.
    """

    rev_range: str | None
    why: str

    @property
    def narrowed(self) -> bool:
        return self.rev_range is not None


def _base_ref_candidates(base: str) -> tuple[str, ...]:
    """Where a base branch may be found, most specific first.

    `GITHUB_BASE_REF` is a bare branch name (`main`), and with
    `fetch-depth: 0` it is fetched as a remote-tracking ref. The bare name is
    tried too, for a checkout that created a local branch for it.
    """
    base = base.strip()
    if not base:
        return ()
    return (f"refs/remotes/origin/{base}", f"origin/{base}", base)


def range_for_ci(env, root: Path, run_git=None) -> CiRange:
    """The range this CI run is responsible for, from the CI environment.

    `env` is a mapping (`os.environ`), `root` the repository, and `run_git` a
    `(root, *args) -> str | None` for tests; the default is the gate's own.

    ⚠ **The event decides, not the presence of a variable.** An earlier shape
    narrowed whenever `GITHUB_BASE_REF` was set, and that variable is set to
    the empty string on a push — which `strip()`ed to nothing and produced the
    range `".."`, a range git reads as every commit. The branches below are
    exhaustive and each one names what it measured.
    """
    if run_git is None:
        from rite_ai.gate.hook import _git as run_git

    if not env.get("GITHUB_ACTIONS"):
        return CiRange(
            None,
            "not running in GitHub Actions, so there is no pull request to "
            "scope to: the whole history is scanned",
        )

    event = (env.get("GITHUB_EVENT_NAME") or "").strip()

    if event in PULL_REQUEST_EVENTS:
        base = (env.get("GITHUB_BASE_REF") or "").strip()
        if not base:
            return CiRange(
                None,
                f"the {event} event set no GITHUB_BASE_REF, so this pull "
                "request's base is unknown: the whole history is scanned",
            )
        resolved = _resolve(run_git, root, _base_ref_candidates(base))
        if resolved is None:
            return CiRange(
                None,
                f"the base branch {base!r} is not in this checkout (a shallow "
                "clone, or it was never fetched), so this pull request's own "
                "commits cannot be told apart: the whole history is scanned. "
                "`fetch-depth: 0` on actions/checkout is what fetches it",
            )
        return CiRange(
            f"{resolved}..HEAD",
            f"this pull request's own commits ({resolved}..HEAD), not every "
            "fetched branch's history",
        )

    if event == "push":
        default = (env.get(DEFAULT_BRANCH_ENV) or "").strip()
        branch = (env.get("GITHUB_REF_NAME") or "").strip()
        if not default:
            return CiRange(
                None,
                f"no {DEFAULT_BRANCH_ENV} is set, so a pushed branch's own "
                "commits cannot be told from the trunk's: the whole history "
                "is scanned (the workflow sets it from "
                "`github.event.repository.default_branch`)",
            )
        if branch and branch == default:
            # ⚠ The trunk itself gets the FULL scan, deliberately. There is no
            # "own range" for it — a push to the default branch is the history
            # everybody else forks from, and "safe to publish" there means the
            # whole of it.
            return CiRange(
                None,
                f"this is a push to the default branch ({default}), whose "
                "whole history is what every other branch inherits: the whole "
                "history is scanned",
            )
        resolved = _resolve(run_git, root, _base_ref_candidates(default))
        if resolved is None:
            return CiRange(
                None,
                f"the default branch {default!r} is not in this checkout, so "
                "this branch's own commits cannot be told apart: the whole "
                "history is scanned",
            )
        return CiRange(
            f"{resolved}..HEAD",
            f"this branch's own commits ({resolved}..HEAD), not every fetched "
            "branch's history",
        )

    return CiRange(
        None,
        f"the event is {event or 'unnamed'}, which carries no base to scope "
        "to: the whole history is scanned",
    )


def _resolve(run_git, root: Path, candidates) -> str | None:
    """The first candidate ref git resolves to a commit, or None.

    Resolved rather than trusted: the range goes to `git log`, and a ref that
    does not exist makes `<missing>..HEAD` an error the gate would report as
    a crash — or, for a name git reads as a path, something else entirely.
    """
    for ref in candidates:
        if run_git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"):
            return ref
    return None


__all__ = [
    "DEFAULT_BRANCH_ENV",
    "PULL_REQUEST_EVENTS",
    "CiRange",
    "range_for_ci",
]
