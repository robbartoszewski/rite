"""Whether a command has left rite's generated files behind, asked after it ran.

The defect this exists for turned up three times in one audit, in three
unrelated places, and each time the fix was the same sentence written in a
new spot: `rite init` announced a publish gate that could not run on that
machine, `rite doctor` reported a missing gitleaks without saying which way
it bit, and `rite add module` wrote `.rite/modules.yaml` while every
generated `CLAUDE.md` went on saying "No modules registered yet".

The shape is not "the check is missing". The check existed every time —
`rite doctor` would have told you all three. The shape is that a condition
rite can detect waits to be ASKED, by a command the person has no reason to
run at that moment, when it was knowable at the moment their action caused
it. A check nobody runs is a check that is not there.

So the answer is not a fourth reminder in a fourth command. Rite's authored
inputs are a declared set (`AUTHORED_CONFIG`), the generated files are a
function of them, and every command goes through one `invoke`. Compare the
inputs across the call: if a command changed one and the generated files are
now behind, say so — whatever the command was, including ones not yet
written.

Two properties this is built for, both of them things a per-command reminder
could not give:

* It cannot be forgotten by a new command. The author of `rite add
  <whatever>` next month gets it without knowing this file exists.
* It cannot cry wolf. The gate is "an authored input changed", but the
  decision is `pending()` — a measurement of the generated files themselves
  — so a command that edits config without disturbing what is generated
  from it stays silent, as does one run against an already-current project.
"""

from __future__ import annotations

from pathlib import Path

# The authored files generated content is RENDERED FROM. Not `AUTHORED_CONFIG`,
# which is "what a project commits": that also holds `.rite/review-checklist.md`
# and `.rite/spec/`, which are themselves generated, so watching it made `rite
# update --files-only` — the command that exists to fix being behind — announce
# that something was behind, every time it ran.
#
# Kept honest from the other end by a test: changing any file named here has to
# move `pending()`, so a name that stops mattering fails rather than lingering.
# The reverse (a generator quietly learning to read something not named here)
# is not mechanised, and the cost of missing one is a notice this does not
# print — `rite doctor` and `rite start` still do.
GENERATION_INPUTS = (
    ".rite/brief.yaml",
    ".rite/modules.yaml",
    ".rite/config.yaml",
)

Snapshot = dict[str, tuple[int, int]]


def project_root() -> Path | None:
    """The project this command is running in, or None.

    None for `rite init` in an empty directory, `rite --help`, and anything
    run outside a project — all of which have nothing generated to be behind.
    """
    from rite_ai.cli.main import _find_project_root

    try:
        return _find_project_root()
    except Exception:
        return None


def snapshot(root: Path | None) -> Snapshot:
    """Cheap fingerprint of the authored inputs: size and mtime, no reads.

    Deliberately not a hash. This runs before every command, including the
    ones a Worker calls in a loop, and its only job is to decide whether the
    29ms measurement is worth taking. A false positive costs that
    measurement, which then prints nothing; a false negative costs one
    notice, and `rite doctor` and `rite start` still report it.
    """
    if root is None:
        return {}
    out: Snapshot = {}
    for rel in GENERATION_INPUTS:
        try:
            stat = (root / rel).stat()
        except OSError:
            continue
        out[rel] = (stat.st_size, stat.st_mtime_ns)
    return out


def report_if_behind(root: Path | None, before: Snapshot) -> list[str]:
    """The lines to print, or none. Never raises: a report about a command
    must not be able to fail the command."""
    if root is None or snapshot(root) == before:
        return []
    try:
        from rite_ai.update.refresh import pending

        plan = pending(root)
    except Exception:
        return []
    if not plan.behind:
        # Only what a refresh would actually rewrite. A CONTESTED section —
        # edited, or an older rite's and unprovable — is reported at length
        # by `rite update --files-only --dry-run`, which is the command that
        # can show the difference; repeating it here would put a second
        # voice on a situation the reader is already being walked through,
        # and after `rite update` itself it read as a complaint about the
        # refresh that had just run.
        return []
    return [
        f"{len(plan.files)} generated file(s) now behind what you just "
        "changed — `rite update --files-only` brings them up to date"
    ]
