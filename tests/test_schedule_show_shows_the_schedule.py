"""`rite schedule show` shows the two things that decide the answer.

⚠ **It showed neither, and it is the command a refusal points at.**
`start_worker`'s refusal ends *"`rite schedule show` lists the windows"*, so
a user refused on a Saturday follows that instruction and lands here.

- **`days` was never printed.** A `Mon-Fri` window and a `Sat-Sun` window
  rendered identically, so the listing could not explain the refusal that
  sent the reader to it.
- **The timezone was the raw config field**, which is empty exactly when
  the zone is machine-local — so it printed `timezone: (not set)` about a
  schedule that is on a clock. `resolve_zone().describe()` existed and was
  wired to `rite start` only.

Both are the same defect as the schedule itself had before 0.5.1: a value
that decides behaviour and is invisible where somebody would look for it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli

CONFIG = """ticket_backend:
  type: none
heartbeat:
  interval_minutes: 10
  stall_threshold: 3
schedule:
{timezone}  windows:
    - {{days: "Mon-Fri", hours: "09:00-17:00", workers: 3}}
    - {{days: "Sat-Sun", hours: "00:00-23:59", workers: 0}}
"""


@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    def build(timezone: str = "") -> Path:
        rite = tmp_path / ".rite"
        rite.mkdir(exist_ok=True)
        (rite / "brief.yaml").write_text(
            "project:\n  name: acme\n  role: owner\n"
            "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
        )
        (rite / "modules.yaml").write_text("modules: {}\n")
        line = f"  timezone: {timezone}\n" if timezone else ""
        (rite / "config.yaml").write_text(CONFIG.format(timezone=line))
        monkeypatch.chdir(tmp_path)
        return tmp_path

    return build


def _show() -> str:
    result = CliRunner().invoke(cli, ["schedule", "show"])
    assert result.exit_code == 0, result.output
    return result.output


def test_the_days_are_printed(project):
    project()
    out = _show()
    assert "Mon-Fri" in out, "a window's days are invisible in the listing"
    assert "Sat-Sun" in out


def test_a_window_with_no_days_says_every_day_rather_than_nothing(
    tmp_path, monkeypatch
):
    """Blank would read as "days not supported here", which is what the
    listing meant before `days:` existed."""
    rite = tmp_path / ".rite"
    rite.mkdir()
    (rite / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "heartbeat:\n  interval_minutes: 10\n  stall_threshold: 3\n"
        "schedule:\n  windows:\n"
        '    - {hours: "09:00-17:00", workers: 3}\n'
    )
    monkeypatch.chdir(tmp_path)
    assert "every day" in _show()


def test_an_unset_timezone_names_the_machine_clock_not_nothing(project):
    """⚠ The regression this file exists for. `(not set)` is false: the
    schedule IS on a clock, and not saying which is the silent-wrong-clock
    D-48 was written against."""
    project()
    out = _show()
    assert "(not set)" not in out, (
        "it reported no timezone for a schedule that resolves to the machine's"
    )
    assert "machine local" in out


def test_a_configured_timezone_is_named_as_configured(project):
    project(timezone="Europe/Warsaw")
    out = _show()
    assert "Europe/Warsaw" in out and "from config" in out


def test_it_agrees_with_what_rite_start_prints(project):
    """One sentence, one source. `rite start` printed the clock through
    `resolve_zone().describe()` while this command printed a raw field, so
    two commands described the same schedule differently."""
    from rite_ai.schedule import resolve_zone

    project()
    assert resolve_zone("").describe() in _show()
