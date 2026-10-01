"""C7: `rite init` does not offer a Manager MACHINE, on either route.

Robert, 2026-10-01 on 0.7.0a4: init asked "Is this the Owner machine or a
Manager machine?" and a Manager machine needs the multi-manager work this
release does not ship. Offering an answer that does not work is worse than
offering one answer, so until 0.9.0 the question is not put and the role is
Owner.

🔴 **The question existed TWICE** — `run_questionnaire` (the from-scratch
route) and `_ask_role` (the "existing spec or code" route, which is the one
Robert's own run hit). A fix that touched only the first would have changed
nothing for him, so both routes now go through `_ask_role` and the property
below is asserted over every route rather than over one.

⚠ Distinct from "Declare Manager 'lead'?", which is a Manager ROLE in
`coordination.managers` and is untouched —
`test_init_offers_a_manager.py` owns that.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

import rite_ai.sandbox as sb
from rite_ai.cli.init import questionnaire
from rite_ai.cli.main import cli

ROLE_QUESTION = "Is this the Owner machine or a Manager machine?"
OWNER_SAID = "This machine is the project's Owner"


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


def _init(root: Path, *args: str, input: str | None = None):
    return CliRunner().invoke(cli, ["init", *args, str(root)], input=input)


def _role(root: Path) -> str:
    return yaml.safe_load((root / ".rite" / "brief.yaml").read_text())["project"][
        "role"
    ]


# Every route into the role, with the answers that follow it. Named so a
# failure says which route asked.
ROUTES = {
    # y · path · changes · add ./? · Manager? Enter · Worker? n
    "existing-code": ([], "y\n\n\n\n\nn\n"),
    # n · name, branch, add ./?, kind, features, platform, languages,
    # frameworks, architecture · board 3 (none) · link, file, commit ·
    # Manager? Enter · Worker? n
    "scratch": ([], "n\n" + "\n" * 9 + "3\n" + "\n" * 3 + "\nn\n"),
    "yes": (["--yes"], None),
}


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_no_route_asks_which_machine_this_is(tmp_path, route):
    args, text = ROUTES[route]
    root = _repo(tmp_path / "app")

    result = _init(root, *args, input=text)

    assert result.exit_code == 0, result.output
    assert ROLE_QUESTION not in result.output, route


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_and_every_route_produces_an_owner_machine(tmp_path, route):
    """⚠ **The property, not an example.** No init path can produce
    `brief.role != "owner"` — which is what makes this checkable at all, and
    is why the suppression is a guard rather than two commented-out prompts.
    """
    args, text = ROUTES[route]
    root = _repo(tmp_path / "app")

    assert _init(root, *args, input=text).exit_code == 0
    assert _role(root) == "owner", route


@pytest.mark.parametrize("route", ["existing-code", "scratch"])
def test_an_interactive_route_says_which_machine_it_took(tmp_path, route):
    """A question that silently stopped being asked is indistinguishable from
    one nobody noticed. `--yes` is excluded: it reports every decision in its
    own ledger, asserted below."""
    args, text = ROUTES[route]
    root = _repo(tmp_path / "app")

    out = _init(root, *args, input=text).output

    assert OWNER_SAID in out
    assert "0.9.0" in out, "the restore version is named"


def test_the_yes_ledger_records_it_as_a_decision_not_a_default(tmp_path):
    root = _repo(tmp_path / "app")

    out = _init(root, "--yes").output

    assert "project.role" in out
    assert "owner-only until multi-manager" in out


class TestAPresetIsRefusedNotIgnored:
    """🔴 Both role sites honoured a preset, so suppressing only the prompt
    would still have built a Manager machine from `--config`. Coercing it to
    Owner instead would silently discard a key the user wrote, which is the
    defect class rite refuses everywhere else — unknown `config.yaml` keys are
    refused, not dropped."""

    def _preset(self, tmp_path: Path, role: str) -> Path:
        path = tmp_path / "p.yaml"
        path.write_text(f"project:\n  role: {role}\n")
        return path

    def test_manager_is_refused_and_names_multi_manager(self, tmp_path):
        root = _repo(tmp_path / "app")

        result = _init(
            root, "--yes", "--config", str(self._preset(tmp_path, "manager"))
        )

        assert result.exit_code == 1
        assert "multi-manager" in result.output
        assert "0.9.0" in result.output

    def test_and_writes_nothing_at_all(self, tmp_path):
        """Refused before `.rite/` is created, so a refusal leaves no
        half-initialised project to clean up."""
        root = _repo(tmp_path / "app")

        _init(root, "--yes", "--config", str(self._preset(tmp_path, "manager")))

        assert not (root / ".rite").exists()
        assert not (root / "CLAUDE.md").exists()

    def test_owner_is_accepted(self, tmp_path):
        """⚠ The control. Refusing every `project.role` would pass the two
        above."""
        root = _repo(tmp_path / "app")

        result = _init(root, "--yes", "--config", str(self._preset(tmp_path, "owner")))

        assert result.exit_code == 0, result.output
        assert _role(root) == "owner"

    def test_a_nonsense_role_is_refused_too(self, tmp_path):
        """Not a special case for the word "manager": anything that is not
        Owner is a machine this release cannot be."""
        root = _repo(tmp_path / "app")

        result = _init(root, "--yes", "--config", str(self._preset(tmp_path, "wizard")))

        assert result.exit_code == 1
        assert "multi-manager" in result.output


class TestTheRestoreIsAReWireNotARewrite:
    """0.9.0 deletes the constant and the two guards that read it. What the
    restore needs must still be here, unreferenced, for that to be true."""

    def test_the_marker_names_the_version_that_restores_it(self):
        source = Path(questionnaire.__file__).read_text()
        marker = source[source.index("# C7 / multi-manager") :][:900]
        assert "0.9.0" in marker
        assert "RESTORE" in marker

    def test_both_role_options_are_still_declared(self):
        assert [v for v, _ in questionnaire.ROLE_OPTIONS] == ["owner", "manager"]

    def test_and_the_owner_config_borrow_is_still_there(self):
        """`_borrow_owner_config` is what a Manager machine does with the
        answer. Kept unreferenced on purpose."""
        assert callable(questionnaire._borrow_owner_config)

    def test_flipping_the_constant_off_restores_the_question(
        self, tmp_path, monkeypatch
    ):
        """⚠ **The 0.9.0 restore rehearsal, and the mutation control for
        every test above.** Without it, a suppression that was really a crash
        would pass the whole file."""
        monkeypatch.setattr(questionnaire, "OWNER_ONLY_UNTIL_MULTI_MANAGER", False)
        root = _repo(tmp_path / "app")

        # y · path · changes · role 2 (Manager) · Owner's project? Enter ·
        # add ./? · Manager? Enter · Worker? n
        result = _init(root, input="y\n\n\n2\n\n\n\nn\n")

        assert result.exit_code == 0, result.output
        assert ROLE_QUESTION in result.output
        assert _role(root) == "manager"

    def test_and_lets_a_preset_name_a_manager_machine_again(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(questionnaire, "OWNER_ONLY_UNTIL_MULTI_MANAGER", False)
        root = _repo(tmp_path / "app")
        preset = tmp_path / "p.yaml"
        preset.write_text("project:\n  role: manager\n")

        result = _init(root, "--yes", "--config", str(preset))

        assert result.exit_code == 0, result.output
        assert _role(root) == "manager"
