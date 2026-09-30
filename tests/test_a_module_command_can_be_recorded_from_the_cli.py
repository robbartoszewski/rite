"""`rite module set-command` — correcting a module's commands (S24).

Detection derives a module's install/build/test/lint/format from its own
manifests. It is right for most modules and wrong for some, and until now
the only way to correct it was to open `.rite/modules.yaml` — which Robert's
principle for 0.7.0 rules out for basic setup.

🔴 **Two things had to be true, and the second is the one that bites.**

1. A recorded command must be written **nested under `commands:`**. A module
   entry refuses an unknown key, and `test:` at the top level is one —
   measured, and pinned below. A writer that wrote it flat would leave a
   `modules.yaml` no later command can read.
2. A module's commands are quoted into the project's `CLAUDE.md` and into
   every Worker's. Recording a correction that never reached those files
   would leave the agent reading the old command out of its instructions —
   the correction made, and nothing behaving differently.
"""

from __future__ import annotations

import yaml
from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.config.parse import ParseError, parse_modules
from rite_ai.workspace.manage import command_keys, set_module_command

COMMANDS = {
    "install": "uv sync --frozen",
    "build": "make build",
    "test": "pytest -q --strict-markers",
    "lint": "ruff check .",
    "format": "ruff format .",
}


def _project(tmp_path, *modules: str):
    rite = tmp_path / ".rite"
    rite.mkdir()
    entries = "\n".join(
        f"  {name}:\n    path: {name}\n    branch: main\n    description: ''"
        for name in modules
    )
    (rite / "modules.yaml").write_text(
        f"modules:\n{entries}\n" if modules else "modules: {}\n"
    )
    return tmp_path


def _run(*args):
    return CliRunner().invoke(
        cli, ["module", "set-command", *args], catch_exceptions=False
    )


def _modules(tmp_path):
    parsed = parse_modules(tmp_path / ".rite" / "modules.yaml")
    assert not isinstance(parsed, ParseError), parsed.message
    return {m.name: m for m in parsed}


def _raw(tmp_path) -> dict:
    return yaml.safe_load((tmp_path / ".rite" / "modules.yaml").read_text())


class TestItIsWrittenWhereTheParserLooks:
    """⚠ The shape, which is not a matter of taste — flat is rejected."""

    def test_the_command_is_nested_under_commands(self, tmp_path, monkeypatch):
        monkeypatch.chdir(_project(tmp_path, "backend"))

        _run("backend", "test", "pytest -q")

        entry = _raw(tmp_path)["modules"]["backend"]
        assert entry["commands"] == {"test": "pytest -q"}

    def test_and_never_at_the_top_level_of_the_entry(self, tmp_path, monkeypatch):
        monkeypatch.chdir(_project(tmp_path, "backend"))

        _run("backend", "test", "pytest -q")

        assert "test" not in _raw(tmp_path)["modules"]["backend"]

    def test_the_flat_shape_really_is_refused(self, tmp_path):
        """⚠ **The claim this whole file rests on, measured rather than
        trusted.** If a flat key ever became legal, the test above would
        still pass while saying nothing."""
        (tmp_path / "modules.yaml").write_text(
            "modules:\n  backend:\n    path: backend\n    test: pytest\n"
        )

        parsed = parse_modules(tmp_path / "modules.yaml")

        assert isinstance(parsed, ParseError)
        assert "unknown key 'test'" in parsed.message

    def test_what_it_writes_parses_back(self, tmp_path, monkeypatch):
        monkeypatch.chdir(_project(tmp_path, "backend"))

        _run("backend", "test", "pytest -q")

        assert _modules(tmp_path)["backend"].commands.test == "pytest -q"


class TestItRefreshesWhatQuotesTheCommand:
    """The half that makes the correction reach the agent."""

    def _real_project(self, tmp_path, monkeypatch):
        """A project as `rite init` leaves it, because the refresher reads
        brief.yaml and config.yaml and renders the real CLAUDE.md."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "backend").mkdir()
        (tmp_path / "backend" / "pyproject.toml").write_text("[project]\n")
        runner = CliRunner()
        result = runner.invoke(cli, ["init", "--yes"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert runner.invoke(cli, ["add", "module", "backend"]).exit_code == 0
        return tmp_path

    def test_the_project_claude_md_quotes_the_recorded_command(
        self, tmp_path, monkeypatch
    ):
        root = self._real_project(tmp_path, monkeypatch)

        result = _run("backend", "test", "pytest -q --strict-markers")

        assert result.exit_code == 0, result.output
        assert "pytest -q --strict-markers" in (root / "CLAUDE.md").read_text()

    def test_and_says_which_section_it_refreshed(self, tmp_path, monkeypatch):
        self._real_project(tmp_path, monkeypatch)

        result = _run("backend", "test", "pytest -q")

        assert "refreshed CLAUDE.md: Modules" in result.output

    def test_the_file_marks_it_as_recorded_rather_than_detected(
        self, tmp_path, monkeypatch
    ):
        """⚠ A recorded command is a claim about the module, not a
        measurement of it, and the instructions say which it is."""
        root = self._real_project(tmp_path, monkeypatch)

        _run("backend", "test", "pytest -q")

        assert "(recorded in modules.yaml)" in (root / "CLAUDE.md").read_text()

    def test_a_hand_edited_section_is_kept_AND_said(self, tmp_path, monkeypatch):
        """⚠ Keeping it is the right rule. Staying quiet about it is not:
        the user would believe a command they just recorded had reached
        instructions it never did."""
        root = self._real_project(tmp_path, monkeypatch)
        text = (root / "CLAUDE.md").read_text()
        edited = text.replace(
            "## Modules", "## Modules\n\nOur own notes about the modules.", 1
        )
        (root / "CLAUDE.md").write_text(edited)

        result = _run("backend", "test", "pytest -q")

        assert result.exit_code == 0
        assert "NOT refreshed CLAUDE.md: Modules" in result.output
        assert "edited by hand" in result.output
        assert "Our own notes about the modules." in (root / "CLAUDE.md").read_text()

    def test_a_file_rite_cannot_read_is_not_called_a_hand_edit(
        self, tmp_path, monkeypatch
    ):
        """⚠ An early draft reported a broken `brief.yaml` under "edited by
        hand", sending the user to look for an edit they never made."""
        root = self._real_project(tmp_path, monkeypatch)
        (root / ".rite" / "brief.yaml").write_text("role: nonsense\n")

        result = _run("backend", "test", "pytest -q")

        assert result.exit_code == 0, "the command IS recorded either way"
        assert "does not parse" in result.output
        assert "edited by hand" not in result.output

    def test_a_refresh_problem_never_loses_the_write(self, tmp_path, monkeypatch):
        """The two halves are separable, and the recorded command is the
        half the user asked for."""
        root = self._real_project(tmp_path, monkeypatch)
        (root / ".rite" / "brief.yaml").write_text("role: nonsense\n")

        _run("backend", "test", "pytest -q")

        assert _modules(root)["backend"].commands.test == "pytest -q"


class TestItRefuses:
    def test_a_key_that_is_not_a_command_rite_records(self, tmp_path, monkeypatch):
        monkeypatch.chdir(_project(tmp_path, "backend"))

        result = _run("backend", "deploy", "make deploy")

        assert result.exit_code == 1
        assert "is not a command a module records" in result.output
        assert "install, build, test, lint, format" in result.output

    def test_an_unknown_module_and_names_the_ones_there_are(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(_project(tmp_path, "backend", "frontend"))

        result = _run("nope", "test", "pytest")

        assert result.exit_code == 1
        assert "no such module" in result.output
        assert "backend" in result.output and "frontend" in result.output

    def test_it_writes_nothing_when_it_refuses(self, tmp_path, monkeypatch):
        monkeypatch.chdir(_project(tmp_path, "backend"))
        before = (tmp_path / ".rite" / "modules.yaml").read_text()

        _run("backend", "deploy", "make deploy")

        assert (tmp_path / ".rite" / "modules.yaml").read_text() == before

    def test_a_project_that_was_never_initialised(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".rite").mkdir()
        (tmp_path / ".rite" / "modules.yaml").write_text("modules: {}\n")

        result = _run("backend", "test", "pytest")

        assert result.exit_code == 1
        assert "none are registered" in result.output


class TestUnrecording:
    """`None` means "fall back to detection", which is not the same as an
    empty command — and it is the only way back once a correction is in."""

    def test_an_empty_command_removes_the_key(self, tmp_path, monkeypatch):
        monkeypatch.chdir(_project(tmp_path, "backend"))
        _run("backend", "test", "pytest -q")

        _run("backend", "test", "")

        assert _modules(tmp_path)["backend"].commands.test is None

    def test_and_the_commands_block_goes_with_the_last_one(self, tmp_path, monkeypatch):
        """`commands: {}` under every module would rewrite every existing
        modules.yaml to say nothing — the serialiser's own rule."""
        monkeypatch.chdir(_project(tmp_path, "backend"))
        _run("backend", "test", "pytest -q")

        _run("backend", "test", "")

        assert "commands" not in _raw(tmp_path)["modules"]["backend"]

    def test_the_field_is_None_and_not_an_empty_string(self, tmp_path):
        """⚠ **Through the file these are the same thing**, because the
        serialiser drops a falsy value and the parser reads a missing key
        as None — so every file-level test above passes either way. In
        memory they are not the same: `None` means "fall back to
        detection", `""` means "this module has a command and it is
        nothing", and a caller reading the returned module would act on
        the difference. Asserted here because nothing else can see it."""
        _project(tmp_path, "backend")
        set_module_command(tmp_path, "backend", "test", "pytest")

        result = set_module_command(tmp_path, "backend", "test", "")

        assert result.module is not None
        assert result.module.commands.test is None
        assert result.module.commands.test != ""

    def test_a_command_of_only_spaces_unrecords_rather_than_recording_blanks(
        self, tmp_path
    ):
        """`"   "` is a user clearing it, not a command made of spaces."""
        _project(tmp_path, "backend")
        set_module_command(tmp_path, "backend", "test", "pytest")

        result = set_module_command(tmp_path, "backend", "test", "   ")

        assert result.module is not None
        assert result.module.commands.test is None

    def test_it_says_which_it_did(self, tmp_path, monkeypatch):
        monkeypatch.chdir(_project(tmp_path, "backend"))

        assert "recorded test" in _run("backend", "test", "pytest").output
        assert "unrecorded test" in _run("backend", "test", "").output


class TestItLeavesEverythingElseAlone:
    def test_another_module_is_untouched(self, tmp_path, monkeypatch):
        monkeypatch.chdir(_project(tmp_path, "backend", "frontend"))
        _run("frontend", "build", "npm run build")

        _run("backend", "test", "pytest -q")

        assert _modules(tmp_path)["frontend"].commands.build == "npm run build"

    def test_the_modules_other_fields_survive(self, tmp_path, monkeypatch):
        root = _project(tmp_path, "backend")
        (root / ".rite" / "modules.yaml").write_text(
            "modules:\n  backend:\n    path: services/api\n    branch: develop\n"
            "    description: the API\n"
        )
        monkeypatch.chdir(root)

        _run("backend", "test", "pytest -q")

        module = _modules(tmp_path)["backend"]
        assert (module.path, module.branch, module.description) == (
            "services/api",
            "develop",
            "the API",
        )

    def test_a_publish_override_survives(self, tmp_path, monkeypatch):
        """Written only when overridden, so a writer that rebuilt the entry
        from defaults would silently drop it."""
        root = _project(tmp_path, "backend")
        (root / ".rite" / "modules.yaml").write_text(
            "modules:\n  backend:\n    path: backend\n    publish:\n"
            "      strategy: pull_request\n"
        )
        monkeypatch.chdir(root)

        _run("backend", "test", "pytest -q")

        assert _modules(tmp_path)["backend"].publish.strategy == "pull_request"


class TestTheInvariantAcrossEveryCommandKey:
    """⚠ **Every key, not the first and the last.**

    The five keys are five separate fields on `RecordedCommands` and five
    separate lines in the rendered instructions. A key that the writer sets
    and the serialiser drops — or that the renderer does not quote — shows
    up on one key in the middle, and `test` alone would never find it.
    """

    def test_every_key_can_be_recorded(self, tmp_path, monkeypatch):
        monkeypatch.chdir(_project(tmp_path, "backend"))

        for key in command_keys():
            result = _run("backend", key, COMMANDS[key])
            assert result.exit_code == 0, f"{key}: {result.output}"

        recorded = _modules(tmp_path)["backend"].commands
        for key in command_keys():
            assert getattr(recorded, key) == COMMANDS[key], key

    def test_every_key_round_trips_through_the_file_together(
        self, tmp_path, monkeypatch
    ):
        """All five in one entry, which no per-key test reaches."""
        monkeypatch.chdir(_project(tmp_path, "backend"))
        for key in command_keys():
            _run("backend", key, COMMANDS[key])

        assert _raw(tmp_path)["modules"]["backend"]["commands"] == COMMANDS

    def test_every_key_can_be_changed_then_unrecorded(self, tmp_path, monkeypatch):
        """Set → change → clear, for each key, so a writer that can only
        add is caught on every one of them."""
        monkeypatch.chdir(_project(tmp_path, "backend"))

        for key in command_keys():
            _run("backend", key, COMMANDS[key])
            _run("backend", key, f"{COMMANDS[key]} --changed")
            assert getattr(_modules(tmp_path)["backend"].commands, key) == (
                f"{COMMANDS[key]} --changed"
            ), key
            _run("backend", key, "")
            assert getattr(_modules(tmp_path)["backend"].commands, key) is None, key

    def test_recording_one_key_never_disturbs_the_others(self, tmp_path, monkeypatch):
        """Each key is set, then every OTHER key is re-checked — the
        middle of the range is where a shared-mutable-default bug lands."""
        monkeypatch.chdir(_project(tmp_path, "backend", "frontend"))
        for key in command_keys():
            _run("backend", key, COMMANDS[key])

        for key in command_keys():
            _run("backend", key, "changed")
            recorded = _modules(tmp_path)["backend"].commands
            for other in command_keys():
                if other != key:
                    assert getattr(recorded, other) in (
                        COMMANDS[other],
                        "changed",
                    ), f"setting {key} disturbed {other}"

    def test_the_key_list_comes_from_the_dataclass(self):
        """⚠ A key added to `RecordedCommands` and not here is one this
        command could not set, and nothing would say so."""
        from dataclasses import fields

        from rite_ai.config.models import RecordedCommands

        assert command_keys() == tuple(f.name for f in fields(RecordedCommands))
        assert set(COMMANDS) == set(command_keys()), "this test's own table drifted"


class TestTheFunctionUnderneath:
    def test_it_reports_the_module_it_changed(self, tmp_path):
        _project(tmp_path, "backend")

        result = set_module_command(tmp_path, "backend", "test", "pytest")

        assert result.ok and result.module is not None
        assert result.module.name == "backend"

    def test_a_missing_rite_directory_is_said_plainly(self, tmp_path):
        result = set_module_command(tmp_path, "backend", "test", "pytest")

        assert not result.ok
        assert "run `rite init` first" in result.message

    def test_an_unparseable_modules_file_is_reported_as_itself(self, tmp_path):
        (tmp_path / ".rite").mkdir()
        (tmp_path / ".rite" / "modules.yaml").write_text("modules: [not, a, mapping]\n")

        result = set_module_command(tmp_path, "backend", "test", "pytest")

        assert not result.ok
        assert "cannot parse modules.yaml" in result.message
