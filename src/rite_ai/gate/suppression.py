"""Per-finding suppression, `.gitleaksignore`-style, with a mandatory reason.

SPEC §11.4: "Opt-out is per-finding suppression with a recorded reason...
Never a global off switch — that's how these end up permanently disabled
after one false positive." There is deliberately no "disable the gate" flag
anywhere in this module, or in `gate.py`'s orchestration — suppressing
everything means writing an entry for every fingerprint, one at a time, each
with its own reason, which is the friction the design wants.

File format, one entry per non-comment line:

    <fingerprint>  # <reason>

`<fingerprint>` is one of two forms, and an entry in either suppresses the
finding it names:

    commit:file:rule:line              where it was found
    commit:file:rule:sha256-<digest>   what was found

(`-` for commit when there is none.) The first is the scheme gitleaks itself
reports, verified against a live gitleaks JSON report. An entry already
written in it keeps working and needs no migration — it just stays exposed to
what the second form fixes. The second is rite's, and is the one to write
from now on: a line number is a proxy for a finding, and editing
anything ABOVE a suppressed line silently moves it, so the entry goes stale
and the finding it covered starts blocking publish while the code it covers
has not changed. See `findings.Finding.content_fingerprint`. Nothing here is
ever handed to gitleaks — rite does its own matching — so the second form
costs no compatibility.

A line with a fingerprint but no `#` reason is a parse error, not a silent
no-op — the reason is the point of this file existing at all, per SPEC.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rite_ai.gate.findings import Finding

DEFAULT_SUPPRESSION_PATH = ".rite/gitleaksignore"

CONTENT_PREFIX = "sha256-"
"""What marks the content form's last field. See
`findings.Finding.content_fingerprint`."""


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
    """Split findings into (blocking, suppressed) by fingerprint match.

    Either form matches — a finding is suppressed by an entry naming where it
    is or one naming what it is."""
    suppressed_fps = {s.fingerprint for s in suppressions}
    blocking: list[Finding] = []
    suppressed: list[Finding] = []
    for f in findings:
        (suppressed if _matches(f, suppressed_fps) else blocking).append(f)
    return blocking, suppressed


def _matches(finding: Finding, fingerprints: set[str]) -> bool:
    return finding.fingerprint in fingerprints or (
        finding.content_fingerprint is not None
        and finding.content_fingerprint in fingerprints
    )


def fingerprints_of(findings: list[Finding]) -> set[str]:
    """Every string that would match one of these findings."""
    live = {f.fingerprint for f in findings}
    live |= {f.content_fingerprint for f in findings if f.content_fingerprint}
    return live


def find_stale(
    suppressions: list[Suppression], findings: list[Finding]
) -> list[Suppression]:
    """Suppressions whose fingerprint no longer matches any current finding —
    SPEC §11.4: "stale suppressions are noise and a sign that someone is
    accumulating rather than deciding." Not a blocking failure on its own
    (see gate.py's exit-code contract) — surfaced as a warning so it gets
    cleaned up rather than silently ignored forever."""
    live_fps = fingerprints_of(findings)
    return [s for s in suppressions if s.fingerprint not in live_fps]


def moved_to(stale: Suppression, findings: list[Finding]) -> Finding | None:
    """The finding this stale suppression probably covers at its new line.

    Only ever a LINE-pinned entry. In that form the line number is part of
    the identity, so an edit ANYWHERE ABOVE a suppressed line invalidates the
    entry, the finding it covered becomes blocking, and the entry is reported
    stale — while nothing about the suppressed code changed. The report
    already prints both halves; it never said they were the same thing.

    A content-pinned entry is returned `None` for, always. It cannot have
    moved: position is not in its identity, so the only way it goes stale is
    that the matched TEXT it named is gone. Offering to re-point it at
    whatever the rule now matches in that file would hand the reader one
    paste that moves an accepted exemption onto a string nobody has looked at
    — which is the case this form exists to force a decision about.

    Matched on `(commit, file, rule)` with a DIFFERENT line. Deliberately
    conservative:

    * a different commit is not a move — that is a history rewrite, whose
      remedy is re-pointing to a new commit, not a new line, and sending the
      reader to the wrong fix is worse than saying nothing;
    * two or more candidates is ambiguous, and two findings of one rule in one
      file is the ordinary case in this repository's own suppression list, so
      it returns None rather than guessing.

    Returns None when there is no single obvious answer. The caller reports
    the stale entry either way; this only adds the "moved to" line.
    """
    want_commit, _, rest = stale.fingerprint.partition(":")
    want_file, _, rest = rest.partition(":")
    want_rule, _, want_line = rest.rpartition(":")
    if not want_rule or want_line.startswith(CONTENT_PREFIX):
        return None
    candidates = [
        f
        for f in findings
        if (f.commit or "-") == want_commit
        and f.file == want_file
        and f.rule_id == want_rule
        and str(f.line) != want_line
    ]
    return candidates[0] if len(candidates) == 1 else None


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


def stale_hint(stale: Suppression, findings: list[Finding]) -> str | None:
    """The indented lines to print under a stale entry, or None.

    One function because there are two reports — `gate.format_report` for the
    pre-push hook and `python -m rite_ai.gate`, and `rite publish check`,
    which is what a human types. The "moved to" hint was added to the shared
    formatter first and the typed command went on printing the fingerprint
    and stopping; the same split later meant one of them handed out the line
    form while everything else said to write the content form. Neither was a
    hard failure, which is exactly why it survived: the text a reader acts on
    lives here now, and both call sites render whatever it returns.
    """
    moved = moved_to(stale, findings)
    if moved is not None:
        return (
            f"    the same rule now matches at line {moved.line} of that file "
            f"— if it is the same finding, re-point this entry to:\n      "
            f"{moved.content_fingerprint or moved.fingerprint}"
        )
    _, _, last = stale.fingerprint.rpartition(":")
    if last.startswith(CONTENT_PREFIX):
        return (
            "    this entry names matched text that is no longer there. If it "
            "was edited,\n    what replaced it is a new decision — look at it "
            "before suppressing it.\n    If it is gone for good, delete this "
            "line."
        )
    return None


HOW_TO_SUPPRESS = (
    f"To accept one of these, add a line to {DEFAULT_SUPPRESSION_PATH}:\n"
    "  <fingerprint>  # why this one is safe\n"
    "copying the fingerprint printed above. The reason is required, not "
    "optional — an entry without one is a parse error."
)
"""Said wherever findings are listed. A reader who has just been blocked
needs the file's name, the shape of a line, and the fact that the reason is
compulsory; the report used to print a fingerprint and leave all three to be
found in the source."""


def covering_more_than_one(
    findings: list[Finding], suppressions: list[Suppression]
) -> list[tuple[Suppression, int]]:
    """Entries that suppress several findings at once, with how many.

    A content-pinned entry names text, not a position, so a second occurrence
    of the same string under the same rule in the same file is covered by the
    same entry — one reason, both findings. That is usually right (it is the
    same string, and the reason is about the string) and it is the stated
    price of not pinning a line. What must not happen is it happening
    quietly: a decision taken about one occurrence silently growing to cover
    an occurrence nobody looked at. So the count is reported.
    """
    counts: list[tuple[Suppression, int]] = []
    for s in suppressions:
        n = len([f for f in findings if _matches(f, {s.fingerprint})])
        if n > 1:
            counts.append((s, n))
    return counts
