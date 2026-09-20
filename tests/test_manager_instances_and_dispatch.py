"""Manager instances, per-Manager directories, and the 0/1/2+ rule.

Three things v0.5.1 needs before `rite start <name>` can exist, and the
first two are what make §5.4's containment property statable at all: before
them there was no per-Manager path and no per-process identity to key one
on, so "nothing outside the acting Manager's own directory" named something
that did not exist (D-77). The identity arrives as the argument.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rite_ai.config.managers import ManagerRole
from rite_ai.managers import (
    ManagerInstance,
    forget_instance,
    instance_path,
    manager_dir,
    manager_to_start,
    pid_alive,
    read_instance,
    record_instance,
    running_instances,
    user_dir,
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".rite").mkdir()
    return tmp_path


class TestTheBoundaryIsAPathNow:
    def test_each_manager_gets_its_own_directory(self, project):
        assert manager_dir(project, "planner") == (
            project / ".rite" / "managers" / "planner"
        )
        assert manager_dir(project, "lead") != manager_dir(project, "planner")

    @pytest.mark.parametrize("hostile", ["../escape", "a/b", "..", "/abs", ""])
    def test_a_manager_name_is_one_path_segment(self, project, hostile):
        """A new name-to-path join, so it is validated AT the join — §5.4.2's
        requirement, and the reason `rite_ai.names` exists."""
        with pytest.raises(ValueError):
            manager_dir(project, hostile)
        with pytest.raises(ValueError):
            instance_path(project, hostile)

    def test_instances_are_per_user_and_profiles_are_not(self, project):
        """`.rite/user/` is uncommitted runtime state. A shared config must
        never claim a Manager is running on somebody else's laptop."""
        record_instance(project, ManagerInstance(name="planner", pid=1))
        assert instance_path(project, "planner").parent == user_dir(project)
        assert user_dir(project) == project / ".rite" / "user"


class TestInstancesRoundTrip:
    def test_a_recorded_instance_reads_back(self, project):
        record_instance(
            project,
            ManagerInstance(
                name="lead",
                session="rite-mgr-lead",
                pid=42,
                engine="claude",
                max_sessions=3,
                window_seconds=3600.0,
            ),
        )
        back = read_instance(project, "lead")
        assert back is not None
        assert (back.name, back.pid, back.engine) == ("lead", 42, "claude")
        assert back.max_sessions == 3 and back.window_seconds == 3600.0
        assert back.started_at > 0

    def test_a_corrupt_instance_returns_none_rather_than_raising(self, project):
        """Read on the refusal path. A refusal that tracebacks is worse than
        one that refuses for a stated reason."""
        path = instance_path(project, "lead")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        assert read_instance(project, "lead") is None

    def test_a_json_file_that_is_not_an_instance_is_not_one(self, project):
        path = instance_path(project, "lead")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(["a", "list"]))
        assert read_instance(project, "lead") is None

    def test_listing_skips_the_unreadable_rather_than_inventing(self, project):
        record_instance(project, ManagerInstance(name="good", pid=1))
        bad = user_dir(project) / "bad.json"
        bad.write_text("{{{")
        assert [i.name for i in running_instances(project)] == ["good"]

    def test_forgetting_is_idempotent(self, project):
        record_instance(project, ManagerInstance(name="lead", pid=1))
        forget_instance(project, "lead")
        forget_instance(project, "lead")
        assert read_instance(project, "lead") is None


class TestPidLivenessKnowsItsOwnLimit:
    def test_a_live_pid_reads_live(self):
        import os

        assert pid_alive(os.getpid())

    def test_zero_and_negative_are_not_alive(self):
        assert not pid_alive(0)
        assert not pid_alive(-1)

    def test_an_out_of_range_pid_refuses_rather_than_tracebacks(self):
        """`os.kill` raises OverflowError, not OSError, for a pid too large
        — the exact shape that tracebacked another command in this project
        when a lock file was corrupt."""
        assert not pid_alive(2**70)


class TestTheZeroOneManyRule:
    """D-78. Each case behaves differently on purpose."""

    def test_zero_configured_fails_rather_than_defaulting(self):
        chosen = manager_to_start([], "")
        assert not chosen.ok
        assert "will not invent a default" in chosen.problem
        assert "manager_roles" in chosen.problem

    def test_one_works_bare(self):
        only = ManagerRole(name="lead")
        chosen = manager_to_start([only], "")
        assert chosen.ok and chosen.role is only

    def test_several_refuse_AND_LIST_THEM(self):
        """A refusal that says 'several are configured' and stops sends the
        user to `rite doctor` to find out what they could have typed."""
        roles = [ManagerRole(name="lead"), ManagerRole(name="planner")]
        chosen = manager_to_start(roles, "")
        assert not chosen.ok
        assert "lead" in chosen.problem and "planner" in chosen.problem
        assert "rite start lead" in chosen.problem

    def test_naming_one_of_several_works(self):
        roles = [ManagerRole(name="lead"), ManagerRole(name="planner")]
        chosen = manager_to_start(roles, "planner")
        assert chosen.ok and chosen.role.name == "planner"

    def test_an_unknown_name_lists_what_exists(self):
        roles = [ManagerRole(name="lead")]
        chosen = manager_to_start(roles, "nope")
        assert not chosen.ok
        assert "lead" in chosen.problem

    def test_an_unknown_name_with_no_managers_says_that_instead(self):
        chosen = manager_to_start([], "nope")
        assert not chosen.ok
        assert "declares none at all" in chosen.problem
