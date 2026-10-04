"""How much a delivery changed, measured from git (SCRUM-65 part 2).

A Worker delivered a fix as 27 files and +1505/-112 and called it done; the
same fix held to its definition of done is 18 files and +621/-72. The review
that ran produced the growth, because every finding was resolved by adding.
The checklist's Scope section is the rule; this is the measurement that
notices when the rule was not followed.

**Shadow mode.** `verdict` is recorded in the delivery event and delivery
proceeds regardless while `ScopeConfig.enforce` is false, so the false-hold
rate can be measured before anything is held on it.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

PASS = "pass"
HOLD = "hold"

_COMMENT_PREFIXES = {
    ".py": ("#",),
    ".sh": ("#",),
    ".toml": ("#",),
    ".yaml": ("#",),
    ".yml": ("#",),
    ".go": ("//",),
    ".js": ("//",),
    ".ts": ("//",),
    ".rs": ("//",),
}


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
    """Why nothing could be measured, when nothing could be. A git that
    cannot be asked is not a pass: `verdict` stays PASS so shadow mode does
    not invent a hold, and this says the number is absent."""

    @property
    def comment_ratio(self) -> float:
        return self.comment_lines / self.code_lines if self.code_lines else 0.0

    def note(self) -> dict:
        """What goes in the delivery event."""
        return {
            "verdict": self.verdict,
            "files": self.files,
            "added": self.added,
            "removed": self.removed,
            "out_of_scope": sorted(self.out_of_scope),
            "comment_ratio": round(self.comment_ratio, 3),
            "allowance": self.allowance,
            "reasons": self.reasons,
            "unmeasured": self.unmeasured,
        }


def _excluded(path: str, patterns: list[str]) -> bool:
    from fnmatch import fnmatch

    return any(fnmatch(path, p) for p in patterns)


def _in_scope(path: str, dod_paths: set[str], claimed_files: set[str]) -> bool:
    if path in claimed_files:
        return True
    # A path the definition of done names, or anything beneath it when it
    # names a directory. A directory CLAIM is deliberately not enough: a
    # claim on all of `docs` made every doc edit look in scope.
    return any(path == d or path.startswith(d.rstrip("/") + "/") for d in dod_paths)


def measure(
    project: Path,
    rev_range: str,
    *,
    dod_paths: set[str],
    claimed_files: set[str],
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
        # A rename arrives as `old => new`; count the destination once.
        if " => " in path:
            path = path.split(" => ")[-1].strip("}")
        if _excluded(path, exclude):
            continue
        budget.files += 1
        budget.added += int(added) if added.isdigit() else 0
        budget.removed += int(removed) if removed.isdigit() else 0
        if not _in_scope(path, dod_paths, claimed_files):
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
    """Added comment lines and added code lines, for extensions rite knows."""
    try:
        proc = subprocess.run(
            ["git", "diff", "--unified=0", rev_range],
            cwd=project,
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
