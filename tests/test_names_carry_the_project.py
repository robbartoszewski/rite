"""A name that reaches a human says which project it belongs to (SPEC §8.10).

Same principle as the `● <project>` message labelling, on a different surface.
The failure it prevents is not confusion — it is **acting on the wrong thing**.
rite is built for a machine running several projects, every project names its
workers `w1`, `w2`, `w3`, and the sandbox for each was called `rite-w1`. So
`yoloai ls` showed `rite-w1` twice and `rite sandbox destroy w1` typed in the
wrong directory destroyed another project's worker: a destructive mistake
reachable by typing the correct command in the wrong place.
"""

from __future__ import annotations

from pathlib import Path

from rite_ai.label import project_slug
from rite_ai.pool import slot_name
from rite_ai.sandbox import legacy_sandbox_name, sandbox_name


def _project(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    (root / ".rite").mkdir(parents=True)
    (root / ".rite" / "brief.yaml").write_text(
        f"project:\n  name: {name}\n  role: owner\n"
    )
    return root


class TestTheCollisionIsGone:
    def test_two_projects_do_not_share_a_sandbox_name(self, tmp_path):
        """THE DEFECT. Both produced `rite-w1`."""
        a = _project(tmp_path, "alpha")
        b = _project(tmp_path, "beta")
        assert sandbox_name("w1", a) != sandbox_name("w1", b)

    def test_two_projects_with_the_SAME_NAME_still_differ(self, tmp_path):
        """Two checkouts, or two clients' `backend`. A listing that shows one
        name twice is worse than one showing a path, because the reader
        believes it is unambiguous."""
        one = _project(tmp_path / "one", "backend")
        two = _project(tmp_path / "two", "backend")
        assert sandbox_name("w1", one) != sandbox_name("w1", two)
        assert slot_name(one, 0) != slot_name(two, 0)

    def test_two_projects_do_not_share_a_pool_session_name(self, tmp_path):
        a = _project(tmp_path, "alpha")
        b = _project(tmp_path, "beta")
        assert slot_name(a, 0) != slot_name(b, 0)


class TestTheNameIsReadable:
    def test_the_project_name_appears_in_the_sandbox_name(self, tmp_path):
        """Uniqueness was never the problem — the pool name was already
        unique. It embedded the resolved PATH, so the project name a human
        scans for never appeared."""
        root = _project(tmp_path, "bentora")
        assert "bentora" in sandbox_name("w1", root)
        assert "bentora" in slot_name(root, 0)

    def test_the_slug_does_not_leak_the_whole_path(self, tmp_path):
        root = _project(tmp_path, "bentora")
        assert str(root) not in slot_name(root, 0)

    def test_a_long_project_name_is_bounded(self, tmp_path):
        root = _project(tmp_path, "a" * 80)
        assert len(project_slug(root)) <= 24 + 1 + 6

    def test_an_awkward_name_still_produces_a_usable_slug(self, tmp_path):
        for raw in ("Bentora Lex!", "wygrane/sprawy", "  ", "..."):
            root = tmp_path / raw.replace("/", "_") or tmp_path / "x"
            (root / ".rite").mkdir(parents=True, exist_ok=True)
            (root / ".rite" / "brief.yaml").write_text(
                f'project:\n  name: "{raw}"\n  role: owner\n'
            )
            slug = project_slug(root)
            assert slug and " " not in slug and "/" not in slug, raw


class TestRiteManagedSandboxesAreStillIdentifiable:
    def test_the_leading_rite_prefix_survives(self, tmp_path):
        """`count_active_sandboxes` filters the worker cap on `rite-`, so
        losing that prefix would silently switch the cap off."""
        root = _project(tmp_path, "bentora")
        assert sandbox_name("w1", root).startswith("rite-")
        assert slot_name(root, 0).startswith("rite-pool-")


class TestUpgradeDoesNotStrandARunningSandbox:
    def test_the_legacy_form_is_still_known(self):
        assert legacy_sandbox_name("w1") == "rite-w1"

    def test_operations_find_a_legacy_sandbox_that_is_actually_running(
        self, tmp_path, monkeypatch
    ):
        """A sandbox started before §8.10 must not appear to have vanished."""
        from rite_ai import sandbox as sb

        root = _project(tmp_path, "bentora")
        monkeypatch.setattr(sb, "_list_sandbox_names", lambda: {"rite-w1"})
        assert sb.existing_sandbox_name("w1", root) == "rite-w1"

    def test_the_scoped_name_wins_when_both_exist(self, tmp_path, monkeypatch):
        from rite_ai import sandbox as sb

        root = _project(tmp_path, "bentora")
        scoped = sb.sandbox_name("w1", root)
        monkeypatch.setattr(sb, "_list_sandbox_names", lambda: {"rite-w1", scoped})
        assert sb.existing_sandbox_name("w1", root) == scoped

    def test_neither_running_reports_the_name_the_user_should_expect(
        self, tmp_path, monkeypatch
    ):
        from rite_ai import sandbox as sb

        root = _project(tmp_path, "bentora")
        monkeypatch.setattr(sb, "_list_sandbox_names", lambda: set())
        assert sb.existing_sandbox_name("w1", root) == sb.sandbox_name("w1", root)

    def test_yoloai_unavailable_does_not_fall_back_to_the_ambiguous_name(
        self, tmp_path, monkeypatch
    ):
        """ "Could not enumerate" must not be read as "a legacy sandbox
        exists" — that would send a destroy at the ambiguous name."""
        from rite_ai import sandbox as sb

        root = _project(tmp_path, "bentora")
        monkeypatch.setattr(sb, "_yoloai_binary", lambda: None)
        assert sb.existing_sandbox_name("w1", root) == sb.sandbox_name("w1", root)
