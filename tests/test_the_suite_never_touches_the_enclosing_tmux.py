"""The suite never touches the tmux server it is run from.

FOUND 2026-09-29, live: a full suite started inside a tmux session killed
that session's whole server about a minute in, and with it a supervisor
holding a live refinement exchange with Robert. The cause:
`test_a_loop_nobody_could_ask_about_is_not_absent` gives itself a private
server with `TMUX_TMPDIR`, but inside a tmux session `$TMUX` names the
enclosing server and tmux obeys it first, so its `display`, SIGSTOP and
`kill-server` all landed on the server the suite was running in: everyone's
default one.

So no test sees `$TMUX` (conftest), and this pins it: a sentinel server,
named by `$TMUX` exactly as tmux sets it for a session, survives a run of the
test that killed the real one.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("tmux") is None, reason="tmux is not installed"
)

ROOT = Path(__file__).resolve().parent.parent


def _sentinel():
    """A tmux server of our own, and the `$TMUX` value a session on it has."""
    where = Path(tempfile.mkdtemp(prefix="sentinel-", dir="/tmp"))
    sock = where / "s"
    subprocess.run(
        ["tmux", "-S", str(sock), "new-session", "-d", "-s", "sentinel", "sleep 600"],
        check=True,
    )
    pid = subprocess.run(
        ["tmux", "-S", str(sock), "display", "-p", "#{pid}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return sock, f"{sock},{pid},0"


def _alive(sock) -> bool:
    return (
        subprocess.run(
            ["tmux", "-S", str(sock), "has-session", "-t", "sentinel"],
            capture_output=True,
        ).returncode
        == 0
    )


def test_a_run_from_inside_tmux_leaves_that_server_alone():
    sock, tmux_env = _sentinel()
    try:
        env = {**os.environ, "TMUX": tmux_env, "TMUX_PANE": "%0"}
        done = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "tests/test_a_loop_nobody_could_ask_about_is_not_absent.py",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert _alive(sock), (
            "the enclosing tmux server was killed by the suite\n" + done.stdout[-2000:]
        )
    finally:
        subprocess.run(["tmux", "-S", str(sock), "kill-server"], capture_output=True)


def test_no_test_sees_the_enclosing_tmux():
    assert "TMUX" not in os.environ and "TMUX_PANE" not in os.environ
