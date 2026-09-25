"""A gone designation must actually start fresh, on a real tmux server.

WHY THIS EXISTS. Robert's rule: if the designated session no longer exists,
`rite start X` is `rite start X --fresh`. The supervisor said so — "could not
be continued, so this run starts FRESH" — and then started nothing. Measured
through `rite start` with an engine that behaves like `claude -p --resume
<gone>` (which prints "No conversation found" and exits 1): the resume
attempt died in its pane, `remain-on-exit` held the dead session under the
Manager's name, and the fresh launch hit `start`'s "left over from an earlier
run" refusal.

Every existing test of the fallback injects `starter`, so no second
`tmux new-session` ever ran against a held session — the same blind spot that
hid the resume path's `duplicate session` defect earlier.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest

from known_session import designate_known
from rite_ai.managers.supervise import supervise

pytestmark = pytest.mark.skipif(
    shutil.which("tmux") is None, reason="tmux is not installed"
)


def test_the_fresh_fallback_launches_after_a_resume_dies_in_its_pane():
    work = Path(tempfile.mkdtemp(prefix="gone-"))
    root = work / "proj"
    (root / ".rite").mkdir(parents=True)
    # ⚠ Inside the project, because a Manager now runs in a sandbox (B9)
    # and the profile grants the project tree, system paths and the engines'
    # own locations — not an arbitrary temp directory beside it. A real
    # engine lives in one of those; only a stub needs saying.
    log = root / "launches.log"
    engine = root / "engine"
    # Behaves like `claude -p`: a gone `--resume` exits 1 at once; a fresh
    # start reads its prompt and runs for a moment.
    engine.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> {log}\n'
        "cat > /dev/null\n"
        'case "$*" in *--resume*) exit 1;; esac\n'
        "sleep 3\n"
    )
    engine.chmod(0o755)
    manager = f"lead{uuid.uuid4().hex[:6]}"
    designate_known(root, manager, "00000000-0000-4000-8000-000000000000")
    said: list[str] = []
    try:
        outcome = supervise(
            root,
            manager,
            engine=str(engine),
            prompt="go",
            max_sessions=1,
            window_seconds=20,
            verdict=lambda _r: "ready",
            resume_id_for=lambda *_a: "",
            note=said.append,
        )
    finally:
        listed = subprocess.run(
            ["tmux", "ls", "-F", "#{session_name}"], capture_output=True, text=True
        ).stdout
        for name in listed.split():
            if name.endswith(manager):
                subprocess.run(["tmux", "kill-session", "-t", f"={name}"])
    launches = log.read_text().splitlines()
    assert "could not be continued" in " ".join(said), said
    assert "left over" not in outcome.reason, outcome.reason
    assert len(launches) == 2 and "--resume" not in launches[1], (
        f"the announced fresh start never launched: {launches}\n{outcome.reason}"
    )
