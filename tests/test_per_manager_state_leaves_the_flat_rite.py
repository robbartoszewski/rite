"""Per-Manager runtime state lives under that Manager's own directory (MM1).

SPEC §5.4.5's step 2. Before this, `.rite/` was flat: the instance record,
the designation and the engine TMPDIR sat in `.rite/user/`, which is writable
by every Manager on both platforms — only `.rite/managers/` was ever fenced
(§5.4.8, `managers.user_dir`). So P1 — "no rite command acting as Manager A
writes rite state belonging to Manager B" — was not expressible for most of
what a Manager keeps.

⚠ **The point is not de-collision.** Those files were already keyed by name,
so two Managers did not overwrite each other by accident. The point is that
they sat where a WRONG JOIN could reach a sibling's, and now they sit under
the one path that Manager's profile grants and no other's does. That is the
bar §5.4.1 sets: ordinary mistakes cannot cross.

⚠ **The enumeration is the load-bearing half, not the move.** §5.4.6 records
why: `state.py` asserted in prose that it had converted "every `.rite` state
file" and had missed four writers, and
`tests/test_shared_state_locking.py` exists because a claim of completeness
in a docstring is not one. So `TestNothingPerManagerIsLeftFlat` EXERCISES
every writer and then looks at the directory, rather than reading the source
and believing it.
"""

from __future__ import annotations

import pytest

from rite_ai.managers import (
    DESIGNATION_FILENAME,
    INSTANCE_FILENAME,
    ManagerInstance,
    designate,
    designation_path,
    instance_path,
    manager_dir,
    managers_with_state,
    read_instance,
    record_instance,
    running_instances,
    user_dir,
)
from rite_ai.managers.enclosure import engine_tmp

SESSION = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def _own_mail_home(tmp_path, monkeypatch):
    """Point the per-Manager root at the test's own directory, so nothing
    reaches the operator's real one.

    ⚠ **Beside the project, never inside it.** `manager_dir` being outside
    the project is the property under test (MM8), so a data directory under
    the project root would make `is_relative_to` assertions pass or fail on
    the fixture's shape rather than on the code's."""
    from rite_ai.managers.mailbox import MAIL_DIR_ENV

    monkeypatch.setenv(MAIL_DIR_ENV, str(tmp_path / "data"))


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / ".rite").mkdir(parents=True)
    return root


def _write_everything(root, name: str) -> None:
    """Every per-Manager writer this ticket moved, exercised for real."""
    record_instance(root, ManagerInstance(name=name, pid=1))
    designate(root, name, SESSION)
    engine_tmp(root, name).mkdir(parents=True, exist_ok=True)


class TestEachManagersStateIsUnderItsOwnDirectory:
    def test_the_instance_record_is(self, project):
        assert instance_path(project, "alpha") == (
            manager_dir(project, "alpha") / INSTANCE_FILENAME
        )

    def test_the_designation_is(self, project):
        assert designation_path(project, "alpha") == (
            manager_dir(project, "alpha") / DESIGNATION_FILENAME
        )

    def test_the_engine_tmpdir_is(self, project):
        assert engine_tmp(project, "alpha").parent == manager_dir(project, "alpha")

    def test_two_managers_do_not_share_a_directory(self, project):
        _write_everything(project, "alpha")
        _write_everything(project, "beta")

        assert manager_dir(project, "alpha") != manager_dir(project, "beta")
        assert read_instance(project, "alpha").name == "alpha"
        assert read_instance(project, "beta").name == "beta"


class TestNothingPerManagerIsLeftFlat:
    """⚠ The guard §5.4.6 asks for: it fails when a NEW flat per-Manager path
    appears. It works by running every writer and then looking, so a writer
    added later is caught by the same assertion without anyone remembering
    to extend a list."""

    def test_the_flat_rite_user_holds_nothing_of_a_managers(self, project):
        _write_everything(project, "alpha")
        _write_everything(project, "beta")

        present = (
            {p.name for p in user_dir(project).iterdir()}
            if user_dir(project).is_dir()
            else set()
        )
        offending = {
            entry
            for entry in present
            if "alpha" in entry or "beta" in entry or entry == "enginetmp"
        }
        assert offending == set(), (
            f"{offending} is per-Manager state still in the flat .rite/user/, "
            "which every Manager can write. Put it under manager_dir() and "
            "add it to relocate.flat_entries so an upgrade moves it"
        )

    def test_every_moved_writer_really_wrote_under_the_manager_directory(self, project):
        """The assertion above passes trivially if the writers wrote nothing.
        This is its control: each one landed where it was meant to."""
        _write_everything(project, "alpha")

        landed = {p.name for p in manager_dir(project, "alpha").iterdir()}
        assert {INSTANCE_FILENAME, DESIGNATION_FILENAME, "enginetmp"} <= landed

    def test_relocate_knows_every_path_this_test_exercises(self, project):
        """⚠ The enumeration the MIGRATION reads must cover every writer, or
        an upgrade silently leaves one behind. Derived from the same
        functions, asserted against the same set."""
        from rite_ai.managers.relocate import flat_entries

        destinations = {b.name for _, b in flat_entries(project, "alpha")}
        assert destinations == {INSTANCE_FILENAME, DESIGNATION_FILENAME, "enginetmp"}


class TestListingManagersReadsTheDirectories:
    def test_it_finds_every_manager_with_state(self, project):
        _write_everything(project, "alpha")
        _write_everything(project, "beta")

        assert managers_with_state(project) == ["alpha", "beta"]

    def test_a_designation_is_not_listed_as_a_running_manager(self, project):
        """C11's defect, structurally impossible now. The old listing globbed
        `.rite/user/*.json`, which matched `<name>.designated.json` too, and
        skipped it BY NAME; with one directory per Manager and a fixed
        record name there is nothing to confuse."""
        designate(project, "alpha", SESSION)

        assert running_instances(project) == []

    def test_it_lists_a_recorded_instance(self, project):
        record_instance(project, ManagerInstance(name="alpha", pid=1))

        assert [i.name for i in running_instances(project)] == ["alpha"]

    def test_something_elses_folder_beside_rites_is_skipped_not_raised(
        self, project, tmp_path
    ):
        from rite_ai.managers.mailbox import checkout_root

        _write_everything(project, "alpha")
        (checkout_root(project) / "not a manager name").mkdir(parents=True)

        assert managers_with_state(project) == ["alpha"]


class TestAnUpgradeMovesTheFlatStateAndNeverUnderARunningManager:
    """MM1's migration, folded into MM8's (`relocate.move_out`) rather than
    added beside it.

    ⚠ **One migration, not two, and that is a safety property.** Two passes
    would take the run locks twice, and a Manager that started between them
    would be caught by neither: the second pass takes a lock the first had
    just released. So the flat state moves under the same locks, in the same
    call, and one refusal covers both.

    The locks here are REAL — a second process takes a Manager's run lock
    through `github_access.hold_run`, exactly as its `rite start` does — and
    the last test is the control for them: a check that ignores the lock
    moves state out from under a running Manager, which is what the refusal
    exists to stop.
    """

    @staticmethod
    def _flat_state(root, name: str, session: str = SESSION) -> None:
        """What a rite older than 0.7.0 left in the flat `.rite/user/`."""
        from rite_ai.managers import legacy_designation_file, legacy_instance_file
        from rite_ai.managers.enclosure import _legacy_engine_tmp  # noqa: PLC2701

        instance = legacy_instance_file(root, name)
        instance.parent.mkdir(parents=True, exist_ok=True)
        instance.write_text(f'{{"name": "{name}", "pid": 1}}')
        legacy_designation_file(root, name).write_text(f'{{"session_id": "{session}"}}')
        scratch = _legacy_engine_tmp(root, name)
        scratch.mkdir(parents=True, exist_ok=True)
        (scratch / "left-behind").write_text("scratch")

    @staticmethod
    def _own_lock(root, manager: str) -> int:
        from rite_ai.managers.github_access import hold_run

        fd = hold_run(root, manager)
        assert fd is not None
        return fd

    @staticmethod
    def _held_elsewhere(root, manager: str):
        """A real second process holding `manager`'s run lock.

        ⚠ It is handed THIS process's credential root: conftest redirects
        `_credential_root` by patching, which a child does not inherit, so
        without it the child would lock the operator's real directory while
        the check looked at the redirected one — "free" meaning "a different
        file", and the move going ahead under a running Manager.
        """
        import subprocess
        import sys

        import rite_ai.managers.github_access as ga

        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys, time\nfrom pathlib import Path\n"
                "import rite_ai.managers.github_access as ga\n"
                "ga._credential_root = lambda home=None: Path(sys.argv[3])\n"
                "fd = ga.hold_run(Path(sys.argv[1]), sys.argv[2])\n"
                "print(ga._credential_dir(Path(sys.argv[1]),"
                " sys.argv[2]), flush=True)\n"
                "print('held' if fd is not None else 'refused', flush=True)\n"
                "time.sleep(600)\n",
                str(root),
                manager,
                str(ga._credential_root()),
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        assert proc.stdout is not None
        locked = proc.stdout.readline().strip()
        assert locked == str(ga._credential_dir(root, manager)), (
            "the child locked a different file than the one checked here"
        )
        assert proc.stdout.readline().strip() == "held"
        return proc

    def test_the_flat_state_moves_and_the_flat_paths_are_gone(self, project):
        import os

        from rite_ai.managers import relocate

        self._flat_state(project, "alpha")
        own = self._own_lock(project, "alpha")
        try:
            got = relocate.move_out(project, "alpha")
        finally:
            os.close(own)

        assert got.refused == "" and got.moved == ["alpha"]
        target = manager_dir(project, "alpha")
        assert read_instance(project, "alpha").name == "alpha"
        assert SESSION in (target / DESIGNATION_FILENAME).read_text()
        assert (target / "enginetmp" / "left-behind").read_text() == "scratch"
        assert not instance_path(project, "alpha").is_relative_to(project)
        # The point of the ticket: nothing of this Manager's is left flat.
        left = {p.name for p in user_dir(project).iterdir()}
        assert not any("alpha" in name for name in left)
        assert "enginetmp" not in left

    def test_a_second_managers_flat_state_moves_too(self, project):
        """Not only the starting one — the same reason MM8 moves every
        Manager's directory: a half-moved project has the Owner reading the
        new place while a secondary still writes the old."""
        import os

        from rite_ai.managers import relocate

        self._flat_state(project, "alpha")
        self._flat_state(project, "beta")
        own = self._own_lock(project, "alpha")
        try:
            got = relocate.move_out(project, "alpha")
        finally:
            os.close(own)

        assert got.moved == ["alpha", "beta"]
        assert read_instance(project, "beta").name == "beta"

    def test_it_happens_once(self, project):
        import os

        from rite_ai.managers import relocate

        self._flat_state(project, "alpha")
        own = self._own_lock(project, "alpha")
        try:
            relocate.move_out(project, "alpha")
            again = relocate.move_out(project, "alpha")
        finally:
            os.close(own)

        assert again.moved == [] and again.refused == ""

    def test_a_running_manager_refuses_the_move_and_is_named(self, project):
        import os

        from rite_ai.managers import relocate

        self._flat_state(project, "alpha")
        self._flat_state(project, "beta")
        holder = self._held_elsewhere(project, "beta")
        own = self._own_lock(project, "alpha")
        try:
            got = relocate.move_out(project, "alpha")
        finally:
            os.close(own)
            holder.kill()
            holder.wait()

        assert got.moved == []
        assert "'beta'" in got.refused and "is running" in got.refused
        assert "rite manager stop beta" in got.refused
        # NOTHING moved, including the Manager that was free. A partial move
        # is the state with no way back.
        from rite_ai.managers import legacy_instance_file

        assert legacy_instance_file(project, "alpha").exists()

    def test_the_lock_is_what_stops_it_not_the_absence_of_a_manager(self, project):
        """⚠ **The control.** Without it the refusal test proves only that
        `move_out` can return a refusal, not that the LOCK is what produced
        it. A `hold` that ignores the lock moves state out from under a
        running Manager — which is exactly the damage the real one prevents.
        """
        import os

        from rite_ai.managers import relocate

        self._flat_state(project, "alpha")
        self._flat_state(project, "beta")
        holder = self._held_elsewhere(project, "beta")
        own = self._own_lock(project, "alpha")
        try:
            got = relocate.move_out(
                project,
                "alpha",
                hold=lambda root, name: os.open(os.devnull, os.O_RDONLY),
            )
        finally:
            os.close(own)
            holder.kill()
            holder.wait()

        assert got.refused == "" and got.moved == ["alpha", "beta"]

    def test_differing_copies_on_both_sides_refuse_rather_than_choose(self, project):
        """An interrupted move can leave both. Identical copies finish it;
        different ones are two candidate designations, and picking one is how
        a Manager silently resumes the wrong conversation."""
        import os

        from rite_ai.managers import relocate

        self._flat_state(project, "alpha", session=SESSION)
        designation = manager_dir(project, "alpha") / DESIGNATION_FILENAME
        designation.parent.mkdir(parents=True, exist_ok=True)
        designation.write_text('{"session_id": "99999999-8888-7777-6666-555555555555"}')

        own = self._own_lock(project, "alpha")
        try:
            got = relocate.move_out(project, "alpha")
        finally:
            os.close(own)

        assert got.moved == []
        assert "both in the project" in got.refused
        assert DESIGNATION_FILENAME in got.refused
