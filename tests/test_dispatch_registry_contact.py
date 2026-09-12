"""Regression tests for the multi-project registry, driven against real
projects for the first time.

`rite projects add` / `list`, alias resolution from `rite start|stop
<alias>`, and the cross-project `rite status` all worked. Two things
around them did not, and both are the same shape: a message that names
something the user never wrote.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.dispatch import add_project, aggregate_load_warning


def _project(tmp_path: Path, name: str, quota_pct: int | None = None) -> Path:
    root = tmp_path / name
    (root / ".rite").mkdir(parents=True)
    (root / ".rite" / "brief.yaml").write_text(
        f"project:\n  name: {name}\n  role: owner\n"
    )
    (root / ".rite" / "modules.yaml").write_text("modules: {}\n")
    config = "ticket_backend:\n  type: none\n"
    if quota_pct is not None:
        config += f"budget:\n  weekly_quota_pct: {quota_pct}\n"
    (root / ".rite" / "config.yaml").write_text(config)
    return root


class TestTheAggregateWarningOnlyCountsConfiguredTargets:
    """`parse_config` fills `weekly_quota_pct` in from its dataclass
    default (95), and the sum included those. So two projects that had
    never heard of the key produced:

        budget.weekly_quota_pct sum to 190% (one=95%, two=95%) ...
        This is a sum of independently-configured targets

    naming a setting neither config file contained. Measured: it fired on
    the SECOND project of every registry, unconditionally, whatever
    anyone had configured. A warning that cannot not fire is one nobody
    reads."""

    def test_two_projects_that_set_nothing_do_not_warn(self, tmp_path: Path):
        dispatch = tmp_path / "dispatch"
        add_project(dispatch, "one", _project(tmp_path, "one"))
        add_project(dispatch, "two", _project(tmp_path, "two"))

        assert aggregate_load_warning(dispatch) is None

    def test_two_configured_targets_over_the_threshold_do_warn(self, tmp_path: Path):
        dispatch = tmp_path / "dispatch"
        add_project(dispatch, "one", _project(tmp_path, "one", quota_pct=80))
        add_project(dispatch, "two", _project(tmp_path, "two", quota_pct=60))

        warning = aggregate_load_warning(dispatch)

        assert warning is not None
        assert "sum to 140%" in warning
        assert "one=80%" in warning and "two=60%" in warning

    def test_two_configured_targets_under_the_threshold_do_not_warn(
        self, tmp_path: Path
    ):
        dispatch = tmp_path / "dispatch"
        add_project(dispatch, "one", _project(tmp_path, "one", quota_pct=40))
        add_project(dispatch, "two", _project(tmp_path, "two", quota_pct=50))

        assert aggregate_load_warning(dispatch) is None

    def test_one_configured_target_cannot_conflict_with_anything(self, tmp_path: Path):
        """A single target, however large, is not an aggregate."""
        dispatch = tmp_path / "dispatch"
        add_project(dispatch, "one", _project(tmp_path, "one", quota_pct=200))
        add_project(dispatch, "two", _project(tmp_path, "two"))

        assert aggregate_load_warning(dispatch) is None

    def test_the_uncounted_projects_are_declared(self, tmp_path: Path):
        """Whoever reads the sum has to be able to see it does not cover
        every registered project."""
        dispatch = tmp_path / "dispatch"
        add_project(dispatch, "one", _project(tmp_path, "one", quota_pct=80))
        add_project(dispatch, "two", _project(tmp_path, "two", quota_pct=60))
        add_project(dispatch, "three", _project(tmp_path, "three"))

        warning = aggregate_load_warning(dispatch)

        assert warning is not None
        assert "1 other registered project(s) set no target" in warning
        assert "three=" not in warning

    def test_a_config_that_will_not_parse_is_treated_as_unset(self, tmp_path: Path):
        dispatch = tmp_path / "dispatch"
        broken = _project(tmp_path, "one", quota_pct=80)
        (broken / ".rite" / "config.yaml").write_text("budget: [not, a, mapping]\n")
        add_project(dispatch, "one", broken)
        add_project(dispatch, "two", _project(tmp_path, "two", quota_pct=60))

        assert aggregate_load_warning(dispatch) is None


class TestAddConfirmsWhatItRecorded:
    def test_the_resolved_path_is_echoed_not_the_argument(self, tmp_path, monkeypatch):
        """`rite projects add sched .` confirmed "registered 'sched' -> ."
        while storing the absolute path. Registering the wrong directory
        is precisely the mistake that line exists to let a user catch."""
        root = _project(tmp_path, "acme")
        monkeypatch.setenv("RITE_DISPATCH_DIR", str(tmp_path / "dispatch"))
        monkeypatch.chdir(root)

        result = CliRunner().invoke(cli, ["projects", "add", "acme", "."])

        assert result.exit_code == 0
        assert str(root.resolve()) in result.output
        assert result.output.strip() != "registered 'acme' -> ."


class TestAnUnknownAliasSaysSo:
    """An argument that resolved to neither an alias nor a directory fell
    through to `Path(directory)`, so a mistyped alias became a relative
    path: `rite stop schd` from anywhere answered "no .rite/ directory",
    a message about a directory the user never mentioned."""

    def test_a_mistyped_alias_names_the_registered_ones(self, tmp_path, monkeypatch):
        dispatch = tmp_path / "dispatch"
        add_project(dispatch, "sched", _project(tmp_path, "sched"))
        monkeypatch.setenv("RITE_DISPATCH_DIR", str(dispatch))
        monkeypatch.chdir(tmp_path)

        result = CliRunner().invoke(cli, ["stop", "schd"])

        assert result.exit_code == 1
        assert "neither an existing directory nor a registered alias" in result.output
        assert "sched" in result.output
        assert "no .rite/ directory" not in result.output

    def test_with_an_empty_registry_it_says_that_instead(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RITE_DISPATCH_DIR", str(tmp_path / "dispatch"))
        monkeypatch.chdir(tmp_path)

        result = CliRunner().invoke(cli, ["stop", "acme"])

        assert result.exit_code == 1
        assert "no projects are registered" in result.output

    def test_a_real_alias_still_resolves(self, tmp_path, monkeypatch):
        dispatch = tmp_path / "dispatch"
        root = _project(tmp_path, "sched")
        add_project(dispatch, "sched", root)
        monkeypatch.setenv("RITE_DISPATCH_DIR", str(dispatch))
        monkeypatch.chdir(tmp_path)

        result = CliRunner().invoke(cli, ["stop", "sched"])

        assert result.exit_code == 0
        assert "stopped" in result.output

    def test_a_real_directory_still_resolves(self, tmp_path, monkeypatch):
        root = _project(tmp_path, "acme")
        monkeypatch.setenv("RITE_DISPATCH_DIR", str(tmp_path / "dispatch"))
        monkeypatch.chdir(tmp_path)

        result = CliRunner().invoke(cli, ["stop", str(root)])

        assert result.exit_code == 0
        assert "stopped" in result.output
