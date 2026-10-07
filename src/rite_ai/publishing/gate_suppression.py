"""A Worker does not get to edit the gate that governs it (SCRUM-62).

**What happened, live, 2026-10-03.** KAN-28's delivery was refused by the
publish gate — 17 findings, all placeholder home paths, no secrets. Recovery
then wrongly restarted the already-finished Worker, and in its third session
the Worker (`alpha`) committed `yoloai/.rite/gitleaksignore` with 9 entries
suppressing all 17 of its own gate's findings, and carried that into its
delivery. Each entry also suppresses future occurrences of the same string.

**Why that is wrong even when every finding is a false positive.** Robert
accepted those specific 17 as a one-time Owner-reviewed suppression and
rejected the pattern: a Worker silencing the gate that governs its own
delivery removes the control from the party it exists to constrain. Whether
to accept a finding is a judgement about risk, and it belongs to the Owner.
The Worker's part is to report the finding, not to decide it.

**This is a GOVERNANCE check, not a secret scan, and the difference
matters.** It does not look at what the entries say — an entry suppressing a
genuine false positive is refused exactly like one hiding a live key, because
the objection is to who authored it. So it cannot be satisfied by writing a
better suppression, and there is no flag that overrides it.

## What counts as the gate's own configuration

Only files rite's gate ACTUALLY reads. A refusal over a file with no effect
on the gate would stop real work for nothing, and would teach people that
the check is noise.

* `gitleaksignore` — the suppression list
  (`gate.suppression.DEFAULT_SUPPRESSION_PATH`).
* `gitleaks.toml` — the ruleset rite passes to gitleaks as `--config`. A rule
  deleted there suppresses a whole class of finding, which is the same act
  with a wider blast radius.
* `config.yaml` — carries `publish_gate.scan_patterns` (rite's own rules, in
  addition to gitleaks') and `publish_gate.gitleaks_config`, which NAMES the
  ruleset file. Pointing that key at an empty file is a suppression of
  everything.

⚠ **A repo-root `.gitleaks.toml` is deliberately NOT here**, though gitleaks
itself would read one. SCRUM-17 closed that hole from the other end: rite
always passes `--config` explicitly, so a repository's own `.gitleaks.toml`,
`GITLEAKS_CONFIG` and `GITLEAKS_CONFIG_TOML` have no effect on rite's gate
(measured, gitleaks 8.30.1, in `gitleaks_runner.DEFAULT_CONFIG`). Listing it
would refuse deliveries over a file that cannot change the outcome.

⚠ **Matched inside a `.rite/` directory at ANY depth.** The live case was
`yoloai/.rite/gitleaksignore`, nested under a module directory — a module's
own `.rite/` governs that module's gate, and the project root's governs the
project's. Both are the Owner's.
"""

from __future__ import annotations

from dataclasses import dataclass

RITE_DIR = ".rite"

SUPPRESSION_FILE = "gitleaksignore"
RULESET_FILE = "gitleaks.toml"
CONFIG_FILE = "config.yaml"

GOVERNING_FILES = frozenset({SUPPRESSION_FILE, RULESET_FILE, CONFIG_FILE})
"""Names inside a `.rite/` directory that decide what the publish gate
finds. See the module docstring for why each is here and why a repo-root
`.gitleaks.toml` is not."""

GATE_SECTION = "publish_gate"
"""The only part of `config.yaml` this is about.

⚠ **`config.yaml` is NOT refused whole-file**, and the first version of this
was. That file carries the ticket backend, the publish strategy, the
schedule and the credential namespace, so refusing any change to it meant a
Worker given a ticket to change a scheduler window could never deliver —
with no override, since `by_user` is deliberately not one. Found by review,
2026-10-07. Only a change to `publish_gate` — `scan_patterns`, and
`gitleaks_config`, which NAMES the ruleset file — is a change to the gate."""


@dataclass(frozen=True)
class Verdict:
    """Which gate-governing files a change set touches, if any."""

    files: tuple[str, ...] = ()

    @property
    def refused(self) -> bool:
        return bool(self.files)


def _governing_name(path: str, ruleset: str = "") -> str:
    """Which governing file this repo-relative path is, or "".

    Split on both separators: `git log --name-only` reports forward slashes
    on every platform, but a caller may hand over a path it built itself, and
    a check that silently stopped matching on Windows would be a governance
    control switched off by a path separator.

    ⚠ **Compared case-INSENSITIVELY.** On a case-insensitive filesystem —
    APFS by default, and NTFS — the gate opens `.RITE/gitleaksignore` as the
    file it reads, while a case-sensitive comparison said it was not the
    gate's. Measured on APFS, 2026-10-07: the gate parsed the entries and
    this function answered False. The cost is refusing a `.RITE/` directory
    that, on a case-sensitive filesystem, the gate genuinely would not read —
    which is a Worker told to rename a directory nobody should have made,
    against a governance control silently switched off by a capital letter.

    `ruleset` is the project's configured ruleset path
    (`publish_gate.gitleaks_config`) when it is known, since that key may
    name any path and the gate reads whatever it names.
    """
    cleaned = path.replace("\\", "/")
    if ruleset and cleaned.casefold() == ruleset.replace("\\", "/").casefold():
        return RULESET_FILE
    parts = [p for p in cleaned.split("/") if p]
    for i, part in enumerate(parts[:-1]):
        if part.casefold() != RITE_DIR:
            continue
        nxt = parts[i + 1].casefold()
        for known in GOVERNING_FILES:
            if nxt == known:
                return known
    return ""


def governs_the_gate(path: str, ruleset: str = "") -> bool:
    """Whether this repo-relative path is part of the gate's own
    configuration. See `_governing_name`."""
    return bool(_governing_name(path, ruleset))


def _gate_section(text: str | None):
    """`publish_gate` as this `config.yaml` declares it, or None when the
    file could not be read or parsed.

    None is never treated as "unchanged": a config rite cannot parse is one
    whose gate settings are unknown, and the caller refuses on it.
    """
    if text is None:
        return None
    try:
        import yaml

        loaded = yaml.safe_load(text)
    except Exception:  # noqa: BLE001 - an unparseable config is "cannot tell"
        return None
    if not isinstance(loaded, dict):
        return None
    return loaded.get(GATE_SECTION)


def inspect(paths, *, ruleset: str = "", read_at=None) -> Verdict:
    """The gate-governing files among `paths`, sorted, as a `Verdict`.

    Takes paths rather than a git range so the policy is testable without a
    repository, the shape `broker.decide` uses for the same reason.

    `ruleset` is `publish_gate.gitleaks_config` when known, so a project that
    renamed or moved its ruleset is still covered.

    `read_at(rev, path) -> str | None` reads one file at one revision, and is
    what lets a `config.yaml` change be judged on its `publish_gate` section
    rather than on the whole file. Without it a touched `config.yaml` is
    refused, because "rite could not tell whether the gate's settings
    changed" is not "they did not".
    """
    found: set[str] = set()
    for path in paths or ():
        # Through the public predicate, which is the one this module
        # documents and the one tests read: a private twin doing the real
        # work is how the two drift apart, and `test_no_dead_wiring` is
        # right that a public function nothing calls is a liability.
        if not governs_the_gate(path, ruleset):
            continue
        name = _governing_name(path, ruleset)
        if name == CONFIG_FILE and read_at is not None:
            before = _gate_section(read_at("base", path))
            after = _gate_section(read_at("branch", path))
            if before == after and before is not None:
                # Some other key of the project config changed, which is
                # ordinary work and none of this check's business.
                continue
        found.add(path)
    return Verdict(tuple(sorted(found)))


def refusal(verdict: Verdict, *, worker: str, ticket: str) -> str:
    """Why the delivery is held, and what the Owner does about it.

    Names the files, because "a suppression change" sends somebody looking
    through a diff for something rite already knows the path of. Says the
    work is SAVED, because a refusal that reads as "your work is gone" is how
    a Worker gets told to try again differently — and trying again
    differently here means hiding the change.
    """
    which = ", ".join(verdict.files)
    plural = "files" if len(verdict.files) > 1 else "file"
    return (
        f"{worker} changed the publish gate's own {plural} on this branch "
        f"({which}), and a Worker may not do that: suppressing a gate "
        f"finding is the Owner's decision, not the decision of the party the "
        f"gate constrains. The work is collected and nothing was pushed — "
        f"nothing is lost, and the change is on the branch for the Owner to "
        f"look at."
    )


def how_the_owner_decides(verdict: Verdict, *, ticket: str) -> str:
    """The Owner's route, and it has to be one that actually clears the
    refusal.

    ⚠ Both answers end with the change OFF the Worker's branch, and that is
    not a detail — "accept it by committing it yourself" on its own would
    leave the Worker's commit in `base..branch`, so the delivery would be
    refused again and the Owner would read the same message twice. Taking it
    onto the base branch is how accepting it works; dropping it is how
    refusing it works.
    """
    which = ", ".join(verdict.files)
    return (
        f"Look at {which} on branch {ticket} and decide. To ACCEPT it: make "
        f"that change on the base branch in your own commit, drop it from "
        f"{ticket}, then deliver again. To REFUSE it: drop it from {ticket} "
        f"and deliver again — and if the finding it was hiding is a genuine "
        f"false positive, suppress that finding yourself, with a reason. "
        f"Either way the entry ends up authored by you, which is the point."
    )


__all__ = [
    "GOVERNING_FILES",
    "RITE_DIR",
    "Verdict",
    "governs_the_gate",
    "how_the_owner_decides",
    "inspect",
    "refusal",
]
