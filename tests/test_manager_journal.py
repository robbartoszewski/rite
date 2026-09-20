"""ACCEPTANCE TEST for SPEC §9.15 — the Manager's process journal (item 4).

NOT YET COMMITTED. Nothing under `src/` implements this, so every test here
fails today. That is the point: this file is what "done" means, written
before the code, so the implementation is measured against §9.15 as it is
written on origin/main (45af1bd) rather than against a paraphrase.

⚠ WHERE THIS DELIBERATELY EXCEEDS THE SPEC. §9.15 leaves open (Q3) whether
"an entry without an anchor is not written" is enforced at the writing path
or is only a requirement on the format. `test_an_entry_without_an_anchor_*`
below asserts the STRONGER reading — a refusal AND no file on disk. If the
implementation does not do that, the failure is a spec question, not a bug.
My answer, for the review: it must be the writing path. A format rule is
honoured by a Manager choosing to honour it, and §9.15.3's own closing line
says a format that makes an unanchored entry impossible to write beats any
amount of exhortation. A rule enforced only by asking is exhortation.

⚠ WHAT THIS FILE DOES NOT TEST, said plainly rather than faked. §9.15.0's
process-versus-work distinction is a judgement a Manager makes about
content. No mechanical test can decide whether a given entry is a process
issue, and a test asserting keywords would be a proxy measure — the exact
defect class this release has been filing all day. It is reviewed by
reading entries, not by CI.

⚠ `claude` is never a command here.
"""

from __future__ import annotations

import subprocess

import pytest  # noqa: F401

# ⚠ A PLAIN IMPORT, DELIBERATELY. The first draft of this file used
# `pytest.importorskip`, and running it produced `1 skipped` — the whole
# acceptance suite green against an unimplemented feature, while the
# docstring three lines above claimed it failed hard. That is the vacuous
# check this project has been filing all day, written into the file that
# exists to prevent it. A plain import raises a collection error until
# `rite_ai.managers.journal` exists, which is the correct signal for an
# acceptance test: not done yet, loudly.
from rite_ai.managers import journal  # noqa: E402

# --- §9.15.3 the anchor requirement, and Q3 -----------------------------------------


def test_an_entry_without_an_anchor_is_refused_and_no_file_appears(tmp_path):
    """Q3, asserted at the STRONGER reading. See the module docstring.

    Two assertions, because they fail for different reasons. A refusal that
    still writes means the check runs after the write. A silent no-write
    means the caller cannot tell a refusal from a success.
    """
    result = journal.write_observation(
        tmp_path,
        manager="lead",
        anchor="",
        observed="the gate reported clean",
        expected="a gate that cannot read a file does not report clean",
    )
    assert not result.ok, "an entry with no anchor was accepted"
    assert result.message, "the refusal must say why, not merely fail"
    written = list((tmp_path / ".rite/managers/lead/journal").glob("*.md"))
    assert not written, (
        f"the entry was refused AND written anyway: {written}. A check that "
        "runs after the write is not a check on the writing path"
    )


def test_an_entry_without_expected_is_refused(tmp_path):
    """§9.15.3: `expected` is required — "X failed" alone is unactionable."""
    result = journal.write_observation(
        tmp_path,
        manager="lead",
        anchor="abc1234",
        observed="the gate reported clean",
        expected="",
    )
    assert not result.ok, "an entry with no `expected` was accepted"


def test_a_complete_entry_is_written_where_9_14_9_says(tmp_path):
    """So the fix cannot be "refuse everything"."""
    result = journal.write_observation(
        tmp_path,
        manager="lead",
        anchor="abc1234",
        observed="the gate reported clean on a file it could not open",
        expected="a gate that cannot read a file does not report clean",
        inferred="the exit code is being read from the wrong process",
    )
    assert result.ok, f"a complete entry was refused: {result.message}"
    entries = list((tmp_path / ".rite/managers/lead/journal").glob("*.md"))
    assert len(entries) == 1, f"expected exactly one file, got {entries}"
    assert entries[0].name.endswith("-observation.md"), (
        f"§9.15.3 names the file <timestamp>-<kind>.md: {entries[0].name}"
    )


def test_observed_and_inferred_are_separate_fields_in_the_file(tmp_path):
    """§9.15.3 measure 3: separate SYNTACTICALLY, not by convention.

    This release's worst errors were conclusions presented as observations.
    A format that concatenates them cannot distinguish the two later.
    """
    journal.write_observation(
        tmp_path,
        manager="lead",
        anchor="abc1234",
        observed="OBSERVED_MARKER",
        expected="EXPECTED_MARKER",
        inferred="INFERRED_MARKER",
    )
    text = next((tmp_path / ".rite/managers/lead/journal").glob("*.md")).read_text()
    for field in ("observed", "expected", "inferred"):
        assert field in text, f"the {field!r} field is not named in the entry"
    assert text.index("OBSERVED_MARKER") != text.index("INFERRED_MARKER")
    before, after = text.split("INFERRED_MARKER", 1)
    assert "OBSERVED_MARKER" in before, (
        "observed and inferred are not separable by parsing the file — a "
        "reader cannot tell which half is a conclusion"
    )


def test_one_file_per_entry_not_a_running_log(tmp_path):
    """§9.15.3: a single appended file invites a stream."""
    for i in range(3):
        journal.write_observation(
            tmp_path,
            manager="lead",
            anchor=f"abc123{i}",
            observed=f"thing {i}",
            expected="something else",
        )
    entries = list((tmp_path / ".rite/managers/lead/journal").glob("*.md"))
    assert len(entries) == 3, f"expected three separate files, got {entries}"


# --- §9.15.4 three facts, and no verdict --------------------------------------------


def test_a_retrospective_cannot_carry_a_verdict(tmp_path):
    """§9.15.4. The obvious metric is inverted, so the format must not offer
    a place to record the conclusion the Manager is not positioned to draw.

    "Round 2 cost 150k and changed nothing" is checkable. "Round 2 was a
    waste" is not, and the round that changed nothing may be the round that
    killed a bad design.
    """
    fields = journal.RETROSPECTIVE_FIELDS
    for banned in ("verdict", "judgement", "judgment", "rating", "score", "value"):
        assert banned not in fields, (
            f"the retrospective format offers a {banned!r} field; §9.15.4 "
            "requires three facts and explicitly no verdict"
        )
    for required in ("cost", "changed", "caught_elsewhere"):
        assert required in fields, f"§9.15.4 requires {required!r}"


# --- §9.15.6 genuinely off, tested as a mechanism -----------------------------------


# ⚠ §9.15.6 IS NOT TESTED HERE, AND NOT BECAUSE IT WAS FORGOTTEN.
#
# The section requires two instructions in the generated `CLAUDE.md` — that
# the journal directory exists, and when to write — present ONLY for a
# Manager started with the flag.
#
# That cannot be implemented as written. `CLAUDE.md` is PROJECT-level and is
# generated by `rite init` (and refreshed by `rite update`); `rite start`
# does not touch `claude_gen` at all. `--record-issues` is a per-START flag.
# So two Managers in one project, one started with the flag and one without,
# would need different `CLAUDE.md` files at the same time from a single
# shared file that neither start writes.
#
# An earlier draft of this file tested `claude_gen.render(..., journal_enabled=)`
# — an API that does not exist, invented to match the requirement rather
# than probed from the interface. Removed rather than kept as a red test:
# the failure would be the spec's, not the code's, and a red test against
# unimplemented-because-unimplementable is noise that teaches a reader to
# ignore this file.
#
# The gap is recorded in `managers/journal.py`'s docstring and raised for
# decision. The likely resolution is that the two instructions belong in the
# PROMPT sent to the session at start (§9.14.11a), which is per-session by
# construction and costs nothing when the flag is off — but that is not this
# item's to decide unilaterally, and a test will follow the decision.


# --- §9.15.5 nothing reads it -------------------------------------------------------


def test_nothing_in_rite_reads_the_journal(tmp_path):
    """§9.15.5: a file that triggers behaviour is a control channel.

    Mechanical half: no module outside the journal writer may open the
    journal directory. The behavioural half — that a Manager does not vary
    its process — is not testable here and is reviewed by reading §9.15.6's
    instructions.
    """
    import pathlib

    # ⚠ A FIRST DRAFT OF THIS TEST FLAGGED ANY MODULE CONTAINING "journal"
    # ALONGSIDE `read_text` OR `glob`. That matches `cli/main.py` the moment
    # the flag is wired, for reasons that have nothing to do with reading
    # entries — a false positive that would have been "fixed" by loosening
    # the test until it said nothing. Two precise properties instead.

    # 1. The module offers no way to read an entry back. Inertness is not a
    #    promise about callers if the API hands them a reader.
    for reader in ("read_entries", "load", "parse", "entries", "read"):
        assert not hasattr(journal, reader), (
            f"`journal.{reader}` exists. §9.15.5 requires the journal be "
            "inert; an API that reads entries is the first step toward a "
            "Manager varying its own process on its own retrospectives"
        )

    # 2. Only the journal module constructs journal paths. Callers write
    #    through `write_observation`/`write_retrospective`/`start_notice`
    #    and never locate the directory themselves, so there is no second
    #    place a reader could grow.
    src = pathlib.Path(journal.__file__).resolve().parent.parent
    offenders = [
        str(path.relative_to(src))
        for path in src.rglob("*.py")
        if path.name != "journal.py"
        and "journal_dir(" in path.read_text(errors="replace")
    ]
    assert not offenders, (
        f"these modules build a journal path themselves: {offenders}. Every "
        "caller should go through the writing API; a module that can locate "
        "the directory is one line from reading it"
    )


# --- §9.15.1 the flag itself --------------------------------------------------------


def _start_help() -> str:
    from click.testing import CliRunner

    from rite_ai.cli.main import cli

    return CliRunner().invoke(cli, ["start", "--help"]).output


def test_the_flag_is_named_what_it_does_and_marked_beta():
    """D-89 and §9.15.1."""
    text = _start_help()
    assert "--record-issues" in text, (
        "D-89 names the flag `--record-issues` — for what it DOES, not the "
        "category it belongs to"
    )
    assert "beta" in text.lower(), (
        "§9.15.1 requires beta in the flag's help text — it is what buys the "
        "licence to change the entry format without a compatibility argument"
    )


def test_somebody_who_does_not_know_the_feature_exists_can_find_it():
    """§9.15's discoverability requirement, and the whole point of the item.

    ⚠ The stakes are not "an opt-in feature goes unused". The next large
    unattended run is operated by somebody who is not the project owner,
    with the owner not watching, and this is the only channel by which
    anything comes back from it. A flag nobody finds means that run
    produces silence and nobody learns so until it is over.

    So this asserts the flag is visible where a person starting a Manager
    looks — `rite start --help` — rather than only in a document they have
    no reason to open.
    """
    text = _start_help()
    assert "--record-issues" in text, (
        "`--record-issues` does not appear in `rite start --help`. The "
        "person running the dogfood has no reason to know it exists"
    )


# --- §9.15.3a the entries must be able to leave the machine -------------------------


def test_starting_with_the_flag_prints_where_entries_will_be_written(tmp_path):
    """§9.15.3a(1): an ABSOLUTE path, at start.

    A diagnostic whose output never leaves the host returns nothing to the
    one reader it exists for. The path is printed at start, beside the
    resolved timezone and the engine line, because a fact is cheapest to
    learn when it is actionable.
    """
    line = journal.start_notice(tmp_path, manager="lead")
    assert line, "nothing is printed at start when the flag is on"
    directory = str(tmp_path / ".rite/managers/lead/journal")
    assert directory in line, (
        f"the start notice does not name the journal directory as an "
        f"absolute path. Got: {line!r}"
    )
    assert not line.strip().startswith("."), "the path printed is not absolute"


def test_the_start_notice_hands_over_the_command_not_just_the_path(tmp_path):
    """§9.15.3a(1): the `git add -f` command is PRINTED, not documented.

    ⚠ The `-f` is the load-bearing character. `.rite/*` excludes
    `.rite/managers` as a directory, and git will not descend into an
    excluded directory, so a plain `git add` on a journal entry silently
    refuses — measured, and it is why §9.15.3's earlier "unless somebody
    deliberately re-includes it" was false.

    Documenting the route is an instruction to a human who may never read
    the docs, and this human is running a dogfood on another machine with
    no reason to open them. §9.15.3's own closing argument applies: a thing
    that hands the operator the right command beats any amount of
    exhortation to go and find it.
    """
    line = journal.start_notice(tmp_path, manager="lead")
    assert "git add -f" in line, (
        "the start notice does not print the `git add -f` command. Without "
        "the -f a plain `git add` silently refuses, so an operator who "
        "tries the obvious thing gets nothing and no error worth noticing"
    )
    assert "REQUIRED" in line or "required" in line, (
        "the notice prints `-f` without saying it is required, so it reads "
        "as one way of doing it rather than the only way that works"
    )


def test_a_manager_without_the_flag_says_nothing_about_journals(tmp_path):
    """§9.15.3a(1) read with §9.15.6: the line appears ONLY with the flag.

    The CLI side of "genuinely off". A Manager running without
    `--record-issues` must not mention a facility it will not use.
    """
    assert journal.start_notice(tmp_path, manager="lead", enabled=False) == "", (
        "a Manager started WITHOUT --record-issues printed a journal line"
    )


def test_nothing_in_the_journal_module_transmits_anything(tmp_path):
    """§9.15.3a(3): nothing collects, uploads or transmits automatically.

    A diagnostic that phoned home would be a worse feature than one that
    returns nothing, and §9.15.5's inertness is not relaxed by the need to
    get entries off the host.
    """
    import pathlib

    text = pathlib.Path(journal.__file__).read_text()
    for banned in ("requests", "urllib", "httpx", "socket", "smtplib", "curl"):
        assert banned not in text, (
            f"the journal module references {banned!r} — §9.15.3a forbids "
            "collecting, uploading or transmitting entries automatically"
        )


# --- §9.15.3 the gitignore claim ----------------------------------------------------


def test_a_journal_entry_is_ignored_by_git(tmp_path):
    """§9.15.3 claims the existing `.rite/*` rule already covers this.

    Verified rather than assumed, because an ignored path silently
    swallowing an intended file is a defect this project has now hit twice.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text(".rite/*\n!.rite/config.yaml\n")
    entry = tmp_path / ".rite/managers/lead/journal/e.md"
    entry.parent.mkdir(parents=True)
    entry.write_text("x\n")
    done = subprocess.run(
        ["git", "check-ignore", "-v", str(entry.relative_to(tmp_path))],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, (
        "a journal entry is NOT ignored by the existing rule, so entries "
        "would be committed into the project by an ordinary `git add -A`"
    )
