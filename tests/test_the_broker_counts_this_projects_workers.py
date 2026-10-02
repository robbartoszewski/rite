"""The broker's Worker cap counts THIS project's sandboxes (SCRUM-36).

🔴 `broker.for_project(...).running()` was `len(list_rite_sandboxes())`: every
`rite-` sandbox on the machine. Another project's Workers and leftover test
probes filled this project's capacity, so the broker refused a Worker for a
project that had none running. Seen in the live dogfood, where the fix was
destroying the alpha and beta sandboxes by hand. The cap is per project (SPEC
§2.5.9), so it now uses `count_active_sandboxes(root, ...)`, the count
`start_worker` already uses.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from rite_ai.managers import broker
from rite_ai.sandbox import sandbox_name

YOLOAI = "/usr/local/bin/yoloai"


def _project(tmp_path, cap=1):
    rite = tmp_path / ".rite"
    rite.mkdir(parents=True)
    (rite / "config.yaml").write_text(
        f"ticket_backend:\n  type: none\nsandbox:\n  enabled: true\n"
        f"  max_concurrent_workers: {cap}\n"
    )
    (tmp_path / "workers" / "w1").mkdir(parents=True)
    (tmp_path / "workers" / "w1" / "worker.yml").write_text("name: w1\n")
    return tmp_path


def _board():
    return SimpleNamespace(list_tickets=lambda: [SimpleNamespace(id="RT-1")])


def _ask(root, *running_names):
    """`handle()` for a request for w1 on RT-1, with yoloAI reporting
    `running_names` and the launch itself faked."""
    payload = json.dumps(
        {"sandboxes": [{"environment": {"name": n}} for n in running_names]}
    )

    def run(args, *a, **kw):
        return MagicMock(returncode=0, stdout=payload, stderr="")

    with (
        patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI),
        patch("rite_ai.sandbox.subprocess.run", side_effect=run),
        patch.object(broker, "honour", lambda root, request: (True, "started")),
    ):
        handle = broker.for_project(root, _board())
        return handle(json.dumps({"worker": "w1", "ticket": "RT-1"}))


def test_another_projects_and_test_sandboxes_do_not_fill_this_cap(tmp_path):
    root = _project(tmp_path / "here")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    ok, message = _ask(
        root,
        sandbox_name("w1", elsewhere),
        sandbox_name("alpha", elsewhere),
        "rite-selftest-abc123",
        "rite-kct-9303d6",
    )
    assert ok, message


def test_control_this_projects_own_worker_still_fills_it(tmp_path):
    root = _project(tmp_path / "here")
    ok, message = _ask(root, sandbox_name("w1", root))
    assert not ok
    assert "already running" in message


def test_a_count_that_cannot_be_read_still_refuses(tmp_path):
    root = _project(tmp_path / "here")
    with patch("rite_ai.sandbox.shutil.which", return_value=None):
        handle = broker.for_project(root, _board())
        ok, message = handle(json.dumps({"worker": "w1", "ticket": "RT-1"}))
    assert not ok and "could not count" in message
