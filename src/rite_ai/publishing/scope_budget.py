"""How much a delivery changed, measured from git (SCRUM-65 part 2).

Nothing reads the verdict yet: it is recorded so the false-hold rate can be
measured before anything is held on it.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from rite_ai.githost import hardened_git_env

PASS = "pass"
HOLD = "hold"

_COMMENT_PREFIXES = {".py": ("#",), ".go": ("//",)}


@dataclass
class Budget:
    """One delivery's size, and whether it needs a scope review."""

    files: int = 0
    added: int = 0
    removed: int = 0
    out_of_scope: list[str] = field(default_factory=list)
    comment_lines: int = 0
    code_lines: int = 0
    allowance: int = 0
    verdict: str = PASS
    reasons: list[str] = field(default_factory=list)
    unmeasured: str = ""
    """Why nothing could be measured. `verdict` stays PASS so a failed
    measurement cannot invent a hold."""

    @property
    def comment_ratio(self) -> float | None:
        """None when no line could be classified — 0.0 would read as "no
        comments" for a language the table does not know."""
        if not (self.comment_lines or self.code_lines):
            return None
        return self.comment_lines / max(1, self.code_lines)

    def note(self) -> dict:
        """What goes in the delivery event."""
        return {
            "verdict": self.verdict,
            "files": self.files,
            "added": self.added,
            "removed": self.removed,
            "out_of_scope": sorted(self.out_of_scope),
            "comment_ratio": None
            if self.comment_ratio is None
            else round(self.comment_ratio, 3),
            "allowance": self.allowance,
            "reasons": self.reasons,
            "unmeasured": self.unmeasured,
        }


def _destination(path: str) -> str:
    """A rename's destination, counted once.

    `git diff --numstat -M` emits three shapes and only the first is a plain
    path: `old => new`, `dir/{old => new}`, and `a/{b => c}/d`. Parsing only
    the first made a rename inside the definition of done's own directory
    read as out of scope.
    """
    if " => " not in path:
        return path
    if "{" not in path:
        return path.split(" => ")[-1]
    before, rest = path.split("{", 1)
    middle, after = rest.split("}", 1)
    return before + middle.split(" => ")[-1] + after


def _excluded(path: str, patterns: list[str]) -> bool:
    from fnmatch import fnmatch

    return any(fnmatch(path, p) for p in patterns)


def _in_scope(path: str, dod_paths: set[str]) -> bool:
    """A path the definition of done names, or anything beneath it."""
    return any(path == d or path.startswith(d.rstrip("/") + "/") for d in dod_paths)


def measure(
    project: Path,
    rev_range: str,
    *,
    dod_paths: set[str],
    exclude: list[str],
    items: int,
    lines_per_item: int,
    factor: float,
) -> Budget:
    """Measure `rev_range` in `project`. Never raises."""
    try:
        proc = subprocess.run(
            ["git", "diff", "--numstat", "-M", rev_range],
            cwd=project,
            env=hardened_git_env(),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return Budget(unmeasured=f"git diff could not be run: {e}")
    if proc.returncode != 0:
        said = (proc.stderr or proc.stdout or "").strip()[:200]
        return Budget(unmeasured=f"git diff exited {proc.returncode}: {said}")

    budget = Budget(allowance=max(1, items) * lines_per_item)
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, removed, path = parts
        path = _destination(path)
        if _excluded(path, exclude):
            continue
        budget.files += 1
        budget.added += int(added) if added.isdigit() else 0
        budget.removed += int(removed) if removed.isdigit() else 0
        if not _in_scope(path, dod_paths):
            budget.out_of_scope.append(path)

    budget.comment_lines, budget.code_lines = _added_comment_ratio(
        project, rev_range, exclude
    )

    if budget.out_of_scope:
        budget.verdict = HOLD
        budget.reasons.append(
            f"{len(budget.out_of_scope)} file(s) outside the definition of "
            f"done's paths: {', '.join(sorted(budget.out_of_scope)[:5])}"
        )
    size = budget.added + budget.removed
    if size > budget.allowance * factor:
        budget.verdict = HOLD
        budget.reasons.append(
            f"{size} changed lines is more than {factor}x the allowance for "
            f"{max(1, items)} definition-of-done item(s) ({budget.allowance})"
        )
    return budget


def _added_comment_ratio(
    project: Path, rev_range: str, exclude: list[str]
) -> tuple[int, int]:
    """Added comment lines and added code lines, for extensions rite knows.

    A second `git diff` because `--numstat` carries no content.
    """
    try:
        # 🔴 --no-textconv (Option A review, measured): `git diff` runs a
        # repo-defined diff textconv PROGRAM on the host, and this runs on the
        # Manager-writable module checkout every delivery (SCRUM-75).
        proc = subprocess.run(
            ["git", "diff", "--no-textconv", "--unified=0", rev_range],
            cwd=project,
            env=hardened_git_env(),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 0, 0
    if proc.returncode != 0:
        return 0, 0
    comments = code = 0
    prefixes: tuple[str, ...] = ()
    for line in proc.stdout.splitlines():
        if line.startswith("+++ b/"):
            path = line[len("+++ b/") :]
            prefixes = (
                ()
                if _excluded(path, exclude)
                else _COMMENT_PREFIXES.get(Path(path).suffix, ())
            )
            continue
        if not prefixes or not line.startswith("+") or line.startswith("+++"):
            continue
        body = line[1:].strip()
        if not body:
            continue
        if body.startswith(prefixes):
            comments += 1
        else:
            code += 1
    return comments, code
