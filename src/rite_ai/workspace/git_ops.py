"""Low-level git operations for workspace preparation.

Every function here returns `None` (success) or `GitError` (failure) —
errors as values, matching this codebase's convention elsewhere (see
`rite_ai.config.parse.ParseError`, `rite_ai.gate.suppression.SuppressionError`).

`GitError.kind` distinguishes "network" from "repo" failures — this is the
specific thing `prepare.py` needs in order to degrade gracefully on a
network problem while still failing loudly on a genuinely broken repository.
The classification is a heuristic over git's stderr text, not a git API
contract, so it is necessarily incomplete — see `classify_git_failure`'s
docstring for exactly what it does and does not claim.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GitError:
    message: str
    kind: str  # "network" | "repo"


# Substrings seen in git/ssh/curl stderr for a genuine connectivity failure.
# Deliberately conservative: an UNRECOGNISED failure is classified "repo",
# not "network" — a session should stop and surface an unfamiliar error
# rather than silently treat it as "just offline" and carry on with stale
# state. This list is not exhaustive and is not meant to be; extend it as
# real failures are observed, the same way the gate module's heuristics are
# documented rather than assumed complete.
_NETWORK_SIGNATURES = (
    "could not resolve host",
    "could not resolve proxy",
    "connection timed out",
    "connection refused",
    "network is unreachable",
    "no route to host",
    "operation timed out",
    "temporary failure in name resolution",
    "ssh: connect to host",
)

# Failures that must NEVER be waved through as "just offline", whatever
# else the message also matches. Checked FIRST, and the list is about
# trust rather than reachability: a certificate that does not verify means
# the server answered and could not be believed, which is the opposite of
# unreachable and is the one failure most worth stopping on.
#
# `"unable to access"` used to be in the list above, annotated as the
# "curl-style" prefix. Measured against real git 2.x, that prefix is
# transport-generic — every one of these produced it:
#
#   fatal: unable to access '<url>': SSL certificate problem: self signed
#   fatal: unable to access '<url>': SSL certificate problem: certificate has expired
#   fatal: unable to access '<url>': SSL: no alternative certificate subject
#                                    name matches target host name
#
# so all three classified as "network", and `prepare` reported the module
# "ready (offline)" and carried on with whatever was already on disk. A
# wrong-host certificate is what interception looks like, and rite's
# answer to it was to shrug. The prefix was also unnecessary for its
# stated purpose: the connectivity failures it was meant to catch carry
# "could not resolve host" / "connection refused" / "operation timed out"
# in the same message and match on those.
_NEVER_NETWORK_SIGNATURES = (
    "ssl certificate problem",
    "certificate has expired",
    "self signed certificate",
    "certificate subject name",
    "server certificate verification failed",
    "unable to get local issuer certificate",
    "certificate verify failed",
    "host key verification failed",
    "remote host identification has changed",
)


def classify_git_failure(stderr: str) -> str:
    """ "network" if stderr matches a known connectivity-failure signature,
    else "repo". Biased toward "repo" on anything unrecognised — see module
    docstring for why an ambiguous failure should block rather than degrade.

    A trust failure (`_NEVER_NETWORK_SIGNATURES`) wins over any
    connectivity signature in the same message: "I could not reach the
    server" and "I reached it and could not believe it" have opposite
    correct responses, and only one of them is safe to carry on from."""
    lowered = stderr.lower()
    if any(sig in lowered for sig in _NEVER_NETWORK_SIGNATURES):
        return "repo"
    if any(sig in lowered for sig in _NETWORK_SIGNATURES):
        return "network"
    return "repo"


def summarise_stderr(stderr: str) -> str:
    """git's stderr, reduced to the part that says what went wrong.

    `prepare` prints one line per module, and embedding raw stderr broke
    that: a diverged branch produced twelve lines of git's `hint:` advice
    block wrapped inside a single status line, so the actual sentence —
    "Not possible to fast-forward" — arrived after a screen of guidance
    about `git merge --no-ff`. The advice is real and belongs in the
    repository, where the person will resolve it; the status line's job
    is to say which module is blocked and why.

    Blank lines and `hint:` lines go; everything else is kept, in order,
    joined by a separator so the line stays one line.
    """
    kept = [
        line.strip()
        for line in stderr.splitlines()
        if line.strip() and not line.strip().startswith("hint:")
    ]
    return " / ".join(kept)


def _run(
    args: list[str],
    cwd: Path,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
    )


def is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


def is_clean(repo_dir: Path) -> bool | GitError:
    try:
        proc = _run(["git", "status", "--porcelain"], cwd=repo_dir)
    except (OSError, subprocess.TimeoutExpired) as e:
        return GitError(f"'git status' failed: {e}", kind="repo")
    if proc.returncode != 0:
        return GitError(
            f"'git status' failed: {summarise_stderr(proc.stderr)}",
            kind=classify_git_failure(proc.stderr),
        )
    return proc.stdout.strip() == ""


def unpushed_commit_count(repo_dir: Path) -> int | GitError:
    """Commits reachable from a local branch and from no remote-tracking
    branch — work that exists only in this checkout.

    The question this answers is "what disappears if this directory is
    deleted", and `is_clean` only answers half of it: a worker that
    committed its afternoon and never pushed has a spotlessly clean tree.
    A repository with no remote at all reports every commit, which is
    correct for the question — nothing is anywhere else.
    """
    try:
        proc = _run(
            ["git", "rev-list", "--count", "--branches", "--not", "--remotes"],
            cwd=repo_dir,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return GitError(f"'git rev-list' failed: {e}", kind="repo")
    if proc.returncode != 0:
        return GitError(
            f"'git rev-list' failed: {summarise_stderr(proc.stderr)}",
            kind=classify_git_failure(proc.stderr),
        )
    try:
        return int(proc.stdout.strip() or 0)
    except ValueError:
        return GitError(
            f"'git rev-list' returned {proc.stdout.strip()[:60]!r}", kind="repo"
        )


def uncommitted_paths(repo_dir: Path) -> list[str] | GitError:
    """The porcelain lines for a dirty tree — modified AND untracked.

    `is_clean` answers yes/no; a message telling someone what they are
    about to lose has to name the files.
    """
    try:
        proc = _run(["git", "status", "--porcelain"], cwd=repo_dir)
    except (OSError, subprocess.TimeoutExpired) as e:
        return GitError(f"'git status' failed: {e}", kind="repo")
    if proc.returncode != 0:
        return GitError(
            f"'git status' failed: {summarise_stderr(proc.stderr)}",
            kind=classify_git_failure(proc.stderr),
        )
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def current_branch(repo_dir: Path) -> str | GitError:
    try:
        proc = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir)
    except (OSError, subprocess.TimeoutExpired) as e:
        return GitError(f"'git rev-parse' failed: {e}", kind="repo")
    if proc.returncode != 0:
        return GitError(
            f"'git rev-parse' failed: {summarise_stderr(proc.stderr)}",
            kind=classify_git_failure(proc.stderr),
        )
    return proc.stdout.strip()


def local_branch_exists(repo_dir: Path, branch: str) -> bool:
    proc = _run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo_dir,
    )
    return proc.returncode == 0


def remote_branch_exists(repo_dir: Path, branch: str, remote: str = "origin") -> bool:
    proc = _run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/remotes/{remote}/{branch}"],
        cwd=repo_dir,
    )
    return proc.returncode == 0


def fetch(repo_dir: Path, remote: str = "origin") -> None | GitError:
    present = _has_remote(repo_dir, remote)
    if isinstance(present, GitError):
        # Propagated, not swallowed. A module whose remote cannot even be
        # listed is not a module that is up to date.
        return present
    if not present:
        return None  # local-only module, nothing to fetch — not an error
    try:
        proc = _run(["git", "fetch", remote, "--prune"], cwd=repo_dir, timeout=120)
    except subprocess.TimeoutExpired:
        return GitError(f"'git fetch {remote}' timed out", kind="network")
    except OSError as e:
        return GitError(f"'git fetch {remote}' failed: {e}", kind="repo")
    if proc.returncode != 0:
        return GitError(
            f"'git fetch {remote}' failed: {summarise_stderr(proc.stderr)}",
            kind=classify_git_failure(proc.stderr),
        )
    return None


def _has_remote(repo_dir: Path, remote: str) -> bool | GitError:
    """True, False, or "could not tell" — THREE answers, not two.

    It returned a bool built from stdout alone: `returncode` was never read
    and `OSError` was never caught, so anything that stopped `git remote`
    producing output — a locked index, a half-finished clone, a corrupt
    repository, git missing — came back as an empty list and therefore as
    "this module has no remote configured".

    `fetch` reads that as "local-only module, nothing to fetch — not an
    error" and returns success. `rite prepare` then reports **ready, up to
    date** for a module it never contacted a remote about, and the Worker
    builds on stale code believing it is current. Measured on a module with
    a real GitHub URL whose checkout's remote was named `upstream`:
    `status = ready | ok = True | message = up to date`.

    "Could not check" is not "nothing to check". Every other function in this
    module returns `None | GitError` for exactly this reason; this one was
    the exception, and it was the one whose wrong answer was silent.
    """
    try:
        proc = _run(["git", "remote"], cwd=repo_dir)
    except (OSError, subprocess.TimeoutExpired) as e:
        return GitError(f"'git remote' failed: {e}", kind="repo")
    if proc.returncode != 0:
        return GitError(
            f"'git remote' failed: {summarise_stderr(proc.stderr)}", kind="repo"
        )
    return remote in proc.stdout.splitlines()


def checkout_existing(repo_dir: Path, branch: str) -> None | GitError:
    """Checkout a branch that already exists locally."""
    try:
        proc = _run(["git", "checkout", branch], cwd=repo_dir)
    except (OSError, subprocess.TimeoutExpired) as e:
        return GitError(f"'git checkout {branch}' failed: {e}", kind="repo")
    if proc.returncode != 0:
        return GitError(
            f"'git checkout {branch}' failed: {summarise_stderr(proc.stderr)}",
            kind="repo",
        )
    return None


def checkout_new_tracking(
    repo_dir: Path, branch: str, remote: str = "origin"
) -> None | GitError:
    """Create a local branch tracking `<remote>/<branch>`, which must already
    exist (call `fetch` first)."""
    try:
        proc = _run(
            ["git", "checkout", "-b", branch, "--track", f"{remote}/{branch}"],
            cwd=repo_dir,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return GitError(f"'git checkout -b {branch}' failed: {e}", kind="repo")
    if proc.returncode != 0:
        return GitError(
            f"'git checkout -b {branch}' failed: {summarise_stderr(proc.stderr)}",
            kind="repo",
        )
    return None


def checkout_new_from(repo_dir: Path, branch: str, base: str) -> None | GitError:
    """Create a brand-new local branch (e.g. a fresh ticket branch) from
    `base`, which must already be checked out or otherwise resolvable."""
    try:
        proc = _run(["git", "checkout", "-b", branch, base], cwd=repo_dir)
    except (OSError, subprocess.TimeoutExpired) as e:
        return GitError(f"'git checkout -b {branch} {base}' failed: {e}", kind="repo")
    if proc.returncode != 0:
        return GitError(
            f"'git checkout -b {branch} {base}' failed: "
            f"{summarise_stderr(proc.stderr)}",
            kind="repo",
        )
    return None


def fast_forward_pull(
    repo_dir: Path,
    branch: str,
    remote: str = "origin",
) -> None | GitError:
    """Fast-forward only — never merges, never rebases, never discards local
    commits. A non-fast-forwardable branch (local history diverged from the
    remote) fails cleanly here rather than being silently resolved one way
    or the other; the caller reports this as a blocking "diverged" state."""
    if not remote_branch_exists(repo_dir, branch, remote):
        # nothing upstream to compare against (brand-new local branch)
        return None
    try:
        proc = _run(["git", "merge", "--ff-only", f"{remote}/{branch}"], cwd=repo_dir)
    except (OSError, subprocess.TimeoutExpired) as e:
        return GitError(f"'git merge --ff-only' failed: {e}", kind="repo")
    if proc.returncode != 0:
        return GitError(
            f"fast-forward failed (local and remote have diverged): "
            f"{summarise_stderr(proc.stderr)}",
            kind="repo",
        )
    return None


def clone(source: str, dest: Path, branch: str) -> None | GitError:
    """`source` is either a remote URL or a local filesystem path (for
    local-only modules — see `prepare.prepare_module`'s docstring for that
    design choice)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = _run(
            [
                "git",
                "clone",
                "--branch",
                branch,
                "--origin",
                "origin",
                source,
                str(dest),
            ],
            cwd=dest.parent,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return GitError(f"clone of {source!r} timed out", kind="network")
    except OSError as e:
        return GitError(f"clone of {source!r} failed: {e}", kind="repo")
    if proc.returncode != 0:
        return GitError(
            f"clone of {source!r} failed: {summarise_stderr(proc.stderr)}",
            kind=classify_git_failure(proc.stderr),
        )
    return None
