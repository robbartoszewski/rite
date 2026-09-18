"""`rite status` says what this machine last concluded (§2.5.2, local only).

`doctor` reads the fleet live and costs a round trip. `status` is what a
human runs by habit and must stay cheap — so it reports the tick's own
conclusion from local state, always stamped with its age.

The age is the whole point: "alpha is Owner" is a lie the moment the machine
stops ticking, which is exactly when somebody runs status to find out why.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from rite_ai.coordination import last_tick
from rite_ai.reporting.status import collect_status, format_status
from rite_ai.scheduler import run_tick


def project(tmp_path: Path, coordination: str = "") -> Path:
    root = tmp_path / "project"
    rite = root / ".rite"
    rite.mkdir(parents=True)
    (rite / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n  site: s\n"
        "  projects: {workers: A, board: B}\n  credential: ''\n"
        "expertise: {}\npublish_gate:\n  scan_patterns: []\n"
        "  gitleaks_config: .rite/gitleaks.toml\n"
        "heartbeat:\n  interval_minutes: 10\n  stall_threshold: 3\n" + coordination
    )
    return root


@pytest.fixture
def enrolled(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    root = project(
        tmp_path,
        f"coordination:\n  managers: [alpha, beta]\n  remote: '{remote}'\n",
    )
    (root / ".rite" / "machine").write_text("alpha\n")
    return root


def test_a_project_without_coordination_says_nothing_about_it(tmp_path):
    """Every project today. No new line, no empty section."""
    text = format_status(collect_status(project(tmp_path)))
    assert "coordination:" not in text


def test_after_a_tick_status_names_the_role(enrolled):
    run_tick(enrolled)
    text = format_status(collect_status(enrolled))
    assert "coordination: alpha: Owner as of" in text, text


def test_it_is_local_and_needs_no_remote(enrolled):
    """The point of reading a recorded conclusion rather than the fleet:
    status must work on a train."""
    run_tick(enrolled)
    config = enrolled / ".rite" / "config.yaml"
    config.write_text(config.read_text().replace("remote.git", "gone.git"))
    text = format_status(collect_status(enrolled))
    assert "coordination: alpha: Owner as of" in text, text


def test_the_age_is_always_there(enrolled):
    """A conclusion without its age reads as current, and the moment a
    machine stops ticking is exactly when somebody runs status."""
    run_tick(enrolled)
    recorded = last_tick.read(enrolled)
    old = last_tick.describe(recorded, now=recorded.at + 3 * 3600 + 25 * 60)
    assert "3h25m ago" in old, old


def test_a_machine_that_is_not_owner_says_so(enrolled, tmp_path):
    """The other half. A Manager that is not Owner must not read as one."""
    last_tick.record(enrolled, "beta", "deferred", owner=False, problems=0)
    text = format_status(collect_status(enrolled))
    assert "beta: not Owner as of" in text, text


def test_problems_are_carried_across(enrolled):
    last_tick.record(enrolled, "alpha", "renewal uncertain", owner=True, problems=2)
    assert "2 problem(s)" in format_status(collect_status(enrolled))


def test_an_unreadable_record_says_nothing_rather_than_guessing(enrolled):
    run_tick(enrolled)
    last_tick.path_for(enrolled).write_text("{ not json")
    text = format_status(collect_status(enrolled))
    assert "coordination: alpha" not in text


def test_recording_never_fails_the_tick(tmp_path):
    """A status convenience must not be able to break the thing that
    produces it."""
    missing = tmp_path / "gone"
    last_tick.record(missing, "alpha", "renewed", owner=True, problems=0)
    assert last_tick.read(missing) is None


def test_the_record_is_runtime_state_under_rite(enrolled):
    """`.rite/` ignores everything outside its authored set, so this is
    ignored because nobody did anything. Anywhere else it lands in the
    project's next commit."""
    run_tick(enrolled)
    assert last_tick.path_for(enrolled).parent.name == ".rite"
    assert time.time() - last_tick.read(enrolled).at < 120
