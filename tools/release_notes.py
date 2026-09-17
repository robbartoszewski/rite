#!/usr/bin/env python3
"""Build a GitHub release's body, and verify the published one resolves.

`docs/install-notes.md` tells a reader to download `install.sh` and compare
its digest "against the release notes". That instruction was unfollowable:
the tags existed and no release did, so the thing it points at was not there
at all. This is what the publish procedure runs to create one.

    python3 tools/release_notes.py v0.3.0              # the body, on stdout
    python3 tools/release_notes.py v0.3.0 --verify     # check the published one

**The notes are not written twice.** The body is the version's own section of
`CHANGELOG.md`, read from the tag, plus the verification block from
`tools/release_checksums.py`. Hand-authoring a second copy beside the
changelog is how the two come apart, which is the defect class this repository
has spent a week removing.

**Everything is read from the TAG**, never from the working tree: the digest a
reader computes comes from `raw.githubusercontent.com/.../<tag>/install.sh`,
so that blob is what gets hashed, and the changelog section is the one that
shipped at that tag. This also makes backfilling an old release correct rather
than approximate.

`--verify` is the half that catches a release that looks published and is not:
it fetches the release body back from GitHub and the installer GitHub serves
at that tag, and fails unless the digest in the body is the digest of those
served bytes. Checking the served bytes, not the intent.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_URL = "https://raw.githubusercontent.com/robbartoszewski/rite/{tag}/install.sh"


def _load_checksums():
    """`release_checksums` by path — `tools/` is a directory of scripts, not
    an importable package, and duplicating the hashing here is exactly the
    second copy this tool exists to avoid."""
    path = REPO_ROOT / "tools" / "release_checksums.py"
    spec = importlib.util.spec_from_file_location("release_checksums", path)
    if spec is None or spec.loader is None:  # pragma: no cover - unreachable
        raise ToolError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ToolError(Exception):
    """Something the caller can act on, reported without a traceback."""


def changelog_section(text: str, version: str) -> str:
    """The one release's entry, from `## <version> (date)` to the next `##`.

    The version is matched as a whole heading token, so `0.2.0` never matches
    `0.2.10`'s section — the kind of near-miss that would publish the wrong
    release's notes under a correct-looking tag.
    """
    pattern = re.compile(
        r"^##[ \t]+" + re.escape(version) + r"(?:[ \t].*)?$", re.MULTILINE
    )
    match = pattern.search(text)
    if match is None:
        raise ToolError(
            f"CHANGELOG.md has no `## {version}` section. Add the section for "
            "this release before publishing it — the release notes are that "
            "section, not a second copy of it."
        )
    rest = text[match.end() :]
    next_heading = re.search(r"^##[ \t]", rest, re.MULTILINE)
    body = rest[: next_heading.start()] if next_heading else rest
    body = body.strip("\n")
    if not body.strip():
        raise ToolError(f"CHANGELOG.md's `## {version}` section is empty")
    return body


def version_of(tag: str) -> str:
    """`v0.3.0` names version `0.3.0`; anything else is used as written."""
    return tag[1:] if tag.startswith("v") and tag[1:2].isdigit() else tag


def body_for(tag: str) -> str:
    checksums = _load_checksums()
    try:
        changelog = checksums.blob(tag, "CHANGELOG.md").decode("utf-8")
    except checksums.ToolError as e:
        raise ToolError(f"{e} — is {tag} pushed?") from None
    section = changelog_section(changelog, version_of(tag))
    return section + "\n\n" + checksums.block(tag, f"tag {tag}")


def published_body(tag: str) -> str:
    """What GitHub actually serves as the release body."""
    proc = subprocess.run(
        ["gh", "release", "view", tag, "--json", "body", "--jq", ".body"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise ToolError(
            f"no published release for {tag}: "
            + (proc.stderr.strip() or "gh reported nothing")
        )
    return proc.stdout


def served_installer(tag: str) -> bytes:
    """The installer bytes a reader downloads — fetched, not assumed."""
    url = RAW_URL.format(tag=tag)
    try:
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310
            return response.read()
    except urllib.error.URLError as e:
        raise ToolError(f"cannot fetch {url}: {e}") from None


def verify(tag: str) -> str:
    """Fail unless the published body carries the digest of the served file."""
    checksums = _load_checksums()
    body = published_body(tag)
    served = checksums.digest(served_installer(tag))
    if served not in body:
        published = re.findall(r"\b[0-9a-f]{64}\b", body)
        raise ToolError(
            f"{tag}: the published release notes do not carry the digest of "
            f"the installer GitHub serves.\n"
            f"  served ({RAW_URL.format(tag=tag)}): {served}\n"
            f"  in the notes: {', '.join(published) or 'no digest at all'}"
        )
    commit = checksums.commit_of(tag)
    if commit not in body:
        raise ToolError(
            f"{tag}: the published notes carry the right digest but not the "
            f"commit {commit}, which is the reference a reader is told to use "
            "when a tag is not enough."
        )
    return (
        f"ok: {tag} — the published notes carry {served} and commit {commit}, "
        "and that digest is the one GitHub serves for install.sh"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="the release tag, e.g. v0.3.0")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="check the PUBLISHED release against the file GitHub serves",
    )
    args = parser.parse_args()

    try:
        if args.verify:
            print(verify(args.tag))
        else:
            sys.stdout.write(body_for(args.tag))
    except ToolError as e:
        print(f"release_notes: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
