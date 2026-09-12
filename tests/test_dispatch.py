from pathlib import Path

from rite_ai.dispatch import (
    add_project,
    aggregate_load_warning,
    load_registry,
    remove_project,
    resolve_alias,
)


class TestAddRemoveProject:
    def test_add_registers_and_persists(self, tmp_path: Path):
        dispatch_dir = tmp_path / "dispatch"
        project_dir = tmp_path / "acme"
        project_dir.mkdir()

        result = add_project(dispatch_dir, "acme", project_dir)
        assert result.ok

        registry = load_registry(dispatch_dir)
        assert "acme" in registry.projects
        assert registry.projects["acme"].path == str(project_dir.resolve())

    def test_add_scaffolds_claude_md(self, tmp_path: Path):
        dispatch_dir = tmp_path / "dispatch"
        add_project(dispatch_dir, "acme", tmp_path / "acme")
        assert (dispatch_dir / "CLAUDE.md").is_file()

    def test_add_duplicate_alias_refuses(self, tmp_path: Path):
        dispatch_dir = tmp_path / "dispatch"
        add_project(dispatch_dir, "acme", tmp_path / "acme")
        result = add_project(dispatch_dir, "acme", tmp_path / "other")
        assert not result.ok

    def test_remove_deregisters(self, tmp_path: Path):
        dispatch_dir = tmp_path / "dispatch"
        add_project(dispatch_dir, "acme", tmp_path / "acme")
        result = remove_project(dispatch_dir, "acme")
        assert result.ok
        assert "acme" not in load_registry(dispatch_dir).projects

    def test_remove_does_not_touch_project_files(self, tmp_path: Path):
        dispatch_dir = tmp_path / "dispatch"
        project_dir = tmp_path / "acme"
        project_dir.mkdir()
        (project_dir / "marker.txt").write_text("still here")
        add_project(dispatch_dir, "acme", project_dir)
        remove_project(dispatch_dir, "acme")
        assert (project_dir / "marker.txt").is_file()

    def test_remove_unknown_alias_refuses(self, tmp_path: Path):
        dispatch_dir = tmp_path / "dispatch"
        result = remove_project(dispatch_dir, "ghost")
        assert not result.ok


class TestResolveAlias:
    def test_resolves_registered_alias(self, tmp_path: Path):
        dispatch_dir = tmp_path / "dispatch"
        project_dir = tmp_path / "acme"
        add_project(dispatch_dir, "acme", project_dir)
        assert resolve_alias(dispatch_dir, "acme") == project_dir.resolve()

    def test_unknown_alias_resolves_to_none(self, tmp_path: Path):
        dispatch_dir = tmp_path / "dispatch"
        assert resolve_alias(dispatch_dir, "ghost") is None

    def test_no_registry_at_all_resolves_to_none(self, tmp_path: Path):
        assert resolve_alias(tmp_path / "nonexistent", "acme") is None


class TestAggregateLoadWarning:
    def _register(self, dispatch_dir: Path, alias: str, pct: int, tmp_path: Path):
        project_dir = tmp_path / alias
        (project_dir / ".rite").mkdir(parents=True)
        (project_dir / ".rite" / "config.yaml").write_text(
            f"budget:\n  weekly_quota_pct: {pct}\n"
        )
        add_project(dispatch_dir, alias, project_dir)

    def test_no_projects_no_warning(self, tmp_path: Path):
        assert aggregate_load_warning(tmp_path / "dispatch") is None

    def test_under_threshold_no_warning(self, tmp_path: Path):
        dispatch_dir = tmp_path / "dispatch"
        self._register(dispatch_dir, "a", 40, tmp_path)
        self._register(dispatch_dir, "b", 40, tmp_path)
        assert aggregate_load_warning(dispatch_dir) is None

    def test_over_threshold_warns_with_breakdown(self, tmp_path: Path):
        dispatch_dir = tmp_path / "dispatch"
        self._register(dispatch_dir, "a", 60, tmp_path)
        self._register(dispatch_dir, "b", 60, tmp_path)
        warning = aggregate_load_warning(dispatch_dir)
        assert warning is not None
        assert "120%" in warning
        assert "a=60%" in warning
        assert "b=60%" in warning

    def test_missing_project_config_defaults_to_default_pct(self, tmp_path: Path):
        dispatch_dir = tmp_path / "dispatch"
        project_dir = tmp_path / "bare"
        project_dir.mkdir()
        add_project(dispatch_dir, "bare", project_dir)
        # ProjectConfig's own default (95) applies when config.yaml is
        # missing entirely — this must not crash.
        warning = aggregate_load_warning(dispatch_dir)
        assert warning is None or "95%" in warning
