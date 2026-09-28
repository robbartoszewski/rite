"""CU7: a Cursor pane ends what its engine left behind, without a pid lookup.

Cursor's `worker-server` outlives the turn that started it (CU1), and
`yoloai destroy` (CU1b). Killing it by a pid found afterwards would be a race:
the process can exit and its number be reused between the lookup and the
signal. So the pane's shell stays alive as its process group's leader and
signals its own group (`cursor_login.REAP_SUFFIX`); a live leader's group id
cannot be recycled.

The engine here is a stand-in that does what Cursor's bundle does: it starts a
child WITHOUT a new session or group (`spawn(..., {detached: false})`), then
exits with a status of its own. ⚠ The child IGNORES SIGHUP. Measured while
writing this: a child that does not is already ended by the hangup the kernel
sends when a pane's session leader exits, so without the ignore the control
below died too and the test could not tell the two mechanisms apart. Whether
Cursor's `worker-server` ignores SIGHUP is not known; the suffix covers it
either way, and it is the only thing that covers a Worker's turn run with no
terminal (`yoloai exec`), where there is no hangup at all. Whether the real
`worker-server` stays in the group needs an authenticated turn, which is held.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import uuid

import pytest

from rite_ai.managers.cursor_login import REAP_SUFFIX

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="needs tmux")


def _run_pane(tmp_path, suffix: str):
    """Start the stand-in in a real tmux pane; return (child pid, exit status,
    socket name)."""
    sock = f"cu7-{uuid.uuid4().hex[:8]}"
    childfile = tmp_path / f"child-{sock}"
    daemon = tmp_path / "daemon.py"
    ready = tmp_path / f"ready-{sock}"
    daemon.write_text(
        "import signal, sys, time\n"
        "signal.signal(signal.SIGHUP, signal.SIG_IGN)\n"
        "open(sys.argv[1], 'w').write('ready')\n"
        "stop = sys.argv[1] + '.stop'\n"
        "import os\n"
        "for _ in range(600):\n"
        "    if os.path.exists(stop): break\n"
        "    time.sleep(0.05)\n"
    )
    # The engine exits only once its child has set up its signal handling, as
    # a long-running daemon has by the time a turn ends. Without this wait the
    # kernel's hangup arrived during the child's interpreter start-up, before
    # the ignore was in place, and killed it (measured).
    starter = tmp_path / "engine.py"
    starter.write_text(
        "import os, subprocess, sys, time\n"
        f"p = subprocess.Popen([sys.executable, {str(daemon)!r}, {str(ready)!r}])\n"
        f"open({str(childfile)!r}, 'w').write(str(p.pid))\n"
        f"while not os.path.exists({str(ready)!r}): time.sleep(0.05)\n"
        "sys.exit(3)\n"
    )
    engine = f"{sys.executable} {starter}"
    subprocess.run(
        ["tmux", "-L", sock, "new-session", "-d", "-s", "t", "sh"], check=True
    )
    subprocess.run(["tmux", "-L", sock, "set-option", "-g", "remain-on-exit", "on"])
    subprocess.run(
        ["tmux", "-L", sock, "respawn-pane", "-k", "-t", "t", engine + suffix],
        check=True,
    )
    deadline = time.time() + 20
    status = ""
    while time.time() < deadline:
        out = subprocess.run(
            [
                "tmux",
                "-L",
                sock,
                "display",
                "-p",
                "-t",
                "t",
                "#{pane_dead} #{pane_dead_status}",
            ],
            capture_output=True,
            text=True,
        ).stdout.split()
        if out and out[0] == "1":
            status = out[1] if len(out) > 1 else ""
            break
        time.sleep(0.2)
    time.sleep(1)
    return int(childfile.read_text()), status, sock, ready


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_the_leftover_child_is_ended_and_the_engines_status_kept(tmp_path):
    child, status, sock, _ = _run_pane(tmp_path, REAP_SUFFIX)
    try:
        assert status == "3", "the pane must report the ENGINE's exit status"
        assert not _alive(child), "a child the engine left behind survived"
    finally:
        subprocess.run(["tmux", "-L", sock, "kill-server"], capture_output=True)


def test_control_without_the_suffix_the_child_survives(tmp_path):
    """Without this, a passing test above could mean the stand-in's child
    never lived."""
    child, status, sock, ready = _run_pane(tmp_path, "")
    try:
        assert status == "3"
        assert _alive(child), "control: the stand-in's child should outlive it"
    finally:
        # Ended by asking it to stop, never by the pid read above: it ignores
        # the hangup, and it exits by itself within 30 s regardless.
        (ready.parent / (ready.name + ".stop")).write_text("")
        subprocess.run(["tmux", "-L", sock, "kill-server"], capture_output=True)
