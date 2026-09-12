from pathlib import Path

from rite_ai.reporting.status import collect_status, format_status


def _setup(tmp_path: Path) -> Path:
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    (rite_dir / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    (rite_dir / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n  site: ''\n"
        "  projects: {}\n  credential: ''\n"
        "expertise: {}\npublish_gate:\n  scan_patterns: []\n"
        "  gitleaks_config: .rite/gitleaks.toml\n"
        "heartbeat:\n  interval_minutes: 10\n"
        "  stall_threshold: 3\n"
    )
    return tmp_path


class TestCollectStatus:
    def test_collects_basic_info(self, tmp_path: Path):
        root = _setup(tmp_path)
        status = collect_status(root)
        assert status.name == "acme"
        assert status.role == "owner"
        assert status.errors == []

    def test_reports_missing_rite_dir(self, tmp_path: Path):
        status = collect_status(tmp_path)
        assert len(status.errors) > 0
        assert "rite init" in status.errors[0]


class TestFormatStatus:
    def test_formats_output(self, tmp_path: Path):
        root = _setup(tmp_path)
        status = collect_status(root)
        text = format_status(status)
        assert "acme" in text
        assert "owner" in text
        assert "no modules" in text
