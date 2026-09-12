"""Pre-push hook installer.

SPEC §11.5: "The gate runs automatically before any `git push` from a
rite-managed workspace (via a `pre-push` hook installed by `rite init`)."
Installing the hook during `rite init` is the init module's call to make (out
of this session's scope — see the CLI entrypoint boundary); this module
provides the hook content and the install function for it to call, and is
also the single source `python -m rite_ai.gate install-hook` (`__main__.py`)
uses — ONE script, ONE marker, so `rite init`'s scaffold and the standalone
module path can never diverge again (implementation-plan Stage 2 #3: they
used to, with different markers and different invocations, and the standalone
path's `python3 -m rite_ai.gate` broke under a pipx install where a bare
`python3` has no access to the isolated venv `rite` lives in).

The hook script execs the pipx-safe `rite` console script's own
`publish pre-push` command — not `python3 -m rite_ai.gate`, which is broken
under a pipx install for the reason above. This is measurably correct for a
`pipx install` (the console script IS `rite` and it's on PATH) and for `uv
run`-managed installs; it is NOT universal — a bare shell with a dev
checkout's `.venv` not activated has no `rite` on PATH either, and the hook
fails closed (blocks the push with "command not found") in that case rather
than silently passing. Measured, not asserted: an earlier version of this
docstring claimed "works identically for … any other installation method,"
which a review round showed false by running the installed hook directly.
All real logic — reading the ref lines git passes on stdin, computing the
push's revision range, running the gate, mapping the result to a hook exit
code — lives in `compute_pre_push_ranges` (this module) and
`rite_ai.gate.gate.run_gate`, so it is testable the same way as everything else
in this package instead of living only as untestable shell.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

HOOK_MARKER = "# installed-by: rite publish gate"

# Markers this module has used historically. `rite init`'s scaffold shipped
# "# rite: publish gate" before the two installers were consolidated onto
# HOOK_MARKER — a hook installed under that marker is still a rite-installed
# hook and must not be treated as foreign (which would refuse the upgrade a
# user needs `rite update` to be able to perform). Add to this set, never
# remove from it, if the marker ever changes again.
_RECOGNISED_MARKERS = (HOOK_MARKER, "# rite: publish gate")

PRE_PUSH_HOOK_SCRIPT = f"""#!/bin/sh
{HOOK_MARKER}
exec rite publish pre-push
"""
# NOT "$@" — git invokes pre-push as `pre-push <remote-name> <remote-url>`
# (the ref lines come on stdin, not argv) and `rite publish pre-push` takes
# no arguments, so forwarding argv made every push fail with a Click usage
# error (exit 2, indistinguishable from a real gate failure) instead of
# ever reading stdin. Found by a review round that actually executed the
# installed hook end to end rather than only checking its text for the
# right substrings — see tests/test_gate_hook.py's execution test.


def _is_rite_installed(hook_text: str) -> bool:
    return any(marker in hook_text for marker in _RECOGNISED_MARKERS)


# What a brand-new ref excludes: everything already on a remote. Passed
# verbatim to `git log` and to gitleaks' `--log-opts`, both of which
# accept it.
_NOT_ALREADY_PUBLISHED = "--not --remotes"


def compute_pre_push_ranges(stdin_lines: list[str]) -> list[str]:
    """Parse git's pre-push protocol (one line per pushed ref:
    `<local ref> <local sha1> <remote ref> <remote sha1>`) into the
    revision ranges to scan. A branch deletion (all-zero local sha)
    contributes nothing.

    A brand-new ref (all-zero remote sha) is the case that matters, and
    the obvious reading of it is wrong. git's own `pre-push.sample` says
    "New branch, examine all commits" and uses the bare local sha —
    everything reachable. Measured on git's own repository, 82,180
    commits: that scans 60,764 commits and 126 MB, takes 22.9 seconds in
    gitleaks alone before the commit-message and pattern passes, and
    reports 18 findings from other people's historical test fixtures.
    The whole `rite publish check` did not finish inside two minutes.

    A gate that blocks a legitimate push for minutes, or demands
    suppression entries for history the user did not write, is a gate
    people turn off — and it would have been doing it on the first push
    of every ticket branch.

    `--not --remotes` is the range that matches what the gate is for:
    the commits this push would publish that are not already published.
    On the same repository and the same new branch, that is 1 commit and
    0.06 seconds. It stays conservative where it should: a first-ever
    push to an empty remote has no remote-tracking refs to exclude, so
    everything is scanned, which is correct because nothing has been
    published yet.
    """
    ranges: list[str] = []
    for line in stdin_lines:
        parts = line.split()
        if len(parts) != 4:
            continue
        _local_ref, local_sha, _remote_ref, remote_sha = parts
        if set(local_sha) == {"0"}:
            continue  # branch deletion — nothing being pushed
        if set(remote_sha) == {"0"}:
            # New ref: everything it would publish that is not already on
            # a remote. See this function's docstring for the measurement.
            ranges.append(f"{local_sha} {_NOT_ALREADY_PUBLISHED}")
        else:
            ranges.append(f"{remote_sha}..{local_sha}")
    return ranges


def redirected_hooks_dir(repo_root: Path) -> Path | None:
    """The directory git will ACTUALLY read hooks from, when that is not this
    repo's own `.git/hooks` — otherwise None.

    `core.hooksPath` (repo-local, global, or system) redirects hooks wholesale,
    and git then ignores `.git/hooks` completely. Writing the gate there anyway
    produces a file that looks installed, reports installed, and never runs:
    found by a cold rehearsal on a machine with a global `core.hooksPath`,
    where a push carrying four planted secrets went through with exit 0 and no
    gate output at all. `rite init` had said "✓ Created .git/hooks/pre-push".

    Deliberately NOT handled by writing into the redirected directory instead:
    that path is typically shared across every repo the user owns, so
    installing there would reach far outside the project `rite init` was
    pointed at — the same "surprise in someone else's repo" this module
    already refuses to cause by clobbering a hand-written hook.

    Fails OPEN (returns None) whenever git cannot answer — not a repo, git
    missing, a timeout. The caller's job is to install a hook, not to police
    git's configuration, and a false "redirected" would block a legitimate
    install for no reason.
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--git-path", "hooks"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    reported = Path(proc.stdout.strip())
    if not reported.is_absolute():
        reported = repo_root / reported
    try:
        if reported.resolve() == (repo_root / ".git" / "hooks").resolve():
            return None
    except OSError:
        return None
    return reported


@dataclass
class InstallResult:
    ok: bool
    message: str


@dataclass
class HookStatus:
    """Whether the publish gate would actually run on a push from this repo.

    `active` is the only state in which it does. The ways it does not are
    kept apart because the fix differs for each, and because a tool that
    only says "not installed" for a redirected `core.hooksPath` sends the
    user to run an installer that will refuse."""

    state: str
    # "active" | "missing" | "redirected" | "foreign" | "not_executable"
    # | "not_a_repo"
    detail: str = ""

    @property
    def active(self) -> bool:
        return self.state == "active"


def gate_hook_status(repo_root: Path) -> HookStatus:
    """Would `git push` from `repo_root` run the publish gate?

    `install_pre_push_hook` answers this at INSTALL time and `rite init`
    reports it loudly, but nothing asked again afterwards — and the
    answer changes without anyone touching the repo, because
    `core.hooksPath` can be set globally, by a colleague's setup script or
    by a tool, long after the hook was installed. Measured on a real
    project: the hook deleted AND `core.hooksPath` pointed elsewhere, and
    `rite doctor` printed "ok"."""
    if not (repo_root / ".git").exists():
        return HookStatus("not_a_repo")

    redirected = redirected_hooks_dir(repo_root)
    if redirected is not None:
        return HookStatus(
            "redirected",
            f"git reads hooks from {redirected} (core.hooksPath), not "
            f"{repo_root / '.git' / 'hooks'} — the gate does not run on push. "
            "Either add `exec rite publish pre-push` to the pre-push hook "
            "there, or run `git config --local core.hooksPath .git/hooks` "
            "followed by `rite publish install-hook`.",
        )

    hook_path = repo_root / ".git" / "hooks" / "pre-push"
    if not hook_path.is_file():
        return HookStatus(
            "missing",
            "no pre-push hook — the gate does not run on push. Install it "
            "with `rite publish install-hook`.",
        )
    try:
        text = hook_path.read_text()
    except OSError as e:
        return HookStatus("missing", f"pre-push hook is unreadable: {e}")

    runs_gate = _is_rite_installed(text) or "rite publish pre-push" in text
    # The second case is a hand-written hook, or one written into a shared
    # hooks directory by a user following the redirect advice above. It
    # runs the gate, which is the thing that matters.
    if runs_gate:
        if not os.access(hook_path, os.X_OK):
            # git does not run a hook without the execute bit. It skips it
            # and says so in a hint nobody reads during a scripted push,
            # so the gate is silently absent while every other signal —
            # the file, its contents, this very check before it asked —
            # says installed. Measured: `chmod -x` on a working hook, and
            # the same commit that had just been blocked pushed a live
            # credential to the remote with exit 0, while `rite doctor`
            # printed "active" and "ok".
            return HookStatus(
                "not_executable",
                f"{hook_path} runs the gate but is not executable, so git "
                "skips it and the gate does not run on push. Fix with "
                f"`chmod +x {hook_path}`, or `rite publish install-hook "
                "--force`.",
            )
        return HookStatus("active")
    return HookStatus(
        "foreign",
        f"{hook_path} exists but was not installed by rite and does not run "
        "`rite publish pre-push` — the gate does not run on push. Add "
        "`rite publish pre-push` to it, or replace it with `rite publish "
        "install-hook --force`.",
    )


def install_pre_push_hook(repo_root: Path, force: bool = False) -> InstallResult:
    """Write `.git/hooks/pre-push`. Refuses to overwrite an existing hook
    that this module didn't install, unless `force=True` — a silently
    clobbered hook is exactly the kind of surprise this tool should never
    cause in someone else's repo."""
    git_dir = repo_root / ".git"
    if not git_dir.is_dir():
        return InstallResult(False, f"{repo_root} is not a git repository (no .git/)")

    redirected = redirected_hooks_dir(repo_root)
    if redirected is not None:
        return InstallResult(
            False,
            f"git reads hooks from {redirected} (core.hooksPath), not "
            f"{git_dir / 'hooks'} — a hook written there would never run, so "
            "the publish gate would be silently inactive on every push. Point "
            "this repo back at its own hooks with `git config --local "
            "core.hooksPath .git/hooks`, or add `exec rite publish pre-push` "
            "to the pre-push hook in that directory yourself.",
        )

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "pre-push"

    if hook_path.exists() and not force:
        existing = hook_path.read_text()
        if not _is_rite_installed(existing):
            return InstallResult(
                False,
                f"{hook_path} already exists and was not installed by rite — "
                "pass force=True to overwrite",
            )

    hook_path.write_text(PRE_PUSH_HOOK_SCRIPT)
    hook_path.chmod(0o755)
    return InstallResult(True, f"installed {hook_path}")
