"""A claim whose holder is gone, named rather than released.

Measured on `67b2631`: a claim 9.3 days old, holder with no heartbeat ever, is
still held and a live worker is refused the path. rite detects the death
(`detect_stalls` says `seconds_silent=inf`) and does nothing about it.

Two review rounds killed automatic release, and the tests encode why:
"the sandbox is gone" is the NORMAL state for an unsandboxed Worker, and
nothing beats on a Worker's behalf — so a guard built on either fires on live
Workers, and the cost is two sessions on one path.

What is tested is therefore the report: that it sees the case that motivated
it, that it stays quiet on healthy projects, and that it never writes.
"""

from __future__ import annotations

import json
import time

from rite_ai.claims.ledger import ClaimsLedger
from rite_ai.claims.suspect import lines, suspect_claims

NOW = 1_759_000_000.0
HOUR = 3600.0
THRESHOLD = 1800.0


def _project(tmp_path):
    (tmp_path / ".rite").mkdir(parents=True)
    return tmp_path


def _claim(root, worker, *paths, ticket="", age=0.0):
    ledger = ClaimsLedger(root / ".rite" / "claims.json")
    ledger.claim(list(paths), worker, ticket)
    if age:
        raw = json.loads((root / ".rite" / "claims.json").read_text())
        for entry in raw:
            if entry["worker"] == worker:
                entry["timestamp"] = NOW - age
        (root / ".rite" / "claims.json").write_text(json.dumps(raw))


def _beat(root, worker, ago):
    beats = root / ".rite" / "heartbeats"
    beats.mkdir(parents=True, exist_ok=True)
    (beats / f"{worker}.json").write_text(
        json.dumps({"worker": worker, "timestamp": NOW - ago})
    )


# --- the case that motivated it ----------------------------------------------------


def test_the_nine_day_claim_from_a_holder_that_never_beat_is_named(tmp_path):
    root = _project(tmp_path)
    _claim(root, "ghost", "engine/parser.py", ticket="BEN-191", age=9.3 * 86400)

    (found,) = suspect_claims(root, registered=["alpha"], now=NOW)

    assert found.worker == "ghost"
    assert found.never_beat
    assert not found.registered


def test_it_sees_a_holder_nobody_registered(tmp_path):
    """`rite status` asks `detect_stalls` about `project.workers` only, and
    the motivating case is a name nobody registered. A report that cannot see
    its own example is not a report."""
    root = _project(tmp_path)
    _claim(root, "ghost", "a.py", age=5 * HOUR)

    assert [s.worker for s in suspect_claims(root, registered=[], now=NOW)] == ["ghost"]


def test_it_prints_the_command_rather_than_implying_it(tmp_path):
    """The whole point is turning an invisible permanent failure into a
    one-line fix."""
    root = _project(tmp_path)
    _claim(root, "ghost", "a.py", age=5 * HOUR)

    out = "\n".join(lines(suspect_claims(root, now=NOW)))

    assert "rite release --worker ghost --force" in out


def test_a_nine_day_age_reads_as_days(tmp_path):
    """ "223h" is not a number anybody reads, and the value of the line is that
    a glance shows something has been wrong for over a week."""
    root = _project(tmp_path)
    _claim(root, "ghost", "a.py", age=9.3 * 86400)

    assert "9.3d" in suspect_claims(root, now=NOW)[0].describe()


# --- and stays quiet when it should ------------------------------------------------


def test_a_fresh_claim_is_not_suspected_even_with_no_heartbeat_yet(tmp_path):
    """`detect_stalls` appends a stall for any holder with no heartbeat file,
    with no age gate at all — so without this the line fires on day one of
    every healthy project, and a line people scroll past is how this class of
    defect survives."""
    root = _project(tmp_path)
    _claim(root, "beta", "a.py", age=10.0)

    assert suspect_claims(root, registered=["beta"], now=NOW) == []


def test_a_holder_beating_normally_is_not_suspected(tmp_path):
    root = _project(tmp_path)
    _claim(root, "beta", "a.py", age=5 * HOUR)
    _beat(root, "beta", ago=60.0)

    assert suspect_claims(root, registered=["beta"], now=NOW) == []


def test_an_old_claim_with_a_recently_quiet_holder_is_not_suspected(tmp_path):
    """Inside the threshold is a Worker thinking, not a Worker gone."""
    root = _project(tmp_path)
    _claim(root, "beta", "a.py", age=5 * HOUR)
    _beat(root, "beta", ago=THRESHOLD - 60)

    assert suspect_claims(root, registered=["beta"], now=NOW) == []


def test_no_ledger_is_silence_not_a_guess(tmp_path):
    assert suspect_claims(_project(tmp_path), now=NOW) == []


def test_an_unreadable_ledger_reports_nothing_rather_than_guessing(tmp_path):
    root = _project(tmp_path)
    (root / ".rite" / "claims.json").write_text("{ truncated")

    assert suspect_claims(root, now=NOW) == []


# --- it never acts -----------------------------------------------------------------


def test_it_never_releases_anything(tmp_path):
    """Two review rounds' conclusion, as a test. A release on this evidence
    puts two sessions on one path — the one thing the ledger exists to
    prevent."""
    root = _project(tmp_path)
    _claim(root, "ghost", "engine/parser.py", age=9.3 * 86400)
    before = (root / ".rite" / "claims.json").read_text()

    suspect_claims(root, registered=[], now=NOW)

    assert (root / ".rite" / "claims.json").read_text() == before
    assert ClaimsLedger(root / ".rite" / "claims.json").list_claims()


def test_the_worst_one_is_reported_first(tmp_path):
    root = _project(tmp_path)
    _claim(root, "old", "a.py", age=9 * 86400)
    _claim(root, "newer", "b.py", age=5 * HOUR)

    assert [s.worker for s in suspect_claims(root, now=NOW)] == ["old", "newer"]


def test_it_uses_wall_time_when_not_given_a_clock(tmp_path):
    """The default path the CLI takes, exercised once so a signature change
    cannot quietly break it."""
    root = _project(tmp_path)
    _claim(root, "ghost", "a.py")
    raw = json.loads((root / ".rite" / "claims.json").read_text())
    raw[0]["timestamp"] = time.time() - 5 * HOUR
    (root / ".rite" / "claims.json").write_text(json.dumps(raw))

    assert [s.worker for s in suspect_claims(root)] == ["ghost"]


# --- "cannot be checked" is not "never beat" (EXC-4) -------------------------------


def test_an_unreadable_heartbeat_suspends_judgement(tmp_path):
    """An ABSENT heartbeat says the holder never started. One that cannot be
    READ says nothing about the holder — and calling the claim abandoned on
    that basis is a guess wearing evidence's clothes."""
    root = _project(tmp_path)
    _claim(root, "alpha", "engine/parser.py", age=5 * HOUR)
    beats = root / ".rite" / "heartbeats"
    beats.mkdir(parents=True, exist_ok=True)
    (beats / "alpha.json").mkdir()  # a directory: exists, cannot be read

    (found,) = suspect_claims(root, registered=["alpha"], now=NOW)

    assert found.unreadable
    assert "CANNOT BE READ" in found.describe()
    assert "no heartbeat ever" not in found.describe()


def test_the_remedy_for_an_unreadable_heartbeat_is_not_a_release(tmp_path):
    """The problem is a file on this machine. Releasing on the strength of a
    heartbeat nobody could read is the guess this module exists not to make."""
    root = _project(tmp_path)
    _claim(root, "alpha", "engine/parser.py", age=5 * HOUR)
    beats = root / ".rite" / "heartbeats"
    beats.mkdir(parents=True, exist_ok=True)
    (beats / "alpha.json").mkdir()

    (found,) = suspect_claims(root, registered=["alpha"], now=NOW)

    assert "rite release" not in found.remedy
    assert ".rite/heartbeats/alpha.json" in found.remedy


def test_a_genuinely_absent_heartbeat_still_reads_as_never_beat(tmp_path):
    """The distinction only helps if the ordinary case is unchanged."""
    root = _project(tmp_path)
    _claim(root, "ghost", "engine/parser.py", age=9.3 * 86400)

    (found,) = suspect_claims(root, registered=[], now=NOW)

    assert not found.unreadable
    assert found.never_beat
    assert "rite release --worker ghost" in found.remedy
