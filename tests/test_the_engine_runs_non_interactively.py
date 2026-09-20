"""Option 1: the engine exits when its turn is done, so `ending` does real work.

⚠ **The cycle boundary is an OBSERVED process exit, not a proxy.** An
interactive `claude` never exits, so a supervisor that wants cycles has to
decide a cycle ended by watching for something else — idleness, output
quiet, a timer. Every one of those is a classifier over a signal that means
other things too, which is the defect class this release spent an afternoon
removing from `ending`. `-p` makes the engine's own exit the boundary, and
`ending` already reads exit statuses correctly.

**The token reaches the engine by INHERITANCE and must never be an
argument.** `tmux new-session -e VAR=value` puts the value on tmux's argv,
readable by `ps` from any local account. A pane inherits the environment of
the process that asked for it, so a token already in rite's environment
arrives with nothing to redact — rite never holds it.
"""

from __future__ import annotations

import pytest

from rite_ai.managers.session import CLAUDE_OAUTH_ENV, token_is_absent
from rite_ai.managers.supervise import launch_command


class TestTheEngineIsToldToExit:
    def test_the_launch_command_is_non_interactive(self):
        assert launch_command("claude").endswith("claude -p")

    def test_a_resume_is_still_non_interactive(self):
        assert launch_command("claude", "abc-123").endswith(
            "claude -p --resume abc-123"
        )

    def test_another_engine_gets_the_same_flag_because_there_is_no_registry(self):
        """Stated rather than silently true: `launch_command`'s docstring
        already says the engine string is an executable name and `--resume`
        is Claude Code's spelling appended unconditionally. `-p` is the
        same bet and no worse, and pretending otherwise would need a
        registry this module deliberately does not have."""
        assert launch_command("someotherengine").endswith("someotherengine -p")


class TestTheTokenIsNeverAnArgument:
    def test_the_launch_command_never_carries_a_credential(self, monkeypatch):
        monkeypatch.setenv(CLAUDE_OAUTH_ENV, "sk-secret-value")
        built = launch_command("claude", "abc-123")
        assert "sk-secret-value" not in built
        assert CLAUDE_OAUTH_ENV not in built

    def test_start_does_not_put_the_token_on_tmuxs_argv(self, monkeypatch, tmp_path):
        """`-e` would be the obvious place and it is the wrong one: the
        value lands on tmux's own argv, which `ps` shows to every local
        account."""
        import rite_ai.managers.session as session_mod

        seen: list[list[str]] = []

        class Refused:
            returncode = 1
            stderr = "refused by the test"
            stdout = ""

        def capture(argv, **kwargs):
            if isinstance(argv, list):
                seen.append(argv)
            return Refused()

        monkeypatch.setenv(CLAUDE_OAUTH_ENV, "sk-secret-value")
        monkeypatch.setattr(session_mod, "_tmux", lambda: "/usr/bin/tmux")
        monkeypatch.setattr(
            session_mod, "liveness", lambda _n: session_mod.Liveness(False, known=True)
        )
        monkeypatch.setattr(session_mod, "session_exists", lambda _n: False)
        monkeypatch.setattr(session_mod.subprocess, "run", capture)
        (tmp_path / ".rite").mkdir()
        session_mod.start(
            tmp_path, "lead", engine="claude", max_sessions=1, window_seconds=0
        )
        assert seen, "no tmux invocation was captured — the test proves nothing"
        for argv in seen:
            joined = " ".join(argv)
            assert "sk-secret-value" not in joined, f"token on argv: {joined}"


class TestAMissingTokenIsNamedBeforeItCosts:
    def test_absent_is_detected(self, monkeypatch):
        monkeypatch.delenv(CLAUDE_OAUTH_ENV, raising=False)
        assert token_is_absent()

    def test_present_is_detected(self, monkeypatch):
        monkeypatch.setenv(CLAUDE_OAUTH_ENV, "sk-anything")
        assert not token_is_absent()

    def test_whitespace_is_absent(self, monkeypatch):
        monkeypatch.setenv(CLAUDE_OAUTH_ENV, "   ")
        assert token_is_absent(), "a blank value is not a credential"

    def test_the_refusal_names_the_token_and_how_to_mint_one(
        self, monkeypatch, tmp_path
    ):
        """⚠ **The user must not be told their credential might be
        invalid.** That message exists for a real ambiguity rite cannot
        resolve — but when the variable is simply not set there is no
        ambiguity, and sending someone to re-authenticate a working login
        is the wrong instruction."""
        import rite_ai.managers.session as session_mod

        monkeypatch.delenv(CLAUDE_OAUTH_ENV, raising=False)
        (tmp_path / ".rite").mkdir()
        result = session_mod.start(
            tmp_path, "lead", engine="claude", max_sessions=1, window_seconds=0
        )
        assert not result.ok
        assert CLAUDE_OAUTH_ENV in result.message
        assert "claude setup-token" in result.message
        assert "invalid" not in result.message.lower(), (
            "this path knows the variable is unset, so it must not raise "
            f"the possibility of a bad credential: {result.message!r}"
        )

    def test_an_explicit_command_is_not_second_guessed(self, monkeypatch, tmp_path):
        """A caller that passed its own command is not launching Claude
        Code, and refusing it for a Claude Code variable would break every
        test in this suite that starts a stub."""
        import rite_ai.managers.session as session_mod

        monkeypatch.delenv(CLAUDE_OAUTH_ENV, raising=False)
        (tmp_path / ".rite").mkdir()
        monkeypatch.setattr(session_mod, "_tmux", lambda: None)
        result = session_mod.start(
            tmp_path, "lead", command="sleep 60", max_sessions=1, window_seconds=0
        )
        assert CLAUDE_OAUTH_ENV not in result.message


class TestThePromptGoesInAtLaunch:
    """⚠ **Measured, not reasoned.** `claude -p` exits 1 with "Input must be
    provided either through stdin or as a prompt argument when using
    --print" if it is launched with nothing on stdin. rite used to type the
    prompt in after the session started, which is correct for a REPL and
    cannot work for a command that has already exited by then — the
    acceptance run got zero cycles because of it.
    """

    def test_the_prompt_is_read_from_a_file_on_stdin(self):
        built = launch_command("claude", "", "/p/prompt.txt")
        assert built == "claude -p < /p/prompt.txt", built

    def test_a_resume_still_gets_its_input(self):
        built = launch_command("claude", "abc-123", "/p/prompt.txt")
        assert built == "claude -p --resume abc-123 < /p/prompt.txt", built

    def test_the_prompt_is_never_an_argument(self, monkeypatch, tmp_path):
        """A prompt quotes ticket text, paths and internal names, and
        `tmux new-session <cmd>` puts its command on tmux's argv."""
        import rite_ai.managers.session as session_mod

        secret = "ACME-1234-internal-hostname"
        seen: list[list[str]] = []

        class Refused:
            returncode = 1
            stderr = "refused by the test"
            stdout = ""

        def capture(argv, **kwargs):
            if isinstance(argv, list):
                seen.append(argv)
            return Refused()

        monkeypatch.setenv(CLAUDE_OAUTH_ENV, "sk-anything")
        monkeypatch.setattr(session_mod, "_tmux", lambda: "/usr/bin/tmux")
        monkeypatch.setattr(
            session_mod, "liveness", lambda _n: session_mod.Liveness(False, known=True)
        )
        monkeypatch.setattr(session_mod, "session_exists", lambda _n: False)
        monkeypatch.setattr(session_mod.subprocess, "run", capture)
        (tmp_path / ".rite").mkdir()
        session_mod.start(
            tmp_path,
            "lead",
            engine="claude",
            prompt=f"Work ticket {secret}",
            max_sessions=1,
            window_seconds=0,
        )
        assert seen, "no tmux invocation captured — this proves nothing"
        for argv in seen:
            assert secret not in " ".join(argv), f"prompt on argv: {argv}"

    def test_the_prompt_is_written_where_the_engine_reads_it(
        self, monkeypatch, tmp_path
    ):
        """⚠ A FILE, not an inherited variable: a pane takes its
        environment from the tmux SERVER, not from the client that asked,
        so an exported prompt never reaches an already-running server."""
        import rite_ai.managers.session as session_mod
        from rite_ai.managers import manager_dir
        from rite_ai.managers.session import PROMPT_FILE

        class Refused:
            returncode = 1
            stderr = "refused"
            stdout = ""

        monkeypatch.setenv(CLAUDE_OAUTH_ENV, "sk-anything")
        monkeypatch.setattr(session_mod, "_tmux", lambda: "/usr/bin/tmux")
        monkeypatch.setattr(
            session_mod, "liveness", lambda _n: session_mod.Liveness(False, known=True)
        )
        monkeypatch.setattr(session_mod, "session_exists", lambda _n: False)
        monkeypatch.setattr(session_mod.subprocess, "run", lambda *a, **k: Refused())
        (tmp_path / ".rite").mkdir()
        session_mod.start(
            tmp_path,
            "lead",
            engine="claude",
            prompt="do the thing",
            max_sessions=1,
            window_seconds=0,
        )
        written = manager_dir(tmp_path, "lead") / PROMPT_FILE
        assert written.read_text() == "do the thing"
        assert oct(written.stat().st_mode)[-3:] == "600"

    def test_a_resumed_cycle_is_told_to_continue_not_to_start_again(self):
        """Re-issuing the opening instruction to a session that already did
        the work is how it gets done twice — the concern D-90 recorded."""
        from rite_ai.managers.supervise import CONTINUATION

        opening = "Implement ticket ACME-1 and open a PR."
        assert CONTINUATION != opening
        assert "continue" in CONTINUATION.lower()


class TestASessionThatDiedIsNotASessionThatStarted:
    """⚠ **`tmux new-session` reports CREATION, not survival**, which is the
    same proxy-measurement shape as everything else fixed today. Pinned
    because the acceptance run depended on `settled_alive` catching it, and
    it did: zero cycles and a message naming the engine, rather than a
    supervisor waiting on a session with nothing in it."""

    def test_a_command_that_exits_at_once_is_reported_as_not_running(self, tmp_path):
        import subprocess as sp

        from rite_ai.managers.session import session_name, start, stop

        if sp.run(["which", "tmux"], capture_output=True).returncode != 0:
            pytest.skip("needs real tmux; mocking it is the bug")
        (tmp_path / ".rite").mkdir()
        name = session_name(tmp_path, "lead")
        stop(name)
        try:
            result = start(
                tmp_path,
                "lead",
                command="sh -c 'exit 1'",
                max_sessions=1,
                window_seconds=0,
            )
            assert not result.ok, (
                "tmux created the session, so `new-session` succeeded — and "
                "the command inside it was already gone"
            )
            assert "exited immediately" in result.message, result.message
        finally:
            stop(name)
