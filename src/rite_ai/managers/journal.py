"""The Manager's process journal — SPEC §9.15. Opt-in, beta, and inert.

WHAT THIS IS FOR, because it decides every judgement below. Feedback about
how rite is WORKING currently only exists where a human is watching. The
next large unattended run is a dogfood on somebody else's machine, with the
project owner not there, and this is the only channel by which anything
comes back from it.

⚠ So the failure mode is NOT "an opt-in feature goes unused". It is "the
run produces nothing and nobody learns that until it is over". That is why
the start notice prints a path and a command rather than the docs
describing one, and why the flag appears in `rite start --help` where a
person starting a Manager will actually meet it.

FOUR THINGS THIS MODULE DELIBERATELY DOES NOT DO.

1. **Nothing reads the journal** (§9.15.5). A file that triggers behaviour
   is a control channel; this is a notebook. A Manager that varied its own
   process on the strength of its own retrospectives — skipping reviews it
   had concluded were unproductive — is the catastrophe this forbids, and
   it is the natural next step the moment anything parses these files.

2. **Nothing collects, uploads or transmits** (§9.15.3a). No export, no
   archive, no sync. The retrieval problem is solved socially: a person
   copies the directory. A diagnostic that phoned home would be a worse
   feature than one that returns nothing.

3. **No verdict field on a retrospective** (§9.15.4). The obvious measure
   is inverted: a review round ending "fix these three things" produces a
   commit, one ending "this design would force-release live Workers, start
   again" produces nothing. Judging by output ranks bad reviewing above
   good. So an entry records what it cost, what changed, and whether the
   change would have been caught elsewhere — three facts, no conclusion.

4. **An unanchored entry is not written** (D-87). Refused here, on the
   writing path, before any file is created — not validated afterwards and
   not asked of the Manager. §9.15.3 closes by saying a format that makes
   an unanchored entry impossible to write beats any amount of exhortation,
   three lines after naming two occasions where an instruction to check
   carefully immediately preceded the error it warned against.

⚠ TWO THINGS ARE NOT IMPLEMENTED, named here rather than left to be found.

**§9.15.3's fifth measure** — "anchors are checked where checking is cheap",
e.g. verifying a cited commit exists. This module refuses an entry with NO
anchor; it does not verify that an anchor which IS present resolves. So it
enforces "an entry has an anchor" and not "an entry has a REAL anchor",
which is a real gap in the measures against invented events.

**§9.15.6 cannot be implemented as written, and this is a spec defect
rather than a deferral.** It requires two instructions in the generated
`CLAUDE.md` — that the directory exists, and when to write — present ONLY
for a Manager started with the flag. But `CLAUDE.md` is PROJECT-level,
written by `rite init` and refreshed by `rite update`; `rite start` never
touches `claude_gen`. `--record-issues` is a per-START flag. Two Managers
in one project, one started with the flag and one without, would need
different `CLAUDE.md` files at the same time out of one shared file that
neither start writes.

The likely resolution is that those two instructions belong in the PROMPT
sent to the session at start (§9.14.11a), which is per-session by
construction and costs nothing when the flag is off — satisfying §9.15.1's
"genuinely off" more exactly than a generated file ever could. Not decided
here, because the prompting path is another item's and a unilateral
resolution to a spec contradiction is how two half-designs meet later.

⚠ THE CONSEQUENCE WHILE IT IS OPEN, stated because it is easy to miss: a
Manager started with `--record-issues` today is told where the journal is
by the start notice, and is NOT told to write anything to it. The
capability is reachable and unannounced to the agent — the same class as
"a capability nobody is told about is a capability nobody uses", which
§9.15.6 exists to prevent and which this gap reinstates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rite_ai.names import name_problem

OBSERVATION = "observation"
RETROSPECTIVE = "retrospective"

# The fields a retrospective carries. ⚠ Read §9.15.4 before adding to this:
# the absence of a verdict field is the point, not an oversight, and a
# `value`/`rating`/`score` column would reintroduce the inverted metric by
# giving the Manager somewhere to put a conclusion it cannot support.
RETROSPECTIVE_FIELDS = ("anchor", "cost", "changed", "caught_elsewhere", "inferred")
OBSERVATION_FIELDS = ("anchor", "observed", "expected", "inferred")

_ANCHOR_HELP = (
    "an anchor is a commit SHA, a file path with a line, a command with its "
    "output, a ticket id, or a named log file with a timestamp in it"
)


@dataclass(frozen=True)
class WriteResult:
    """Refusals carry a sentence, because every caller reports to a human."""

    ok: bool
    message: str = ""
    path: Path | None = None


def journal_dir(root: Path, manager: str) -> Path:
    """Where this Manager's entries live (§9.14.9, §9.15.3)."""
    return Path(root) / ".rite" / "managers" / manager / "journal"


def start_notice(root: Path, manager: str, *, enabled: bool = True) -> str:
    """What `rite start --record-issues` prints, or "" when the flag is off.

    ⚠ Empty when disabled, and that is the CLI half of §9.15.1's "genuinely
    off when off". A Manager running without the flag must not be told about
    a facility it will not use.

    The `-f` is the load-bearing character and the reason this prints a
    command rather than a path. `.gitignore`'s `.rite/*` excludes
    `.rite/managers` as a DIRECTORY, and git does not descend into an
    excluded directory, so a plain `git add` on a journal path refuses —
    measured against this repository, on the directory form printed here,
    which is what an operator will paste. Telling somebody where the files
    are and leaving them to discover that the obvious command silently
    fails is the kind of documentation that reads as helpful and is not.
    """
    if not enabled:
        return ""
    directory = journal_dir(root, manager).resolve()
    relative = Path(".rite") / "managers" / manager / "journal"
    return (
        f"recording issues to {directory}\n"
        f"  copy that directory to share the entries; to commit them "
        f"instead, `git add -f {relative}` is needed "
        f"(a plain `git add` refuses here)"
    )


def instructions(root: Path, manager: str, *, enabled: bool = True) -> str:
    """The three things a recording Manager is told, or "" when off.

    ⚠ ONE COPY OF THIS TEXT, ON PURPOSE. It is delivered in the start
    prompt (D-93) rather than in `CLAUDE.md`, because `CLAUDE.md` is
    project-level and written by `rite init` while `--record-issues` is
    per-start — two Managers in one project started differently would need
    two versions of one shared file. The prompt is per-session by
    construction, so §9.15.1's "genuinely off when off" is exact: a Manager
    without the flag does not receive these because they were never
    composed.

    `managers/prompt.py` appends whatever this returns and does not know
    what it says. Two copies of this text drifting apart is the failure
    this arrangement exists to prevent.

    ⚠ THE THIRD PARAGRAPH IS THE ONE THAT MATTERS, and §9.15.6 gained it as
    item 3 only after the command existed. Telling a Manager WHERE and WHEN
    but not HOW leaves it to hand-write markdown into the directory —
    bypassing the anchor refusal entirely and producing exactly the
    unanchored entries D-87 exists to prevent. Once the refusal lives
    behind a command, naming the command is part of the instruction or the
    mechanism is optional.
    """
    if not enabled:
        return ""
    directory = journal_dir(root, manager).resolve()
    return (
        "\n\nYou are recording process issues this session. They go in:\n"
        f"  {directory}\n\n"
        "Write an entry when something behaves differently from what the "
        "docs, or a tool's own output, claimed — a command reporting "
        "success while doing nothing, a check passing on input it could "
        "not read, a probe reporting a capability the same call then "
        "denied. Not every failing test: a failing test is work, and work "
        "goes to the board.\n\n"
        "Record it with:\n"
        f"  rite journal observe --manager {manager} "
        "--anchor <what makes it checkable> \\\n"
        "    --observed <what you saw> --expected <what should have "
        "happened>\n\n"
        "An entry without an anchor is refused: a commit SHA, a file and "
        "line, a command with its output, a ticket id, or a named log file "
        "with a timestamp in it."
    )


def _refuse(problem: str) -> WriteResult:
    return WriteResult(False, problem)


def _field_problem(manager: str, anchor: str, required: dict[str, str]) -> str:
    """Every reason this entry must not be written, or "".

    Checked BEFORE anything touches the filesystem: D-87 requires the
    refusal on the writing path, and a check that runs after the write is
    not a check on the writing path.
    """
    bad_name = name_problem(manager, kind="manager name")
    if bad_name:
        return bad_name
    if not anchor.strip():
        return (
            "refusing to write a journal entry with no anchor: an entry "
            "nobody can check is worse than no entry, because it reads like "
            f"evidence. {_ANCHOR_HELP}"
        )
    for field, value in required.items():
        if not value.strip():
            return (
                f"refusing to write a journal entry with no `{field}`: "
                f"'X failed' without what was expected instead is "
                "unactionable the next morning, and a reader cannot "
                "disagree with half of it"
            )
    return ""


def _write(root: Path, manager: str, kind: str, body: str) -> WriteResult:
    directory = journal_dir(root, manager)
    directory.mkdir(parents=True, exist_ok=True)
    # Microseconds, because three entries in a loop are a realistic case and
    # a second-resolution stamp would silently overwrite the earlier ones —
    # one file per entry is §9.15.3's requirement, not a preference.
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = directory / f"{stamp}-{kind}.md"
    path.write_text(body, encoding="utf-8")
    return WriteResult(True, path=path)


def _section(name: str, value: str) -> str:
    return f"## {name}\n\n{value.strip()}\n\n"


def write_observation(
    root: Path,
    *,
    manager: str,
    anchor: str,
    observed: str,
    expected: str,
    inferred: str = "",
) -> WriteResult:
    """Record something that behaved differently from what was claimed.

    ⚠ The trigger is narrow on purpose (§9.15.2). NOT "something looks
    wrong" — that admits every failing test and produces the dumping ground
    §9.15.0 exists to prevent. The judgement is a document, or a tool's own
    output, saying one thing while the system did another: a gate reporting
    success on a file it could not open, a wrong invocation producing output
    indistinguishable from the feature working, a probe reporting a
    capability the same call then denied. None of those is a test going red.

    `observed` and `inferred` are separate sections, syntactically, because
    this project's best findings had exactly that shape and its worst errors
    were conclusions presented as observations.
    """
    problem = _field_problem(
        manager, anchor, {"observed": observed, "expected": expected}
    )
    if problem:
        return _refuse(problem)
    body = (
        "# observation\n\n"
        + _section("anchor", anchor)
        + _section("observed", observed)
        + _section("expected", expected)
        + _section("inferred", inferred or "(nothing inferred)")
    )
    return _write(root, manager, OBSERVATION, body)


def write_retrospective(
    root: Path,
    *,
    manager: str,
    anchor: str,
    cost: str,
    changed: str,
    caught_elsewhere: str,
    inferred: str = "",
) -> WriteResult:
    """Record what a boundary cost and what it changed — with no verdict.

    Written at a ticket closing, a review round finishing, a merge landing,
    because the valuable process questions are not knowable in the moment:
    "a bug escaped testing" is only visible when the bug turns up later.

    ⚠ `changed` may legitimately be "nothing", and that is the entry worth
    having. "Round 2 cost 150k and changed nothing" is checkable; "round 2
    was a waste" is a conclusion the Manager is not positioned to draw,
    because the round that changed nothing may be the round that killed a
    design which looked fine.
    """
    problem = _field_problem(
        manager,
        anchor,
        {"cost": cost, "changed": changed, "caught_elsewhere": caught_elsewhere},
    )
    if problem:
        return _refuse(problem)
    body = (
        "# retrospective\n\n"
        + _section("anchor", anchor)
        + _section("cost", cost)
        + _section("changed", changed)
        + _section("caught_elsewhere", caught_elsewhere)
        + _section("inferred", inferred or "(nothing inferred)")
    )
    return _write(root, manager, RETROSPECTIVE, body)
