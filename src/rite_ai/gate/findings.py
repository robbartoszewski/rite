"""Shared finding model for the publish gate.

Every source the gate consults — gitleaks (content, history, commit messages
via the pseudo-file relay) and rite's own pattern scanner (project-specific
markers, hardcoded paths, filenames) — normalises into this one dataclass so
suppression, reporting, and exit-code logic only need to handle one shape.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

# "fail" blocks publish. "warn" is surfaced but does not block — currently
# only used for stale suppression entries, never for an actual finding: a
# real leak is always "fail". A future severity-tiered rule set could extend
# this, but nothing in the spec asks for that yet.
Severity = str  # "fail" | "warn"


@dataclass(frozen=True)
class Finding:
    rule_id: str
    description: str
    file: str
    line: int
    commit: str | None
    match_preview: str
    # "gitleaks" | "gitleaks-commit-msg" | "rite-pattern" | "rite-path" | "rite-kb"
    source: str
    severity: Severity = "fail"

    @property
    def fingerprint(self) -> str:
        """`.gitleaksignore`-style identity: <commit-or-'-'>:<file>:<rule>:<line>.

        Matches gitleaks' own fingerprint scheme exactly (verified against a
        live gitleaks JSON report) so a finding sourced from gitleaks and one
        sourced from rite's own scanner look the same to a human editing the
        suppression file.
        """
        commit = self.commit or "-"
        return f"{commit}:{self.file}:{self.rule_id}:{self.line}"


def redact(secret: str, keep: int = 4) -> str:
    """Truncate + mask a matched string for display. Never log the raw secret."""
    if len(secret) <= keep * 2:
        return "*" * len(secret)
    return f"{secret[:keep]}{'*' * (len(secret) - keep * 2)}{secret[-keep:]}"


def rev_range_args(rev_range: str | None) -> list[str]:
    """A revision range as `git log` ARGV, not as one argument.

    A range is a string because that is what gitleaks' `--log-opts`
    takes, and because it is what the gate prints as its heading. But
    rite also runs `git log` itself, and appending the string whole makes
    a multi-token range one argument: `"<sha> --not --remotes"` came back
    as `fatal: ambiguous argument ... unknown revision or path not in the
    working tree`. Found by running the hook rather than by reading it.

    Splitting on whitespace is right for the ranges this package builds —
    shas, `a..b`, and git's own revision flags. It is deliberately not
    `shlex.split`: a range is never quoted, and a `shlex` failure on an
    odd quote would be a crash where a clean `git log` error is better.
    """
    return rev_range.split() if rev_range else []


def iter_commit_messages(
    root: Path, rev_range: str | None = None, timeout: int = 120
) -> list[tuple[str, str]] | str:
    """`(sha, message)` for every commit in `rev_range`, from ONE `git log`.

    Returns an error string rather than raising, so both callers can turn
    it into their own error type.

    There were two copies of this loop — one in `gitleaks_runner` relaying
    messages to gitleaks, one in `pattern_scan` matching rite's own rules
    — and both spawned `git show -s --format=%B` once per commit. On git's
    own repository that is 82,180 subprocesses each, and `rite publish
    check` did not finish in the minutes it was given. One `git log`
    emitting `%x00%H%n%B` does the same job in 0.8 seconds.

    They had not diverged, which is the only reason the defect was one
    defect rather than two different ones; sharing the implementation is
    what keeps that true.

    The NUL is a parse delimiter and must never reach a file gitleaks
    scans — gitleaks treats any file containing one as binary and skips
    it, which `gitleaks_runner`'s docstring records as an earlier defect.
    A commit message cannot contain a NUL (git refuses it); if one
    somehow did, the cost is a message attributed to the wrong commit,
    never a message dropped.
    """
    args = ["git", "log", "--format=%x00%H%n%B", *rev_range_args(rev_range)]
    try:
        proc = subprocess.run(
            args,
            cwd=root,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return f"failed to run 'git log': {e}"
    if proc.returncode != 0:
        return f"'git log' failed: {proc.stderr.strip()[:300]}"

    messages: list[tuple[str, str]] = []
    for record in proc.stdout.split("\x00"):
        if not record.strip():
            continue
        sha, _, body = record.partition("\n")
        sha = sha.strip()
        if sha:
            messages.append((sha, body))
    return messages
