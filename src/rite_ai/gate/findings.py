"""Shared finding model for the publish gate.

Every source the gate consults — gitleaks (content, history, commit messages
via the pseudo-file relay) and rite's own pattern scanner (project-specific
markers, hardcoded paths, filenames) — normalises into this one dataclass so
suppression, reporting, and exit-code logic only need to handle one shape.
"""

from __future__ import annotations

import hashlib
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
    digest: str = ""
    """`content_digest` of the matched text, when the source has it. Never the
    text itself: a Finding is printed, logged and passed around, and a real
    secret must not ride along in it."""

    @property
    def content_fingerprint(self) -> str | None:
        """Identity by WHAT was found, not where: the same scheme with
        `sha256-<digest>` in place of the line number.

        A line number is a proxy for a finding, and the proxy drifts. Add a
        line anywhere above a suppressed one and its `commit:file:rule:line`
        entry stops matching: the finding it covered starts blocking publish
        and the entry is reported stale, while nothing about the suppressed
        code changed. Measured on this repository — three comment lines in
        `gate.py` moved a docstring from 253 to 256 and broke its suppression
        mid-push.

        Pinning the matched text instead cannot drift, and is narrower where
        it counts: change the secret and the suppression stops applying, which
        is exactly when someone should look again. It is wider in one way,
        recorded here as the price: two identical matches of one rule in one
        file share an entry, so one reason covers both. They are the same
        string with the same reason, but the count is no longer visible.

        `None` when the source records no matched text, in which case the line
        form is the only identity there is.
        """
        if not self.digest:
            return None
        commit = self.commit or "-"
        return f"{commit}:{self.file}:{self.rule_id}:sha256-{self.digest}"

    @property
    def fingerprint(self) -> str:
        """`.gitleaksignore`-style identity: <commit-or-'-'>:<file>:<rule>:<line>.

        Matches gitleaks' own fingerprint scheme exactly (verified against a
        live gitleaks JSON report), so an entry copied out of a gitleaks
        report suppresses the same finding here. Reports print
        `content_fingerprint` in preference to this one wherever there is
        one; this stays the identity for a finding with no matched text to
        pin, and the form every already-written suppression file is full of.
        """
        commit = self.commit or "-"
        return f"{commit}:{self.file}:{self.rule_id}:{self.line}"


def content_digest(secret: str) -> str:
    """A stable id for matched text — 32 hex chars of its SHA-256.

    Truncated at all because this is read and copied by hand out of a report
    into a suppression file. Truncated at 128 bits rather than fewer: a
    shorter digest invites GRINDING. Rules like `generic-api-key` accept any
    high-entropy body, so a contributor free to choose a token's text could
    search for one whose digest matches an entry already in the file and get
    it exempted by a diff that touches nothing but a test fixture, with the
    gate green. 64 bits puts that within reach of rented hardware; 128 does
    not. Accidental collisions were never the risk worth sizing for.

    Hashed rather than stored verbatim, but understand what the hash does and
    does not hide. The pre-image is a MATCHED STRING THE PROJECT DECIDED TO
    KEEP — suppression is for false positives, so while an entry is live its
    text is sitting in the scanned tree, public to anyone who can read the
    repository this file is committed to. Nothing here is protecting a
    secret; the digest is a pointer to text that is already in the open, and
    hashing keeps the file from quoting matched strings back at readers and
    from tripping the gate's own rules on itself.

    The corollary is the one thing to be careful about: if you ever scrub a
    matched string out of the tree, DELETE its entry in the same change. An
    entry outliving its text is a commitment to a string that is no longer
    public — and for the low-entropy shapes these rules catch (a home path,
    a username) a digest is a confirmable guess, not a one-way function. The
    gate reports exactly that entry as stale, which is the prompt to do it.
    """
    return hashlib.sha256(secret.encode("utf-8", "surrogatepass")).hexdigest()[:32]


def redact(secret: str, keep: int = 4) -> str:
    """Truncate + mask a matched string for display. Never log the raw secret."""
    if len(secret) <= keep * 2:
        return "*" * len(secret)
    return f"{secret[:keep]}{'*' * (len(secret) - keep * 2)}{secret[-keep:]}"


COMMIT_MESSAGE_FILE = "<commit message>"
"""The `file` both commit-message scanners report, since a commit message is
not a file. Named, because `_split_off_pre_existing` has to recognise it:
matching it against a touched-FILE set can only ever fail."""

PUBLISHABLE_REFS = ("--branches", "--tags", "--remotes")
"""Every ref a push could carry — NOT `--all`.

`--all` adds `refs/stash`, which is local by definition: a stash cannot be
pushed. Measured — a `git stash push -m "wip <token>"` failed the gate over
text in no commit and no branch, and the block could not be cleared, because
a suppression fingerprint embeds the stash commit's sha and that sha changes
every time the stash is rebuilt. A block nobody can clear is a gate people
turn off, and one stash stack is shared by every worktree of a repository.

`refs/notes` go too, and the honest reason is narrower: notes are not
carried by a branch push and are not pushed without asking. They CAN be
pushed deliberately (`git push origin refs/notes/*`), so this is a scope
limit rather than an impossibility — a secret written into a git note and
then explicitly pushed is out of scope for this gate.
"""


def publishable_scope(root: Path) -> list[str]:
    """`PUBLISHABLE_REFS`, plus `HEAD` when there is one.

    `HEAD` is not redundant with `--branches`: a detached HEAD is on no
    branch, and a worktree checked out at a sha is the ordinary state of
    several of this project's own. Measured — a repo whose only commits are
    reachable from a detached HEAD reports 0 commits to
    `--branches --tags --remotes`, so the scan read nothing and said clean
    over a planted token. That is a NARROWING introduced by the commit whose
    whole purpose was to widen coverage, which is the way this keeps going
    wrong: every change to a scan's scope has to be measured in both
    directions.

    Conditional because an unborn HEAD is a hard error, not an empty set:
    `git log --branches HEAD` in a repository with no commits exits 128 with
    `ambiguous argument 'HEAD'`, and that would fail the gate on a freshly
    initialised project.
    """
    scope = list(PUBLISHABLE_REFS)
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return scope
    if proc.returncode == 0 and proc.stdout.strip():
        scope.append("HEAD")
    return scope


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
    # Every publishable ref when no range is given, because that is what the
    # OTHER half of the history scan covers. 🔴 SCRUM-63: gitleaks' own
    # `detect` walks every ref (measured on 8.30.1 — a repo with one commit on
    # HEAD and one on an unmerged branch reports "2 commits scanned"), while
    # this ran bare `git log` and saw only HEAD's ancestry. So the audit — the
    # audit that means "safe for the whole history to become public" — scanned
    # unmerged branches for secrets in file CONTENT and not for secrets in
    # their commit MESSAGES. Measured: a GitHub PAT in the commit message of a
    # branch not reachable from HEAD, reported clean.
    #
    # Only when no range is given. A range is the pre-push hook naming exactly
    # what it is about to publish, and widening that would scan refs the push
    # does not touch — the thing `_split_off_pre_existing` exists to stop.
    #
    # On an empty repository this is also the kinder answer: bare `git log`
    # fails with "does not have any commits yet", a ref selector exits 0 with
    # nothing.
    scope = rev_range_args(rev_range) if rev_range else publishable_scope(root)
    args = ["git", "log", "--format=%x00%H%n%B", *scope]
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
