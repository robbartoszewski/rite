"""Every Manager's own directory leaves the project, once, and never under a
running Manager (MM8, approved by Robert 2026-09-28).

`managers.manager_dir` is outside the tree now; `relocate.move_out` moves what
an older rite left in `.rite/managers/<name>/`. It moves EVERY Manager's state,
each under that Manager's run lock, the `flock` its `rite start` holds for its
whole run. v0.6.0 holds the same lock on the same file (checked against the
tag), so a Manager running the old release is seen as running. A running one
refuses the start, by name, with what to do.

The locks here are REAL: a second process takes a Manager's run lock through
`github_access.hold_run`, as `rite start` does.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from rite_ai.managers import legacy_manager_dir, manager_dir, relocate


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    for name, files in {
        "lead": {"prompt.txt": "lead's instruction", "routes/r1.json": "{}"},
        "helper": {
            "prompt.txt": "helper's instruction",
            "checkins/ledger.jsonl": '{"event": "cycle"}\n',
            "journal/2026-09-28.md": "an entry",
            "slack.json": "{}",
            "mail/.adopted": "",
        },
    }.items():
        for rel, text in files.items():
            path = legacy_manager_dir(root, name) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    return root


def _hold_run_elsewhere(root: Path, manager: str) -> subprocess.Popen:
    """A real process holding `manager`'s run lock, as its `rite start` would.

    ⚠ It is handed THIS process's credential root, because conftest redirects
    `_credential_root` by patching, which a child does not inherit. The first
    version did not: the child locked the operator's real Application Support
    while `move_out` looked at the redirected one, so "free" meant "a
    different file", the move went ahead under a "running" Manager, and the
    child left run-lock directories in the real data directory. The asserts
    below on the file each side locked are what keep that from recurring."""
    import rite_ai.managers.github_access as ga

    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time\nfrom pathlib import Path\n"
            "import rite_ai.managers.github_access as ga\n"
            "ga._credential_root = lambda home=None: Path(sys.argv[3])\n"
            "fd = ga.hold_run(Path(sys.argv[1]), sys.argv[2])\n"
            "print(ga._credential_dir(Path(sys.argv[1]), sys.argv[2]), flush=True)\n"
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
    locked_dir = proc.stdout.readline().strip()
    assert locked_dir == str(ga._credential_dir(root, manager)), (
        "the child locked a different file than the one checked here"
    )
    assert proc.stdout.readline().strip() == "held"
    return proc


def _own_lock(root: Path, manager: str) -> int:
    from rite_ai.managers.github_access import hold_run

    fd = hold_run(root, manager)
    assert fd is not None
    return fd


class TestItMovesEverything:
    def test_every_managers_state_moves_not_only_the_starting_ones(self, tmp_path):
        import os

        root = _project(tmp_path)
        own = _own_lock(root, "lead")
        try:
            got = relocate.move_out(root, "lead")
        finally:
            os.close(own)

        assert got.refused == "" and got.moved == ["helper", "lead"]
        assert (manager_dir(root, "lead") / "routes" / "r1.json").read_text() == "{}"
        helper = manager_dir(root, "helper")
        assert (helper / "prompt.txt").read_text() == "helper's instruction"
        assert (helper / "journal" / "2026-09-28.md").read_text() == "an entry"
        assert (helper / "checkins" / "ledger.jsonl").exists()
        # Nothing of theirs is left in the tree but the legacy mail and a note.
        left = sorted(p.name for p in legacy_manager_dir(root, "helper").iterdir())
        assert left == ["MOVED.txt", "mail"]
        assert not manager_dir(root, "helper").is_relative_to(root)

    def test_it_happens_once(self, tmp_path):
        import os

        root = _project(tmp_path)
        own = _own_lock(root, "lead")
        try:
            relocate.move_out(root, "lead")
            again = relocate.move_out(root, "lead")
        finally:
            os.close(own)
        assert again.moved == [] and again.refused == ""

    def test_a_project_with_nothing_in_the_tree_is_untouched(self, tmp_path):
        assert relocate.move_out(tmp_path, "lead") == relocate.Relocation()

    def test_it_says_what_it_moved(self, tmp_path):
        line = relocate.notes(relocate.Relocation(moved=["helper", "lead"]))
        assert (
            line and "'helper', 'lead'" in line[0] and "out of the project" in line[0]
        )


class TestNeverUnderARunningManager:
    def test_a_running_sibling_refuses_by_name_and_moves_nothing(self, tmp_path):
        import os

        root = _project(tmp_path)
        helper = _hold_run_elsewhere(root, "helper")
        own = _own_lock(root, "lead")
        try:
            got = relocate.move_out(root, "lead")
        finally:
            os.close(own)
            helper.kill()
            helper.wait()

        assert got.moved == []
        assert "Manager 'helper' is running" in got.refused
        assert "rite manager stop helper" in got.refused
        assert "rite start lead" in got.refused
        assert "Nothing was moved" in got.refused
        assert (legacy_manager_dir(root, "lead") / "prompt.txt").exists()
        assert (legacy_manager_dir(root, "helper") / "prompt.txt").exists()

    def test_once_it_stops_the_move_goes_ahead(self, tmp_path):
        import os

        root = _project(tmp_path)
        helper = _hold_run_elsewhere(root, "helper")
        helper.kill()
        helper.wait()
        own = _own_lock(root, "lead")
        try:
            got = relocate.move_out(root, "lead")
        finally:
            os.close(own)
        assert got.moved == ["helper", "lead"]

    def test_the_other_managers_locks_are_let_go_afterwards(self, tmp_path):
        """Held only while moving, or `helper` could never start again."""
        import os

        from rite_ai.managers.github_access import hold_run

        root = _project(tmp_path)
        own = _own_lock(root, "lead")
        try:
            relocate.move_out(root, "lead")
        finally:
            os.close(own)
        fd = hold_run(root, "helper")
        assert fd is not None
        os.close(fd)

    def test_control_a_check_that_ignores_the_lock_moves_state_under_it(self, tmp_path):
        """Proves the test above can see the failure: with a lock check that
        always says "free", state moves out from under a running Manager."""
        import os

        root = _project(tmp_path)
        helper = _hold_run_elsewhere(root, "helper")
        own = _own_lock(root, "lead")
        try:
            got = relocate.move_out(
                root, "lead", hold=lambda r, m: os.open(os.devnull, os.O_RDONLY)
            )
        finally:
            os.close(own)
            helper.kill()
            helper.wait()
        assert got.moved == ["helper", "lead"]


class TestAnInterruptedMove:
    def test_identical_copies_on_both_sides_finish_the_move(self, tmp_path):
        import os
        import shutil

        root = _project(tmp_path)
        target = manager_dir(root, "helper")
        target.mkdir(parents=True)
        shutil.copytree(
            legacy_manager_dir(root, "helper") / "journal", target / "journal"
        )
        own = _own_lock(root, "lead")
        try:
            got = relocate.move_out(root, "lead")
        finally:
            os.close(own)
        assert got.moved == ["helper", "lead"]
        assert not (legacy_manager_dir(root, "helper") / "journal").exists()

    def test_different_copies_refuse_naming_both(self, tmp_path):
        import os

        root = _project(tmp_path)
        target = manager_dir(root, "helper")
        target.mkdir(parents=True)
        (target / "prompt.txt").write_text("a different instruction")
        own = _own_lock(root, "lead")
        try:
            got = relocate.move_out(root, "lead")
        finally:
            os.close(own)
        assert got.moved == []
        assert "'prompt.txt' both in the project" in got.refused
        assert str(target / "prompt.txt") in got.refused
        assert (legacy_manager_dir(root, "helper") / "prompt.txt").exists()
