"""Worker workspace preparation — SPEC §2.1: "A workspace preparation script
runs before each task to ensure the workspace is clean and current — right
repos, right branches, no residue from the previous task."

This is distinct from `manage.add_worker`, which creates a worker's
directory structure once. `prepare_workspace` is the function that runs
before *every* task — first time or the hundredth — and must be idempotent.

Two hard rules, both load-bearing:

1. **A dirty tree fails loudly; it is never auto-discarded.** Uncommitted
   work left over from a previous task is a signal that something needs a
   human's or a session's attention, not noise to clean up. This is also
   how "no residue from the previous task" is actually enforced: residue
   left in a module's working tree shows up as "dirty" and blocks, rather
   than being silently swept away by some separate cleanup step that could
   just as easily delete work someone meant to keep. Two mechanisms trying
   to do the same job independently is how one of them ends up deleting
   something the other would have preserved.

2. **Offline degrades, it never corrupts.** A `git fetch` that fails for a
   network reason (see `git_ops.classify_git_failure`) does not block —
   the module is reported "ready (offline)" and prep proceeds using
   whatever state is already on disk. A fetch failure that does NOT match a
   known network signature is treated as a repository problem instead, and
   blocks — see `git_ops`'s module docstring for the reasoning: an
   unrecognised failure should be surfaced, not silently waved through as
   "probably offline".

Local-only modules (no `url` in `modules.yaml`, e.g. a `shared/` package
with no remote — SPEC §8.2) still need a worker-local checkout. This module
clones them from the project root's own copy of the module
(`project_root / module.path`) as a local git source, which works because
`git clone` accepts a filesystem path exactly like it accepts a URL — the
project root's checkout becomes the de facto "origin" for that module's
worker copies. This is stated as a design choice, not implied: SPEC does
not specify how a worker gets its own copy of a remote-less module, and
this is the interpretation used here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rite_ai.config.models import Module
from rite_ai.workspace import git_ops
from rite_ai.workspace.git_ops import GitError

# "ok" statuses — safe to proceed working in this module.
STATUS_READY = "ready"
STATUS_READY_OFFLINE = "ready_offline"
STATUS_CLONED = "cloned"

# How the module ended up on its target branch. All four cases were
# already computed by `_ensure_branch` and then thrown away, so every one
# of them printed "ready: up to date".
#
# The one that costs someone their afternoon is BRANCH_CREATED. `rite
# prepare --worker alpha --branch feature/RW-12` and the same command with
# a typo'd ticket id both reported
#
#     ✓ backend @ feature/RW-12 — ready: up to date
#
# — one of them having checked out the branch with your work on it, the
# other having just created an empty branch off `main`. "up to date" is
# true of both and tells you which one happened about neither. This is the
# command whose whole job is SPEC §2.1's "right repos, right branches";
# saying which branch you are on is not the same as saying how you got
# there.
BRANCH_UNCHANGED = ""  # already on it — nothing worth saying


def _branch_resumed(branch: str) -> str:
    return f"resumed existing local branch '{branch}'"


def _branch_tracking(branch: str) -> str:
    return f"checked out origin's '{branch}'"


def _branch_created(branch: str, base: str) -> str:
    return f"NEW branch '{branch}' created from '{base}'"


# blocking statuses — prep stops and surfaces this module without touching it further.
STATUS_DIRTY = "dirty"
STATUS_DIVERGED = "diverged"
STATUS_REPO_ERROR = "repo_error"
STATUS_NETWORK_UNREACHABLE = "network_unreachable"


@dataclass
class ModulePrepResult:
    module: str
    status: str
    ok: bool
    message: str
    branch: str = ""


@dataclass
class WorkspacePrepResult:
    ok: bool
    modules: list[ModulePrepResult] = field(default_factory=list)

    @property
    def blocking(self) -> list[ModulePrepResult]:
        return [m for m in self.modules if not m.ok]

    def summary(self) -> str:
        if not self.modules:
            # Previously returned "" here — `rite prepare` on a worker
            # with zero modules assigned printed a single blank line and
            # nothing else, indistinguishable from a crash. Found running
            # `rite prepare` by hand on a worker created before its module
            # was registered.
            return "  (no modules assigned to this worker — nothing to prepare)"
        lines = []
        for m in self.modules:
            marker = "✓" if m.ok else "✗"
            branch = f" @ {m.branch}" if m.branch else ""
            lines.append(f"  {marker} {m.module}{branch} — {m.status}: {m.message}")
        return "\n".join(lines)


def prepare_workspace(
    worker_dir: Path,
    modules: list[Module],
    project_root: Path,
    branch: str | None = None,
) -> WorkspacePrepResult:
    """Prepare every module a worker needs. Idempotent: calling this again
    with nothing changed produces the same "ready" result, at the cost of
    one `git fetch` per module (a fetch that finds nothing new is a no-op).

    `branch`, when given, is the ticket branch to work on — created from
    each module's configured default branch if it does not exist yet. When
    omitted, each module is prepared on its own configured default branch.
    """
    results = [prepare_module(worker_dir, m, project_root, branch) for m in modules]
    return WorkspacePrepResult(ok=all(r.ok for r in results), modules=results)


def prepare_module(
    worker_dir: Path,
    module: Module,
    project_root: Path,
    branch: str | None = None,
) -> ModulePrepResult:
    module_dir = worker_dir / module.name
    target_branch = branch or module.branch

    if not module_dir.exists():
        return _first_clone(module_dir, module, target_branch, project_root)

    if not git_ops.is_git_repo(module_dir):
        return ModulePrepResult(
            module.name,
            STATUS_REPO_ERROR,
            False,
            f"{module_dir} exists but is not a git repository — "
            "will not overwrite; resolve by hand",
        )

    clean = git_ops.is_clean(module_dir)
    if isinstance(clean, GitError):
        return _error_result(module.name, clean)
    if not clean:
        return ModulePrepResult(
            module.name,
            STATUS_DIRTY,
            False,
            "uncommitted changes present — not touched. Commit, stash, or "
            "otherwise resolve before this workspace is prepared again.",
        )

    offline = False
    fetch_err = git_ops.fetch(module_dir)
    if isinstance(fetch_err, GitError):
        if fetch_err.kind == "network":
            offline = True
        else:
            return _error_result(module.name, fetch_err)

    branch_result = _ensure_branch(module_dir, module, target_branch, offline)
    if isinstance(branch_result, ModulePrepResult):
        return branch_result
    branch_note = branch_result

    if not offline:
        ff_err = git_ops.fast_forward_pull(module_dir, target_branch)
        if isinstance(ff_err, GitError):
            if ff_err.kind == "network":
                offline = True
            else:
                return ModulePrepResult(
                    module.name, STATUS_DIVERGED, False, ff_err.message
                )

    status = STATUS_READY_OFFLINE if offline else STATUS_READY
    message = (
        "offline — using last-known local state, not updated from origin"
        if offline
        else "up to date"
    )
    if branch_note:
        message = f"{branch_note}; {message}"
    return ModulePrepResult(module.name, status, True, message, branch=target_branch)


def _first_clone(
    module_dir: Path, module: Module, target_branch: str, project_root: Path
) -> ModulePrepResult:
    source = module.url or str(project_root / module.path)
    err = git_ops.clone(source, module_dir, module.branch)
    if isinstance(err, GitError):
        kind = (
            STATUS_NETWORK_UNREACHABLE if err.kind == "network" else STATUS_REPO_ERROR
        )
        return ModulePrepResult(module.name, kind, False, err.message)

    # Route through `_ensure_branch` rather than going straight to
    # `_create_ticket_branch`. This branch of the code knew only how to
    # CREATE, so a fresh clone ignored an existing `origin/<target>` and
    # started the ticket over from the module's default branch —
    # silently, and reporting "branch created from main" as though that
    # were the whole story. The other path has handled that case all
    # along (`remote_branch_exists` -> `checkout_new_tracking`); the two
    # had simply diverged.
    #
    # It is the resume case that makes this matter: the same ticket
    # prepared on a second machine, or after a workspace is wiped, is a
    # first clone — and is exactly when the branch already exists on the
    # remote. Starting from main there discards the branch point of work
    # that is already pushed.
    #
    # `offline=False` because the clone above just succeeded, so the
    # remote is demonstrably reachable.
    branch_result = _ensure_branch(module_dir, module, target_branch, offline=False)
    if isinstance(branch_result, ModulePrepResult):
        return branch_result

    message = (
        f"cloned; {branch_result}" if branch_result else f"cloned, on {target_branch}"
    )
    return ModulePrepResult(
        module.name,
        STATUS_CLONED,
        True,
        message,
        branch=target_branch,
    )


def _ensure_branch(
    module_dir: Path, module: Module, target_branch: str, offline: bool
) -> ModulePrepResult | str:
    """A blocking `ModulePrepResult` on failure, or — on success — a short
    note saying WHICH of the four ways it got onto the branch, for the
    caller to put in front of its own message. `BRANCH_UNCHANGED` (the
    empty string) when the module was already on it and there is nothing
    to report.

    Returning `None` for all four, as this did, is what made a created
    branch and a resumed one read identically. See the constants above."""
    current = git_ops.current_branch(module_dir)
    if isinstance(current, GitError):
        return _error_result(module.name, current)
    if current == target_branch:
        return BRANCH_UNCHANGED

    if git_ops.local_branch_exists(module_dir, target_branch):
        err = git_ops.checkout_existing(module_dir, target_branch)
        if isinstance(err, GitError):
            return _error_result(module.name, err)
        return _branch_resumed(target_branch)

    if not offline and git_ops.remote_branch_exists(module_dir, target_branch):
        err = git_ops.checkout_new_tracking(module_dir, target_branch)
        if isinstance(err, GitError):
            return _error_result(module.name, err)
        return _branch_tracking(target_branch)

    # Doesn't exist locally or remotely — this is a new branch (e.g. a fresh
    # ticket). Base it on the module's own configured default branch.
    err = _create_ticket_branch(module_dir, target_branch, module.branch)
    if isinstance(err, GitError):
        return _error_result(module.name, err)
    return _branch_created(target_branch, module.branch)


def _create_ticket_branch(
    module_dir: Path, target_branch: str, base_branch: str
) -> None | GitError:
    if git_ops.current_branch(module_dir) != base_branch:
        if git_ops.local_branch_exists(module_dir, base_branch):
            err = git_ops.checkout_existing(module_dir, base_branch)
        elif git_ops.remote_branch_exists(module_dir, base_branch):
            err = git_ops.checkout_new_tracking(module_dir, base_branch)
        else:
            return GitError(
                f"base branch '{base_branch}' not found locally or on origin",
                kind="repo",
            )
        if isinstance(err, GitError):
            return err
    return git_ops.checkout_new_from(module_dir, target_branch, base_branch)


def _error_result(module_name: str, err: GitError) -> ModulePrepResult:
    status = STATUS_NETWORK_UNREACHABLE if err.kind == "network" else STATUS_REPO_ERROR
    return ModulePrepResult(module_name, status, False, err.message)
