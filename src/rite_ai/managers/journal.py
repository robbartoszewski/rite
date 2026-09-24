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

import os
import re
import string
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rite_ai.names import name_problem, require_safe_name

OBSERVATION = "observation"
RETROSPECTIVE = "retrospective"

# The fields a retrospective carries. ⚠ Read §9.15.4 before adding to this:
# the absence of a verdict field is the point, not an oversight, and a
# `value`/`rating`/`score` column would reintroduce the inverted metric by
# giving the Manager somewhere to put a conclusion it cannot support.
# ⚠ NO `inferred` HERE, and its absence is the point. An earlier version
# carried one, and `--inferred "round 2 was a waste"` was accepted and
# written — the exact string §9.15.4 names as the conclusion a Manager
# is not positioned to draw. The module docstring, the CHANGELOG and the
# command help all claimed there was nowhere to put it while there was,
# in the feature whose subject is documented claims not matching
# behaviour. A guard that bans the WORDS "verdict"/"rating"/"score" from
# this tuple never had to notice, because the field doing the job was
# called something else.
RETROSPECTIVE_FIELDS = ("anchor", "cost", "changed", "caught_elsewhere")
OBSERVATION_FIELDS = ("anchor", "observed", "expected", "inferred")

# ⚠ `str.strip()` IS NOT ENOUGH, and this is the anchor rule's entire
# enforcement. `strip()` removes Python-whitespace only, so U+200B ZERO
# WIDTH SPACE, U+200C ZWNJ and U+2800 BRAILLE PATTERN BLANK all survived it
# and were accepted as anchors — measured. An entry whose anchor section
# renders blank is worse than the documented "a present anchor is not
# verified to resolve" gap: an invented SHA at least looks like something a
# reader will try to check, where this reads as no anchor at all having
# passed the refuser.
#
# ⚠ THE FIRST FIX WAS A BLACKLIST of Unicode categories (Cf, Zs, Cc) and it
# missed U+2800 on the first run, whose category is So. Blacklisting the
# invisible is whack-a-mole against a character set that keeps growing, and
# the miss was found only because the check was run against the exact
# characters the review named.
#
# ⚠ THE SECOND FIX WAS ALSO A RULE ABOUT CHARACTERS, and a third review
# defeated it. `str.isalnum()` is a property of the Unicode character CLASS,
# and category Lo contains characters that are alphanumeric AND render as
# nothing: U+3164 HANGUL FILLER, U+115F and U+1160 the CHOSEONG/JUNGSEONG
# FILLERS, U+FFA0 HALFWIDTH HANGUL FILLER. All four passed `isalnum()`, all
# four were written, and the `## anchor` section rendered blank — the exact
# bypass the blacklist had, reached through the allowlist instead.
#
# So the rule stops describing characters and describes THE ANCHOR'S JOB.
# An anchor exists to be CHECKED BY A READER: a commit SHA, a `file:line`,
# a command with its output, a ticket id, a log file with a timestamp.
# **Every one of those is ASCII**, so requiring an ASCII alphanumeric costs
# nothing real and removes the whole Lo-category family at once — not by
# naming its members, which is what the previous two rules tried, but by
# not admitting them in the first place.
#
# ⚠ This is a FLOOR, not a verification. §9.15.3's fifth measure — that a
# present anchor RESOLVES — is still unimplemented, and this does not
# change that: `deadbeef` is still accepted. What it guarantees is that the
# anchor section contains something a reader can see and try.

_LEGIBLE = frozenset(string.ascii_letters + string.digits)


def _is_blank(value: str) -> bool:
    """True when nothing a reader could check is present.

    ASCII deliberately. See the note above: two previous rules described
    which characters are blank, and each was defeated by a character its
    author had not met. This describes what an anchor must CONTAIN, and the
    set is closed — Unicode cannot add a new ASCII alphanumeric.
    """
    return not any(ch in _LEGIBLE for ch in value)


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
    """Where this Manager's entries live (§9.14.9, §9.15.3).

    ⚠ Validated, not merely joined. `managers.manager_dir` already refuses
    these names with "a name reaching a path unchecked is the defect
    `rite_ai.names` exists for, and this is a new join" — and this function
    re-spelled the same path by hand without the check. Today the write
    path catches a bad name first, so nothing escapes; `start_notice` and
    `instructions` call this with no such guard and would PRINT an escaping
    path. A guard that works only because a different function runs first
    is not a guard.
    """
    require_safe_name(manager, kind="manager name", must_be_a_tmux_target=True)
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

    ⚠ **`--manager` IS SPELLED OUT ON PURPOSE, and it is not redundant.**
    It looks redundant since `RITE_MANAGER`, and was carried into 0.6.0 as a
    tidy-up to remove (C13). Measured before doing it: the variable reaches
    the pane only through `tmux new-session -e`, and on a tmux older than 3.2
    `start` falls back to a launch WITHOUT it — saying, in its own start
    message, that these commands will refuse until given `--manager`. Run
    through the CLI with `RITE_MANAGER` unset:

        rite journal observe --manager lead --anchor … ->  recorded
        rite journal observe --anchor …                 ->  refused, rc 1

    So on that path the literal name in this text is the only identity the
    Manager has, and removing it turns a working journal into a refused one.
    Where the variable IS set the two agree, so spelling it costs nothing.
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
        "At a BOUNDARY — a ticket closing, a review round finishing, a "
        "merge landing — record what it cost instead:\n"
        f"  rite journal retrospective --manager {manager} "
        "--anchor <what makes it checkable> \\\n"
        "    --cost <tokens, wall-clock, rounds> --changed <what changed> "
        "\\\n"
        "    --caught-elsewhere <would anything else have caught it>\n\n"
        '"changed: nothing" is a legitimate and useful entry. Do not '
        "judge whether a round was worth it — record the three facts and "
        "leave the conclusion to a reader.\n\n"
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
    bad_name = name_problem(manager, kind="manager name", must_be_a_tmux_target=True)
    if bad_name:
        return bad_name
    # ⚠ Length is a path rule, not a name rule, which is why `name_problem`
    # does not carry it — but the traceback it produced (`OSError: [Errno
    # 63] File name too long`) reaches an AGENT, and an agent that gets a
    # traceback from the command it was told to use falls back to writing
    # the file by hand. That is the bypass §9.15.6 item 3 exists to stop,
    # reached through an error message. 200 is well under every filesystem
    # limit and far past any real Manager name.
    if len(manager) > 200:
        return (
            f"a manager name of {len(manager)} characters is too long to be "
            "a directory on any filesystem rite supports — 200 is the limit "
            "here, and a real Manager name is a word"
        )
    if _is_blank(anchor):
        return (
            "refusing to write a journal entry with no anchor: an entry "
            "nobody can check is worse than no entry, because it reads like "
            f"evidence. {_ANCHOR_HELP}"
        )
    for field, value in required.items():
        if _is_blank(value):
            return (
                f"refusing to write a journal entry with no `{field}`: "
                f"'X failed' without what was expected instead is "
                "unactionable the next morning, and a reader cannot "
                "disagree with half of it"
            )
    return ""


_ENV_ASSIGNMENT = re.compile(r"\b([A-Z_][A-Z0-9_]*=)([^\s]{8,})")


def _redacted(body: str) -> str:
    """The entry with credential-shaped values removed, BY STRUCTURE (C7).

    ⚠ **A Manager is TOLD to paste command output** — "a command with its
    output" is a permitted anchor — and §9.15.3a tells an operator to zip this
    directory and send it. Measured before this existed, through `rite
    journal observe`: an `env | grep TOKEN` dump and a `cat .envrc` in
    `--observed` reached the file verbatim, three tokens and all.

    Two passes, both about SHAPE and neither about names:
      1. `redact_secrets`'s own pass — `export NAME='value'`, whatever the
         name, including a value containing spaces;
      2. any environment-shaped assignment, `UPPER_NAME=value`, quoted or not,
         `export` or not, with a value of eight characters or more.

    Eight is `redact_secrets`'s threshold and for the same reason, and upper
    case is what makes it environment-shaped: `--sessions=3` and
    `PYTHONPATH=src` survive, so an entry about a flag or a path stays
    useful. That matters more than it looks — a redaction that ruins entries
    teaches a Manager to write the file by hand, past every rule here.

    ⚠ Known holes, stated: a value that is not in an assignment (a bare token
    in a log line, an `Authorization:` header) is not recognised. No list of
    token formats was added for it — a list loses to the next format.
    """
    from rite_ai.sandbox import redact_secrets

    return _ENV_ASSIGNMENT.sub(
        lambda m: m.group(1) + "[redacted]", redact_secrets(body)
    )


def _write(root: Path, manager: str, kind: str, body: str) -> WriteResult:
    """One file per entry (§9.15.3), enforced by the filesystem.

    ⚠ THE TIMESTAMP IS NOT A LOCK, and a comment here previously claimed
    microseconds made a collision impossible. Measured: twelve processes
    writing as the same Manager produced 30000 successful calls, each
    returning `ok=True` with a distinct path, and 29659 files — **341
    entries lost, every one of them reported as written.** Two processes
    still lost 73. A Manager running two tool calls at once is the ordinary
    case, not a contrived one.

    Losing an entry while reporting success is precisely the shape the
    instructions tell a Manager to write an entry ABOUT, so the feature was
    producing the defect it exists to record.

    `O_CREAT | O_EXCL` makes the filesystem decide. On a collision the
    stamp gains a suffix and we try again rather than overwrite; the loop
    is bounded because an unbounded retry on a full or read-only disk is a
    hang, and this project has spent the day on things that wait forever.
    """
    directory = journal_dir(root, manager)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    for attempt in range(50):
        suffix = "" if attempt == 0 else f"-{attempt}"
        path = directory / f"{stamp}{suffix}-{kind}.md"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        except OSError as e:
            return WriteResult(
                False, f"could not write the journal entry to {path}: {e}"
            )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(_redacted(body))
        return WriteResult(True, path=path)
    return WriteResult(
        False,
        f"could not find a free filename in {directory} after 50 attempts — "
        "entries are timestamped to the microsecond, so this means something "
        "is writing them faster than the clock advances",
    )


def _section(name: str, value: str) -> str:
    """One field, as a heading a value cannot forge.

    ⚠ MEASURED INJECTION. The fields are free text written by an agent, and
    the format is markdown headings. An `observed` value containing a line
    `## inferred` produced a file with two `## inferred` sections and two
    `## anchor` sections — so a conclusion supplied as an observation sat
    under the `inferred` heading, the real `inferred` read "(nothing
    inferred)", and a second, invented anchor appeared indistinguishable
    from the first.

    That defeats the one thing §9.15.3 requires be SYNTACTIC rather than
    conventional: that `observed` and `inferred` are separable. The spec's
    own example of the error it is guarding — "conclusions presented as
    observations, *'the mutant survived'* when it had never run" — is
    exactly what the injection produces.

    Escaping with a backslash is markdown's own mechanism: a
    backslashed hash renders as
    a literal `#`, and a reader or a parser splitting on `^## ` no longer
    matches. The value survives readably; only its ability to start a
    section is removed.
    """
    escaped = "\n".join(
        "\\" + line if line.lstrip().startswith("#") else line
        for line in value.strip().splitlines()
    )
    return f"## {name}\n\n{escaped}\n\n"


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
    )
    return _write(root, manager, RETROSPECTIVE, body)
