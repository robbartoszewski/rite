"""Can this machine force-push to the coordination remote? (P2-1e)

The state branch is rewritten with `--force-with-lease` on every update
(§3.3.1), and many hosts' branch-protection defaults reject force-push outright.
Without a probe that fails as a silent "race loss": every write is rejected,
every writer re-fetches and retries, and nothing ever says the remote is
misconfigured.

**The probe writes to the remote, and never to the state branch.** Git offers no
way to ask a remote whether it would accept a force-push: a `--dry-run` never
reaches the server-side checks that refuse one. So the probe creates a
throwaway sibling branch, force-pushes an unrelated commit over it, and deletes
it. The state branch itself is not touched — force-pushing it to find out would
race every live Manager.

**Its limit, stated rather than hidden:** a protection rule that names only the
exact state branch does not apply to a sibling, so this probe cannot see it. The
report says so.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

# git's well-known empty tree; commits on it need no working tree and no files.
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_URL_LIKE = re.compile(r"^([a-z][a-z0-9+.-]*://|[^/\s]+@[^:\s]+:|/|\.{1,2}/|~)", re.I)


@dataclass
class ForcePushProbe:
    ok: bool
    detail: str
    remedy: str = ""
    leftover_ref: str = ""
    """A probe branch the remote would not let the probe delete."""


def _git(args: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "rite doctor",
        "GIT_AUTHOR_EMAIL": "rite-doctor@example.invalid",
        "GIT_COMMITTER_NAME": "rite doctor",
        "GIT_COMMITTER_EMAIL": "rite-doctor@example.invalid",
    }
    return subprocess.run(
        # The user's own hooks and signing must not decide a probe's outcome.
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        env=env,
    )


def _why(proc: subprocess.CompletedProcess) -> str:
    lines = [
        ln.strip() for ln in (proc.stderr or proc.stdout).splitlines() if ln.strip()
    ]
    lines = [ln for ln in lines if not ln.startswith("To ")]
    return "; ".join(lines[-2:]) if lines else f"git exited {proc.returncode}"


def resolve_remote(remote: str, project_root: Path, timeout: int = 10) -> str | None:
    """The URL to push to. `coordination.remote` may be a URL or path, used as
    given, or the name of a remote in the project's repository."""
    if _URL_LIKE.match(remote):
        return remote
    try:
        proc = _git(["remote", "get-url", remote], project_root, timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None


def probe_force_push(
    remote: str, state_branch: str, project_root: Path, timeout: int = 60
) -> ForcePushProbe:
    url = resolve_remote(remote, project_root)
    if url is None:
        return ForcePushProbe(
            False,
            f"coordination.remote `{remote}` is neither a URL nor a remote in "
            f"{project_root}",
            remedy="set `coordination.remote` in .rite/config.yaml to the "
            "coordination repo's URL",
        )
    ref = f"refs/heads/{state_branch}-rite-probe-{uuid.uuid4().hex[:8]}"
    protection_remedy = (
        f"allow force-push on `{state_branch}` in the coordination repo's branch "
        "protection — rite rewrites that branch on every state update "
        "(SPEC §3.3.1), so a remote that refuses force-push makes every write "
        "look like a lost race"
    )
    with tempfile.TemporaryDirectory(prefix="rite-probe-") as tmp:
        work = Path(tmp)
        try:
            _git(["init", "-q"], work, timeout)
            first = _git(
                ["commit-tree", _EMPTY_TREE, "-m", "rite doctor probe 1"], work, timeout
            )
            second = _git(
                ["commit-tree", _EMPTY_TREE, "-m", "rite doctor probe 2"], work, timeout
            )
            a, b = first.stdout.strip(), second.stdout.strip()
            if not (a and b):
                return ForcePushProbe(
                    False, "could not build probe commits locally: " + _why(first)
                )

            created = _git(["push", "--porcelain", url, f"{a}:{ref}"], work, timeout)
            if created.returncode != 0:
                return ForcePushProbe(
                    False,
                    "could not create a branch on the coordination remote: "
                    + _why(created),
                    remedy="check this machine can push to the coordination repo "
                    "(credentials and write access)",
                )

            forced = _git(
                [
                    "push",
                    "--porcelain",
                    f"--force-with-lease={ref}:{a}",
                    url,
                    f"{b}:{ref}",
                ],
                work,
                timeout,
            )
            deleted = _git(["push", "--porcelain", url, f":{ref}"], work, timeout)
            leftover = "" if deleted.returncode == 0 else ref
        except (OSError, subprocess.SubprocessError) as e:
            return ForcePushProbe(False, f"git could not run the probe: {e}")

    if forced.returncode != 0:
        return ForcePushProbe(
            False,
            f"the coordination remote refused a force-push: {_why(forced)}",
            remedy=protection_remedy,
            leftover_ref=leftover,
        )
    return ForcePushProbe(
        True,
        f"force-push accepted on a probe branch beside `{state_branch}` "
        f"(a rule naming only `{state_branch}` itself would not show here)",
        leftover_ref=leftover,
    )
