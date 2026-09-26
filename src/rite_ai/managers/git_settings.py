"""The operator's own git settings that would stop a Manager, what rite sets
instead for that Manager's git, and what it deliberately does not.

🔴 **A Manager runs git with YOUR global config, inside a sandbox that cannot
reach what that config points at.** Two settings stop it outright, and
neither said why:

- `commit.gpgsign` (and `tag.gpgsign`). Signing needs the operator's key —
  under `~/.ssh` for `gpg.format ssh` — and the sandbox does not grant it, so
  every commit the Manager made failed.
- A `core.hooksPath` the project did not set. A global hooks directory is
  outside the sandbox, and git fails a push on a hook it cannot run.

rite already protects its OWN git from exactly this (`git_backend`,
`remote_probe` run git with `-c core.hooksPath=/dev/null -c
commit.gpgsign=false`), and a Worker's sandbox already gets signing off and
the clone's own hooks (`sandbox.sandbox_git_environment`). The Manager was
the one left out.

⚠ **Signing is overridden; a hooks path is NOT, and that asymmetry is the
point.** Turning signing off for an agent's commits is the Worker's
precedent and removes nothing that guards anything. A global hook can be a
guard: on the first machine this ran against, the global `pre-push` exists
to stop firm data leaving the machine. Inside the sandbox that hook cannot
run and the push fails CLOSED; overriding `core.hooksPath` would make it
fail OPEN, silently skipping a guard the operator installed on purpose. A
Worker's sandbox makes that trade (`sandbox_git_environment`) for a clone
of its own; for a Manager in the operator's real checkout it is not rite's
to make. So a global hooks path is REPORTED — with the one-line opt-in and
what it costs — and the push keeps failing until the operator chooses.

A `core.hooksPath` the project set locally (husky's `.husky`, say) is the
project's choice and is neither overridden nor reported.

⚠ **Said, never silent.** Turning off signing for someone who signs
deliberately is a decision, so every override is announced by `rite start`
and reported by `rite doctor`. Nothing here writes a config file: the values
travel as `GIT_CONFIG_*` in the Manager's pane environment, which git reads
after every config file, and which applies to that Manager's session alone.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Finding:
    """One host git setting that would stop a Manager."""

    key: str
    found: str
    """What the operator's config says."""
    value: str | None
    """What the Manager's git gets instead, or None: reported, not replaced."""
    said: str
    """The sentence `rite start` and `rite doctor` print."""


def _git(root: Path, *args: str) -> str | None:
    """stdout of a `git config`-style read, or None when unset or unreadable."""
    try:
        done = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip() or None


def host_git_findings(root: Path) -> list[Finding]:
    """What this machine's git config would break for a Manager in `root`."""
    found: list[Finding] = []

    fmt = _git(root, "config", "--get", "gpg.format") or "openpgp"
    where = (
        "under ~/.ssh (gpg.format ssh)"
        if fmt == "ssh"
        else f"outside the sandbox (gpg.format {fmt})"
    )
    for key, what in (("commit.gpgsign", "commit"), ("tag.gpgsign", "tag")):
        if _git(root, "config", "--type=bool", "--get", key) != "true":
            continue
        found.append(
            Finding(
                key=key,
                found="true",
                value="false",
                said=(
                    f"{key} is true in your git config. Signing needs your key, "
                    f"{where}, which a Manager's sandbox cannot read, so every "
                    f"{what} it made would fail. rite turns signing OFF for "
                    f"Manager {what}s only: they are the agent's work, not "
                    "yours, as a Worker's already are. Your own commits and "
                    "your git config are unchanged. If this project requires "
                    f"signed {what}s, a Manager's will not meet that; sign "
                    "them yourself when you take them."
                ),
            )
        )

    hooks = _git(root, "config", "--get", "core.hooksPath")
    if hooks and _git(root, "config", "--local", "--get", "core.hooksPath") is None:
        path = Path(hooks).expanduser()
        # A relative value resolves inside each repository, so the sandbox
        # can reach it; so can an absolute one inside this project.
        outside = path.is_absolute() and not path.resolve().is_relative_to(
            root.resolve()
        )
        common = _git(root, "rev-parse", "--git-common-dir")
        if outside and common:
            own = shlex.quote(str((root / common).resolve() / "hooks"))
            found.append(
                Finding(
                    key="core.hooksPath",
                    found=hooks,
                    value=None,
                    said=(
                        f"core.hooksPath is {hooks} in your git config, not "
                        "this project's. A Manager's sandbox cannot run hooks "
                        "from there, so git will fail its commits and pushes "
                        "on any hook there. rite does NOT bypass them: a "
                        "global hook may be a guard you rely on, and skipping "
                        "it silently would be worse than failing. To let "
                        "Managers commit and push here, point THIS project at "
                        "its own hooks, where `rite init` installs the publish "
                        f"gate: `git config --local core.hooksPath {own}`. "
                        "That also stops your global hooks running for your "
                        "own commits and pushes in this project."
                    ),
                )
            )
    return found


def pane_environment(root: Path, existing: dict[str, str]) -> dict[str, str]:
    """`GIT_CONFIG_*` entries for the replaced settings, numbered after
    `existing`'s.

    `existing` is the rest of the pane environment: `github_access` already
    uses `GIT_CONFIG_COUNT` for the credential helper, and a second count
    would replace the first rather than add to it.
    """
    overrides = [f for f in host_git_findings(root) if f.value is not None]
    if not overrides:
        return {}
    start = int(existing.get("GIT_CONFIG_COUNT", "0"))
    env = {"GIT_CONFIG_COUNT": str(start + len(overrides))}
    for i, o in enumerate(overrides, start=start):
        env[f"GIT_CONFIG_KEY_{i}"] = o.key
        env[f"GIT_CONFIG_VALUE_{i}"] = str(o.value)
    return env
