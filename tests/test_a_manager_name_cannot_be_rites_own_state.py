"""A Manager's name cannot collide with rite's own state (C30).

`.rite/user/` holds files keyed by a Manager's name — its instance record,
its designation, its sandbox profile — in the same directory as files rite
keeps for itself. A Manager named `permissions` had its instance record at
`.rite/user/permissions.json`, the permission allowlist rite passes to every
Manager, so each overwrote the other. C11 was the same collision for
designations.

So the CLASS is refused where a Manager is declared, computed from the real
path functions rather than from a list of words, and the registry of rite's
own entries is pinned by writing them all for a real Manager.
"""

from __future__ import annotations

import pytest

from rite_ai.config.managers import parse_managers
from rite_ai.managers import (
    ManagerInstance,
    _per_manager_user_entries,
    _rite_owned_user_entries,
    designate,
    instance_path,
    manager_name_problem,
    record_instance,
    user_dir,
)
from rite_ai.managers.enclosure import engine_tmp, write_profile
from rite_ai.managers.permissions import settings_path, write_settings


@pytest.mark.parametrize("name", ["permissions", "Permissions", "PERMISSIONS"])
def test_a_name_whose_record_is_rites_own_file_is_refused(name):
    """Case-folded because macOS volumes are case-insensitive by default:
    `Permissions.json` IS `permissions.json` there."""
    problem = manager_name_problem(name)
    assert "permissions.json" in problem.lower() and "overwrite" in problem


def test_ordinary_names_are_not_refused():
    for name in ("lead", "planner", "small", "enginetmp", "permission", "perms"):
        assert manager_name_problem(name) == "", name


def test_the_collision_was_real_before_this(tmp_path):
    """What C30 measured: the two paths were one."""
    assert user_dir(tmp_path) / _per_manager_user_entries("permissions")[
        0
    ] == settings_path(tmp_path)


def test_the_record_path_refuses_it_too(tmp_path):
    with pytest.raises(ValueError, match="rite keeps for itself"):
        instance_path(tmp_path, "permissions")


def test_the_config_refuses_it_where_it_is_declared():
    parsed = parse_managers([{"name": "permissions", "engine": "claude"}])
    assert "cannot be called 'permissions'" in parsed.error


def test_two_names_differing_only_in_case_are_refused():
    """One directory on a case-insensitive disk: each Manager would
    overwrite the other's state."""
    parsed = parse_managers(["lead", "Lead"])
    assert "differ only in case" in parsed.error


def test_every_fixed_entry_rite_writes_into_user_dir_is_registered(tmp_path):
    """⚠ The registry is what the refusal is computed from, so an entry rite
    writes and does not register is a collision nobody is refused for. This
    writes everything rite writes into `.rite/user/` for a real Manager and
    fails on any entry that is neither that Manager's nor registered."""
    root = tmp_path
    (root / ".rite").mkdir()
    write_settings(root)
    write_profile(root, "lead", home=tmp_path / "home")
    engine_tmp(root, "lead").mkdir(parents=True, exist_ok=True)
    record_instance(root, ManagerInstance(name="lead", pid=1))
    designate(root, "lead", "11111111-2222-3333-4444-555555555555")
    present = {p.name for p in user_dir(root).iterdir()}
    mine = set(_per_manager_user_entries("lead"))
    unregistered = present - mine - set(_rite_owned_user_entries())
    assert unregistered == set(), (
        f"rite wrote {unregistered} into .rite/user/ without registering it "
        "in _rite_owned_user_entries(), so a Manager with that name would not "
        "be refused"
    )
    assert mine <= present, "_per_manager_user_entries has drifted from the writers"
