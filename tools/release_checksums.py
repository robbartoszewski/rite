#!/usr/bin/env python3
"""Emit the checksum block for a release's notes, and verify it.

README install option 2 tells a reader to run `shasum -a 256 install.sh` and
compare the result "against the v0.1.0 release notes". That instruction is
only worth anything if the release notes actually carry the digest, and carry
the digest of the bytes GitHub will serve from the tag. Until now both halves
were a step in `docs/private/PUBLISH_RUNBOOK.md` that a human ran by hand and pasted
by hand, with nothing checking either.

    python3 tools/release_checksums.py            # the block to paste
    python3 tools/release_checksums.py --check <digest>

**It hashes the file as it is at the RELEASE TAG**, not the working copy and
not HEAD — `git show <tag>:install.sh`, with the tag read out of
`install.sh`'s own `VERSION=` default so the two cannot disagree about which
release is being published. The digest a reader computes comes from
`raw.githubusercontent.com/.../<tag>/install.sh`, which is that blob.

Hashing HEAD instead was the first version's bug and it was wrong in both
directions: after any post-tag commit it published a digest no reader would
ever compute, with exit 0, and then reported the correctly published digest
as a mismatch. Before the tag exists it falls back to HEAD and says so.

Refuses to publish when the working copy differs from that blob, rather than
guessing which one you meant.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# What a reader downloads and checksums. Singular, deliberately: an earlier
# version made this a tuple and looped, which read as generality and was wrong
# the moment it was used — one `--check` digest was compared against every
# entry, so verifying a correct `install.sh` digest against a two-artifact
# tuple reported MISMATCH. Nothing publishes a second artifact today (the
# wheel is not published at all), so the honest shape is one.
RELEASE_ARTIFACT = "install.sh"

# The command README option 2 tells the reader to run. Kept here so a test can
# assert the three places agree; see tests/test_release_checksums.py.
READER_COMMAND = "shasum -a 256 install.sh"


class ToolError(Exception):
    """Something the caller can act on, reported without a traceback."""


def _git(*args: str) -> bytes:
    proc = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True)
    if proc.returncode != 0:
        # git's own stderr, not a CalledProcessError with the reason thrown
        # away. "not a git repository" is the whole answer and the first
        # version deleted it.
        raise ToolError(
            f"git {' '.join(args)} failed: "
            + (proc.stderr.decode("utf-8", "replace").strip() or "no output")
        )
    return proc.stdout


def release_ref() -> str:
    """The tag `install.sh` itself installs, read out of `install.sh`.

    Not a second copy of the version string: the installer's `VERSION=`
    default IS the release, so reading it is what keeps this tool and the
    thing it describes from disagreeing about which release is being
    published.
    """
    # Read from disk rather than from a ref, necessarily: this is the value
    # that NAMES the ref everything else is read from. Wrapped, because an
    # absent file raised FileNotFoundError straight past the ToolError
    # handler — the class this tool's error handling was fixed for one round
    # earlier, surviving in the one place that reads the working tree.
    try:
        text = (REPO_ROOT / "install.sh").read_text()
    except OSError as e:
        raise ToolError(f"cannot read install.sh: {e}") from None
    match = re.search(r'^VERSION="\$\{RITE_VERSION:-(\S+)\}"', text, re.M)
    if not match:
        raise ToolError(
            "install.sh does not declare a non-empty default "
            'VERSION="${RITE_VERSION:-<tag>}" — this tool reads the release '
            "tag from there so the two cannot disagree about which release "
            "is being published"
        )
    return match.group(1)


def source_ref() -> tuple[str, str]:
    """What to hash, and how to describe it.

    The tag if it exists, because that is what a reader downloads. HEAD only
    when the tag has not been created yet — with a warning, since a digest of
    HEAD is not the digest of the release unless the two are the same commit.
    An earlier version always hashed HEAD, which published the wrong digest
    with exit 0 after any post-tag commit, and then reported the CORRECT
    published digest as a mismatch.
    """
    tag = release_ref()
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}"],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    if proc.returncode == 0:
        return tag, f"tag {tag}"
    return "HEAD", f"HEAD (tag {tag} does not exist yet)"


def blob(ref: str, relative: str) -> bytes:
    """The file as it is at `ref` — the bytes GitHub serves raw from it."""
    return _git("show", f"{ref}:{relative}")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def commit_of(ref: str) -> str:
    return _git("rev-parse", f"{ref}^{{commit}}").decode().strip()


def working_copy_differs(ref: str, relative: str) -> bool:
    return (REPO_ROOT / relative).read_bytes() != blob(ref, relative)


_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def normalise_digest(raw: str) -> str:
    """Accept what a human actually pastes.

    A bare digest, `sha256:<digest>`, or the whole `shasum` output line. The
    first version rejected the latter two — the two most likely pastes — and,
    worse, accepted an EMPTY string by falling through to the publish path
    and exiting 0, so `--check "$UNSET_VAR"` printed a checksum block and
    reported success.
    """
    text = raw.strip().lower()
    if text.startswith("sha256:"):
        text = text[len("sha256:") :].strip()
    text = text.split()[0] if text.split() else ""
    if not _DIGEST_RE.match(text):
        raise ToolError(
            f"not a sha256 digest: {raw!r}. Expected 64 hex characters, "
            "optionally as `sha256:<digest>` or a whole `shasum` line."
        )
    return text


def block(ref: str, described: str) -> str:
    """The paste-ready release-notes block.

    Carries the commit SHA as well as the digest, because the digest covers
    `install.sh` and nothing else — rite itself is fetched from the tag at
    install time, and a tag is a movable pointer. A reader who wants a
    reference that cannot move needs the SHA, and it has to be published or
    the advice to use one is unusable.
    """
    lines = [
        "## Verifying this release",
        "",
        f"Commit: `{commit_of(ref)}`",
        "",
        f"Hashed from {described}. A tag can be repointed; a commit SHA",
        "cannot — `git checkout <the SHA above>` if that matters to you.",
        "",
        "Checksum of the installer (README install option 2):",
        "",
        "```",
        f"{READER_COMMAND}",
        "```",
        "",
    ]
    lines.append(f"    {digest(blob(ref, RELEASE_ARTIFACT))}  {RELEASE_ARTIFACT}")
    lines += [
        "",
        "This digest covers the installer only. `install.sh` then fetches rite",
        "itself from the tag, so verifying it does not verify the payload —",
        "use the commit SHA, or read the source (install option 3).",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", metavar="DIGEST", help="verify a published digest")
    args = parser.parse_args()

    try:
        ref, described = source_ref()

        if args.check is not None:
            # No dirty-tree guard here: `--check` reads the blob at the ref
            # and never the working copy, so an unrelated edit has no bearing
            # on it. An earlier version refused anyway, which made it
            # impossible to verify the digest of the LAST release while
            # editing for the next one.
            wanted = normalise_digest(args.check)
            actual = digest(blob(ref, RELEASE_ARTIFACT))
            if wanted != actual:
                print(
                    f"MISMATCH against {described}\n  published: {wanted}\n"
                    f"  {RELEASE_ARTIFACT}: {actual}",
                    file=sys.stderr,
                )
                return 1
            print(f"ok: {wanted}  {RELEASE_ARTIFACT}  ({described})")
            return 0

        # The guard applies to the HEAD FALLBACK ONLY, and that is the whole
        # of its reasoning. Once the tag exists, `block()` hashes the TAG's
        # blob, so the digest is right whatever the working copy says —
        # refusing there was "a guard applied where its own reason does not
        # hold" reproduced inside the fix that moved hashing to the tag. It
        # refused on a clean, fully committed tree whose only sin was a commit
        # made after the release, and told the maintainer to "tag first",
        # which means `git tag -f`: the exact move the README calls the
        # security hazard.
        if ref == "HEAD" and working_copy_differs(ref, RELEASE_ARTIFACT):
            print(
                f"refusing: no {release_ref()} tag exists yet, so this would "
                f"publish a digest of HEAD — and your {RELEASE_ARTIFACT} "
                "differs from HEAD too, so it would not be the digest of "
                "anything committed. Commit, then tag, then run this again.",
                file=sys.stderr,
            )
            return 1

        print(block(ref, described), end="")
        return 0
    except ToolError as e:
        print(f"release_checksums: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
