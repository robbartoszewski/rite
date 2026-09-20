"""A release must name the commands it ships, in its own changelog section.

Measured on v0.4.0 and recorded in `.docs/FUTURE_IMPROVEMENTS.md`: that
release shipped the entire spec digest and its changelog did not say so.
Measured again on the unreleased 0.5.0 while writing this: `rite loop`
gained four subcommands, and the only occurrence of the word "loop" in the
section was inside a caveat listing what was LEAST confirmed. A release's
headline feature appeared in its own changelog solely as a warning.

**Scoped to what this release adds, deliberately.** 58 of rite's 75 leaf
commands are named nowhere in `CHANGELOG.md`, so a repo-wide version of
this check fails on day one and gets muted, which is worse than not having
it. This asks only that what you ship NOW is announced now — the part a
reader upgrading actually needs, and the part nobody can be retrospectively
blamed for.

The comparison is against the newest `v*` tag, so the question is "what has
happened since the last release", which is exactly what an unreleased
section is for.

⚠ **This is a tripwire, not a proof, and the difference is worth stating
because a tripwire mistaken for a proof is how a real check gets deleted
later as redundant.**

It catches SILENCE: a release that shipped a command and never wrote the
name down. That is the failure actually measured, on v0.4.0.

It cannot catch a mention in passing, and this was demonstrated on itself.
Written against the unreleased 0.5.0, whose only occurrence of `rite loop`
was inside a caveat listing what was LEAST confirmed, the check passed —
the caveat names the group and the subcommands, so every string it looks
for was present. Tightening from "the group appears" to "the group and the
leaf appear" did not change that, because the caveat contained both.

Distinguishing an announcement from a warning is a judgement about
meaning, and there is no string property that expresses it. So the check
asks the question it can answer honestly and says here that it is not the
question anybody actually cares about; a human reading the section is
still the only thing that can tell the two apart.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = "src/rite_ai/cli/main.py"

# `@cli.command("name")`, `@loop.command("name")`, `@board.command()` ...
DECORATOR = re.compile(r"^\+@(\w+)\.command\(\s*(?:\"([^\"]+)\"|'([^']+)')?")
# `def loop_status(` under a bare @x.command() — click derives the name
DEF_AFTER = re.compile(r"^\+def (\w+)\(")


def _git(*args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _latest_tag() -> str | None:
    out = _git("tag", "--list", "v*", "--sort=-v:refname")
    if not out:
        return None
    tags = [t.strip() for t in out.splitlines() if t.strip()]
    return tags[0] if tags else None


def _commands_added_since(tag: str) -> set[str]:
    """Command names introduced in `cli/main.py` since `tag`.

    Reads the diff rather than importing two versions of the package: the
    old one is not installed and would have to be, which is a lot of
    machinery to answer a question a diff answers directly.
    """
    diff = _git("diff", f"{tag}..HEAD", "--", CLI)
    if not diff:
        return set()
    found: set[str] = set()
    lines = diff.splitlines()
    for i, line in enumerate(lines):
        match = DECORATOR.match(line)
        if not match:
            continue
        group, dq, sq = match.groups()
        explicit = dq or sq
        if explicit:
            found.add(f"{group} {explicit}" if group != "cli" else explicit)
            continue
        # Bare `@x.command()` — click names it after the function, with
        # underscores becoming hyphens.
        for ahead in lines[i + 1 : i + 6]:
            named = DEF_AFTER.match(ahead)
            if named:
                leaf = named.group(1).replace("_", "-")
                found.add(f"{group} {leaf}" if group != "cli" else leaf)
                break
    return found


def _unreleased_section() -> str:
    text = (ROOT / "CHANGELOG.md").read_text()
    body = text.split("\n## ")
    # body[0] is the title; body[1] is the newest section.
    return body[1] if len(body) > 1 else text


# ⚠ COMMANDS DELIBERATELY NOT ANNOUNCED. An entry here is a DECISION, not a
# convenience, and it must say whose and why — a silent exemption is how
# this guard would stop meaning anything.
#
# The rule this suspends is real and stays in force for everything else: a
# reader upgrading finds out what a release gave them from its changelog.
UNANNOUNCED_ON_PURPOSE = {
    "journal observe": (
        "Issue recording ships UNDOCUMENTED in 0.5.1 by the project owner's "
        "decision. It is absent from the README, the guide and the 0.5.1 "
        "changelog section on purpose: the journal applies NO redaction "
        "while the spec asks a Manager to record 'a command with its "
        "output' and tells an operator to zip the directory and send it. "
        "The machinery works and is not advertised until that leak path is "
        "closed — see docs/design/CREDENTIAL_HANDLING_FOR_UNATTENDED_RUNS.md. "
        "Delete this entry when the feature is announced; it is not "
        "permanent."
    ),
    "journal retrospective": "See `journal observe` above — same decision.",
}


def test_every_command_added_since_the_last_tag_is_named_in_the_changelog():
    tag = _latest_tag()
    if tag is None:
        pytest.skip("no v* tags, or git cannot answer — a packaged copy")

    added = _commands_added_since(tag)
    if not added:
        pytest.skip(f"no new commands since {tag} — nothing for this to check")

    section = _unreleased_section()
    # `rite loop status` verbatim, OR the group named AND the leaf word
    # present — because "new: `rite loop`, with `start`, `status` and
    # `stop`" is how a changelog actually reads and refusing it would make
    # the check pedantic enough to be muted.
    #
    # The group ALONE is not enough, and that leniency is what the first
    # version of this test shipped with. It passed on the exact case that
    # motivated it: 0.5.0's section mentioned `rite loop` once, inside a
    # caveat about what was LEAST confirmed, and a check satisfied by any
    # occurrence read that as an announcement. Measuring whether the string
    # appears is not measuring whether the release was announced, and the
    # gap between those two is the whole subject.
    missing = sorted(
        cmd
        for cmd in added
        if cmd not in UNANNOUNCED_ON_PURPOSE
        and f"rite {cmd}" not in section
        and not (
            f"rite {cmd.split()[0]}" in section
            and re.search(rf"\b{re.escape(cmd.split()[-1])}\b", section)
        )
    )
    assert not missing, (
        f"these commands were added since {tag} and the newest CHANGELOG "
        f"section does not name any of them: {', '.join(missing)}.\n"
        "A reader upgrading finds out what a release gave them from its "
        "changelog; v0.4.0 shipped the whole spec digest without saying so, "
        "and nothing noticed until somebody went looking months later."
    )


def test_the_check_can_actually_see_commands():
    """The premise. If the decorator pattern stops matching how commands are
    declared, the test above passes for ever while reading nothing — it
    would skip on an empty `added` set and look like a clean run."""
    tag = _latest_tag()
    if tag is None:
        pytest.skip("no v* tags")
    text = (ROOT / CLI).read_text()
    declared = re.findall(r"^@(\w+)\.command\(", text, re.MULTILINE)
    assert len(declared) > 20, (
        "the decorator pattern this test greps for no longer matches how "
        f"commands are declared in {CLI} (found {len(declared)}), so the "
        "check above is reading nothing and cannot fail"
    )
