"""A Manager name and a project alias can be the same word, and one wins.

`rite start <word>` matches Manager names BEFORE aliases, so a collision is
not a tie: the Manager starts and the alias becomes unreachable by name,
with no message. The user typed a project and got a session.

**The hard part is where to say so.** `manager_roles` is committed — every
member of the project has it. The alias registry is per machine — nobody
else has it. So the collision exists on ONE laptop, and a refusal at `rite
start` would reject a config that is correct, shared, and not that user's
to change. It is reported at the two points where somebody can act: the
moment the local name is chosen, and `rite doctor`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.managers import name_collisions


def _project(root: Path, manager: str = "planner") -> Path:
    rite_dir = root / ".rite"
    rite_dir.mkdir(parents=True, exist_ok=True)
    (rite_dir / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    (rite_dir / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "heartbeat:\n  interval_minutes: 10\n  stall_threshold: 3\n"
        "coordination:\n"
        f"  managers:\n    - {manager}\n"
        "  manager_roles:\n"
        f"    - name: {manager}\n      engine: claude\n"
    )
    return root


class TestTheCollisionItself:
    def test_a_shared_name_is_a_collision(self):
        assert name_collisions(["planner", "dev"], ["acme", "planner"]) == ["planner"]

    def test_no_shared_name_is_not(self):
        assert name_collisions(["planner"], ["acme"]) == []

    def test_it_is_sorted_so_reports_do_not_reorder_themselves(self):
        assert name_collisions(["b", "a"], ["a", "b"]) == ["a", "b"]


class TestRegisteringACollidingAlias:
    """The one place the colliding name costs nothing to change, so the one
    place this refuses."""

    @pytest.fixture
    def machine(self, tmp_path, monkeypatch):
        dispatch = tmp_path / "dispatch"
        dispatch.mkdir()
        import rite_ai.dispatch as d

        monkeypatch.setattr(d, "default_dispatch_dir", lambda: dispatch)
        return _project(tmp_path / "proj")

    def test_it_refuses_and_registers_nothing(self, machine, tmp_path):
        result = CliRunner().invoke(cli, ["projects", "add", "planner", str(machine)])
        assert result.exit_code == 1, result.output
        assert "refusing to register 'planner'" in result.output
        listed = CliRunner().invoke(cli, ["projects", "list"])
        assert "planner" not in listed.output, "it refused and registered anyway"

    def test_the_refusal_says_which_name_it_cannot_change(self, machine):
        result = CliRunner().invoke(cli, ["projects", "add", "planner", str(machine)])
        assert "not yours to change here" in result.output
        assert "Pick another alias" in result.output

    def test_a_non_colliding_alias_registers_normally(self, machine):
        result = CliRunner().invoke(cli, ["projects", "add", "acme", str(machine)])
        assert result.exit_code == 0, result.output
        assert "registered 'acme'" in result.output


class TestDoctorReportsWhatItCannotRefuse:
    """A Manager committed AFTER the alias existed. Nobody did anything
    wrong and the alias is dead; doctor is where it becomes visible."""

    @pytest.fixture
    def machine(self, tmp_path, monkeypatch):
        dispatch = tmp_path / "dispatch"
        dispatch.mkdir()
        import rite_ai.dispatch as d

        monkeypatch.setattr(d, "default_dispatch_dir", lambda: dispatch)
        proj = _project(tmp_path / "proj")
        other = tmp_path / "other"
        other.mkdir()
        # Registered FIRST, while nothing collides — then the Manager
        # appears. Nobody did anything wrong and the alias is dead.
        assert (
            CliRunner()
            .invoke(cli, ["projects", "add", "planner", str(other)])
            .exit_code
            == 0
        )
        monkeypatch.chdir(proj)
        return proj

    def test_doctor_names_the_shadowed_alias_and_both_ways_out(self, machine):
        result = CliRunner().invoke(cli, ["doctor"])
        assert "'planner' is both a Manager here and your alias" in result.output
        assert "rite projects remove planner" in result.output, (
            "it reported the collision without saying how to end it"
        )
        assert "if the team agrees" in result.output, (
            "it offered only the fix that is not the user's to make"
        )

    def test_doctor_says_so_when_nothing_is_shadowed(self, tmp_path, monkeypatch):
        dispatch = tmp_path / "dispatch"
        dispatch.mkdir()
        import rite_ai.dispatch as d

        monkeypatch.setattr(d, "default_dispatch_dir", lambda: dispatch)
        monkeypatch.chdir(_project(tmp_path / "proj"))
        result = CliRunner().invoke(cli, ["doctor"])
        assert "manager names: no alias shadowed" in result.output

    def test_the_check_is_not_nested_under_an_unrelated_condition(self):
        """⚠ It was. The first version landed inside `if local_roles:`, so
        it ran only for projects declaring a `local:*` Manager — which is
        the rarer case and has nothing to do with names. Written, tested,
        and reached by almost nobody: the defect class this project keeps
        finding, one indentation level deep."""
        import inspect

        from rite_ai.cli import main

        body = inspect.getsource(main._doctor_report)
        check = body.index('_doctor_check("manager names"')
        line_start = body.rindex("\n", 0, check) + 1
        indent = len(body[line_start:check]) - len(body[line_start:check].lstrip())
        local = body.index("if local_roles:")
        local_start = body.rindex("\n", 0, local) + 1
        local_indent = len(body[local_start:local]) - len(
            body[local_start:local].lstrip()
        )
        assert indent <= local_indent, (
            "the manager-name check is nested deeper than `if local_roles:`, "
            "so it runs only for projects with a local engine"
        )
