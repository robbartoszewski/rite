"""Which release a tag-relative guard should judge this tree against
(SCRUM-68).

🔴 **A release tag at HEAD turned CI red.** Two guards ask what the last
release SHIPPED and compare it against a generated table
(`update.template_history.RELEASED`, `update.section_history.RELEASES`). Those
tables are produced by `tools/template_history.py` and
`tools/section_history.py`, which read git TAGS — so at the moment the tag is
created the table cannot possibly carry it yet, and the guard fails on a
release that has done nothing wrong. The regeneration commit that fixes it
comes after, which means CI is red in between, on main, at the one moment
somebody is cutting a release.

⚠ **And a `pytest.skip` is NOT the fix, which is why this module exists.** A
test that skips in every job passes NOWHERE, and
`tools/every_test_passes_somewhere.py` turns that into a red check —
`.github/workflows/ci.yml` says it in its own comment: *"a check that skips in
CI reports the same green as one that passed."* Skipping would move the
redness, not remove it. That is exactly why the ticket asks for a **declared
"nothing to check" state**: the guard PASSES, having said what it did not
check and why.

**So:** the newest tag at HEAD is declared, and the guard judges the newest
tag that is NOT at HEAD instead. Coverage continues against a real release,
and a forgotten regeneration goes red on the very next commit — which is the
next push.

🔴 **Ordered by VERSION, never by string.** `sorted(["v0.7.0a9", "v0.7.0"])`
ends in `v0.7.0a9`, so the moment `v0.7.0` is cut a string-ordered guard goes
on judging the last ALPHA and stops checking the release — silently, which is
worse than the redness this module is about.
`test_update_refresh`'s section-history half already said so ("Not string
order: `v0.10.0` sorts before `v0.4.0` that way, and the answer would quietly
become some older release"); its template half still sorted strings. One
module now, so the two cannot disagree again.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


def by_version(tags: list[str]) -> list[str]:
    """`tags` oldest first, by VERSION. Unparseable tags are dropped rather
    than ordered arbitrarily: a tag that is not a version says nothing about
    which release is newest."""
    from packaging.version import InvalidVersion, Version

    keyed = []
    for tag in tags:
        try:
            keyed.append((Version(tag.lstrip("v")), tag))
        except InvalidVersion:
            continue
    return [tag for _, tag in sorted(keyed, key=lambda pair: pair[0])]


def release_tags(repo: Path) -> list[str]:
    """Every `v*` tag in `repo`, oldest first by version."""
    return by_version([t for t in _git(repo, "tag", "-l", "v*").split() if t])


def tags_at_head(repo: Path) -> set[str]:
    """Every tag pointing at HEAD. ⚠ `--points-at HEAD`, not `describe`:
    `describe` answers with the nearest tag whether or not it is AT the
    commit, which is the opposite of the question."""
    return {t for t in _git(repo, "tag", "--points-at", "HEAD").split() if t}


@dataclass(frozen=True)
class Judgement:
    """Which release to judge this tree against, and what was not judged."""

    tag: str | None = None
    declared: str = ""
    """What this run deliberately did NOT check, and why. Non-empty is the
    "nothing to check" state the guard reports rather than failing on."""
    nothing: str = ""
    """Why there is nothing at all to judge — a checkout shape, not a release
    state. The only case a guard may skip on."""


def judge(repo: Path) -> Judgement:
    """The release a tag-relative guard should check, with what it skipped.

    `tag` is None only when there is genuinely nothing: a tagless or shallow
    checkout (`nothing`), or every tag being at HEAD (`declared`).
    """
    repo = Path(repo)
    try:
        tags = release_tags(repo)
        at_head = tags_at_head(repo)
    except (OSError, subprocess.CalledProcessError) as e:  # pragma: no cover
        return Judgement(nothing=f"git tags are not readable here ({e})")
    if not tags:
        return Judgement(nothing="no release tags in this checkout")

    newest = tags[-1]
    if newest not in at_head:
        return Judgement(tag=newest)

    # The newest tag IS this commit. The history tables are regenerated AFTER
    # tagging, by tools that read tags, so nothing about this release can be
    # checked yet — and that is a declared state, not a failure.
    declared = (
        f"{newest} is AT HEAD, so the release-history tables cannot carry it "
        "yet: `tools/template_history.py` and `tools/section_history.py` read "
        "git tags and run after the tag exists. Nothing about "
        f"{newest} is checked by this run (SCRUM-68). Run them and commit the "
        "result; the next commit makes this guard live again."
    )
    earlier = [t for t in tags if t not in at_head]
    if not earlier:
        return Judgement(declared=declared)
    return Judgement(tag=earlier[-1], declared=declared)
