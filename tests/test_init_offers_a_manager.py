"""S15: `rite init` offers to declare a Manager, through `rite add manager`'s
own mechanism.

Robert, 2026-09-30 on 0.7.0a3: init asked "Owner machine or Manager machine?"
and wrote `project.role` only, so every new project needed a config edit
before `rite start` would run a Manager. S16 (#162) gave rite one writer of
`coordination.managers` and `manager_roles`, `declare_manager`, validated by
the parser itself. Init now offers a Manager and declares it through that
writer, never a copy of it: what init writes is what `rite add manager`
would have written, and whatever the parser refuses, init refuses too.

"Found by `rite start`" is checked with the resolver `rite start` and every
other Manager command use (`cli.main._manager_roles`), not by reading keys.
"""

from __future__ import annotations

import itertools
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

import rite_ai.sandbox as sb
from rite_ai.cli import main as cli_main
from rite_ai.cli.main import cli
from rite_ai.config.managers import configuration_problems
from rite_ai.config.parse import parse_config


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(sb, "platform_can_sandbox", lambda: False)
    monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path / "rite-home"))


def _repo(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "main.go").write_text("package main\n")
    for args in (
        ["init", "-q", "-b", "main"],
        ["add", "-A"],
        ["commit", "-q", "-m", "c"],
    ):
        subprocess.run(
            ["git", "-c", "user.email=a@b", "-c", "user.name=t", *args],
            cwd=root,
            check=True,
            capture_output=True,
        )
    return root


def _init(root: Path, *args: str, input: str | None = None) -> str:
    result = CliRunner().invoke(cli, ["init", *args, str(root)], input=input)
    assert result.exit_code == 0, result.output
    return result.output


def _found_by_start(root: Path) -> list[str]:
    """The Managers `rite start` would find, or an assertion that it could."""
    roles, problems = cli_main._manager_roles(root)
    assert not problems, problems
    return [r.name for r in roles]


def _keys(root: Path):
    c = parse_config(root / ".rite" / "config.yaml").coordination
    return c.managers, c.manager_roles


def test_yes_declares_none_and_says_how(tmp_path):
    """A declared Manager makes `rite doctor` report a one-machine project as
    uncoordinated, so `--yes` declares none by default, and says how to add
    one; `--config` declares one (below)."""
    root = _repo(tmp_path / "app")
    out = _init(root, "--yes")
    assert _keys(root) == ([], [])
    assert "no Manager is declared" in out
    assert "rite add manager lead --preset lead" in out


def test_accepting_the_default_declares_it_with_no_hand_edit(tmp_path):
    root = _repo(tmp_path / "app")
    # existing code? y · path · changes · add ./? · Manager? Enter · Worker? n
    # (C7: no role question — this machine is the Owner, and init says so.)
    out = _init(root, input="y\n\n\n\n\nn\n")
    assert "Declare Manager 'lead' (preset lead:" in out
    assert _found_by_start(root) == ["lead"]


def test_declining_leaves_both_keys_empty_and_says_how(tmp_path):
    root = _repo(tmp_path / "app")
    # … add ./? · Manager? n · another name? Enter · Worker? n
    out = _init(root, input="y\n\n\n\nn\n\nn\n")
    names, roles = _keys(root)
    assert names == [] and roles == []
    assert "no Manager is declared" in out
    assert "rite add manager lead --preset lead" in out
    assert "Ready. Start" not in out


def test_another_name_and_preset(tmp_path):
    root = _repo(tmp_path / "app")
    # … add ./? · Manager? n · name planner · preset nonsense (asked again) ·
    # planner · Worker? n
    out = _init(root, input="y\n\n\n\nn\nplanner\nnonsense\nplanner\nn\n")
    assert "'nonsense' is not a preset" in out
    assert _found_by_start(root) == ["planner"]
    _, roles = _keys(root)
    assert roles[0].preset == "planner"


def test_an_owner_machine_is_offered_a_lead(tmp_path):
    """C7 made this the only reachable case: init no longer offers a Manager
    MACHINE, so `DEFAULT_MANAGER["owner"]` is the only entry a route can
    reach. The `manager` entry is kept and asserted below, unreached, because
    0.9.0 restores the route to it."""
    root = _repo(tmp_path / "app")
    # … add ./? · Manager? Enter · Worker? n
    _init(root, input="y\n\n\n\n\nn\n")
    _, roles = _keys(root)
    assert [(r.name, r.preset) for r in roles] == [("lead", "lead")]


def test_the_manager_machine_default_is_kept_for_when_the_route_returns(
    tmp_path,
):
    """Unreachable, not deleted: `setup.DEFAULT_MANAGER` is what a Manager
    machine will be offered again when multi-manager ships, and deleting it
    would make that a rewrite rather than a re-wire."""
    from rite_ai.cli.init.setup import DEFAULT_MANAGER

    assert DEFAULT_MANAGER["manager"] == ("executor", "executor")


def test_the_config_names_it_or_declines_it(tmp_path):
    root = _repo(tmp_path / "a")
    preset = tmp_path / "p.yaml"
    preset.write_text("managers:\n  add: scribe\n  preset: pm\n")
    out = _init(root, "--yes", "--config", str(preset))
    assert "--config: declared Manager 'scribe' (preset pm)" in out
    assert _found_by_start(root) == ["scribe"]

    root = _repo(tmp_path / "b")
    preset.write_text("managers:\n  add: false\n")
    _init(root, "--yes", "--config", str(preset))
    assert _keys(root) == ([], [])


def test_the_parsers_refusal_is_inits_refusal(tmp_path):
    """Validation is `declare_manager`'s, which is the parser's: an unknown
    preset is refused in the parser's words, and nothing is written."""
    root = _repo(tmp_path / "a")
    preset = tmp_path / "p.yaml"
    preset.write_text("managers:\n  add: lead\n  preset: wizard\n")
    out = _init(root, "--yes", "--config", str(preset))
    assert "Manager 'lead' was NOT declared" in out and "wizard" in out
    assert _keys(root) == ([], [])


def test_it_goes_through_add_managers_own_writer(tmp_path, monkeypatch):
    """No second copy: init calls `declare_manager`, the function `rite add
    manager` calls."""
    from rite_ai.config import managers

    calls = []
    real = managers.declare_manager

    def spy(*a, **kw):
        calls.append((a[2], kw.get("preset")))
        return real(*a, **kw)

    monkeypatch.setattr(managers, "declare_manager", spy)
    _init(_repo(tmp_path / "app"), input="y\n\n\n\n\n\nn\n")
    assert calls == [("lead", "lead")]


# --- the invariant, over routes, roles and answers -----------------------------


ROUTES = {
    "yes": (["--yes"], None),
    # y · path · changes · add ./? · Manager … · Worker? n
    "existing-code": ([], "y\n\n\n\n{manager}n\n"),
    # n · name, branch, add ./?, kind, features, platform, languages,
    # frameworks, architecture · board 3 (none) · link, file, commit ·
    # Manager … · Worker? n
    "scratch": ([], "n\n" + "\n" * 9 + "3\n" + "\n" * 3 + "{manager}n\n"),
}
# C7: the machine role is no longer an axis — init produces an Owner machine
# on every route, so there is one value here rather than two. Restored to
# {"owner": …, "manager": …} when multi-manager ships (0.9.0).
ROLES = {"owner": ""}
ANSWERS = {"accept": "\n", "decline": "n\n\n", "other": "n\nplanner\nplanner\n"}


@pytest.mark.parametrize(
    "route,role,answer",
    [
        (r, ro, a)
        for r, ro, a in itertools.product(ROUTES, ROLES, ANSWERS)
        if r != "yes" or a == "decline"
    ],
)
def test_whatever_is_declared_rite_start_finds_and_the_keys_agree(
    tmp_path, route, role, answer
):
    """For every route, machine role and answer: the two keys agree and the
    parser is content (what `rite doctor` reports is empty), `rite start`
    finds exactly the Manager declared, none is declared only when declined,
    and the last line says so exactly then."""
    args, template = ROUTES[route]
    root = _repo(tmp_path / "app")
    text = None if template is None else template.format(manager=ANSWERS[answer])
    out = _init(root, *args, input=text)
    names, roles = _keys(root)
    assert names == [r.name for r in roles]
    assert configuration_problems(roles, names) == []
    expected = {
        "accept": ["lead"],
        "decline": [],
        "other": ["planner"],
    }[answer]
    assert names == expected
    if expected:
        assert _found_by_start(root) == expected
    assert ("no Manager is declared" in out) == (not expected)
    raw = yaml.safe_load((root / ".rite" / "config.yaml").read_text())
    assert bool(raw.get("coordination", {}).get("manager_roles")) == bool(expected)
