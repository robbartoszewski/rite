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

GOVERNING_FILES = frozenset({"gitleaksignore", "gitleaks.toml", "config.yaml"})
"""Names inside a `.rite/` directory that decide what the publish gate
finds. See the module docstring for why each is here and why a repo-root
`.gitleaks.toml` is not."""


@dataclass(frozen=True)
class Verdict:
    """Which gate-governing files a change set touches, if any."""

    files: tuple[str, ...] = ()

    @property
    def refused(self) -> bool:
        return bool(self.files)


def governs_the_gate(path: str) -> bool:
    """Whether this repo-relative path is part of the gate's own
    configuration.

    Split on both separators: `git log --name-only` reports forward slashes
    on every platform, but a caller may hand over a path it built itself, and
    a check that silently stopped matching on Windows would be a governance
    control switched off by a path separator.
    """
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    for i, part in enumerate(parts[:-1]):
        if part == RITE_DIR and parts[i + 1] in GOVERNING_FILES:
            return True
    return False


def inspect(paths) -> Verdict:
    """The gate-governing files among `paths`, sorted, as a `Verdict`.

    Takes paths rather than a git range so the policy is testable without a
    repository, the shape `broker.decide` uses for the same reason.
    """
    found = sorted({p for p in paths or () if governs_the_gate(p)})
    return Verdict(tuple(found))


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
