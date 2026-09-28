"""CU4: a Cursor Manager's key reaches its engine and nothing else.

The route (`cursor_login`): a 0600 copy no profile grants, read by tmux's
shell OUTSIDE the boundary into the engine's own environment. These tests use
a FAKE key only. The point of each is where the value ends up: in the
engine's environment, and not in the command text, tmux's argv, the pane's
environment, or anywhere the Manager can read it from disk.
"""

from __future__ import annotations

import stat
import subprocess
import sys

import pytest

from rite_ai.managers import cursor_login, github_access
from rite_ai.managers.session import StartResult

FAKE = "fake-cursor-key-for-tests-0000"


@pytest.fixture
def project(tmp_path):
    (tmp_path / ".rite").mkdir()
    return tmp_path


class TestTheCopy:
    def test_it_is_0600_in_a_0700_directory_and_holds_the_key_stripped(self, project):
        assert cursor_login.prepare(project, "lead", FAKE + "\n") == ""
        path = cursor_login._key_path(project, "lead")
        assert path.read_text() == FAKE
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert cursor_login.state_dir(project, "lead").is_dir()

    def test_it_is_beside_the_writable_state_not_in_it(self, project):
        """The state directory is Manager-writable; the key must not be."""
        key = cursor_login._key_path(project, "lead")
        state = cursor_login.state_dir(project, "lead")
        assert key.parent == state.parent and state not in key.parents

    def test_no_key_is_refused_with_how_to_store_one(self, project):
        refusal = cursor_login.prepare(project, "lead", None)
        assert "cursor_api_key" in refusal and "--stdin" in refusal
        assert not cursor_login._key_path(project, "lead").exists()

    def test_a_copy_a_killed_run_left_is_removed_and_said(self, project):
        cursor_login.prepare(project, "lead", FAKE)
        said: list[str] = []
        cursor_login.prepare(project, "lead", None, say=said.append)
        assert not cursor_login._key_path(project, "lead").exists()
        assert said and "did not exit cleanly" in said[0]
        assert FAKE not in said[0]

    def test_the_end_of_the_run_removes_it(self, project):
        cursor_login.prepare(project, "lead", FAKE)
        cursor_login.remove_login(project, "lead")
        assert not cursor_login._key_path(project, "lead").exists()


class TestWhatThePaneIsGiven:
    def test_nothing_without_a_copy(self, project):
        assert cursor_login.launch_prefix(project, "lead") == ""
        assert cursor_login.pane_environment(project, "lead") == {}

    def test_the_prefix_names_the_path_never_the_key(self, project):
        cursor_login.prepare(project, "lead", FAKE)
        prefix = cursor_login.launch_prefix(project, "lead")
        assert FAKE not in prefix
        assert str(cursor_login._key_path(project, "lead")) in prefix

    def test_the_pane_environment_is_paths_only(self, project):
        cursor_login.prepare(project, "lead", FAKE)
        env = cursor_login.pane_environment(project, "lead")
        state = str(cursor_login.state_dir(project, "lead"))
        assert env == {"CURSOR_CONFIG_DIR": state, "CURSOR_DATA_DIR": state}
        assert cursor_login.KEY_ENV not in env

    def test_the_key_variable_may_never_travel_on_tmux_argv(self):
        from rite_ai.managers.session import ALLOWED_ON_TMUX_ARGV

        assert cursor_login.KEY_ENV not in ALLOWED_ON_TMUX_ARGV

    def test_the_prefix_really_delivers_it_through_a_shell(self, project):
        """The quoting, exercised: a shell expands the prefix and the command
        after it sees the key. Compared inside the child; the value is never
        printed or put on an argv."""
        cursor_login.prepare(project, "lead", FAKE)
        prefix = cursor_login.launch_prefix(project, "lead")
        copy = cursor_login._key_path(project, "lead")
        check = (
            f'{prefix}/bin/sh -c \'test "$CURSOR_API_KEY" = "$(cat {copy})" '
            '&& test -n "$CURSOR_API_KEY"\''
        )
        assert subprocess.run(["/bin/sh", "-c", check]).returncode == 0


class TestTheSupervisorsLaunch:
    """Through `_default_starter`, with `start_session` captured: what tmux
    would actually have been given."""

    def _launch(self, project, monkeypatch, engine):
        import rite_ai.managers.supervise as sup

        seen = {}

        def capture(root, manager, *, pane_env, command, **kw):
            seen["env"] = pane_env
            seen["command"] = command
            return StartResult(True, "ok", session="s", attach="a", pane="%1")

        monkeypatch.setattr(sup, "start_session", capture)
        sup._default_starter(
            project,
            "lead",
            engine=engine,
            resume_id="0b6f0c1e-1234-4abc-8def-0123456789ab"
            if engine == "cursor"
            else "",
            prompt="go",
            permission="",
            agent="",
            max_sessions=1,
            window_seconds=0,
        )
        return seen

    def test_a_cursor_launch_carries_the_prefix_and_never_the_key(
        self, project, monkeypatch
    ):
        cursor_login.prepare(project, "lead", FAKE)
        seen = self._launch(project, monkeypatch, "cursor")
        assert seen["command"].startswith(cursor_login.launch_prefix(project, "lead"))
        assert FAKE not in seen["command"]
        assert all(FAKE not in str(v) for v in seen["env"].values())
        assert seen["env"]["CURSOR_CONFIG_DIR"] == str(
            cursor_login.state_dir(project, "lead")
        )

    def test_a_copy_left_behind_never_reaches_another_engine(
        self, project, monkeypatch
    ):
        """Gated on the engine, not on the file: a killed Cursor run's copy
        must not put the key in a Claude Manager's environment."""
        cursor_login.prepare(project, "lead", FAKE)
        seen = self._launch(project, monkeypatch, "claude")
        assert "CURSOR_API_KEY" not in seen["command"]
        assert "CURSOR_CONFIG_DIR" not in seen["env"]


class TestTheBoundary:
    def test_seatbelt_grants_the_state_and_denies_the_key_by_name(self, project):
        cursor_login.prepare(project, "lead", FAKE)
        lines = "\n".join(github_access.profile_lines(project, "lead"))
        state = cursor_login.state_dir(project, "lead")
        key = cursor_login._key_path(project, "lead")
        grant = f'(allow file-read* file-write* (subpath "{state}"))'
        deny = f'(deny file-read* file-write* (literal "{key}"))'
        assert grant in lines and deny in lines
        assert lines.index(deny) > lines.index(grant), "the deny must come last"

    def test_landlock_grants_the_state_and_not_the_key(self, project, tmp_path_factory):
        from rite_ai.managers.landlock import compose_policy

        # OUTSIDE the project: the project is granted writable, so a home
        # inside it would put the copy under a grant and prove nothing.
        home = tmp_path_factory.mktemp("home")
        # The policy is composed for an explicit home, so the copy is written
        # under the same one.
        cursor_login._write_key(project, "lead", FAKE, home=home)
        policy = compose_policy(project, "lead", home)
        granted = [*policy["readable"], *policy["writable"]]
        state = cursor_login.state_dir(project, "lead", home)
        assert str(state) in policy["writable"]
        key = str(cursor_login._key_path(project, "lead", home))
        assert not any(key == g or key.startswith(g.rstrip("/") + "/") for g in granted)

    @pytest.mark.skipif(sys.platform != "darwin", reason="seatbelt is macOS only")
    def test_inside_seatbelt_key_unreadable_state_writable_prefix_crosses(
        self, project
    ):
        """Run, not read. The positive control (the state directory beside
        the key IS writable) is what makes the refusal mean something."""
        from rite_ai.managers.enclosure import write_profile

        cursor_login.prepare(project, "lead", FAKE)
        profile = write_profile(project, "lead")
        key = cursor_login._key_path(project, "lead")
        state = cursor_login.state_dir(project, "lead")

        def under(command: str, prefix: str = "") -> int:
            return subprocess.run(
                [
                    "/bin/sh",
                    "-c",
                    f"{prefix}sandbox-exec -f '{profile}' /bin/sh -c '{command}'",
                ],
                capture_output=True,
                timeout=60,
            ).returncode

        assert under(f"touch {state}/written-inside") == 0, "control: state writable"
        assert (state / "written-inside").exists()
        assert under(f"cat {key} >/dev/null") != 0, "the key copy was readable"
        assert under(f"echo x > {key}") != 0, "the key copy was writable"
        assert key.read_text() == FAKE
        prefix = cursor_login.launch_prefix(project, "lead")
        assert under('test -n "$CURSOR_API_KEY"', prefix) == 0, (
            "the prefix did not deliver the key across the boundary"
        )
        assert under('test -n "$CURSOR_API_KEY"') != 0, "control: no prefix, no key"
