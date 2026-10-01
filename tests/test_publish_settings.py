"""Which publish strategy is in force, per module, and which are refused (PB1).

The property: **the settings rite will publish under are the ones printed**,
per module, with where each came from. `push_to_shared` is accepted in
config and refused at every start. It is also refused again at the publish
step, which PB1's next piece builds; `_unavailable` is the one text both use.

What is real here: the parser, the writers, and the CLI entry points run
through Click. Nothing reaches a sandbox or a network.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from rite_ai.cli.init.scaffold import config_to_yaml, modules_to_yaml
from rite_ai.cli.main import cli
from rite_ai.config.models import Module, ModulePublish, PublishConfig
from rite_ai.config.parse import ParseError, parse_config, parse_modules
from rite_ai.publishing.settings import (
    MODULE,
    PROJECT,
    _unavailable,
    effective,
    refusals,
)


def _config(tmp_path: Path, text: str):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return parse_config(path)


def _modules(tmp_path: Path, text: str):
    path = tmp_path / "modules.yaml"
    path.write_text(text)
    return parse_modules(path)


# --- parsing ------------------------------------------------------------------------


def test_the_default_is_a_pull_request_with_nothing_opted_in(tmp_path):
    parsed = _config(tmp_path, "ticket_backend:\n  type: none\n")
    assert parsed.publish == PublishConfig("pull_request", False, False)


@pytest.mark.parametrize(
    "strategy", ["commit", "push", "pull_request", "push_to_shared"]
)
def test_every_named_strategy_parses(tmp_path, strategy):
    """`push_to_shared` included: it is refused at START, by name, not by the
    parser as if it were a typo."""
    parsed = _config(tmp_path, f"publish:\n  strategy: {strategy}\n")
    assert not isinstance(parsed, ParseError), parsed
    assert parsed.publish.strategy == strategy


def test_a_misspelt_strategy_is_refused_and_named(tmp_path):
    parsed = _config(tmp_path, "publish:\n  strategy: pull-request\n")
    assert isinstance(parsed, ParseError)
    assert "'pull-request'" in parsed.message
    assert "did you mean 'pull_request'" in parsed.message


def test_a_number_is_not_a_strategy(tmp_path):
    """The release plan's history says "strategy 2"; the file must not."""
    parsed = _config(tmp_path, "publish:\n  strategy: 2\n")
    assert isinstance(parsed, ParseError)


@pytest.mark.parametrize("key", ["squash", "auto_merge"])
@pytest.mark.parametrize("value", ['"no"', '"false"', "1", "0"])
def test_a_switch_must_be_true_or_false(tmp_path, key, value):
    """`squash: "no"` is a truthy string; read by truthiness it would squash
    a project that said no."""
    parsed = _config(tmp_path, f"publish:\n  {key}: {value}\n")
    assert isinstance(parsed, ParseError), parsed
    assert f"publish.{key} must be true or false" in parsed.message


def test_an_unknown_key_is_refused_not_ignored(tmp_path):
    parsed = _config(tmp_path, "publish:\n  auto_merg: true\n")
    assert isinstance(parsed, ParseError)
    assert "did you mean 'auto_merge'" in parsed.message


def test_shared_repo_is_a_module_key_not_a_project_one(tmp_path):
    parsed = _config(tmp_path, "publish:\n  shared_repo: git@x:y/z.git\n")
    assert isinstance(parsed, ParseError)
    assert "unknown key 'shared_repo'" in parsed.message


@pytest.mark.parametrize("strategy", ["commit", "push", "push_to_shared"])
def test_auto_merge_written_beside_a_strategy_that_never_merges_is_refused(
    tmp_path, strategy
):
    parsed = _config(
        tmp_path, f"publish:\n  strategy: {strategy}\n  auto_merge: true\n"
    )
    assert isinstance(parsed, ParseError)
    assert "only a pull request is ever merged" in parsed.message


def test_a_module_override_parses_key_by_key(tmp_path):
    parsed = _modules(
        tmp_path,
        "modules:\n  svc:\n    path: svc/\n    publish:\n      strategy: commit\n",
    )
    assert parsed[0].publish == ModulePublish(strategy="commit")


def test_a_malformed_module_override_is_refused_naming_the_module(tmp_path):
    parsed = _modules(
        tmp_path,
        "modules:\n  svc:\n    path: svc/\n    publish:\n      strategy: comit\n",
    )
    assert isinstance(parsed, ParseError)
    assert (
        "module 'svc'" in parsed.message and "did you mean 'commit'" in parsed.message
    )


# --- writing it back ----------------------------------------------------------------


def test_config_round_trips_the_publish_block(tmp_path):
    written = PublishConfig(strategy="commit", squash=True, auto_merge=False)
    first = _config(tmp_path, "publish:\n  strategy: commit\n  squash: true\n")
    assert first.publish == written
    again = _config(tmp_path, config_to_yaml(first))
    assert again.publish == written


def test_a_module_without_an_override_is_written_without_one(tmp_path):
    """`publish: {}` under every module would rewrite every modules.yaml."""
    text = modules_to_yaml([Module(name="svc", path="svc/")])
    assert "publish" not in text


def test_a_module_override_survives_a_rewrite(tmp_path):
    module = Module(name="svc", path="svc/", publish=ModulePublish(squash=True))
    again = _modules(tmp_path, modules_to_yaml([module]))
    assert again[0].publish == ModulePublish(squash=True)


# --- resolution ---------------------------------------------------------------------


def test_an_override_wins_for_its_own_key_only():
    project = PublishConfig(strategy="pull_request", squash=True, auto_merge=True)
    module = Module(name="svc", path="svc/", publish=ModulePublish(strategy="commit"))
    got = effective(project, module)
    assert (got.strategy, got.squash, got.auto_merge) == ("commit", True, True)
    assert got.source == {"strategy": MODULE, "squash": PROJECT, "auto_merge": PROJECT}


def test_an_inherited_auto_merge_says_it_does_not_apply_and_never_merges():
    project = PublishConfig(strategy="pull_request", auto_merge=True)
    module = Module(name="svc", path="svc/", publish=ModulePublish(strategy="push"))
    got = effective(project, module)
    assert not got.merges
    assert "auto_merge does not apply under push" in got.describe()


def test_an_override_false_is_not_an_absent_override():
    """`None` means "not overridden"; `False` is a value. Collapsing the two
    would let a module that turned auto-merge OFF inherit the project's ON."""
    project = PublishConfig(auto_merge=True)
    module = Module(name="svc", path="svc/", publish=ModulePublish(auto_merge=False))
    got = effective(project, module)
    assert got.auto_merge is False and got.source["auto_merge"] == MODULE
    assert not got.merges


# --- refused at start ---------------------------------------------------------------


def test_push_to_shared_is_refused_with_the_release_that_brings_it():
    text = _unavailable("push_to_shared")
    assert "not available until rite v0.8.0" in text
    assert "commit, push or pull_request" in text  # the remedy is in it
    for strategy in ("commit", "push", "pull_request"):
        assert _unavailable(strategy) == ""


def test_refusals_name_the_project_and_each_overriding_module():
    from rite_ai.config.models import ProjectConfig

    config = ProjectConfig(publish=PublishConfig(strategy="push_to_shared"))
    modules = [
        Module(name="inherits", path="a/"),
        Module(name="opts_out", path="b/", publish=ModulePublish(strategy="commit")),
    ]
    found = refusals(config, modules)
    assert len(found) == 1 and found[0].startswith("config.yaml:")

    config = ProjectConfig()
    modules = [
        Module(name="svc", path="s/", publish=ModulePublish(strategy="push_to_shared"))
    ]
    found = refusals(config, modules)
    assert found == [f"module 'svc': {_unavailable('push_to_shared')}"]


def test_shared_repo_is_refused_under_every_strategy_until_it_exists():
    from rite_ai.config.models import ProjectConfig

    for strategy in ("commit", "pull_request", "push_to_shared"):
        module = Module(
            name="svc",
            path="s/",
            publish=ModulePublish(strategy=strategy, shared_repo="git@h:x/y.git"),
        )
        found = refusals(ProjectConfig(), [module])
        assert any("publish.shared_repo is for push_to_shared" in f for f in found)


def test_a_modules_own_auto_merge_under_a_strategy_that_never_merges_is_refused():
    from rite_ai.config.models import ProjectConfig

    config = ProjectConfig(publish=PublishConfig(strategy="commit"))
    module = Module(name="svc", path="s/", publish=ModulePublish(auto_merge=True))
    found = refusals(config, [module])
    assert found and "only a pull request is ever merged" in found[0]


def test_an_inherited_auto_merge_is_not_refused():
    from rite_ai.config.models import ProjectConfig

    config = ProjectConfig(publish=PublishConfig(auto_merge=True))
    module = Module(name="svc", path="s/", publish=ModulePublish(strategy="commit"))
    assert refusals(config, [module]) == []


# --- the entry points ---------------------------------------------------------------


def _project(
    tmp_path: Path,
    monkeypatch,
    *,
    config: str = "",
    module: str = "",
    sandbox: bool = True,
) -> Path:
    rite = tmp_path / ".rite"
    rite.mkdir()
    (rite / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
    (rite / "modules.yaml").write_text(
        f"modules:\n  app:\n    path: app/\n    branch: main\n{module}"
    )
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        + ("sandbox:\n  enabled: true\n  backend: seatbelt\n" if sandbox else "")
        + config
    )
    worker = tmp_path / "workers" / "alpha"
    worker.mkdir(parents=True)
    (worker / "worker.yml").write_text(
        "worker:\n  name: alpha\n  manager: ''\n  modules: [app]\n"
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


SHARED_PROJECT = "publish:\n  strategy: push_to_shared\n"
SHARED_MODULE = "    publish:\n      strategy: push_to_shared\n"


def _sandbox_start():
    """`rite sandbox start`, failing the test if the workspace is even
    prepared: the refusal must come before anything is spent."""
    with (
        patch(
            "rite_ai.workspace.prepare_workspace",
            side_effect=AssertionError("the workspace was prepared"),
        ),
        patch(
            "rite_ai.sandbox.start_worker",
            side_effect=AssertionError("a sandbox was started"),
        ),
    ):
        return CliRunner().invoke(cli, ["sandbox", "start", "alpha", "--ticket", "K-1"])


@pytest.mark.parametrize(
    ("config", "module"), [(SHARED_PROJECT, ""), ("", SHARED_MODULE)]
)
def test_sandbox_start_refuses_push_to_shared_before_spending_anything(
    tmp_path, monkeypatch, config, module
):
    _project(tmp_path, monkeypatch, config=config, module=module)
    result = _sandbox_start()
    assert result.exit_code == 1, result.output
    assert "not available until rite v0.8.0" in result.output
    assert "commit, push or pull_request" in result.output


def test_sandbox_start_refuses_when_the_settings_cannot_be_read(tmp_path, monkeypatch):
    """An unreadable modules.yaml is "cannot tell", which refuses; it is not
    "nothing is refused"."""
    _project(tmp_path, monkeypatch)
    (tmp_path / ".rite" / "modules.yaml").write_text("modules: [\n")
    result = _sandbox_start()
    assert result.exit_code == 1, result.output
    assert "cannot read which publish settings are in force" in result.output


@pytest.mark.parametrize(
    ("config", "module"), [(SHARED_PROJECT, ""), ("", SHARED_MODULE)]
)
def test_rite_start_refuses_push_to_shared(tmp_path, monkeypatch, config, module):
    _project(tmp_path, monkeypatch, config=config, module=module)
    with patch(
        "rite_ai.lifecycle.start", side_effect=AssertionError("start went ahead")
    ):
        bare = CliRunner().invoke(cli, ["start"])
        by_path = CliRunner().invoke(cli, ["start", str(tmp_path)])
    for result in (bare, by_path):
        assert result.exit_code == 1, result.output
        assert "refusing to start" in result.output
        assert "not available until rite v0.8.0" in result.output


def test_rite_start_goes_ahead_on_an_available_strategy(tmp_path, monkeypatch):
    """The control for the refusal above: the same project with `commit`
    reaches the lifecycle start."""
    from rite_ai.lifecycle.commands import StartResult

    _project(tmp_path, monkeypatch, config="publish:\n  strategy: commit\n")
    with patch(
        "rite_ai.lifecycle.start", return_value=StartResult(True, "ready")
    ) as started:
        result = CliRunner().invoke(cli, ["start", str(tmp_path)])
    assert result.exit_code == 0, result.output
    started.assert_called_once()


def _doctor_publish_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith("publish:")]


def test_doctor_prints_each_modules_effective_line_and_it_follows_the_override(
    tmp_path, monkeypatch
):
    """PB1's "done when": the line is observed CHANGING when an override is
    added and removed."""
    _project(tmp_path, monkeypatch, sandbox=False)
    modules = tmp_path / ".rite" / "modules.yaml"
    base = modules.read_text()

    before = _doctor_publish_lines(CliRunner().invoke(cli, ["doctor"]).output)
    assert before == [
        "publish: app: pull_request (project default), squash off (project "
        "default), auto_merge off (project default)"
    ]

    modules.write_text(base + "    publish:\n      strategy: commit\n")
    during = _doctor_publish_lines(CliRunner().invoke(cli, ["doctor"]).output)
    assert during == [
        "publish: app: commit (module override), squash off (project "
        "default), auto_merge off (project default)"
    ]

    modules.write_text(base)
    after = _doctor_publish_lines(CliRunner().invoke(cli, ["doctor"]).output)
    assert after == before


def test_doctor_counts_push_to_shared_as_a_problem(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, module=SHARED_MODULE, sandbox=False)
    result = CliRunner().invoke(cli, ["doctor"])
    assert result.exit_code == 1
    assert any(
        "REFUSED" in line and "v0.8.0" in line
        for line in _doctor_publish_lines(result.output)
    )
