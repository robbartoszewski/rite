"""C6. No credential may reach tmux's argv — the `tmux -e` trap.

`tmux new-session -e NAME=value` puts the value on tmux's command line, where
`ps -ww` shows it to every local account. rite already passes the Manager's
NAME that way, which is harmless — and which makes "pass the token the same
way" look like consistency. That is the most likely way the leak arrives.

⚠ These assert a POSITIVE rule over what really ran: every argv rite hands
to tmux during a real start is captured, every `-e` must name an allowed
variable, and a sentinel token must appear on none. A test built from a list
of forbidden names would miss the next credential.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest

import rite_ai.managers.session as session_mod
from rite_ai.managers.session import (
    ALLOWED_ON_TMUX_ARGV,
    CLAUDE_OAUTH_ENV,
    _on_tmux_argv,
)

SENTINEL = "sk-ant-oat01-SENTINEL-" + "x" * 24


def test_a_credential_cannot_be_put_on_tmux_argv():
    with pytest.raises(ValueError, match="argv"):
        _on_tmux_argv(CLAUDE_OAUTH_ENV, SENTINEL)


def test_the_managers_name_still_can():
    """The control: a guard that refused everything would pass the test above
    and stop every Manager knowing its own name."""
    (name,) = ALLOWED_ON_TMUX_ARGV
    assert _on_tmux_argv(name, "lead") == ["-e", f"{name}=lead"]


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")
def test_a_real_start_puts_nothing_but_allowed_names_on_tmux_argv(monkeypatch):
    from rite_ai.managers.supervise import _default_starter

    monkeypatch.setenv(CLAUDE_OAUTH_ENV, SENTINEL)
    ran: list[list[str]] = []
    real_run = subprocess.run

    def recording(argv, *a, **kw):
        if isinstance(argv, list):
            ran.append([str(x) for x in argv])
        return real_run(argv, *a, **kw)

    monkeypatch.setattr(session_mod.subprocess, "run", recording)
    root = Path(tempfile.mkdtemp(prefix="argv-"))
    (root / ".rite").mkdir()
    manager = f"lead{uuid.uuid4().hex[:6]}"
    result = _default_starter(
        root,
        manager,
        engine="sh",
        resume_id="",
        prompt="sleep 30",
        permission="",
        max_sessions=1,
        window_seconds=60,
    )
    try:
        assert result.ok, f"the start failed, so this proves nothing: {result.message}"
        tmux_calls = [a for a in ran if Path(a[0]).name == "tmux"]
        assert any("new-session" in a for a in tmux_calls), (
            "no new-session was captured — the recorder saw nothing"
        )
        for argv in tmux_calls:
            assert SENTINEL not in " ".join(argv), f"token on tmux argv: {argv}"
            for i, arg in enumerate(argv):
                if arg == "-e":
                    name = argv[i + 1].partition("=")[0]
                    assert name in ALLOWED_ON_TMUX_ARGV, (
                        f"{name} was passed with `tmux -e`, so its value is on "
                        f"argv for every local account: {argv}"
                    )
    finally:
        if result.session:
            real_run(
                ["tmux", "kill-session", "-t", f"={result.session}"],
                capture_output=True,
            )


class TestARefusalDoesNotRelayWhatTmuxEchoed:
    """C16. Three refusals put tmux's stderr in front of a user, and tmux
    echoes what it was given. Coupled to the `-e` trap above: if a value ever
    reaches tmux's argv, these are where it surfaces."""

    @pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")
    def test_a_real_refusal_carries_no_assigned_value_from_tmux(self):
        """Measured before the redaction, through `stop`: tmux answered
        `can't find window: TOKEN=<value>` and the refusal carried it. The
        session name is only the vehicle that makes tmux echo an assignment
        — rite's own names cannot contain one — so the value is allowed to
        appear ONCE, where rite repeats the name it was handed, and not
        again in what tmux said."""
        from rite_ai.managers.session import stop

        name = f"s{uuid.uuid4().hex[:6]}:TOKEN={SENTINEL}"
        made = subprocess.run(
            ["tmux", "new-session", "-d", "-s", name, "sleep 30"],
            capture_output=True,
            text=True,
        )
        assert made.returncode == 0, made.stderr
        try:
            refused = stop(name)
            assert not refused.ok, "tmux did not refuse, so this proves nothing"
            assert "TOKEN=[redacted]" in refused.detail, refused.detail
            assert refused.detail.count(SENTINEL) == 1, refused.detail
        finally:
            listed = subprocess.run(
                ["tmux", "list-sessions", "-F", "#{session_id} #{session_name}"],
                capture_output=True,
                text=True,
            ).stdout
            for line in listed.splitlines():
                sid, _, sname = line.partition(" ")
                if sname == name:
                    subprocess.run(["tmux", "kill-session", "-t", sid])

    def test_a_value_at_the_cut_leaves_only_the_marker(self):
        """At the 200-character cut, what follows `TOKEN=` is a prefix of
        the marker and never the start of the value."""
        from rite_ai.managers.session import _what_tmux_said

        said = "x" * 185 + f" TOKEN={SENTINEL}"
        done = subprocess.CompletedProcess([], 1, stdout="", stderr=said)
        out = _what_tmux_said(done)
        assert len(out) <= 200
        tail = out.split("TOKEN=", 1)[-1]
        assert tail and "[redacted]".startswith(tail), out
