"""`rite start <Manager>` — the path that spends money unattended.

**This file exists because there were no tests here at all.** The
interception, the two bounds and the refusals were written, reviewed and
reported as done; a terminating check asked what covered them and the
answer was nothing. That is defect class 13 in the place it costs most.

The property most of these tests exist for is not "the flag parses". It is
**that a value the user typed reaches `supervise`** — class 13's blind
spot, stated in its own file: the dead-wiring guard asks whether a FUNCTION
is called, never whether a PARAMETER is ever supplied. `window_seconds` was
recorded, threaded through three modules and passed to `supervise` by a
call site that always passed the same zero, because no flag set it. Every
individual piece reviewed clean.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    (rite_dir / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    (rite_dir / "config.yaml").write_text(
        "ticket_backend:\n  type: github\n  repo: acme/acme\n"
        "heartbeat:\n  interval_minutes: 10\n  stall_threshold: 3\n"
        "coordination:\n"
        "  managers:\n    - planner\n"
        "  manager_roles:\n"
        "    - name: planner\n      engine: claude\n"
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def supervised(monkeypatch) -> list[dict]:
    """Capture what `supervise` was called with, and never start anything.

    ⚠ `claude` must never be a test command anywhere. Nothing in this file
    launches a provider session; the point is what the CLI *would* pass.
    """
    import rite_ai.managers.session as ses
    import rite_ai.managers.supervise as sup

    calls: list[dict] = []

    class Outcome:
        ok = True
        reason = "stopped: test"

    def fake(root, manager, **kw):
        calls.append({"root": root, "manager": manager, **kw})
        return Outcome()

    import rite_ai.cli.main as main_mod

    # A board that exists and answers. These tests are about what the CLI
    # passes to `supervise`, not about reaching GitHub — but `type: none`
    # would now route them into the no-board setup session, which is a
    # different feature with a different prompt and a ceiling of one.
    monkeypatch.setattr(
        main_mod, "_ticket_backend", lambda role="workers": (object(), None)
    )
    monkeypatch.setattr(sup, "supervise", fake)
    monkeypatch.setattr(ses, "exit_status_available", lambda: True)
    monkeypatch.setattr(ses, "running", lambda *a, **k: None)
    return calls


class TestBothBoundsReachTheSupervisor:
    """The class-13 property. A bound the user typed that stops at the CLI
    is indistinguishable, from every angle except this one, from a bound
    that works."""

    def test_the_session_ceiling_arrives(self, project, supervised):
        result = CliRunner().invoke(
            cli, ["start", "planner", "--sessions", "3", "--minutes", "90"]
        )
        assert result.exit_code == 0, result.output
        assert supervised, "supervise was never reached"
        assert supervised[0]["max_sessions"] == 3

    def test_the_duration_arrives_as_seconds(self, project, supervised):
        """Minutes at the boundary, seconds inside. A flag that arrived in
        the wrong unit would bound the run sixty times too loosely — which
        on this path is money, and would look exactly like working."""
        result = CliRunner().invoke(
            cli, ["start", "planner", "--sessions", "3", "--minutes", "90"]
        )
        assert result.exit_code == 0, result.output
        assert supervised[0]["window_seconds"] == pytest.approx(5400.0)

    def test_a_fractional_duration_is_not_truncated(self, project, supervised):
        CliRunner().invoke(
            cli, ["start", "planner", "--sessions", "1", "--minutes", "0.5"]
        )
        assert supervised[0]["window_seconds"] == pytest.approx(30.0)


class TestNeitherBoundHasADefault:
    """Measured (D-82): sessions that end instantly hit the COUNT with the
    clock untouched; sessions of realistic length hit the CLOCK after three
    with the count untouched. Neither suffices alone, so neither may be
    silently chosen — the reasoning that made `--sessions` required applies
    unchanged to a duration, and a duration defaulting to forever is a
    bound in name only."""

    def test_no_sessions_refuses_and_starts_nothing(self, project, supervised):
        result = CliRunner().invoke(cli, ["start", "planner", "--minutes", "90"])
        assert result.exit_code == 1
        assert "--sessions is required" in result.output
        assert not supervised, "it refused and started a Manager anyway"

    def test_no_minutes_refuses_and_starts_nothing(self, project, supervised):
        result = CliRunner().invoke(cli, ["start", "planner", "--sessions", "3"])
        assert result.exit_code == 1
        assert "--minutes is required" in result.output
        assert not supervised, "it refused and started a Manager anyway"

    def test_the_refusal_says_why_rather_than_just_what(self, project, supervised):
        """A refusal nobody understands is a refusal people work around by
        typing any number that makes it stop."""
        result = CliRunner().invoke(cli, ["start", "planner", "--sessions", "3"])
        assert "neither suffices alone" in result.output


class TestTheManagerNameIsInterceptedFirst:
    def test_a_manager_name_is_not_read_as_a_directory(self, project, supervised):
        """Before the fix the name fell through to directory/alias
        resolution and came back "neither an existing directory nor a
        registered alias" — true of the word, and silent about the
        Manager."""
        result = CliRunner().invoke(
            cli, ["start", "planner", "--sessions", "1", "--minutes", "1"]
        )
        assert "registered alias" not in result.output
        assert supervised and supervised[0]["manager"] == "planner"

    def test_an_unknown_name_still_falls_through(self, project, supervised):
        result = CliRunner().invoke(
            cli, ["start", "nosuchthing", "--sessions", "1", "--minutes", "1"]
        )
        assert not supervised
        assert result.exit_code != 0


class TestFreshReachesTheSupervisor:
    """⚠ The class-13 property again: a flag that stops at the CLI is
    indistinguishable, from every angle except this one, from a flag that
    works. `--minutes` shipped threaded through three modules and supplied
    by nobody."""

    def test_fresh_arrives(self, project, supervised):
        result = CliRunner().invoke(
            cli, ["start", "planner", "--sessions", "1", "--minutes", "5", "--fresh"]
        )
        assert result.exit_code == 0, result.output
        assert supervised[0]["fresh"] is True

    def test_its_absence_arrives_too(self, project, supervised):
        """Continuation is the DEFAULT, so the default must reach the
        supervisor as False rather than as nothing."""
        result = CliRunner().invoke(
            cli, ["start", "planner", "--sessions", "1", "--minutes", "5"]
        )
        assert result.exit_code == 0, result.output
        assert supervised[0]["fresh"] is False
