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

import hashlib
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
    """`git ls-files -z` from the repo root — respects .gitignore by
    construction (untracked and ignored files never appear), which is exactly
    the "committed content" boundary the gate needs.

    ⚠ `-z` IS THE WHOLE POINT OF THIS FUNCTION AND IT WAS MISSING. Without it
    git quotes and octal-escapes any path outside ASCII: `café.py` comes back
    as the seven characters `"caf\303\251.py"`, quotes included. That string
    was used as a path, the open failed, `_read_text_safe` returned None, and
    the caller skipped it — silently.

    Measured, same repository, same secret, one byte different in the name:

        café.py -> exit 0, "clean", "scanned 1 tracked file(s)"
        cafe.py -> exit 2, finding: hardcoded macOS home directory path

    A secrets gate reporting "clean" about a file it could not open is the
    worst failure this tool has, because the count beside it asserts the scan
    happened. `-z` makes the name exact; `unreadable` below makes a skip
    visible. Neither alone is enough.
    """
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return ScanError(f"failed to run 'git ls-files': {e}")
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        return ScanError(f"'git ls-files' failed: {stderr}")
    # Bytes, split on NUL, decoded per entry: a path that is not valid UTF-8
    # still names a real file, and `surrogateescape` round-trips it back to
    # the same bytes when it is opened.
    return [
        entry.decode("utf-8", errors="surrogateescape")
        for entry in proc.stdout.split(b"\0")
        if entry
    ]


_BYTES_TAG = " [bytes:"


def display_path(rel: str) -> str:
    """A repo-relative path safe to PRINT and to use as identity.

    THE HALF THE `-z` FIX MISSED, found in review because my own corpus —
    `café.py`, `naïve/ünï.py` — was all valid UTF-8 and therefore never
    produced a surrogate at all. It proved the NUL splitting and never
    touched the decoder.

    `surrogateescape` is what lets a non-UTF-8 name (latin-1, shift-jis, a
    name copied off another filesystem — ext4 permits arbitrary bytes, and
    APFS does not, so this is Linux-only and invisible on a macOS run) round
    trip back to the bytes that open the file. That is correct for OPENING
    and unusable for everything after it: `f"{rel}".encode()` raises
    `UnicodeEncodeError`, and so does printing to any UTF-8 stream — which is
    what CI captures and what a redirected run writes. Left alone, the `-z`
    fix would have turned "silently skips the file" into "traceback instead
    of a finding": louder, and still not a reported secret.

    A plain `errors="replace"` display is not enough on its own, and this is
    the trap worth naming: `caf\\xe9.py` and `caf\\xe8.py` both render to
    `caf?.py`, so a fingerprint built from the display form would collapse two
    different files into one suppression — a suppression silently covering a
    file nobody approved it for. So an unprintable name carries a short
    digest of its own BYTES, which keeps distinct files distinct while
    staying printable.
    """
    raw = rel.encode("utf-8", "surrogateescape")
    try:
        rel.encode("utf-8")
    except UnicodeEncodeError:
        shown = raw.decode("utf-8", "replace")
        return f"{shown}{_BYTES_TAG}{hashlib.sha256(raw).hexdigest()[:8]}]"
    if _BYTES_TAG in rel:
        # FORGEABLE OTHERWISE. The suffix is only added to names that cannot
        # be encoded, so a file literally NAMED `caf?.py [bytes:34e37bc1]` is
        # valid UTF-8, passes through untouched, and renders byte-identical
        # to the undecodable `caf\xe9.py`. Fingerprints embed this string, so
        # one suppression would cover both — and the digest is computable
        # rather than guessable, so the decoy can be named deliberately.
        #
        # Any name containing the marker gets tagged too, which makes the
        # tag present on both and the digests distinct.
        return f"{rel}{_BYTES_TAG}{hashlib.sha256(raw).hexdigest()[:8]}]"
    return rel


def blame_keys(rel: str) -> set[str]:
    """Every spelling a finding might carry for one path.

    `Finding.file` is used for THREE things with conflicting needs: printing
    (must be encodable), suppression fingerprints (must be stable and
    distinct), and blame — "did this push add it?" — which compares against
    `files_touched_by`. Three consumers, one string, and the fix for one
    broke another: routing findings through `display_path` made them
    printable and stopped them matching the raw path from `git log`, so a
    secret THIS PUSH ADDED was demoted to "already in the repository". That
    is the exact failure the surrounding work exists to close, reintroduced
    by the fix for it.

    The three spellings that legitimately exist for one file:
      - the raw name, `surrogateescape`-decoded, which is what `git log` gives;
      - `display_path`'s form, which rite's own scanner attaches to findings;
      - the `errors="replace"` form, which is what GITLEAKS reports, because
        it decodes its own JSON that way and the bytes are gone by then.

    Blame matches on the union, so a finding from either scanner lines up
    with the same file. Normalising one side only would leave gitleaks —
    the primary detector — still mismatching.
    """
    raw = rel.encode("utf-8", "surrogateescape")
    return {rel, display_path(rel), raw.decode("utf-8", "replace")}


def _read_text_safe(path: Path) -> str | None:
    """Read a file as text, or None if it's binary/unreadable. Never raises.

    ⚠ THIS DOCSTRING USED TO CITE A CHECK THAT DOES NOT EXIST — "see the
    caller for the zero-files sanity check that catches wholesale scan
    breakage instead". There is no such check in `gate.py`, and naming an
    imaginary guarantee is worse than naming none: it stopped the next reader
    looking, and it is the reason a silently-skipped file went unnoticed.
    The caller now passes `unreadable` and the report prints what was
    skipped. Skipping is safe only because the skip is visible."""
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
    root: Path,
    files: list[str],
    patterns: list[ScanPattern],
    source: str,
    unreadable: list[str] | None = None,
) -> list[Finding]:
    """Match each pattern against the content of every tracked file.

    `unreadable`, when given, collects the paths that were listed and could
    NOT be read — binary, vanished, permission-denied, or a name this process
    cannot open. Pass it whenever the caller reports a count to a human.

    WHY IT EXISTS. This loop skipped an unreadable file with a bare
    `continue`, and `_read_text_safe`'s docstring justified that by citing a
    "zero-files sanity check" in the caller. **There is no such check.** So a
    file the gate could not open was indistinguishable from one it read and
    found clean, while `files_scanned` counted it as scanned. A skip that
    cannot be seen is the same defect as a check whose exit code nobody
    reads — absence of a complaint standing in for evidence.
    """
    compiled = [(p, _compile(p)) for p in patterns]
    compiled = [(p, rx) for p, rx in compiled if rx is not None]
    findings: list[Finding] = []
    for rel_path in files:
        text = _read_text_safe(root / rel_path)
        if text is None:
            if unreadable is not None:
                # display form: this list is printed
                unreadable.append(display_path(rel_path))
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
                            file=display_path(rel_path),
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
                        file=display_path(rel_path),
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
    already in the repository before it.

    ⚠ `-z` FOR THE SAME REASON AS `list_tracked_files`, AND IT IS WORSE HERE.
    This set decides whether a finding is blamed on this push or excused as
    pre-existing. An escaped, quoted name never matches the path a finding
    carries, so a secret THIS PUSH ADDED was demoted to "already in the
    repository, NOT from this push" — the gate does not merely miss it, it
    reassures the person about it.
    """
    args = ["git", "log", "--name-only", "-z", "--format=", *rev_range_args(rev_range)]
    try:
        proc = subprocess.run(args, cwd=root, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return ScanError(f"'git log --name-only' failed: {e}")
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()[:200]
        return ScanError(f"'git log --name-only' failed: {stderr}")
    touched: set[str] = set()
    for entry in proc.stdout.split(b"\0"):
        if entry.strip():
            touched |= blame_keys(entry.decode("utf-8", errors="surrogateescape"))
    return touched


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
                            f"Line from {display_path(kb_path)} also appears "
                            f"verbatim in {display_path(target_file)}:"
                            f"{target_line} — kb/ content must "
                            "not be copied into published files"
                        ),
                        file=display_path(kb_path),
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
