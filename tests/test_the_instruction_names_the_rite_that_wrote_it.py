"""An instruction names the `rite` that composed it, not whatever is on PATH.

⚠ **Measured 2026-09-25:** a Manager resolved `~/.local/bin/rite` → **0.4.0**
while the code writing its instructions was 0.5.1. It was told to run
`rite reply`, which 0.4.0 does not have, and got:

    Usage: rite [OPTIONS] COMMAND [ARGS]...
    Try 'rite --help' for help.

exit 2 — naming neither the version nor the path. A Manager reads that as bad
syntax and retries, which is the same wearing-familiar-words shape as a
sandbox denial that looks like a broken tool.

⚠ **Option 3 of three, chosen by scope rather than by design.** A pre-flight
version check and a warn-and-continue were both considered and deliberately
NOT built: rite has no users today and the dogfood starts on v0.6.0, so the
prompt text and the binary come from the same release and the skew cannot
arise for the person who matters — while a refusal would block a user
mid-upgrade tomorrow. Naming the binary removes the class instead of
detecting it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from rite_ai import own_command
from rite_ai.managers.mailbox import OUTBOX, how_to_reply, read
from rite_ai.managers.prompt import for_manager


class TestItResolvesThisInstall:
    def test_it_is_an_absolute_path_that_exists(self):
        resolved = own_command()
        assert Path(resolved).is_absolute()
        assert Path(resolved).is_file()

    def test_it_is_this_environments_rite(self):
        """⚠ `sys.prefix`, not `sys.executable`: `update.detect` measured
        that under `uv` the interpreter resolves THROUGH a symlink to a
        shared toolchain python, so its directory is not the tool's `bin`."""
        assert own_command() == str(Path(sys.prefix) / "bin" / "rite")

    def test_it_falls_back_to_the_bare_name_rather_than_a_path_that_is_not_there(
        self, monkeypatch, tmp_path
    ):
        """An instruction saying `rite` is worse than one naming the right
        path and better than one naming a path that does not exist."""
        monkeypatch.setattr(sys, "prefix", str(tmp_path))
        monkeypatch.setattr(sys, "executable", str(tmp_path / "bin" / "python"))
        assert own_command() == "rite"


class TestTheInstructionsNameIt:
    def test_the_reply_instruction_does(self, tmp_path):
        said = how_to_reply(tmp_path, "lead")
        assert f"{own_command()} reply --manager lead" in said

    def test_it_is_not_a_bare_rite_any_more(self, tmp_path):
        """The exact string that failed against an older PATH rite."""
        assert "  rite reply --manager" not in how_to_reply(tmp_path, "lead")

    def test_the_opening_prompt_does_too(self):
        said = for_manager("lead")
        assert own_command() in said

    def test_what_the_USER_runs_is_still_a_bare_name(self, tmp_path):
        """⚠ Deliberate. `rite connect` is typed by a person into their own
        shell; their PATH is their business, and an absolute path into a
        scratch venv would be wrong advice."""
        assert "`rite connect lead`" in how_to_reply(tmp_path, "lead")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell fixture")
class TestAManagerWithAnOlderRiteOnPathStillSucceeds:
    """⚠ **The half that matters.** A test that only checks the string is the
    proxy this release refuses — the property is that the command RUNS."""

    def _project(self, tmp_path):
        (tmp_path / ".rite" / "managers" / "lead").mkdir(parents=True)
        (tmp_path / ".rite" / "brief.yaml").write_text(
            "project:\n  name: t\n  role: owner\n"
        )
        (tmp_path / ".rite" / "config.yaml").write_text(
            "coordination:\n"
            "  managers:\n    - lead\n"
            "  manager_roles:\n    - name: lead\n      engine: claude\n"
        )
        return tmp_path

    def _older_rite_first_on_path(self, tmp_path):
        """A `rite` with no `reply`, which is what 0.4.0 is."""
        where = tmp_path / "oldbin"
        where.mkdir()
        (where / "rite").write_text(
            "#!/bin/sh\n"
            'case "$1" in --version) echo "rite, version 0.4.0"; exit 0;; esac\n'
            'echo "Usage: rite [OPTIONS] COMMAND [ARGS]..."\n'
            "exit 2\n"
        )
        (where / "rite").chmod(0o755)
        return where

    def test_the_bare_name_fails_and_the_written_instruction_does_not(self, tmp_path):
        root = self._project(tmp_path / "proj")
        older = self._older_rite_first_on_path(tmp_path)
        env = dict(
            os.environ,
            PATH=f"{older}:{os.environ['PATH']}",
            RITE_PROJECT_ROOT=str(root),
        )
        message = "hello from the manager"

        # What shipped before: the bare name, against that PATH.
        bare = subprocess.run(
            ["sh", "-c", f'rite reply --manager lead "{message}"'],
            capture_output=True,
            text=True,
            env=env,
            cwd=root,
            timeout=120,
        )
        assert bare.returncode != 0
        assert "Usage: rite" in (bare.stdout or "") + (bare.stderr or "")

        # What rite writes now, against the SAME PATH.
        line = next(
            line.strip()
            for line in how_to_reply(root, "lead").splitlines()
            if "reply --manager" in line
        )
        run = subprocess.run(
            ["sh", "-c", line.replace("<your message>", message)],
            capture_output=True,
            text=True,
            env=env,
            cwd=root,
            timeout=120,
        )
        assert run.returncode == 0, (run.stdout or "") + (run.stderr or "")

        # ⚠ And the message actually arrived — not merely a zero exit.
        waiting = read(root, "lead", OUTBOX)
        assert [m.text for m in waiting] == [message]
