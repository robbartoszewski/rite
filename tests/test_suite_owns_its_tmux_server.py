"""The suite must not share a tmux server with the machine it runs on (C1).

WHY THIS EXISTS. Every `tmux` call in the suite and in the product uses the
DEFAULT socket, so `pytest` and a Manager an operator started with `rite
start` landed on the same server. Two consequences, both measured:

- a stray `rite-loop-looptest-*` session appeared during a run of a file that
  creates no such session — it belonged to a concurrently running suite;
- a control run had a **five-file markdown-only diff flip a tmux-detection
  test**. A documentation change cannot affect tmux; a neighbour can.

⚠ And the failure that matters most is the one nobody would see as a test
problem: a suite tearing down "leftover" sessions could kill the Manager an
operator is actually running.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid

import pytest

_HAS_TMUX = shutil.which("tmux") is not None


def test_the_suite_has_its_own_socket_directory():
    """The variable is set for every test, including ones that never touch
    tmux — the product reads it too, and it inherits our environment."""
    assert os.environ.get("TMUX_TMPDIR"), (
        "TMUX_TMPDIR is unset, so this test and the code it exercises are "
        "talking to the machine's own tmux server"
    )


def test_the_socket_path_stays_under_the_unix_limit():
    """⚠ The trap this fixture was written around. A socket path at or over
    ~104 bytes fails with 'File name too long', which names neither tmux nor
    the fixture — an hour of debugging for a constant."""
    socket = os.path.join(os.environ["TMUX_TMPDIR"], f"tmux-{os.getuid()}", "default")
    assert len(socket) < 104, f"{len(socket)} bytes: {socket}"


@pytest.mark.skipif(not _HAS_TMUX, reason="tmux is not installed")
def test_a_session_this_suite_starts_is_invisible_on_the_default_socket():
    """The observation C1 exists for, from the suite's side.

    A session started under the suite's `TMUX_TMPDIR` must not appear on the
    machine's own server — which is the same property, viewed from the other
    end, as an operator's Manager being invisible to us.
    """
    name = f"rite-c1-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", name, "sleep 30"],
        capture_output=True,
        check=False,
    )
    try:
        ours = subprocess.run(
            ["tmux", "has-session", "-t", f"={name}"], capture_output=True
        )
        assert ours.returncode == 0, (
            "the suite could not see a session it just created on its own "
            "socket, so this proves nothing about isolation"
        )

        # The machine's own server, reached by clearing the variable for one
        # call rather than by guessing its socket path.
        default_env = dict(os.environ)
        default_env.pop("TMUX_TMPDIR", None)
        theirs = subprocess.run(
            ["tmux", "has-session", "-t", f"={name}"],
            capture_output=True,
            env=default_env,
        )
        assert theirs.returncode != 0, (
            f"{name} is visible on the machine's default tmux server — the "
            "suite and the operator are still sharing one server"
        )
    finally:
        subprocess.run(["tmux", "kill-session", "-t", f"={name}"], capture_output=True)
