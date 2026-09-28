"""What `rite init` writes passes rite's own publish gate (dogfood F1).

Measured in the v0.6.0 dogfood: `rite init` with every default, then
`git add -A; git commit; git push`, was blocked by rite's own pre-push hook —
`.rite/brief.yaml:14 — Hardcoded macOS home directory path`, the resolved
`source.path`. The only ways past were a suppression with a throwaway reason
or `--no-verify`, so the first thing rite taught a new user was that its gate
is skippable.

The built-in rule only matches a path containing `/Users/<name>/` or
`/home/<name>/`, and pytest's `tmp_path` on macOS is under `/private/var/…`:
a project there would pass this test with the defect in place. So the project
is built in `app` under a `Users` then `alice` directory in `tmp_path`, with
HOME pointed at `alice`, which the unfixed code does trip — see
`test_the_control_is_caught`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

import rite_ai.sandbox as sb
from rite_ai.cli.init.questionnaire import portable_source_path
from rite_ai.cli.main import cli
from rite_ai.gate import gitleaks_runner
from rite_ai.gate.gate import EXIT_CLEAN, EXIT_FAIL, run_gate
from tests.gate_helpers import commit_all, init_repo, write

requires_gitleaks = pytest.mark.skipif(
    gitleaks_runner.find_gitleaks_binary() is None, reason="gitleaks not installed"
)

HOME_RULE = "rite-hardcoded-macos-home-directory-path"


@pytest.fixture(autouse=True)
def _no_verified_sandbox(monkeypatch):
    monkeypatch.setattr(sb, "platform_can_sandbox", lambda: False)


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "Users" / "alice"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return home


def _app_with_code(home: Path) -> Path:
    app = home / "app"
    app.mkdir()
    init_repo(app)
    write(app, "main.py", "print('hi')\n")
    commit_all(app, "code")
    return app


def _init_with_defaults(app: Path) -> None:
    # spec or existing code? y · path [.] · changes Enter · role Enter
    result = CliRunner().invoke(cli, ["init", str(app)], input="y\n\n\n\n\n")
    assert result.exit_code == 0, result.output
    commit_all(app, "rite init")


def _brief(app: Path) -> dict:
    return yaml.safe_load((app / ".rite" / "brief.yaml").read_text())


@requires_gitleaks
def test_init_with_defaults_then_commit_passes_the_gate(home: Path):
    app = _app_with_code(home)
    _init_with_defaults(app)

    report = run_gate(app)

    assert not report.errors, report.errors
    assert report.files_scanned > 0
    assert [f.rule_id for f in report.findings] == []
    assert report.exit_code == EXIT_CLEAN
    assert _brief(app)["source"]["path"] == "."


@requires_gitleaks
def test_the_control_is_caught(home: Path):
    """The same tree with the path v0.6.0 wrote fails, on the rule F1 named.

    Without this, the test above could be green because the project was built
    somewhere the rule cannot match."""
    app = _app_with_code(home)
    _init_with_defaults(app)
    brief_path = app / ".rite" / "brief.yaml"
    brief = _brief(app)
    brief["source"]["path"] = str(app.resolve())
    brief_path.write_text(yaml.safe_dump(brief, sort_keys=False))
    commit_all(app, "the path v0.6.0 wrote")

    report = run_gate(app)

    assert report.exit_code == EXIT_FAIL
    assert [(f.file, f.rule_id) for f in report.findings] == [
        (".rite/brief.yaml", HOME_RULE)
    ]


def test_a_source_elsewhere_under_home_is_written_from_home(home: Path):
    root = home / "app"
    root.mkdir()
    spec = home / "docs" / "spec.md"
    assert portable_source_path(root, spec) == "~/docs/spec.md"


def test_a_source_inside_the_project_is_relative(home: Path):
    root = home / "app"
    assert portable_source_path(root, root) == "."
    assert portable_source_path(root, root / "code") == "code"


def test_a_source_outside_home_stays_absolute(tmp_path: Path, home: Path):
    elsewhere = tmp_path / "srv" / "spec"
    assert portable_source_path(home / "app", elsewhere) == str(elsewhere)


@requires_gitleaks
def test_a_local_origin_under_home_passes_the_gate_too(home: Path):
    """Once init registers the repository it runs in (F2), that repository's
    origin is written into `modules.yaml` and the generated CLAUDE.md. An
    origin that is a local directory under home was the F1 failure again, in
    two new files. It is written `~/…`, and a Worker still clones from it."""
    remote = home / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    app = _app_with_code(home)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=app, check=True)
    _init_with_defaults(app)

    report = run_gate(app)

    assert [(f.file, f.rule_id) for f in report.findings] == []
    assert report.exit_code == EXIT_CLEAN
    modules = yaml.safe_load((app / ".rite" / "modules.yaml").read_text())
    assert modules["modules"]["app"]["url"] == "~/remote.git"


def test_a_home_relative_module_url_is_read_back_absolute(home: Path):
    from rite_ai.config.parse import expand_home, home_relative

    local = str(home / "remote.git")
    assert home_relative(local) == "~/remote.git"
    assert expand_home("~/remote.git") == local
    for url in ("https://github.com/acme/app.git", "git@github.com:acme/app.git"):
        assert home_relative(url) == url
