"""`modules.yaml` records a module's commands, and refuses keys it does not know.

`Module` had no field for commands, so the only place to correct a detected one
was a note under `.rite/context/`. Writing `test: swift test` into modules.yaml
looked as if it worked: `parse_modules` ignored the key, and the next
`rite add module` rewrote the file without it. A key accepted and discarded is
CFG-1's defect class — this is a real field, and a parser that says when a key
means nothing.
"""

from pathlib import Path

import yaml

from rite_ai.cli.init import detect
from rite_ai.cli.init.claude_gen import generate_claude_md
from rite_ai.cli.init.scaffold import config_to_yaml, modules_to_yaml
from rite_ai.config.models import (
    Module,
    ProjectBrief,
    ProjectConfig,
    RecordedCommands,
    SandboxConfig,
)
from rite_ai.config.parse import ParseError, parse_modules
from rite_ai.workspace.manage import add_module, add_worker, remove_module

_PACKAGE = 'let package = Package(name: "NewsKit")\n'


def _modules_file(root: Path, text: str) -> Path:
    path = root / ".rite" / "modules.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _project(root: Path, modules: list[Module], sandbox: SandboxConfig) -> Path:
    rite = root / ".rite"
    rite.mkdir(parents=True)
    (rite / "modules.yaml").write_text(modules_to_yaml(modules))
    (rite / "config.yaml").write_text(config_to_yaml(ProjectConfig(sandbox=sandbox)))
    for m in modules:
        (root / m.path).mkdir(parents=True, exist_ok=True)
    return root


# --- the field ----------------------------------------------------------------


def test_recorded_commands_survive_the_round_trip(tmp_path: Path):
    modules = [
        Module(
            name="app",
            path="app/",
            commands=RecordedCommands(
                test="make check", lint="swiftlint", format="swift format -i -r ."
            ),
        ),
        Module(name="docs", path="docs/"),
    ]
    path = _modules_file(tmp_path, modules_to_yaml(modules))
    assert parse_modules(path) == modules


def test_nothing_recorded_writes_no_commands_key(tmp_path: Path):
    data = yaml.safe_load(modules_to_yaml([Module(name="app", path="app/")]))
    assert "commands" not in data["modules"]["app"]


def test_a_v0_1_0_modules_yaml_still_parses(tmp_path: Path):
    """Every key a v0.1.0 project can hold — measured against a pristine
    `rite init --yes` (`modules: {}`) and the entry `rite add module` writes."""
    assert parse_modules(_modules_file(tmp_path, "modules: {}\n")) == []
    path = _modules_file(
        tmp_path,
        "modules:\n  backend:\n    path: backend/\n"
        "    url: git@github.com:org/backend.git\n"
        "    branch: main\n    description: API\n",
    )
    assert [m.name for m in parse_modules(path)] == ["backend"]


# --- keys that mean nothing ---------------------------------------------------


def test_a_misspelled_key_is_refused_and_named(tmp_path: Path):
    path = _modules_file(
        tmp_path, "modules:\n  app:\n    path: app/\n    descripton: iOS\n"
    )
    result = parse_modules(path)
    assert isinstance(result, ParseError)
    assert "descripton" in result.message
    assert "did you mean 'description'" in result.message


def test_a_command_written_flat_says_where_it_belongs(tmp_path: Path):
    path = _modules_file(
        tmp_path, "modules:\n  app:\n    path: app/\n    test: swift test\n"
    )
    result = parse_modules(path)
    assert isinstance(result, ParseError)
    assert "'test'" in result.message
    assert "commands:" in result.message


def test_an_unknown_command_is_refused(tmp_path: Path):
    path = _modules_file(
        tmp_path,
        "modules:\n  app:\n    path: app/\n    commands:\n      tests: swift test\n",
    )
    result = parse_modules(path)
    assert isinstance(result, ParseError)
    assert "did you mean 'test'" in result.message


def test_a_command_that_is_not_text_is_refused(tmp_path: Path):
    path = _modules_file(
        tmp_path,
        "modules:\n  app:\n    path: app/\n    commands:\n      test: [swift, test]\n",
    )
    assert isinstance(parse_modules(path), ParseError)


def test_a_refused_file_is_left_exactly_as_it_was(tmp_path: Path):
    """The property that makes refusing safe: no writer overwrites the file it
    could not read, so the key its author wrote is still there to fix."""
    text = "modules:\n  app:\n    path: app/\n    test: swift test\n"
    path = _modules_file(tmp_path, text)

    added = add_module(tmp_path, "other")
    removed = remove_module(tmp_path, "app")

    assert not added.ok and "cannot parse modules.yaml" in added.message
    assert not removed.ok and "cannot parse modules.yaml" in removed.message
    assert path.read_text() == text


# --- who reads it -------------------------------------------------------------


def test_a_recorded_command_wins_for_its_own_key_only(tmp_path: Path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "Package.swift").write_text(_PACKAGE)
    module = Module(
        name="app", path="app/", commands=RecordedCommands(test="make check")
    )

    cmds = detect.module_commands(module, tmp_path, SandboxConfig(enabled=False))

    assert cmds.test == "make check"
    assert cmds.build == "swift build"
    assert cmds.source == "modules.yaml + Package.swift"


def test_the_root_claude_md_renders_the_recorded_command(tmp_path: Path):
    (tmp_path / "app").mkdir()
    modules = [
        Module(name="app", path="app/", commands=RecordedCommands(test="make check"))
    ]
    brief = ProjectBrief(name="news", role="owner")
    md = generate_claude_md("owner", brief, modules, ProjectConfig(), tmp_path)
    assert "- Test: `make check`" in md


def test_a_worker_sees_a_command_recorded_after_init(tmp_path: Path):
    """The root CLAUDE.md is written once, by `rite init`. A Worker's is written
    when the Worker is, so it is the one that can see a later correction."""
    modules = [
        Module(name="app", path="app/", commands=RecordedCommands(test="make check"))
    ]
    root = _project(tmp_path, modules, SandboxConfig(enabled=False))

    add_worker(root, "alpha")

    md = (root / "workers" / "alpha" / "CLAUDE.md").read_text()
    commands = md[md.index("## Module commands") : md.index("## Workflow")]
    assert "### `app/`" in commands
    assert "- Test: `make check`" in commands


def test_a_worker_s_swift_commands_follow_the_sandbox_setting(tmp_path: Path):
    for enabled in (True, False):
        root = tmp_path / str(enabled)
        root = _project(
            root, [Module(name="app", path="app/")], SandboxConfig(enabled=enabled)
        )
        (root / "app" / "Package.swift").write_text(_PACKAGE)

        add_worker(root, "alpha")

        md = (root / "workers" / "alpha" / "CLAUDE.md").read_text()
        expected = "swift test --disable-sandbox" if enabled else "swift test`"
        assert expected in md


def test_a_worker_with_no_modules_still_has_the_section_step_4_points_to(
    tmp_path: Path,
):
    root = _project(tmp_path, [], SandboxConfig())
    add_worker(root, "alpha")
    md = (root / "workers" / "alpha" / "CLAUDE.md").read_text()
    assert "## Module commands" in md
    assert "_(no modules)_" in md


# --- a configured command survives everything that rewrites modules.yaml ------


def _with_recorded_commands(root: Path) -> Path:
    return _modules_file(
        root,
        "modules:\n"
        "  app:\n"
        "    path: app/\n"
        "    branch: main\n"
        "    description: iOS client\n"
        "    commands:\n"
        "      test: make check\n"
        "      format: swift format -i -r .\n",
    )


def test_adding_a_module_keeps_every_other_modules_commands(tmp_path: Path):
    path = _with_recorded_commands(tmp_path)

    assert add_module(tmp_path, "backend").ok

    app = next(m for m in parse_modules(path) if m.name == "app")
    assert app.commands == RecordedCommands(
        test="make check", format="swift format -i -r ."
    )


def test_removing_a_module_keeps_the_rest_of_the_commands(tmp_path: Path):
    path = _with_recorded_commands(tmp_path)
    assert add_module(tmp_path, "backend").ok

    assert remove_module(tmp_path, "backend").ok

    app = next(m for m in parse_modules(path) if m.name == "app")
    assert app.commands.test == "make check"
