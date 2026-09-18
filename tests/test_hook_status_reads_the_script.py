"""A pre-push hook that only MENTIONS the gate is not running it.

`gate_hook_status` decided a hook runs the gate with
`_is_rite_installed(text) or "rite publish pre-push" in text` — a marker, or
a substring. Neither is the question:

* the marker says rite AUTHORED the file, and the first thing anyone does to
  a generated hook is edit it;
* a substring is true of a hook that mentions the command in a comment, and
  true of the line someone commented out to get one push through.

Both leave `rite doctor` printing `publish gate hook: active` over a hook git
runs and that does nothing — SPEC 11.5.1's shape exactly, and the reason the
CI half of the same question stopped substring-matching. The two halves now
use one inspector, so a case fixed for one is fixed for both.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rite_ai.gate.hook import (
    PRE_PUSH_HOOK_SCRIPT,
    gate_hook_status,
    install_pre_push_hook,
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def _hook(repo: Path, text: str) -> None:
    path = repo / ".git" / "hooks" / "pre-push"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


class TestAMentionIsNotAnInvocation:
    def test_ritess_own_hook_with_the_exec_line_commented_out(self, repo):
        """THE DEFECT. The marker is still there, so this reported active."""
        _hook(repo, PRE_PUSH_HOOK_SCRIPT.replace("exec rite", "# exec rite"))

        status = gate_hook_status(repo)

        assert not status.active, status.detail

    def test_a_hook_that_only_talks_about_the_command(self, repo):
        _hook(
            repo,
            "#!/bin/sh\n"
            "# to re-enable the gate, put `rite publish pre-push` back below\n"
            'echo "skipping rite publish pre-push for now"\n',
        )

        assert not gate_hook_status(repo).active

    def test_a_hook_that_cannot_fail(self, repo):
        """A gate that cannot go red is not a gate — 11.5.1 again."""
        _hook(repo, "#!/bin/sh\nrite publish pre-push || true\n")

        assert not gate_hook_status(repo).active

    def test_errexit_switched_off(self, repo):
        _hook(repo, "#!/bin/sh\nset +e\nrite publish pre-push\n")

        assert not gate_hook_status(repo).active


class TestTheOrdinaryCasesStillPass:
    """The direction this must not be wrong in twice. A false alarm on a
    working hook is how someone learns to ignore `rite doctor`."""

    def test_the_hook_rite_actually_installs(self, repo):
        assert install_pre_push_hook(repo).ok

        status = gate_hook_status(repo)

        assert status.active, status.detail

    def test_a_hand_written_hook_counts_too(self, repo):
        """What matters is that the gate runs, not who typed it — this is
        what `rite init` tells a user with `core.hooksPath` set to do."""
        _hook(repo, "#!/bin/sh\nset -e\nexec rite publish pre-push\n")

        assert gate_hook_status(repo).active

    def test_a_hook_that_does_other_work_first(self, repo):
        _hook(
            repo,
            "#!/bin/sh\nset -e\nnpm run lint\nrite publish pre-push\n",
        )

        assert gate_hook_status(repo).active


class TestItSaysWhoseHookItIs:
    """Deciding liveness from the script makes the not-running message
    reachable for a hook rite DID write. Reporting that one as "not
    installed by rite" is a false claim about the user's own repository, and
    it points them at someone else's file instead of at their own edit."""

    def test_a_disarmed_rite_hook_is_not_called_foreign(self, repo):
        _hook(repo, PRE_PUSH_HOOK_SCRIPT.replace("exec rite", "# exec rite"))

        status = gate_hook_status(repo)

        assert "was not installed by rite" not in status.detail
        assert "written by rite" in status.detail
        assert status.state == "disarmed"

    def test_a_genuinely_foreign_hook_still_says_so(self, repo):
        _hook(repo, "#!/bin/sh\nnpm run lint\n")

        status = gate_hook_status(repo)

        assert "was not installed by rite" in status.detail
        assert status.state == "foreign"
