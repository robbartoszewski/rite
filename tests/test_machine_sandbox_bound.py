"""A bound on how many sandboxes THIS MACHINE runs, whoever is asking.

`sandbox.max_concurrent_workers` is per Manager (SPEC §2.5.9) and lives in
a project's committed `config.yaml`, so it cannot express a fact about the
box: four projects each correctly capped at five give twenty Claude
sessions on one laptop and no project is at fault. Machine-wide counting
used to provide this by ACCIDENT — the project cap counted every `rite-`
sandbox — and narrowing that count to the project it belongs to (rightly)
removed the accident with it.

Unset means unbounded, so an upgrade changes nothing for a fleet already
running. That is the property most of these tests are about.
"""

from __future__ import annotations

import contextlib
import json

import pytest

from rite_ai.machine import MAX_SANDBOXES_ENV, max_sandboxes


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path))
    monkeypatch.delenv(MAX_SANDBOXES_ENV, raising=False)
    return tmp_path


def _write(home, value):
    (home / "machine.json").write_text(json.dumps({"max_sandboxes": value}))


class TestUnsetMeansUnbounded:
    def test_no_file_and_no_variable(self, home):
        assert max_sandboxes() is None

    def test_a_file_without_the_key(self, home):
        (home / "machine.json").write_text(json.dumps({"something_else": 3}))

        assert max_sandboxes() is None

    def test_an_unreadable_file(self, home):
        (home / "machine.json").write_text("{not json")

        assert max_sandboxes() is None

    def test_a_file_that_is_not_an_object(self, home):
        (home / "machine.json").write_text("[1, 2, 3]")

        assert max_sandboxes() is None


class TestWhatCounts:
    def test_the_file(self, home):
        _write(home, 4)

        assert max_sandboxes() == 4

    def test_a_string_in_the_file(self, home):
        """JSON written by hand is as likely to quote the number as not."""
        _write(home, "4")

        assert max_sandboxes() == 4

    def test_the_environment_wins(self, home, monkeypatch):
        """The one channel that reaches a process which cannot read the
        file, and how a CI run says "not on this box" without editing
        anybody's home directory."""
        _write(home, 4)
        monkeypatch.setenv(MAX_SANDBOXES_ENV, "1")

        assert max_sandboxes() == 1

    @pytest.mark.parametrize("bad", ["", "  ", "lots", "3.5", "-2", "0"])
    def test_a_value_that_is_not_a_positive_whole_number_is_ignored(
        self, home, monkeypatch, bad
    ):
        """Ignored, not guessed at — and 0 with it. "Start nothing" is a
        thing somebody might mean and a thing a typo produces, and this
        path cannot tell them apart; an unusable setting is `rite doctor`'s
        argument to have, not something to act on while starting work."""
        monkeypatch.setenv(MAX_SANDBOXES_ENV, bad)

        assert max_sandboxes() is None


class TestTheBoundIsEnforcedAtTheStartSite:
    """The reader is only half of it. `count_active_sandboxes()` with no
    root — the machine-wide path whose docstring says it had no caller in
    `src/` — is the other half, and this gives it one."""

    def _start(self, monkeypatch, tmp_path, *, running, bound, project_cap=5):
        from unittest.mock import patch

        from rite_ai import sandbox as sb
        from rite_ai.config.models import SandboxConfig

        worker_dir = tmp_path / "workers" / "w1"
        worker_dir.mkdir(parents=True)
        (tmp_path / ".rite").mkdir(exist_ok=True)
        if bound is not None:
            monkeypatch.setenv(MAX_SANDBOXES_ENV, str(bound))

        with (
            patch.object(sb, "_yoloai_binary", return_value="/bin/true"),
            patch.object(sb, "count_active_sandboxes", side_effect=[0, running]),
        ):
            return sb.start_worker(
                tmp_path,
                "w1",
                SandboxConfig(enabled=True, max_concurrent_workers=project_cap),
                allow_dirty=True,
            )

    def test_it_refuses_when_the_machine_is_full(self, home, monkeypatch, tmp_path):
        result = self._start(monkeypatch, tmp_path, running=3, bound=3)

        assert not result.ok
        assert "this machine already runs 3" in result.message

    def test_it_counts_nothing_when_no_bound_is_set(self, home, monkeypatch, tmp_path):
        """The upgrade property: without a bound the machine-wide count is
        never taken, so nothing new can refuse and nothing new can fail."""
        from unittest.mock import patch

        from rite_ai import sandbox as sb
        from rite_ai.config.models import SandboxConfig

        (tmp_path / "workers" / "w1").mkdir(parents=True)
        (tmp_path / ".rite").mkdir(exist_ok=True)
        calls = []

        def counting(root=None, workers=None):
            calls.append(root)
            return 0

        with (
            patch.object(sb, "_yoloai_binary", return_value="/bin/true"),
            patch.object(sb, "count_active_sandboxes", side_effect=counting),
        ):
            # What happens after the caps is not this test's business —
            # it goes on to exec yoloAI, which is not here.
            with contextlib.suppress(Exception):
                sb.start_worker(
                    tmp_path,
                    "w1",
                    SandboxConfig(enabled=True),
                    allow_dirty=True,
                )

        assert calls == [tmp_path], (
            f"the machine-wide count was taken with no bound set: {calls}"
        )

    def test_an_uncountable_machine_refuses_rather_than_assuming_zero(
        self, home, monkeypatch, tmp_path
    ):
        """`count_active_sandboxes` returns `int | CountUnavailable`, and the
        number is the easy half to read. A `yoloai ls` that cannot answer
        means the tooling here is broken, and starting anyway is what a
        bound exists to stop."""
        from rite_ai.sandbox import CountUnavailable

        result = self._start(
            monkeypatch, tmp_path, running=CountUnavailable("ls failed"), bound=3
        )

        assert not result.ok
        assert "cannot count this machine's sandboxes" in result.message

    def test_the_project_cap_is_reported_first(self, home, monkeypatch, tmp_path):
        """Someone who trips both should be told the one they can act on:
        "this project is at its limit" is specific, "this box is full,
        possibly because of another project" is environmental."""
        from unittest.mock import patch

        from rite_ai import sandbox as sb
        from rite_ai.config.models import SandboxConfig

        (tmp_path / "workers" / "w1").mkdir(parents=True)
        (tmp_path / ".rite").mkdir(exist_ok=True)
        monkeypatch.setenv(MAX_SANDBOXES_ENV, "1")

        with (
            patch.object(sb, "_yoloai_binary", return_value="/bin/true"),
            patch.object(sb, "count_active_sandboxes", return_value=9),
        ):
            result = sb.start_worker(
                tmp_path,
                "w1",
                SandboxConfig(enabled=True, max_concurrent_workers=2),
                allow_dirty=True,
            )

        assert not result.ok
        assert "max_concurrent_workers" in result.message
        assert "this machine already runs" not in result.message
