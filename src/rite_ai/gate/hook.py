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

from rite_ai.gate.ci import script_invokes

HOOK_MARKER = "# installed-by: rite publish gate"

# Markers this module has used historically. `rite init`'s scaffold shipped
# "# rite: publish gate" before the two installers were consolidated onto
# HOOK_MARKER — a hook installed under that marker is still a rite-installed
# hook and must not be treated as foreign (which would refuse the upgrade a
# user needs `rite update` to be able to perform). Add to this set, never
# remove from it, if the marker ever changes again.
_RECOGNISED_MARKERS = (HOOK_MARKER, "# rite: publish gate")

# Every spelling that runs the gate from a pre-push hook. Every release so
# far writes the first one (checked against v0.1.0, v0.2.0 and v0.3.0); the
# others are for a hook someone wrote by hand after following the advice
# `rite init` prints when git reads hooks from elsewhere.
PRE_PUSH_INVOCATIONS = (
    "rite publish pre-push",
    "rite-ai publish pre-push",
    "rite_ai.gate pre-push",
)

PRE_PUSH_HOOK_SCRIPT = f"""#!/bin/sh
{HOOK_MARKER}
exec rite publish pre-push
"""

# The chained form, for a repo where `core.hooksPath` sends git's hooks
# somewhere else. See `install_pre_push_hook`: rite's gate runs AFTER whatever
# that directory's own `pre-push` does, and this repo's `core.hooksPath` is
# pointed back here so git reads this file at all.
#
# 🔴 **SECURITY-SENSITIVE, in someone else's control.** The redirect is
# typically another project's deliberate choice and the hook it points at may
# be a confidentiality gate — the one measured here is a firm data-leak gate
# whose own header says a hook inside a worktree is "silently disarmed by
# checking out any commit that predates it". So:
#
# * the redirect is resolved at RUN time, from git's global and system config,
#   not baked in: if that project moves or changes its hook, the chain follows
#   whatever git says today rather than carrying a stale path;
# * the upstream hook runs FIRST and its exit code is final. A gate that is
#   consulted after the push has been decided is not a gate;
# * the ref list reaches BOTH. git feeds pre-push its refs on stdin, which one
#   reader consumes; it is written to a file and each hook is fed from that,
#   byte for byte, rather than echoed back out of a shell variable;
# * argv (`<remote-name> <remote-url>`) is forwarded to the upstream hook,
#   which is a real pre-push hook and may use it, and NOT to `rite publish
#   pre-push`, which takes no arguments (see the comment below this script);
# * `mktemp` failing blocks the push. A gate that cannot read the refs must not
#   wave them through;
# * `RITE_PRE_PUSH_CHAINED` stops an upstream hook that somehow re-enters this
#   one from looping, belt to the path comparison's braces.
#
# Not `set -e`: `gate/ci.py::script_invokes` reads this file to decide whether
# the gate actually runs, and bails on constructs it cannot judge. The exit
# codes are handled explicitly instead.
CHAINED_PRE_PUSH_TEMPLATE = f"""#!/bin/sh
{HOOK_MARKER}
# chained: this repo's core.hooksPath is pointed at __OWN_HOOKS__ so git reads
# this file, and the hook git WOULD have run is run first, from wherever
# core.hooksPath says it is now. Do not reorder.
refs_file=$(mktemp) || {{
	echo "pre-push: could not make a temporary file for the ref list, so" >&2
	echo "  neither gate could read it. Refusing the push." >&2
	exit 1
}}
trap 'rm -f "$refs_file"' EXIT
cat > "$refs_file"

own_hooks='__OWN_HOOKS__'
# 🔴 `--type=path`, NOT a bare --get. git stores core.hooksPath verbatim, so a
# value written as `~/…` comes back with a literal tilde, which no shell
# expands inside quotes: `[ -x "~/…/pre-push" ]` is then FALSE for a hook that
# exists, this script falls through to the gate alone, and the upstream hook is
# silently disarmed while the installer reports a chain. The Python half
# already expanduser()s, so the two halves disagreed about the same value —
# which is the defect, not the tilde. Measured: `--type=path` expands `~` and
# leaves absolute and relative values exactly as they are.
upstream_dir=$(git config --global --type=path --get core.hooksPath 2>/dev/null)
if [ -z "$upstream_dir" ]; then
	upstream_dir=$(git config --system --type=path --get core.hooksPath 2>/dev/null)
fi
if [ -n "$upstream_dir" ] && [ "$upstream_dir" != "$own_hooks" ] &&
	[ -z "$RITE_PRE_PUSH_CHAINED" ] && [ -x "$upstream_dir/pre-push" ]; then
	RITE_PRE_PUSH_CHAINED=1 "$upstream_dir/pre-push" "$@" < "$refs_file"
	upstream_status=$?
	if [ "$upstream_status" -ne 0 ]; then
		echo "pre-push: $upstream_dir/pre-push refused this push (exit \
$upstream_status). rite's publish gate was not reached." >&2
		exit "$upstream_status"
	fi
fi

rite publish pre-push < "$refs_file"
"""


def chained_pre_push_script(own_hooks: Path) -> str:
    return CHAINED_PRE_PUSH_TEMPLATE.replace("__OWN_HOOKS__", str(own_hooks))


# NOT "$@" — git invokes pre-push as `pre-push <remote-name> <remote-url>`
# (the ref lines come on stdin, not argv) and `rite publish pre-push` takes
# no arguments, so forwarding argv made every push fail with a Click usage
# error (exit 2, indistinguishable from a real gate failure) instead of
# ever reading stdin. Found by a review round that actually executed the
# installed hook end to end rather than only checking its text for the
# right substrings — see tests/test_gate_hook.py's execution test.


def _is_rite_installed(hook_text: str) -> bool:
    """Did rite AUTHOR this hook? Not whether it runs — see `gate_hook_status`.

    The distinction is the one `ci.py` already draws between
    `is_rite_workflow` and `workflow_runs_gate`. Authorship decides whether
    `install_pre_push_hook` may overwrite the file; it says nothing about
    what the file does now, because the first thing anyone does to a
    generated hook is edit it.
    """
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


def _git(repo_root: Path, *args: str) -> str | None:
    """`git <args>`'s stdout, stripped, or None when git could not answer.

    Fails OPEN for the same reason `redirected_hooks_dir` does: this module's
    job is to install a hook, not to police git's configuration.
    """
    try:
        proc = subprocess.run(
            ["git", *args],
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
    return proc.stdout.strip()


def own_hooks_dir(repo_root: Path) -> Path | None:
    """This repository's OWN hooks directory, absolute — or None when
    `repo_root` is not a repository.

    🔴 **Not `repo_root / ".git" / "hooks"`.** In a git worktree `.git` is a
    *file*, not a directory, and the hooks live in the common directory the
    worktrees share. Measured: `install_pre_push_hook` answered "is not a git
    repository (no .git/)" for every worktree, and `redirected_hooks_dir`
    reported a `core.hooksPath` redirect in every worktree where there was
    none — because it compared git's answer against a path that does not
    exist there. So no worktree has ever had the publish gate installed, for
    a reason that had nothing to do with the redirect it blamed.

    `--git-common-dir` is the one git reports for the shared directory, and
    `git rev-parse --git-path hooks` confirms hooks are read from there in a
    worktree (measured, not assumed).

    ⚠ **Only for a directory that is itself the top of a working tree.** git
    answers for any directory INSIDE a repository, so asking it alone would
    make a plain subdirectory — a module registered as `backend/` that is not
    its own checkout — report the parent project's hooks as its own, and
    `rite doctor` would say its gate was active instead of saying it is not a
    repository.
    """
    top = _git(repo_root, "rev-parse", "--show-toplevel")
    if not top:
        return None
    try:
        if Path(top).resolve() != repo_root.resolve():
            return None
    except OSError:
        return None
    common = _git(repo_root, "rev-parse", "--git-common-dir")
    if common is None:
        return None
    path = Path(common)
    if not path.is_absolute():
        path = repo_root / path
    return (path / "hooks").resolve()


def _resolved(value: str, repo_root: Path) -> Path:
    """A `core.hooksPath` value as the directory git will read.

    ⚠ **A relative value is relative to where hooks RUN** — the top of the
    working tree — not to the git directory. That is git's documented rule and
    it is the trap: `core.hooksPath = .git/hooks` works in the main checkout
    and, in a worktree, points at `<worktree>/.git/hooks`, which does not
    exist, so git runs NO pre-push hook at all and the push goes through.
    Measured end to end, both directions, before this module was allowed to
    set the value.
    """
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    try:
        return path.resolve()
    except OSError:
        return path


def local_hooks_path(repo_root: Path) -> Path | None:
    """What THIS repository's own config says, resolved, or None."""
    value = _git(repo_root, "config", "--local", "--get", "core.hooksPath")
    return _resolved(value, repo_root) if value else None


def upstream_hooks_dir(repo_root: Path) -> Path | None:
    """The hooks directory a global or system `core.hooksPath` sends git to,
    resolved — or None when neither says anything, or it is already this
    repository's own.

    Kept apart from `redirected_hooks_dir` because the two answer different
    questions, and conflating them would disarm the chain. That one asks
    "where is git reading hooks from right now", which stops being the
    redirected directory the moment this module points `core.hooksPath` back
    at the repo. This one asks "what would git have read if we had not", which
    is what has to keep running — so re-running `rite publish install-hook` on
    an already-chained repo writes the chain again instead of quietly
    replacing it with the plain script.
    """
    own = own_hooks_dir(repo_root)
    for scope in ("--global", "--system"):
        value = _git(repo_root, "config", scope, "--get", "core.hooksPath")
        if not value:
            continue
        resolved = _resolved(value, repo_root)
        return None if resolved == own else resolved
    return None


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
    already refuses to cause by clobbering a hand-written hook. What
    `install_pre_push_hook` does instead is CHAIN behind it, touching nothing
    outside this repository.

    Fails OPEN (returns None) whenever git cannot answer — not a repo, git
    missing, a timeout. The caller's job is to install a hook, not to police
    git's configuration, and a false "redirected" would block a legitimate
    install for no reason.
    """
    reported = _git(repo_root, "rev-parse", "--git-path", "hooks")
    if reported is None:
        return None
    where = Path(reported)
    if not where.is_absolute():
        where = repo_root / where
    own = own_hooks_dir(repo_root)
    try:
        resolved = where.resolve()
    except OSError:
        return None
    # ⚠ Compared against the repo's OWN hooks directory, which in a worktree
    # is the shared one and never `<worktree>/.git/hooks` — see
    # `own_hooks_dir`. Comparing against that path reported a redirect in
    # every worktree, with no `core.hooksPath` set anywhere.
    return None if own is None or resolved == own else where


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
    # | "disarmed" | "not_a_repo"
    #
    # "foreign" and "disarmed" differ only in who wrote the hook that is not
    # running the gate, and they are two states because the remedy differs:
    # one is a file to add a line to, the other is a line to put back.
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
    own_hooks = own_hooks_dir(repo_root)
    if own_hooks is None:
        return HookStatus("not_a_repo")

    redirected = redirected_hooks_dir(repo_root)
    if redirected is not None:
        return HookStatus(
            "redirected",
            f"git reads hooks from {redirected} (core.hooksPath), not "
            f"{own_hooks} — the gate does not run on push. `rite publish "
            "install-hook` now installs a hook that runs that one first and "
            "the gate second, and points this repository's core.hooksPath "
            "here; nothing outside this repository is changed.",
        )

    hook_path = own_hooks / "pre-push"
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

    # Read as a SCRIPT, not searched as text. This was
    # `_is_rite_installed(text) or "rite publish pre-push" in text`: a marker
    # is authorship, and a substring is true of a hook that only mentions the
    # command — in a comment, or in the line someone commented out to get one
    # push through and never restored. Both leave every signal saying
    # "active" over a gate git runs and that does nothing, which is §11.5.1's
    # shape and the reason the CI half stopped substring-matching.
    #
    # A hand-written hook counts as much as rite's own: what matters is that
    # the gate runs, not who typed it.
    runs_gate = script_invokes(text, PRE_PUSH_INVOCATIONS)
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
    if _is_rite_installed(text):
        # rite wrote this file and it no longer runs the gate. Saying "was
        # not installed by rite" here — which is what this reported once
        # liveness stopped following the marker — is a false claim about the
        # user's own repository, and it sends them looking for a hook someone
        # else wrote instead of at the edit they made.
        return HookStatus(
            "disarmed",
            f"{hook_path} was written by rite but no longer runs `rite "
            "publish pre-push` — commented out, removed, or neutralised with "
            "`|| true` or `set +e`. The gate does not run on push. Restore "
            "the line, or `rite publish install-hook --force`.",
        )
    return HookStatus(
        "foreign",
        f"{hook_path} exists but was not installed by rite and does not run "
        "`rite publish pre-push` — the gate does not run on push. Add "
        "`rite publish pre-push` to it, or replace it with `rite publish "
        "install-hook --force`.",
    )


def install_pre_push_hook(repo_root: Path, force: bool = False) -> InstallResult:
    """Write this repository's `pre-push` hook, and make sure git reads it.

    Refuses to overwrite an existing hook this module didn't install, unless
    `force=True` — a silently clobbered hook is exactly the kind of surprise
    this tool should never cause in someone else's repo.

    🔴 **A global `core.hooksPath` is CHAINED behind, not switched off.** This
    used to refuse outright, for two correct reasons: a hook git will not read
    is a gate that reports installed and never runs, and the redirected
    directory is typically shared across every repo the user owns, so writing
    into it reaches far outside this project. The defect was stopping there.
    Measured on a real machine: a global redirect meant rite's publish gate ran
    NOWHERE automatically, in any rite project, for the life of that
    redirect — and the directory it pointed at held one hook, a firm
    data-leak gate belonging to another project.

    So: this repository's own `pre-push` runs the redirected hook first and
    the gate second, and this repository's `core.hooksPath` is pointed at its
    own hooks directory so git reads it. Nothing outside the repository is
    written — not `~/.gitconfig`, not the shared hook. The other project's
    gate keeps running, resolved at run time rather than baked in, and the
    ref list reaches both.

    ⚠ **And when the chain cannot be built, this REFUSES rather than
    installing half of it.** The order below is the whole safety property:
    the hook is written BEFORE `core.hooksPath` is pointed at it, so a
    failure can only ever leave an inert file in a directory git is not
    reading — the status quo. The reverse order has a window in which git
    reads a directory with no `pre-push` in it, and that window silently drops
    the other project's gate, which is strictly worse than today's loud
    refusal.
    """
    own_hooks = own_hooks_dir(repo_root)
    if own_hooks is None:
        # ⚠ Asked of git, not of `(repo_root / ".git").is_dir()`: in a worktree
        # `.git` is a file, and that check answered "not a git repository" for
        # every worktree there has ever been.
        return InstallResult(False, f"{repo_root} is not a git repository")

    local = local_hooks_path(repo_root)
    if local is not None and local != own_hooks:
        # This repository's OWN config sends hooks somewhere else. Someone
        # chose that deliberately, here, for this repo; overwriting it would be
        # the "surprise in someone else's repo" a few lines up, and chaining
        # behind it would mean baking a path this module cannot resolve at run
        # time (run-time resolution reads the global and system scopes, which
        # is what the local value is overriding). So: say so, and stop.
        return InstallResult(
            False,
            f"this repository's own git config sends hooks to {local} "
            "(core.hooksPath, --local), so a hook written in "
            f"{own_hooks} would never run. rite will not change a hooks path "
            "you set on this repository. Either remove it with `git config "
            "--local --unset core.hooksPath` and run this again, or add `exec "
            f"rite publish pre-push` to {local}/pre-push yourself.",
        )

    upstream = upstream_hooks_dir(repo_root)
    hook_path = own_hooks / "pre-push"

    if hook_path.exists() and not force:
        try:
            existing = hook_path.read_text()
        except OSError as e:
            return InstallResult(False, f"{hook_path} could not be read: {e}")
        if not _is_rite_installed(existing):
            return InstallResult(
                False,
                f"{hook_path} already exists and was not installed by rite — "
                "pass force=True to overwrite",
            )

    script = (
        chained_pre_push_script(own_hooks)
        if upstream is not None
        else PRE_PUSH_HOOK_SCRIPT
    )
    try:
        own_hooks.mkdir(parents=True, exist_ok=True)
        hook_path.write_text(script)
        hook_path.chmod(0o755)
    except OSError as e:
        return InstallResult(False, f"{hook_path} could not be written: {e}")

    if upstream is None and local is None:
        return InstallResult(True, f"installed {hook_path}")

    # Pointed back at this repository's own hooks, ABSOLUTELY. A relative
    # `.git/hooks` resolves against the working tree a hook runs in, so in a
    # worktree it names a path that does not exist and git runs nothing at
    # all — measured, and the reason this is not spelled the short way.
    if local != own_hooks and (
        _git(repo_root, "config", "--local", "core.hooksPath", str(own_hooks)) is None
    ):
        # 🔴 The loud refusal. The hook above is inert while git still reads
        # the redirected directory, so the other project's gate is untouched
        # and nothing has been silently weakened — which is exactly why the
        # write came first.
        return InstallResult(
            False,
            f"wrote {hook_path}, but could not point this repository's "
            f"core.hooksPath at {own_hooks} (`git config --local` failed), so "
            f"git still reads hooks from {upstream} and the publish gate does "
            "NOT run on push. Nothing else was changed. Set it by hand with "
            f"`git config --local core.hooksPath {own_hooks}`, then run this "
            "again.",
        )
    if upstream is None:
        return InstallResult(True, f"installed {hook_path}")
    return InstallResult(
        True,
        f"installed {hook_path}, chained behind {upstream}/pre-push — that "
        "hook runs first and its refusal is final, then the publish gate. "
        f"This repository's core.hooksPath now points at {own_hooks}; "
        "core.hooksPath elsewhere, and the hook it names, were not touched.",
    )
