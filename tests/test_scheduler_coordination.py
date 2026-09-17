"""The scheduler tick runs the coordination loop (the caller P2-4d lacked).

The Phase 2 machinery landed complete and called by nobody — D-14's defect
shape, a function whose caller was never written. These tests are about the
caller existing, and about the three ways wiring it to unattended cron goes
wrong: running on a machine nobody enrolled, taking the whole tick down when
the remote is unreachable, and touching things cron has no business touching.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rite_ai.coordination.identity import this_manager
from rite_ai.scheduler import run_tick


def project(tmp_path: Path, coordination: str = "") -> Path:
    root = tmp_path / "project"
    rite = root / ".rite"
    rite.mkdir(parents=True)
    (rite / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n  site: test.atlassian.net\n"
        "  projects: {workers: ABC, board: XYZ}\n  credential: ''\n"
        "expertise: {}\npublish_gate:\n  scan_patterns: []\n"
        "  gitleaks_config: .rite/gitleaks.toml\n"
        "heartbeat:\n  interval_minutes: 10\n  stall_threshold: 3\n" + coordination
    )
    return root


def remote(tmp_path: Path) -> str:
    path = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(path)], check=True)
    return str(path)


def coordination_lines(result) -> list[str]:
    return [m for m in result.messages if m.startswith("coordination")]


class TestWhenItStaysOut:
    def test_a_project_with_no_coordination_says_nothing(self, tmp_path):
        """Every project today. The tick must behave exactly as it did."""
        result = run_tick(project(tmp_path))
        assert result.ok
        assert coordination_lines(result) == []

    def test_a_machine_that_is_not_enrolled_is_named_not_skipped(self, tmp_path):
        """Silence here is the dangerous option: a machine that believes it
        is coordinating and is not looks exactly like one that is."""
        root = project(
            tmp_path,
            "coordination:\n  managers: [alpha, beta]\n"
            f"  remote: '{remote(tmp_path)}'\n",
        )
        result = run_tick(root)
        assert result.ok
        lines = coordination_lines(result)
        assert len(lines) == 1 and "not enrolled" in lines[0]

    def test_a_machine_calling_itself_a_name_nobody_listed_is_named(self, tmp_path):
        root = project(
            tmp_path,
            "coordination:\n  managers: [alpha, beta]\n"
            f"  remote: '{remote(tmp_path)}'\n",
        )
        (root / ".rite" / "machine").write_text("gamma\n")
        lines = coordination_lines(run_tick(root))
        assert len(lines) == 1 and "gamma" in lines[0]


class TestWhenItRuns:
    @pytest.fixture
    def enrolled(self, tmp_path):
        root = project(
            tmp_path,
            "coordination:\n  managers: [alpha, beta]\n"
            f"  remote: '{remote(tmp_path)}'\n",
        )
        (root / ".rite" / "machine").write_text("alpha\n")
        return root

    def test_the_first_tick_takes_the_owner_role(self, enrolled):
        """The whole point: unattended, on the cadence §5.1.2 already
        establishes, with nothing else needed from a human."""
        result = run_tick(enrolled)
        assert result.ok
        lines = coordination_lines(result)
        assert any("promoted" in line for line in lines), lines

    def test_a_later_tick_renews_rather_than_re_electing(self, enrolled):
        run_tick(enrolled)
        lines = coordination_lines(run_tick(enrolled))
        assert any("renewed" in line for line in lines), lines

    def test_it_publishes_this_machines_liveness(self, enrolled):
        """Other machines' elections read this; without it every Manager is
        invisible and nobody ever defers."""
        from rite_ai.coordination.git_backend import GitStateLayer
        from rite_ai.coordination.heartbeat import status_key
        from rite_ai.coordination.state_layer import Present

        run_tick(enrolled)
        config = enrolled / ".rite" / "config.yaml"
        url = [
            line.split("'")[1]
            for line in config.read_text().splitlines()
            if "remote:" in line and "'" in line
        ][0]
        layer = GitStateLayer(url, enrolled / ".rite" / "verify-cache.git")
        assert isinstance(layer.read_state(status_key("alpha")), Present)

    def test_the_cache_lives_under_rite_so_it_is_not_committed(self, enrolled):
        """`.rite/` ignores everything outside its authored set, so a bare
        repo here is ignored because nobody did anything. Anywhere else it
        would land in the project's next commit."""
        run_tick(enrolled)
        assert (enrolled / ".rite" / "coordination-cache.git").is_dir()

    def test_cron_does_not_touch_the_ticket_backend(self, enrolled):
        """A Manager also distributes work to its Workers (P2-4b). That
        writes labels and comments on a shared board, and doing it
        unattended is its own decision — so the tick must not have started
        doing it by accident."""
        run_tick(enrolled)
        assert not (enrolled / ".rite" / "outbox").exists()


class TestItCannotTakeTheTickDown:
    def test_an_unreachable_remote_is_reported_and_the_tick_still_succeeds(
        self, tmp_path
    ):
        """The tick also runs the watchdog and the window check, from cron,
        unattended. Coordination failing must not take those with it."""
        root = project(
            tmp_path,
            "coordination:\n  managers: [alpha]\n"
            f"  remote: '{tmp_path / 'nope.git'}'\n",
        )
        (root / ".rite" / "machine").write_text("alpha\n")
        result = run_tick(root)
        assert result.ok, "a bad remote failed the whole tick"
        assert coordination_lines(result)


    def test_an_unexpected_exception_is_caught_rather_than_killing_cron(
        self, tmp_path, monkeypatch
    ):
        """The guard is for what nobody predicted, so the test supplies
        exactly that. Caught by mutation: every FORESEEN failure in this
        stack returns Unavailable instead of raising, so narrowing the catch
        to one exception type passed the whole suite — the backstop was
        never exercised by anything."""
        from rite_ai.coordination import monitor as monitor_module

        root = project(
            tmp_path,
            "coordination:\n  managers: [alpha]\n"
            f"  remote: '{remote(tmp_path)}'\n",
        )
        (root / ".rite" / "machine").write_text("alpha\n")

        def explode(self, now=None):
            raise RuntimeError("something nobody thought of")

        monkeypatch.setattr(monitor_module.ManagerMonitor, "tick", explode)
        result = run_tick(root)
        assert result.ok, "an unexpected error took the whole tick down"
        assert any("could not run" in line for line in coordination_lines(result))


class TestTheNameItself:
    def test_a_missing_file_means_not_enrolled(self, tmp_path):
        assert this_manager(project(tmp_path)) is None

    def test_a_blank_file_means_not_enrolled(self, tmp_path):
        root = project(tmp_path)
        (root / ".rite" / "machine").write_text("   \n")
        assert this_manager(root) is None

    def test_a_path_like_name_is_refused(self, tmp_path):
        """The name is also a state key; one with a directory in it names a
        different file entirely."""
        root = project(tmp_path)
        (root / ".rite" / "machine").write_text("../elsewhere\n")
        assert this_manager(root) is None
