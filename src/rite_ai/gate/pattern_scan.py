"""Pure-Python scanner for project-specific and built-in patterns.

This is deliberately NOT a secret detector — gitleaks owns that (see
`gitleaks_runner.py`). This module covers the ground gitleaks' own rule
engine cannot: matching a user-declared or built-in pattern against a file's
*name* (not just its content), and — because gitleaks does not scan commit
message text at all (verified empirically against gitleaks 8.30.1: a secret
placed only in a commit message produced zero findings from `gitleaks
detect`) — matching those same patterns against commit message text too.

Scope is git-tracked files only (`git ls-files`), matching SPEC §11.2's
"all committed content" — this is a publish gate, not a pre-commit linter,
so untracked/staged-only content is out of scope by design.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rite_ai.config.models import ScanPattern
from rite_ai.gate.findings import (
    Finding,
    content_digest,
    iter_commit_messages,
    redact,
    rev_range_args,
)


@dataclass
class ScanError:
    message: str


def list_tracked_files(root: Path) -> list[str] | ScanError:
    """`git ls-files` from the repo root — respects .gitignore by construction
    (untracked and ignored files never appear), which is exactly the
    "committed content" boundary the gate needs."""
    try:
        proc = subprocess.run(
            ["git", "ls-files"],
            cwd=root,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return ScanError(f"failed to run 'git ls-files': {e}")
    if proc.returncode != 0:
        return ScanError(f"'git ls-files' failed: {proc.stderr.strip()}")
    return [line for line in proc.stdout.splitlines() if line]


def _read_text_safe(path: Path) -> str | None:
    """Read a file as text, or None if it's binary/unreadable. Never raises —
    an unreadable file is skipped, not a scan failure (see the caller for the
    zero-files sanity check that catches wholesale scan breakage instead)."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw:
        return None  # binary heuristic — matches gitleaks' own behaviour
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _compile(pattern: ScanPattern) -> re.Pattern[str] | None:
    try:
        return re.compile(pattern.pattern)
    except re.error:
        return None


def scan_content(
    root: Path, files: list[str], patterns: list[ScanPattern], source: str
) -> list[Finding]:
    """Match each pattern against the content of every tracked file."""
    compiled = [(p, _compile(p)) for p in patterns]
    compiled = [(p, rx) for p, rx in compiled if rx is not None]
    findings: list[Finding] = []
    for rel_path in files:
        text = _read_text_safe(root / rel_path)
        if text is None:
            continue
        lines = text.splitlines()
        for lineno, line in enumerate(lines, start=1):
            for pattern, rx in compiled:
                m = rx.search(line)
                if m:
                    findings.append(
                        Finding(
                            rule_id=_rule_id(pattern),
                            description=pattern.description or pattern.pattern,
                            file=rel_path,
                            line=lineno,
                            commit=None,
                            match_preview=redact(m.group(0)),
                            digest=content_digest(m.group(0)) if m.group(0) else "",
                            source=source,
                        )
                    )
    return findings


def scan_filenames(
    files: list[str], patterns: list[ScanPattern], source: str
) -> list[Finding]:
    """Match each pattern against the tracked file's own path string —
    catches a file *named* after a customer or a project even if its
    content never trips a content rule."""
    compiled = [(p, _compile(p)) for p in patterns]
    compiled = [(p, rx) for p, rx in compiled if rx is not None]
    findings: list[Finding] = []
    for rel_path in files:
        for pattern, rx in compiled:
            m = rx.search(rel_path)
            if m:
                findings.append(
                    Finding(
                        rule_id=_rule_id(pattern) + "-filename",
                        description=(
                            f"{pattern.description or pattern.pattern} (in file name)"
                        ),
                        file=rel_path,
                        line=0,
                        commit=None,
                        match_preview=redact(m.group(0)),
                        digest=content_digest(m.group(0)) if m.group(0) else "",
                        source=source,
                    )
                )
    return findings


def files_touched_by(root: Path, rev_range: str) -> set[str] | ScanError:
    """Repo-relative paths the commits in `rev_range` add or change.

    Used to tell a finding this push is responsible for from one that was
    already in the repository before it."""
    args = ["git", "log", "--name-only", "--format=", *rev_range_args(rev_range)]
    try:
        proc = subprocess.run(
            args, cwd=root, capture_output=True, text=True, errors="replace", timeout=30
        )
    except (OSError, subprocess.SubprocessError) as e:
        return ScanError(f"'git log --name-only' failed: {e}")
    if proc.returncode != 0:
        return ScanError(f"'git log --name-only' failed: {proc.stderr.strip()[:200]}")
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def scan_commit_messages(
    root: Path, patterns: list[ScanPattern], source: str, rev_range: str | None = None
) -> list[Finding] | ScanError:
    """Match each pattern against every commit message (optionally scoped to
    a `git log`-style `rev_range`, for pre-push speed).

    Line numbers are relative to each commit's own message text (`git show
    -s --format=%B <sha>`), not any pseudo-file offset — that keeps this
    scanner's findings independently correct regardless of how
    `gitleaks_runner` chooses to relay messages through gitleaks for the
    generic-secret pass.
    """
    compiled = [(p, _compile(p)) for p in patterns]
    compiled = [(p, rx) for p, rx in compiled if rx is not None]
    if not compiled:
        return []
    messages = iter_commit_messages(root, rev_range)
    if isinstance(messages, str):
        return ScanError(messages)

    findings: list[Finding] = []
    for sha, body in messages:
        for lineno, line in enumerate(body.splitlines(), start=1):
            for pattern, rx in compiled:
                m = rx.search(line)
                if m:
                    findings.append(
                        Finding(
                            rule_id=_rule_id(pattern),
                            description=pattern.description or pattern.pattern,
                            file="<commit message>",
                            line=lineno,
                            commit=sha,
                            match_preview=redact(m.group(0)),
                            digest=content_digest(m.group(0)) if m.group(0) else "",
                            source=source,
                        )
                    )
    return findings


# Minimum verbatim line length considered for the kb/ cross-reference check —
# below this, matches are overwhelmingly markdown syntax, punctuation, or
# common phrasing rather than a genuine copy-paste leak. This is a heuristic,
# not a proof: it will miss a leak split across lines or reworded, and it can
# still fire on a long boilerplate line two files legitimately share. Stated
# here rather than silently, per the instrument-honesty rule this workspace's
# review culture insists on — a scanner that never says what it can't see is
# worse than one that does.
_KB_MIN_LINE_LEN = 40


def scan_kb_cross_reference(root: Path, files: list[str]) -> list[Finding]:
    """SPEC §8.7/§11.2: kb/ is scanned hardest, specifically for content that
    also appears outside kb/ — the usual leak path is copying a snippet from
    a reference into source.

    Substring containment, not exact-line equality: a copy-paste leak is
    routinely wrapped in a comment marker (`# `, `// `, `-- `) or otherwise
    lightly reformatted at the copy site, so requiring the two lines to be
    byte-identical misses the realistic case. Checked both directions (kb
    line contained in the outside line, or vice versa) since either file
    could be the one carrying the extra wrapping."""
    kb_files = [f for f in files if f.startswith(".rite/kb/")]
    other_files = [f for f in files if not f.startswith(".rite/kb/")]
    if not kb_files or not other_files:
        return []

    outside_lines: list[tuple[str, str, int]] = []  # (stripped_line, file, lineno)
    for rel_path in other_files:
        text = _read_text_safe(root / rel_path)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if len(stripped) < _KB_MIN_LINE_LEN:
                continue
            outside_lines.append((stripped, rel_path, lineno))

    findings: list[Finding] = []
    for kb_path in kb_files:
        text = _read_text_safe(root / kb_path)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if len(stripped) < _KB_MIN_LINE_LEN:
                continue
            hit = next(
                (
                    (target_file, target_line)
                    for outside_stripped, target_file, target_line in outside_lines
                    if stripped in outside_stripped or outside_stripped in stripped
                ),
                None,
            )
            if hit:
                target_file, target_line = hit
                findings.append(
                    Finding(
                        rule_id="kb-cross-reference",
                        description=(
                            f"Line from {kb_path} also appears verbatim in "
                            f"{target_file}:{target_line} — kb/ content must "
                            "not be copied into published files"
                        ),
                        file=kb_path,
                        line=lineno,
                        commit=None,
                        match_preview=redact(stripped, keep=8),
                        # The WHERE-IT-WENT is part of what was found here.
                        # For every other rule the finding is a string in a
                        # file; this one is a pair — a kb line and the
                        # published file it turned up in — and only the kb
                        # half is in `file`. Digesting the line alone would
                        # let "this line is also in a test fixture, which is
                        # fine" go on suppressing the same line appearing in
                        # README.md, which is the leak the rule exists for.
                        digest=content_digest(f"{stripped}\n\x00{target_file}")
                        if stripped
                        else "",
                        source="rite-kb",
                    )
                )
    return findings


def _rule_id(pattern: ScanPattern) -> str:
    base = pattern.description or pattern.pattern
    slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    return f"rite-{slug[:40]}" if slug else "rite-pattern"
