from pathlib import Path
from unittest.mock import MagicMock, patch

import rite_ai.update as update_mod
from rite_ai.update import (
    CURRENT_SCHEMA_VERSION,
    OLDEST_SCHEMA_VERSION,
    Migration,
    detect_install_method,
    migrate_config,
    read_schema_version,
    run_self_update,
    update_command_for,
)


class TestUpdateCommandFor:
    def test_pipx(self):
        assert update_command_for("pipx") == ["pipx", "upgrade", "rite-ai"]

    def test_pip(self):
        cmd = update_command_for("pip")
        assert cmd[-3:] == ["install", "--upgrade", "rite-ai"]

    def test_upgrades_the_distribution_not_the_command_name(self):
        """`rite` on PyPI is an unrelated package. Every self-update path
        must name `rite-ai`, or `rite update` installs someone else's
        project over this one."""
        for method in ("pipx", "pip"):
            assert "rite" not in update_command_for(method)

    def test_dev_has_no_command(self):
        assert update_command_for("dev") is None


class TestRunSelfUpdate:
    def test_dev_checkout_tells_user_to_git_pull(self):
        result = run_self_update("dev")
        assert result.ok
        assert "git pull" in result.message

    @patch("rite_ai.update.subprocess.run")
    @patch("rite_ai.update.shutil.which", return_value="/usr/bin/pipx")
    def test_pipx_success(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        result = run_self_update("pipx")
        assert result.ok
        assert "pipx upgrade rite-ai" in result.message

    @patch("rite_ai.update.subprocess.run")
    @patch("rite_ai.update.shutil.which", return_value="/usr/bin/pipx")
    def test_pipx_failure_surfaces_stderr(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="network error")
        result = run_self_update("pipx")
        assert not result.ok
        assert "network error" in result.message

    @patch("rite_ai.update.shutil.which", return_value=None)
    def test_missing_binary_refuses_cleanly(self, mock_which):
        result = run_self_update("pipx")
        assert not result.ok
        assert "not found" in result.message


class TestDetectInstallMethod:
    def test_pipx_path_detected(self, monkeypatch):
        monkeypatch.setattr(
            "rite_ai.update.sys.executable",
            "/Users/x/.local/pipx/venvs/rite-ai/bin/python",
        )
        assert detect_install_method() == "pipx"

    def test_falls_back_to_pip_when_not_editable_or_pipx(self, monkeypatch, tmp_path):
        fake_exe = tmp_path / "venv" / "bin" / "python"
        fake_exe.parent.mkdir(parents=True)
        fake_exe.touch()
        monkeypatch.setattr("rite_ai.update.sys.executable", str(fake_exe))
        with patch("importlib.metadata.distribution") as mock_dist:
            mock_dist.return_value.read_text.return_value = (
                '{"url": "https://pypi.org/...", "archive_info": {}}'
            )
            assert detect_install_method() == "pip"

    def test_dev_checkout_detected_via_direct_url_editable_flag(
        self, monkeypatch, tmp_path
    ):
        """The real signal (PEP 660's `direct_url.json`), not a path
        heuristic on `sys.executable` — which `uv` resolves THROUGH a
        symlink to a shared toolchain interpreter, breaking any check that
        assumes it points at the project's own `.venv/bin/python`."""
        fake_exe = tmp_path / ".venv" / "bin" / "python"
        fake_exe.parent.mkdir(parents=True)
        fake_exe.touch()
        monkeypatch.setattr("rite_ai.update.sys.executable", str(fake_exe))
        with patch("importlib.metadata.distribution") as mock_dist:
            mock_dist.return_value.read_text.return_value = (
                '{"url": "file:///repo", "dir_info": {"editable": true}}'
            )
            assert detect_install_method() == "dev"

    def test_missing_distribution_falls_back_to_pip(self, monkeypatch, tmp_path):
        fake_exe = tmp_path / "venv" / "bin" / "python"
        fake_exe.parent.mkdir(parents=True)
        fake_exe.touch()
        monkeypatch.setattr("rite_ai.update.sys.executable", str(fake_exe))
        with patch("importlib.metadata.distribution", side_effect=LookupError):
            assert detect_install_method() == "pip"


class TestSchemaVersion:
    def test_no_stamp_reads_as_current(self, tmp_path: Path):
        assert read_schema_version(tmp_path) == CURRENT_SCHEMA_VERSION

    def test_reads_stamped_value(self, tmp_path: Path):
        (tmp_path / ".schema_version").write_text("1\n")
        assert read_schema_version(tmp_path) == 1

    def test_garbage_stamp_reads_as_current_not_a_crash(self, tmp_path: Path):
        (tmp_path / ".schema_version").write_text("not-a-number\n")
        assert read_schema_version(tmp_path) == CURRENT_SCHEMA_VERSION


class TestMigrateConfig:
    def test_no_migrations_registered_is_a_noop(self, tmp_path: Path):
        applied = migrate_config(tmp_path)
        assert applied == []
        assert read_schema_version(tmp_path) == CURRENT_SCHEMA_VERSION

    def test_stamps_current_version_after_running(self, tmp_path: Path):
        migrate_config(tmp_path)
        stamp = (tmp_path / ".schema_version").read_text().strip()
        assert stamp == str(CURRENT_SCHEMA_VERSION)

    def test_a_registered_migration_would_apply_and_be_reported(self, tmp_path: Path):
        """Exercises the mechanism with a fake migration, since MIGRATIONS
        is empty in production — this is what proves the scaffolding
        works, not just that an empty list is empty."""
        calls = []
        fake_migration = Migration(
            from_version=1,
            to_version=2,
            description="test migration",
            apply=lambda rite_dir: calls.append(rite_dir),
        )
        import rite_ai.update as update_mod

        original_migrations = update_mod.MIGRATIONS
        original_current = update_mod.CURRENT_SCHEMA_VERSION
        try:
            update_mod.MIGRATIONS = [fake_migration]
            update_mod.CURRENT_SCHEMA_VERSION = 2
            (tmp_path / ".schema_version").write_text("1\n")
            applied = update_mod.migrate_config(tmp_path)
            assert applied == ["test migration"]
            assert calls == [tmp_path]
            assert (tmp_path / ".schema_version").read_text().strip() == "2"
        finally:
            update_mod.MIGRATIONS = original_migrations
            update_mod.CURRENT_SCHEMA_VERSION = original_current


class TestSchemaVersionOfProjectsThatPredateStamping:
    """`read_schema_version` treated an unstamped project as CURRENT, and
    `rite init` never wrote a stamp — so every project in existence read as
    already-migrated. The first migration to ship would have applied to
    nothing:

        migrations applied : []
        marker written     : False
        stamp now          : 2

    and stamped every project as version 2 regardless, which is the half
    that makes it unrecoverable: the stamp is the only record of what ran,
    so the next `rite update` skips the project forever.
    """

    def _migration(self, marker: str = "migrated.marker"):
        def apply(rite_dir: Path) -> None:
            (rite_dir / marker).write_text("y")

        return Migration(
            from_version=1,
            to_version=2,
            description="a config change",
            apply=apply,
        )

    def test_an_unstamped_project_is_the_oldest_schema_not_the_current_one(
        self, tmp_path: Path
    ):
        rite_dir = tmp_path / ".rite"
        rite_dir.mkdir()
        assert read_schema_version(rite_dir) == OLDEST_SCHEMA_VERSION

    def test_an_unreadable_stamp_is_also_the_oldest(self, tmp_path: Path):
        rite_dir = tmp_path / ".rite"
        rite_dir.mkdir()
        (rite_dir / ".schema_version").write_text("not a number")
        assert read_schema_version(rite_dir) == OLDEST_SCHEMA_VERSION

    def test_a_migration_reaches_a_project_that_predates_stamping(
        self, tmp_path: Path, monkeypatch
    ):
        rite_dir = tmp_path / ".rite"
        rite_dir.mkdir()
        monkeypatch.setattr(update_mod, "CURRENT_SCHEMA_VERSION", 2)
        monkeypatch.setattr(update_mod, "MIGRATIONS", [self._migration()])

        applied = update_mod.migrate_config(rite_dir)

        assert applied == ["a config change"]
        assert (rite_dir / "migrated.marker").is_file()
        assert (rite_dir / ".schema_version").read_text().strip() == "2"

    def test_a_project_is_never_stamped_past_what_actually_ran(
        self, tmp_path: Path, monkeypatch
    ):
        """No migration registered from version 1, but CURRENT is 3. The
        project must stay on 1 so a later 1->2 migration can still find
        it — stamping it 3 would silently retire it."""
        rite_dir = tmp_path / ".rite"
        rite_dir.mkdir()
        monkeypatch.setattr(update_mod, "CURRENT_SCHEMA_VERSION", 3)
        monkeypatch.setattr(
            update_mod,
            "MIGRATIONS",
            [Migration(2, 3, "later change", lambda d: None)],
        )

        applied = update_mod.migrate_config(rite_dir)

        assert applied == []
        assert (rite_dir / ".schema_version").read_text().strip() == "1", (
            "stamped as migrated without running anything"
        )

    def test_rite_init_stamps_the_schema_it_wrote(self, tmp_path: Path):
        from rite_ai.cli.init import run_init

        run_init(tmp_path, yes=True)

        stamp = tmp_path / ".rite" / ".schema_version"
        assert stamp.is_file(), "a project created today carries no stamp"
        assert stamp.read_text().strip() == str(CURRENT_SCHEMA_VERSION)

    def test_a_freshly_created_project_still_migrates(
        self, tmp_path: Path, monkeypatch
    ):
        """Stamping at init must not make new projects skip migrations."""
        from rite_ai.cli.init import run_init

        run_init(tmp_path, yes=True)
        monkeypatch.setattr(update_mod, "CURRENT_SCHEMA_VERSION", 2)
        monkeypatch.setattr(update_mod, "MIGRATIONS", [self._migration()])

        applied = update_mod.migrate_config(tmp_path / ".rite")

        assert applied == ["a config change"]
