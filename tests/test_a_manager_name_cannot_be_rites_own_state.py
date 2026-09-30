"""A Manager's name cannot collide with rite's own state (C30).

A directory that holds entries keyed by a Manager's NAME beside entries rite
keeps for ITSELF is a collision waiting to be named. `.rite/user/` was one: a
Manager called `permissions` had its instance record at
`.rite/user/permissions.json`, the permission allowlist rite passes to every
Manager, so each overwrote the other. C11 was the same collision for
designations.

So the CLASS is refused where a Manager is declared, computed from the real
path functions rather than from a list of words, and the registry of rite's
own entries is pinned by writing them all for a real Manager.

⚠ **MM1 MOVED THIS COLLISION; IT DID NOT REMOVE IT, and that is what these
tests now say.** The instance record and the designation are under
`manager_dir` now, and the profile and Landlock policy are in the Manager's
credential directory, so nothing per-Manager is written into `.rite/user/`
and `permissions` is an ordinary name again. But a Manager's directory IS
`mailbox.checkout_root/<name>`, and rite keeps a `project` marker file
there — so `project` is the same defect in the new place. Deleting the check
because its one known instance had gone would have shipped that, which is
why the refusal is computed over BOTH directories and why the registry test
below exercises the new one.
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


@pytest.mark.parametrize("name", ["project", "Project", "PROJECT"])
def test_a_name_whose_directory_is_rites_own_entry_is_refused(name):
    """C30 in its post-MM1 place: a Manager's directory is
    `checkout_root/<name>`, and `checkout_root/project` is rite's marker
    file recording which project this checkout key is.

    Case-folded because macOS volumes are case-insensitive by default, so
    `Project` IS `project` there."""
    problem = manager_name_problem(name)
    assert "project" in problem.lower() and "overwrite" in problem


def test_the_collision_is_real(tmp_path):
    """The two paths are one — the same measurement C30 made for
    `.rite/user/permissions.json`, at the directory MM1 moved state into."""
    from rite_ai.managers import manager_dir
    from rite_ai.managers.mailbox import PROJECT_MARKER, checkout_root

    assert manager_dir(tmp_path, "project").parent == (
        checkout_root(tmp_path) / PROJECT_MARKER
    )


def test_permissions_is_an_ordinary_name_again_since_mm1(tmp_path):
    """⚠ A behaviour CHANGE, recorded rather than left to be noticed. The
    instance record left `.rite/user/`, so it cannot collide with the
    permission allowlist any more. Refusing the name would now be ceremony
    against a collision that does not exist — and pretending it still did
    would mean `_per_manager_user_entries` lying about where rite writes."""
    assert manager_name_problem("permissions") == ""
    assert instance_path(tmp_path, "permissions") != settings_path(tmp_path)


def test_ordinary_names_are_not_refused():
    for name in ("lead", "planner", "small", "enginetmp", "permission", "projects"):
        assert manager_name_problem(name) == "", name


def test_nothing_per_manager_is_written_into_the_flat_user_dir_any_more():
    """The other half of MM1's move, as the refusal sees it: with no
    per-Manager entries there, no name can collide in that directory. Kept
    as a function rather than deleted so that anything coming BACK restores
    the refusal (`_per_manager_user_entries`)."""
    assert _per_manager_user_entries("anything") == ()


def test_the_record_path_refuses_it_too(tmp_path):
    with pytest.raises(ValueError, match="rite keeps for itself"):
        instance_path(tmp_path, "project")


def test_the_config_refuses_it_where_it_is_declared():
    parsed = parse_managers([{"name": "project", "engine": "claude"}])
    assert "cannot be called 'project'" in parsed.error


def test_two_names_differing_only_in_case_are_refused():
    """One directory on a case-insensitive disk: each Manager would
    overwrite the other's state."""
    parsed = parse_managers(["lead", "Lead"])
    assert "differ only in case" in parsed.error


def _write_everything(root, tmp_path, name: str = "lead") -> None:
    """Every writer that puts something in either directory, for real."""
    (root / ".rite").mkdir(parents=True, exist_ok=True)
    write_settings(root)
    write_profile(root, name, home=tmp_path / "home")
    engine_tmp(root, name).mkdir(parents=True, exist_ok=True)
    record_instance(root, ManagerInstance(name=name, pid=1))
    designate(root, name, "11111111-2222-3333-4444-555555555555")


def test_every_fixed_entry_rite_writes_into_user_dir_is_registered(tmp_path):
    """⚠ The registry is what the refusal is computed from, so an entry rite
    writes and does not register is a collision nobody is refused for. This
    writes everything rite writes into `.rite/user/` for a real Manager and
    fails on any entry that is neither that Manager's nor registered."""
    root = tmp_path / "proj"
    _write_everything(root, tmp_path)
    present = {p.name for p in user_dir(root).iterdir()}
    mine = set(_per_manager_user_entries("lead"))
    unregistered = present - mine - set(_rite_owned_user_entries())
    assert unregistered == set(), (
        f"rite wrote {unregistered} into .rite/user/ without registering it "
        "in _rite_owned_user_entries(), so a Manager with that name would not "
        "be refused"
    )
    assert mine <= present, "_per_manager_user_entries has drifted from the writers"


def test_every_fixed_entry_rite_writes_beside_the_manager_dirs_is_registered(
    tmp_path, monkeypatch
):
    """⚠ The same guard for the directory MM1 moved state INTO, which is
    where the collision lives now. Without this, `project` is refused today
    because someone noticed, and the next fixed-name entry rite writes there
    is a collision nobody is refused for — C30 exactly."""
    from rite_ai.managers import _rite_owned_checkout_entries
    from rite_ai.managers.mailbox import INBOX, MAIL_DIR_ENV, checkout_root, send

    monkeypatch.setenv(MAIL_DIR_ENV, str(tmp_path / "data"))
    root = tmp_path / "proj"
    _write_everything(root, tmp_path)
    # The marker is written by delivery, not by a start, so send something.
    send(root, "lead", INBOX, "hello")

    present = {p.name for p in checkout_root(root).iterdir()}
    unregistered = present - {"lead"} - set(_rite_owned_checkout_entries())
    assert unregistered == set(), (
        f"rite wrote {unregistered} beside the per-Manager directories "
        "without registering it in _rite_owned_checkout_entries(), so a "
        "Manager with that name would not be refused"
    )
    assert set(_rite_owned_checkout_entries()) <= present, (
        "_rite_owned_checkout_entries names something nothing writes"
    )
