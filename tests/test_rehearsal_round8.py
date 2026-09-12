"""Regression tests for the defect found while closing round 8's brief.

The reported claim was that `install.sh --force` "deletes three standing
rules its payload source lacks". `install.sh` takes no arguments at all —
no `$1`, no `$@` — so that command does not exist, and `rite init` does
NOT delete `.claude/` files the templates lack: a project's own
`commands/house-rules.md`, `commands/escalation.md` and
`agents/security-reviewer.md` all survive it, measured.

What it did do was destroy `CLAUDE.md`.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from rite_ai.cli.main import cli

STANDING = "STANDING INSTRUCTIONS\nDo not touch production.\n"


def _init(tmp_path: Path) -> object:
    return CliRunner().invoke(cli, ["init", str(tmp_path), "--yes"])


class TestInitDoesNotDestroyAnExistingClaudeMd:
    """`install_claude_config` was a bare `write_text`: `rite init`
    replaced whatever `CLAUDE.md` it found, with no prompt, no backup and
    exit 0.

    The directory that gets hit is the ordinary one. `init` refuses
    outright once `.rite/` exists, so the only reachable path is a FIRST
    init — on a repository that already has standing instructions and is
    adopting rite, which is the common case rather than an edge one. The
    dogfood guide carries it as its first ⛔ STOP, against a 77 KB file
    that is what every live session on that machine loads.

    Same class as `rite remove worker`'s unconditional `rmtree`
    (`tests/test_rehearsal_round5.py`): a documented path destroying the
    one thing in reach that cannot be rebuilt. Preserved rather than
    refused, because writing `CLAUDE.md` is what `init` is FOR and
    refusing would mean rite could never initialise a repository that
    already had one.
    """

    def test_the_users_file_is_still_on_disk(self, tmp_path: Path):
        (tmp_path / "CLAUDE.md").write_text(STANDING)

        _init(tmp_path)

        preserved = tmp_path / "CLAUDE.md.pre-rite"
        assert preserved.is_file(), (
            "the user's CLAUDE.md was destroyed, not moved aside"
        )
        assert preserved.read_text() == STANDING
        assert "Do not touch production" not in (tmp_path / "CLAUDE.md").read_text()

    def test_init_says_where_it_went(self, tmp_path: Path):
        (tmp_path / "CLAUDE.md").write_text(STANDING)

        result = _init(tmp_path)

        assert "CLAUDE.md.pre-rite" in result.output, result.output
        assert "not deleted" in result.output

    def test_regenerating_rites_own_file_leaves_no_litter(self, tmp_path: Path):
        """A file rite wrote is overwritten without ceremony — that is
        regeneration, not destruction. Told apart by the marker in the
        file, never by who ran the command."""
        from rite_ai.cli.init.claude_gen import GENERATED_MARKER

        (tmp_path / "CLAUDE.md").write_text(
            f"# CLAUDE.md — x\n\n{GENERATED_MARKER} This machine is an Owner.\n"
        )

        result = _init(tmp_path)

        assert not (tmp_path / "CLAUDE.md.pre-rite").exists()
        assert "pre-rite" not in result.output

    def test_a_second_foreign_file_does_not_overwrite_the_first(self, tmp_path: Path):
        """The preserved copy is itself something that must not be
        destroyed by the next run."""
        from rite_ai.cli.init.claude_gen import preserve_foreign_claude_md

        (tmp_path / "CLAUDE.md").write_text("first\n")
        preserve_foreign_claude_md(tmp_path)
        (tmp_path / "CLAUDE.md").write_text("second\n")
        preserve_foreign_claude_md(tmp_path)

        assert (tmp_path / "CLAUDE.md.pre-rite").read_text() == "first\n"
        assert (tmp_path / "CLAUDE.md.pre-rite.2").read_text() == "second\n"

    def test_nothing_to_preserve_is_not_an_event(self, tmp_path: Path):
        result = _init(tmp_path)

        assert not (tmp_path / "CLAUDE.md.pre-rite").exists()
        assert "pre-rite" not in result.output
        assert (tmp_path / "CLAUDE.md").is_file()


class TestInitLeavesTheProjectsOwnClaudeDirectoryAlone:
    """Measured against the reported claim, which does not reproduce:
    `rite init` only ADDS to `.claude/`. Kept because a future change that
    starts pruning what the templates lack would be the reported defect,
    arriving for real."""

    def test_files_the_templates_do_not_carry_survive(self, tmp_path: Path):
        rules = {
            ".claude/commands/house-rules.md": "# never force-push\n",
            ".claude/commands/escalation.md": "# escalate first\n",
            ".claude/agents/security-reviewer.md": "# our own reviewer\n",
        }
        for rel, body in rules.items():
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)

        _init(tmp_path)

        for rel, body in rules.items():
            assert (tmp_path / rel).read_text() == body, f"{rel} was lost"


# --- the consolidation pass's one new mechanism -----------------------------


def _leaf_commands(command, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    import click

    if isinstance(command, click.Group):
        out: list[tuple[str, ...]] = []
        for name, sub in sorted(command.commands.items()):
            out.extend(_leaf_commands(sub, (*prefix, name)))
        return out
    return [prefix]


# Commands this cannot safely invoke, each with the reason. Kept as small
# as it can be: every entry is a command NOT covered by the sweep below,
# so the list is a liability and its length is the measure of how much of
# the surface is checked by hand instead.
UNPROBEABLE: dict[tuple[str, ...], str] = {
    ("pool", "fill"): "starts real `claude` sessions; spent quota is the one "
    "damage no cleanup reverses",
    ("scheduler", "install"): "registers a persistent launchd/cron entry on "
    "the developer's own machine",
    ("scheduler", "uninstall"): "removes one, which may not be this test's",
    ("sandbox", "start"): "launches a real yoloAI sandbox",
    ("credential", "set"): "writes to the login keychain",
    ("credential", "remove"): "deletes from the login keychain",
    ("credential", "rotate"): "interactive, and writes to the keychain",
    ("init",): "creating `.rite/` is the whole point of this one",
}


class TestNoCommandInventsAProjectWhereItStands:
    """The one mechanism this consolidation adds, and the reason it is a
    mechanism rather than a line in the checklist.

    Round 3 found ten commands that created `.rite/` wherever they were
    run and reported success — `rite claim src/ --worker alpha` typed one
    directory too high printed "claimed 1 path(s)" and exited 0 against a
    ledger no other session reads. The fix, `_require_project_root`, is
    correct and is called by the ten commands that had the defect.

    Nothing makes the ELEVENTH call it. Round 3's own regression test
    names its ten in a list, so a new writing command added tomorrow
    passes that test by not being in it — which is the same
    hand-enumeration failure that let "a zero meaning I could not look"
    recur inside its own fix in round 7.

    So the list is derived from the command tree instead: every leaf
    command rite has, invoked with no arguments in a directory that is not
    a project, must leave no `.rite/` behind. A command that refuses for
    any reason — missing argument, not a project, nothing configured —
    passes. Only creating state outside a project fails.
    """

    def test_every_leaf_command_leaves_a_bare_directory_bare(
        self, tmp_path: Path, monkeypatch
    ):
        from rite_ai.cli.main import cli as root_command

        monkeypatch.setenv("RITE_DISPATCH_DIR", str(tmp_path / "hub"))
        offenders = []
        for argv in _leaf_commands(root_command):
            if argv in UNPROBEABLE:
                continue
            here = tmp_path / "bare" / "-".join(argv)
            here.mkdir(parents=True)
            monkeypatch.chdir(here)
            CliRunner().invoke(cli, [*argv])
            if (here / ".rite").exists():
                offenders.append(" ".join(argv))

        assert offenders == [], (
            "these commands manufactured a .rite/ in a directory that is not "
            f"a rite project, and so wrote state no other session reads: "
            f"{offenders}"
        )

    def test_the_unprobeable_list_stays_small_and_reasoned(self):
        """Each exclusion is surface this sweep does NOT cover. The test
        exists so adding one is a decision rather than a convenience."""
        from rite_ai.cli.main import cli as root_command

        leaves = set(_leaf_commands(root_command))
        assert set(UNPROBEABLE) <= leaves, (
            f"excluded a command that no longer exists: "
            f"{sorted(set(UNPROBEABLE) - leaves)}"
        )
        assert all(reason.strip() for reason in UNPROBEABLE.values())
        assert len(UNPROBEABLE) <= 10, (
            f"{len(UNPROBEABLE)} of {len(leaves)} commands are now exempt "
            "from the only automatic check that they do not invent a project"
        )


class TestTheDefectClassesFileStaysTrue:
    """`DEFECT_CLASSES.md` names the mechanisms that hold each class, and a
    file like that is exactly what class 6 — prose drifting in the
    direction that flatters the tool — does to a repository. The README
    went stale about `install.sh`'s length twice before a test held it.

    This checks only what is mechanically checkable: that every mechanism
    the document claims exists, exists. The prose still needs a human.
    """

    def _document(self) -> str:
        return (Path(__file__).resolve().parents[1] / "DEFECT_CLASSES.md").read_text()

    def test_every_mechanism_it_names_is_real(self):
        import importlib

        text = self._document()
        root = Path(__file__).resolve().parents[1]

        for symbol, module in (
            ("UNREADABLE_FIELDS", "rite_ai.cli.main"),
            ("unsaved_work", "rite_ai.workspace"),
            ("format_duration", "rite_ai.duration"),
            ("CorruptStateError", "rite_ai.state"),
            ("CountUnavailable", "rite_ai.sandbox"),
        ):
            assert symbol in text, f"{symbol} is no longer named in the document"
            assert hasattr(importlib.import_module(module), symbol), (
                f"DEFECT_CLASSES.md credits {module}.{symbol}, which is gone"
            )

        for test_file in (
            "test_spec_citations.py",
            "test_release_checksums.py",
            "test_artifacts_have_readers.py",
            "test_packaging.py",
            "test_module_entry_point.py",
        ):
            assert test_file in text
            assert (root / "tests" / test_file).is_file(), (
                f"DEFECT_CLASSES.md credits tests/{test_file}, which is gone"
            )

    def test_the_two_derived_guards_it_singles_out_still_derive(self):
        """The document's closing claim is that exactly two classes have a
        guard that enumerates its own subjects. If either stops deriving,
        the claim is false and the ratio it reports is wrong."""
        text = self._document()
        assert "TestEveryUnreadableFieldReachesTheAggregate" in text
        assert "TestNoCommandInventsAProjectWhereItStands" in text

        suite = Path(__file__).resolve().parents[1] / "tests"
        round7 = (suite / "test_rehearsal_round7.py").read_text()
        assert "dataclasses" in round7 and "fields(ProjectStatus)" in round7, (
            "the UNREADABLE_FIELDS guard no longer derives its list from the "
            "dataclass — DEFECT_CLASSES.md's central claim depends on it"
        )
        own = Path(__file__).read_text()
        assert "_leaf_commands(root_command)" in own, (
            "the command sweep no longer derives its list from the click tree"
        )

    def test_the_unprobeable_count_it_quotes_is_current(self):
        text = self._document()
        assert f"The {len(UNPROBEABLE)} commands in `UNPROBEABLE`" in text or (
            "eight commands in `UNPROBEABLE`" in text and len(UNPROBEABLE) == 8
        ), (
            f"the document says eight commands are exempt; there are now "
            f"{len(UNPROBEABLE)}"
        )
