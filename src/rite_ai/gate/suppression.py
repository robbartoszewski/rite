"""Per-finding suppression, `.gitleaksignore`-style, with a mandatory reason.

SPEC §11.4: "Opt-out is per-finding suppression with a recorded reason...
Never a global off switch — that's how these end up permanently disabled
after one false positive." There is deliberately no "disable the gate" flag
anywhere in this module, or in `gate.py`'s orchestration — suppressing
everything means writing an entry for every fingerprint, one at a time, each
with its own reason, which is the friction the design wants.

File format, one entry per non-comment line:

    <fingerprint>  # <reason>

`<fingerprint>` is `commit:file:rule:line` (`-` for commit when there is
none) — see `findings.Finding.fingerprint`, which uses the identical scheme
gitleaks itself reports, verified against a live gitleaks JSON report. A line
with a fingerprint but no `#` reason is a parse error, not a silent no-op —
the reason is the point of this file existing at all, per SPEC.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rite_ai.gate.findings import Finding

DEFAULT_SUPPRESSION_PATH = ".rite/gitleaksignore"


@dataclass
class Suppression:
    fingerprint: str
    reason: str
    line_no: int  # 1-based line in the suppression file, for error messages


@dataclass
class SuppressionError:
    message: str


def parse(path: Path) -> list[Suppression] | SuppressionError:
    if not path.exists():
        return []
    entries: list[Suppression] = []
    for line_no, raw_line in enumerate(path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "#" not in line:
            return SuppressionError(
                f"{path}:{line_no}: suppression has no reason — "
                f"expected '<fingerprint>  # <reason>', got: {raw_line!r}"
            )
        fingerprint, _, reason = line.partition("#")
        fingerprint = fingerprint.strip()
        reason = reason.strip()
        if not fingerprint:
            return SuppressionError(f"{path}:{line_no}: empty fingerprint")
        if not reason:
            return SuppressionError(
                f"{path}:{line_no}: suppression for {fingerprint!r} has an "
                "empty reason — a reason is required, not optional"
            )
        entries.append(
            Suppression(fingerprint=fingerprint, reason=reason, line_no=line_no)
        )
    return entries


def apply(
    findings: list[Finding], suppressions: list[Suppression]
) -> tuple[list[Finding], list[Finding]]:
    """Split findings into (blocking, suppressed) by fingerprint match."""
    suppressed_fps = {s.fingerprint for s in suppressions}
    blocking = [f for f in findings if f.fingerprint not in suppressed_fps]
    suppressed = [f for f in findings if f.fingerprint in suppressed_fps]
    return blocking, suppressed


def find_stale(
    suppressions: list[Suppression], findings: list[Finding]
) -> list[Suppression]:
    """Suppressions whose fingerprint no longer matches any current finding —
    SPEC §11.4: "stale suppressions are noise and a sign that someone is
    accumulating rather than deciding." Not a blocking failure on its own
    (see gate.py's exit-code contract) — surfaced as a warning so it gets
    cleaned up rather than silently ignored forever."""
    live_fps = {f.fingerprint for f in findings}
    return [s for s in suppressions if s.fingerprint not in live_fps]


def append(path: Path, fingerprint: str, reason: str) -> None:
    """Add one suppression entry. Never truncates or rewrites existing
    entries — suppression is additive, one decision at a time."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text() if path.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    with path.open("w") as f:
        f.write(existing)
        f.write(f"{fingerprint}  # {reason}\n")
