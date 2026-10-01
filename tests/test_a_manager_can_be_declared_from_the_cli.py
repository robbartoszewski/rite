"""`rite add manager` — declaring a Manager without editing config.yaml (S16).

🔴 **Until this command there was no way to declare a Manager at all.** The
file was the only interface: a user adding their second Manager had to open
`.rite/config.yaml`, find `coordination:`, and know that a Manager lives in
TWO keys there. Robert's principle for 0.7.0 is that a normal user never
hand-edits that file for basic setup, and a Manager is basic setup.

⚠ **The two keys are the whole risk.** `coordination.managers` is priority
order, which thirty-nine call sites read; `manager_roles` is what each
Manager is FOR. They are parsed independently and are allowed to disagree on
disk, so a writer that updated one and not the other would produce exactly
the configuration `configuration_problems` exists to report — and it would
be rite that wrote it. Most of what follows is about that.
"""

from __future__ import annotations

import yaml
from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.config.managers import (
    CLAUDE,
    DUTIES,
    PRESETS,
    ManagerRole,
    declare_manager,
    effective_duties,
)
from rite_ai.config.parse import ParseError, parse_config


def _project(tmp_path):
    (tmp_path / ".rite").mkdir()
    return tmp_path


def _run(tmp_path, *args):
    runner = CliRunner()
    return runner.invoke(cli, ["add", "manager", *args], catch_exceptions=False)


def _config(tmp_path):
    parsed = parse_config(tmp_path / ".rite" / "config.yaml")
    assert not isinstance(parsed, ParseError), parsed.message
    return parsed


def _raw(tmp_path) -> dict:
    return yaml.safe_load((tmp_path / ".rite" / "config.yaml").read_text())


class TestItWritesBothKeys:
    """The disagreement rite must not author."""

    def test_the_name_lands_in_the_priority_list(self, tmp_path, monkeypatch):
        monkeypatch.chdir(_project(tmp_path))

        assert _run(tmp_path, "lead", "--preset", "lead").exit_code == 0

        assert _raw(tmp_path)["coordination"]["managers"] == ["lead"]

    def test_and_the_declaration_lands_in_manager_roles(self, tmp_path, monkeypatch):
        monkeypatch.chdir(_project(tmp_path))

        _run(tmp_path, "lead", "--preset", "lead")

        roles = _raw(tmp_path)["coordination"]["manager_roles"]
        assert roles == [{"name": "lead", "preset": "lead"}]

    def test_the_two_keys_agree_after_every_add(self, tmp_path, monkeypatch):
        """⚠ The property, not an example of it: whatever is in one key is
        in the other, however many Managers were added."""
        monkeypatch.chdir(_project(tmp_path))
        for name, preset in (("lead", "lead"), ("planner", "planner"), ("odd", "pm")):
            _run(tmp_path, name, "--preset", preset)

        config = _config(tmp_path)

        assert config.coordination.managers == [
            r.name for r in config.coordination.manager_roles
        ]

    def test_rite_doctor_never_reports_the_keys_disagreeing(
        self, tmp_path, monkeypatch
    ):
        """The check that would catch a half-written pair, run for real.

        ⚠ Asserted on the two problems THIS command could author, not on
        an empty list: `configuration_problems` also reports real design
        problems with a set of Managers — a planner whose decompositions
        only its own engine reviews, say — and those are the user's
        choices to answer, not this writer's mistakes.
        """
        from rite_ai.config.managers import configuration_problems

        monkeypatch.chdir(_project(tmp_path))
        for name, preset in (("lead", "lead"), ("planner", "planner"), ("x", "pm")):
            _run(tmp_path, name, "--preset", preset)
        config = _config(tmp_path)

        problems = configuration_problems(
            config.coordination.manager_roles, config.coordination.managers
        )

        assert not [p for p in problems if "not in coordination.managers" in p]
        assert not [p for p in problems if "have no role" in p]


class TestWhatItWritesParses:
    """⚠ Validation is the parser's, spliced-and-reparsed rather than
    copied — so this is the property that matters: nothing this command
    writes can be refused by the next command to read it."""

    def test_a_preset_is_checked_against_the_ones_rite_ships(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(_project(tmp_path))

        result = _run(tmp_path, "scribe", "--preset", "wizard")

        assert result.exit_code == 1
        assert "not one rite ships" in result.output

    def test_a_misspelt_duty_is_refused_rather_than_dropped(
        self, tmp_path, monkeypatch
    ):
        """A closed vocabulary: `plan-reveiw` accepted is a Manager that
        silently skips plan review."""
        monkeypatch.chdir(_project(tmp_path))

        result = _run(tmp_path, "scribe", "--duties", "plan-reveiw")

        assert result.exit_code == 1
        assert "is not a duty rite enforces" in result.output

    def test_a_name_that_would_collide_with_rites_own_state_is_refused(
        self, tmp_path, monkeypatch
    ):
        """C30, enforced where a Manager is DECLARED."""
        monkeypatch.chdir(_project(tmp_path))

        result = _run(tmp_path, "permissions.json", "--preset", "lead")

        assert result.exit_code == 1

    def test_a_secret_cannot_be_smuggled_in_as_a_credential_value(
        self, tmp_path, monkeypatch
    ):
        """`--credential` takes a NAME (SPEC §10). Nothing here should ever
        put a value in the file, and the written file is what is checked."""
        monkeypatch.chdir(_project(tmp_path))

        _run(
            tmp_path,
            "small",
            "--preset",
            "executor",
            "--engine",
            "local:small",
            "--endpoint",
            "http://localhost:11434",
            "--model",
            "qwen3:8b",
            "--agent",
            "goose",
            "--context-window",
            "32768",
            "--credential",
            "ollama_key",
        )

        entry = _raw(tmp_path)["coordination"]["manager_roles"][0]
        assert entry["credential"] == "ollama_key"
        assert "api_key" not in entry and "token" not in entry

    def test_the_file_it_writes_reparses(self, tmp_path, monkeypatch):
        monkeypatch.chdir(_project(tmp_path))
        _run(tmp_path, "lead", "--preset", "lead")

        assert not isinstance(
            parse_config(tmp_path / ".rite" / "config.yaml"), ParseError
        )


class TestPriorityIsNotDisturbed:
    """§2.4: the order IS priority, and the first active Manager is Owner."""

    def test_a_new_manager_is_appended_never_prepended(self, tmp_path, monkeypatch):
        monkeypatch.chdir(_project(tmp_path))
        _run(tmp_path, "lead", "--preset", "lead")

        _run(tmp_path, "planner", "--preset", "planner")

        assert _config(tmp_path).coordination.managers == ["lead", "planner"]

    def test_the_owner_is_the_same_manager_after_adding_five(
        self, tmp_path, monkeypatch
    ):
        """⚠ The one thing adding a Manager must never do to a running
        project. Five, not two: an off-by-one insert shows up in the middle."""
        monkeypatch.chdir(_project(tmp_path))
        _run(tmp_path, "first", "--preset", "lead")
        owner_before = _config(tmp_path).coordination.managers[0]

        for i in range(5):
            _run(tmp_path, f"later{i}", "--preset", "executor")

        names = _config(tmp_path).coordination.managers
        assert names[0] == owner_before == "first"
        assert names == ["first", *[f"later{i}" for i in range(5)]]

    def test_updating_a_manager_leaves_it_where_it_was(self, tmp_path, monkeypatch):
        """Re-declaring the Owner must not send it to the back of the queue."""
        monkeypatch.chdir(_project(tmp_path))
        for name in ("first", "second", "third"):
            _run(tmp_path, name, "--preset", "executor")

        _run(tmp_path, "first", "--preset", "pm")

        assert _config(tmp_path).coordination.managers == ["first", "second", "third"]


class TestTheProjectThatAlreadyHasABareManager:
    """⚠ The case that decides whether this command is usable at all.

    `parse_managers` requires every Manager to declare a preset or duties
    as soon as ONE does. So on a project whose first Manager is a bare name
    — every project written before roles existed — declaring a SECOND one
    fails on the FIRST. Without a way to declare the one that already
    exists, the only route left is hand-editing config.yaml, which is the
    thing this command is for.
    """

    def _bare(self, tmp_path):
        root = _project(tmp_path)
        (root / ".rite" / "config.yaml").write_text(
            "coordination:\n  managers:\n  - lead\n  manager_roles:\n  - lead\n"
        )
        return root

    def test_adding_a_second_one_says_what_is_wrong(self, tmp_path, monkeypatch):
        monkeypatch.chdir(self._bare(tmp_path))

        result = _run(tmp_path, "planner", "--preset", "planner")

        assert result.exit_code == 1
        assert "declare no preset and no duties" in result.output

    def test_and_names_the_command_that_fixes_it(self, tmp_path, monkeypatch):
        """⚠ Without this line the only remaining route is the file."""
        monkeypatch.chdir(self._bare(tmp_path))

        result = _run(tmp_path, "planner", "--preset", "planner")

        assert "rite add manager lead --preset" in result.output

    def test_and_following_that_advice_works(self, tmp_path, monkeypatch):
        """The remedy is run, not just printed."""
        monkeypatch.chdir(self._bare(tmp_path))

        assert _run(tmp_path, "lead", "--preset", "lead").exit_code == 0
        assert _run(tmp_path, "planner", "--preset", "planner").exit_code == 0

        config = _config(tmp_path)
        assert config.coordination.managers == ["lead", "planner"]
        assert [r.preset for r in config.coordination.manager_roles] == [
            "lead",
            "planner",
        ]

    def test_a_bare_duplicate_is_still_refused(self, tmp_path, monkeypatch):
        """Updating is for a declaration. `add` with nothing to declare is
        a user who meant a different name."""
        monkeypatch.chdir(self._bare(tmp_path))

        result = _run(tmp_path, "lead")

        assert result.exit_code == 1
        assert "already declared" in result.output


class TestItDoesNotEatTheRestOfTheFile:
    """`config_to_yaml` round-trips the WHOLE file; a section this path
    drops is one it silently deletes from a real project."""

    def test_an_unrelated_section_survives(self, tmp_path, monkeypatch):
        root = _project(tmp_path)
        (root / ".rite" / "config.yaml").write_text(
            "slack:\n  owner_user: U123\n  broadcast_channel: C456\n"
        )
        monkeypatch.chdir(root)

        _run(tmp_path, "lead", "--preset", "lead")

        config = _config(tmp_path)
        assert config.slack.owner_user == "U123"
        assert config.slack.broadcast_channel == "C456"

    def test_a_manager_listed_without_a_role_is_not_dropped(
        self, tmp_path, monkeypatch
    ):
        """⚠ The file is ALLOWED to hold a listed Manager with no role, and
        `rite doctor` reports it. Rebuilding the name list from the roles
        would delete it instead — a repair nobody asked for, made by a
        command about something else."""
        root = _project(tmp_path)
        (root / ".rite" / "config.yaml").write_text(
            "coordination:\n  managers:\n  - ghost\n  manager_roles: []\n"
        )
        monkeypatch.chdir(root)

        _run(tmp_path, "lead", "--preset", "lead")

        assert "ghost" in _config(tmp_path).coordination.managers


class TestTheInvariantAcrossEveryPresetAndDuty:
    """⚠ **Across the full range, not two extremes.**

    A preset is a named set of duties, and what a Manager HOLDS is what
    every gate keys on. A declaration that parses but whose duties are not
    the preset's is a Manager that silently skips a gate — the failure the
    closed vocabulary exists to prevent — and it would show up on one
    preset in the middle, not on the first or the last.
    """

    def test_every_preset_declares_exactly_its_duties(self, tmp_path, monkeypatch):
        monkeypatch.chdir(_project(tmp_path))

        for i, preset in enumerate(sorted(PRESETS)):
            name = f"m{i}"
            result = _run(tmp_path, name, "--preset", preset)
            assert result.exit_code == 0, f"{preset}: {result.output}"

        config = _config(tmp_path)
        by_name = {r.name: r for r in config.coordination.manager_roles}
        declared = len(config.coordination.manager_roles)
        for i, preset in enumerate(sorted(PRESETS)):
            role = by_name[f"m{i}"]
            assert role.preset == preset
            assert effective_duties(role, declared) == frozenset(PRESETS[preset]), (
                f"preset {preset!r} does not hold its own duties"
            )

    def test_every_duty_can_be_declared_on_its_own(self, tmp_path, monkeypatch):
        """All nine, so a vocabulary entry that cannot survive the CLI is
        found here rather than by the user who needs that one."""
        monkeypatch.chdir(_project(tmp_path))

        for i, duty in enumerate(DUTIES):
            result = _run(tmp_path, f"d{i}", "--duties", duty)
            assert result.exit_code == 0, f"{duty}: {result.output}"

        by_name = {r.name: r for r in _config(tmp_path).coordination.manager_roles}
        for i, duty in enumerate(DUTIES):
            assert by_name[f"d{i}"].duties == (duty,), duty

    def test_every_pair_of_duties_round_trips_in_the_order_given(
        self, tmp_path, monkeypatch
    ):
        """Duties are a list, and a set would lose the order the file shows
        the reader. Every adjacent pair, so the middle of the vocabulary is
        covered and not only its ends."""
        monkeypatch.chdir(_project(tmp_path))
        pairs = list(zip(DUTIES, DUTIES[1:]))

        for i, (a, b) in enumerate(pairs):
            assert _run(tmp_path, f"p{i}", "--duties", f"{a},{b}").exit_code == 0

        by_name = {r.name: r for r in _config(tmp_path).coordination.manager_roles}
        for i, (a, b) in enumerate(pairs):
            assert by_name[f"p{i}"].duties == (a, b), f"{a},{b}"

    def test_the_whole_range_lands_in_one_file_that_parses(self, tmp_path, monkeypatch):
        """Every preset and every duty declared into ONE config, which is
        the state a per-case test never reaches."""
        monkeypatch.chdir(_project(tmp_path))
        for i, preset in enumerate(sorted(PRESETS)):
            _run(tmp_path, f"m{i}", "--preset", preset)
        for i, duty in enumerate(DUTIES):
            _run(tmp_path, f"d{i}", "--duties", duty)

        config = _config(tmp_path)

        assert len(config.coordination.manager_roles) == len(PRESETS) + len(DUTIES)
        assert config.coordination.managers == [
            r.name for r in config.coordination.manager_roles
        ]


class TestTheFunctionUnderneath:
    """`declare_manager` without the CLI, for the cases the CLI cannot
    reach and for the ones easier to state here."""

    def test_it_refuses_rather_than_guessing_on_a_bad_engine(self):
        declared = declare_manager([], [], "m", engine="gpt")

        assert "is not one rite knows" in declared.error
        assert declared.roles == []

    def test_a_local_engine_must_carry_its_configuration(self):
        """The class is a label, not a configuration (design §3.1)."""
        declared = declare_manager([], [], "m", engine="local:small", preset="executor")

        assert "must also say" in declared.error

    def test_a_local_engine_with_no_duties_is_told_how_to_declare_them(self):
        """⚠ An engine is not a declaration. Giving one and no duties is
        refused, and the CLI turns that into the flag to add — without it
        the user is left with the file again."""
        declared = declare_manager(
            [],
            [],
            "m",
            engine="local:small",
            endpoint="http://x",
            model="q",
            agent="goose",
            context_window=32768,
        )

        assert "declare no preset and no duties" in declared.error

    def test_an_error_returns_no_partial_write(self):
        """⚠ The caller writes whatever comes back. A refusal that also
        carried half a list would be a config.yaml written from a refusal."""
        declared = declare_manager(
            ["lead"], [ManagerRole(name="lead")], "x", preset="nope"
        )

        assert declared.error
        assert declared.names == [] and declared.roles == []

    def test_a_plain_claude_manager_is_not_written_as_an_engine_key(self):
        """`claude` is the default, and writing it back would rewrite every
        existing file's entries for nothing."""
        declared = declare_manager([], [], "m", preset="lead")

        assert declared.roles[0].engine == CLAUDE
        from rite_ai.config.managers import to_yaml_entry

        assert "engine" not in to_yaml_entry(declared.roles[0])
