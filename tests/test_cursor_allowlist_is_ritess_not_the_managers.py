"""CU8: a Cursor Manager's allowlist is written by rite, and the Manager cannot
rewrite it on macOS.

Cursor reads its allowlist from `cli-config.json` in its config directory, a
file it rewrites itself on every run: it keeps `permissions`, `approvalMode`
and `attribution` and drops keys it does not know (measured 2026-09-28, with
no credential). A boundary defined by a file the bounded process controls is
no boundary. So the supervisor writes the file before every launch, from
outside, the Manager's profile denies writing it, and the supervisor checks
it after every cycle.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from rite_ai.managers import cursor_login, github_access
from rite_ai.managers.permissions import TOOL_ALLOW


@pytest.fixture
def project(tmp_path):
    (tmp_path / ".rite").mkdir()
    return tmp_path


class TestTheTranslation:
    def test_a_shell_entry_becomes_cursors_spelling(self):
        assert cursor_login._cursor_allowlist(("Bash(git:*)",)) == ["Shell(git)"]

    def test_claudes_own_tool_names_are_skipped_by_name(self):
        assert cursor_login._cursor_allowlist(TOOL_ALLOW) == []

    @pytest.mark.parametrize(
        "entry", ["WebFetch", "Bash(git status)", "Bash(rm -rf:*)", "Bash(:*)"]
    )
    def test_an_entry_it_cannot_translate_is_refused_not_dropped(self, entry):
        with pytest.raises(ValueError, match="refused rather than dropped"):
            cursor_login._cursor_allowlist((entry,))

    def test_the_shipped_list_translates_whole(self):
        from rite_ai.managers.permissions import DEFAULT_ALLOW

        shell = [e for e in DEFAULT_ALLOW if e not in TOOL_ALLOW]
        assert len(cursor_login._cursor_allowlist()) == len(shell)

    def test_attribution_to_cursor_is_off(self):
        attribution = cursor_login.rite_config()["attribution"]
        assert attribution == {
            "attributeCommitsToAgent": False,
            "attributePRsToAgent": False,
        }


class TestTheCheckAfterACycle:
    def test_what_rite_wrote_passes(self, project):
        cursor_login.write_config(project, "lead")
        assert cursor_login.config_problem(project, "lead") == ""

    def test_cursors_own_rewrite_passes(self, project):
        """Cursor drops keys it does not know and adds its own, keeping the
        three rite decides (measured). That is not tampering."""
        path = cursor_login.write_config(project, "lead")
        body = json.loads(path.read_text())
        body["privacyCache"] = {"ghostMode": True}
        body["display"] = {"mode": "zen"}
        path.write_text(json.dumps(body))
        assert cursor_login.config_problem(project, "lead") == ""

    @pytest.mark.parametrize(
        "change",
        [
            lambda b: b["permissions"]["allow"].append("Shell(curl)"),
            lambda b: b.__setitem__("approvalMode", "unrestricted"),
            lambda b: b["attribution"].__setitem__("attributeCommitsToAgent", True),
        ],
    )
    def test_a_changed_allowlist_is_a_problem(self, project, change):
        path = cursor_login.write_config(project, "lead")
        body = json.loads(path.read_text())
        change(body)
        path.write_text(json.dumps(body))
        assert "not the one rite wrote" in cursor_login.config_problem(project, "lead")

    def test_a_missing_config_is_a_problem_not_fine(self, project):
        assert "could not be read" in cursor_login.config_problem(project, "lead")


class TestTheBoundary:
    def test_seatbelt_denies_writing_it_after_granting_the_directory(self, project):
        cursor_login.state_dir(project, "lead").mkdir(parents=True)
        lines = "\n".join(github_access.profile_lines(project, "lead"))
        state = cursor_login.state_dir(project, "lead")
        grant = f'(allow file-read* file-write* (subpath "{state}"))'
        deny = f'(deny file-write* (literal "{state / "cli-config.json"}"))'
        assert grant in lines and deny in lines
        assert lines.index(deny) > lines.index(grant), "the deny must come last"

    @pytest.mark.skipif(sys.platform != "darwin", reason="seatbelt is macOS only")
    def test_inside_seatbelt_the_manager_cannot_change_it(self, project):
        """Run, not read. The control is a write BESIDE it, which succeeds."""
        import shlex

        from rite_ai.managers.enclosure import write_profile

        path = cursor_login.write_config(project, "lead")
        before = path.read_bytes()
        profile = write_profile(project, "lead")
        q = shlex.quote
        state = cursor_login.state_dir(project, "lead")

        def under(command: str) -> int:
            return subprocess.run(
                ["sandbox-exec", "-f", str(profile), "/bin/sh", "-c", command],
                capture_output=True,
                timeout=60,
            ).returncode

        assert under(f"echo x > {q(str(state / 'beside'))}") == 0, "control"
        assert under(f"echo '{{}}' > {q(str(path))}") != 0, "overwrite"
        evil = q(str(state / "evil"))
        assert under(f"echo '{{}}' > {evil} && mv {evil} {q(str(path))}") != 0
        assert under(f"rm -f {q(str(path))} && test ! -e {q(str(path))}") != 0
        assert under(f"chmod 666 {q(str(path))}") != 0, "chmod"
        assert path.read_bytes() == before


class TestTheSupervisor:
    def test_it_is_written_before_the_first_launch_and_a_change_stops_the_run(
        self, project, monkeypatch
    ):
        import rite_ai.managers.supervise as sup
        from rite_ai.managers.session import StartResult

        state = project / "cursor-state"
        monkeypatch.setattr(
            sup.cursor_chat, "config_dir", lambda r, m, home=None: state
        )
        monkeypatch.setattr(cursor_login, "state_dir", lambda r, m, home=None: state)
        monkeypatch.setattr(
            sup, "liveness", lambda n: type("L", (), {"alive": False, "known": True})()
        )
        monkeypatch.setattr(sup, "was_attached", lambda n: False)
        monkeypatch.setattr(sup, "stop_session", lambda n: None)
        monkeypatch.setattr(
            sup,
            "ending",
            lambda n, human_was_present, pane="": type(
                "E", (), {"kind": "finished", "resume": True, "status": 0, "detail": ""}
            )(),
        )
        seen_at_launch = []

        def cursor(root, manager, *, engine, resume_id, **kw):
            path = state / "cli-config.json"
            seen_at_launch.append(json.loads(path.read_text()))
            chat = state / "chats" / "h" / resume_id
            chat.mkdir(parents=True, exist_ok=True)
            (chat / "meta.json").write_text(json.dumps({"createdAtMs": 1}))
            # Something inside the boundary widens its own allowlist.
            body = json.loads(path.read_text())
            body["permissions"]["allow"].append("Shell(curl)")
            path.write_text(json.dumps(body))
            return StartResult(True, "ok", session="s", attach="a", pane="%1")

        said: list[str] = []
        outcome = sup.supervise(
            project,
            "lead",
            engine="cursor",
            prompt="go",
            max_sessions=3,
            window_seconds=0,
            verdict=lambda _r: "ready",
            starter=cursor,
            note=said.append,
            poll=0,
        )
        assert (
            seen_at_launch[0]["permissions"]
            == cursor_login.rite_config()["permissions"]
        )
        assert len(seen_at_launch) == 1, "a cycle ran after the allowlist changed"
        assert not outcome.ok and "not the one rite wrote" in outcome.reason


def test_linux_is_told_the_manager_can_write_it(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert "CAN write it" in cursor_login.announcement("lead")
