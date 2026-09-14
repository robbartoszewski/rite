"""Regression tests for the defects found by rehearsal round 4.

Round 4 came at `rite handover` from the READING side. Every earlier round
exercised it by writing a snapshot and checking the file; nobody had asked
what a session arriving cold actually gets from one — which is the whole
point of the mechanism, since its job is to make a session's death cheap
for whoever comes next.

Each test reproduces the ORIGINAL SYMPTOM, and each was run against the
previous commit to confirm it fails there.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.handover import read_snapshot, read_snapshots, write_snapshot


def _project(tmp_path: Path) -> Path:
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir(parents=True)
    (rite_dir / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    (rite_dir / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "heartbeat:\n  interval_minutes: 10\n  stall_threshold: 3\n"
    )
    return tmp_path


def _age(root: Path, worker: str, seconds: float) -> None:
    """Backdate one snapshot, which is what a session dying does to it."""
    path = root / ".rite" / "handover" / f"{worker or '_owner'}.json"
    data = json.loads(path.read_text())
    data["timestamp"] = time.time() - seconds
    path.write_text(json.dumps(data, indent=2))


def _run(root: Path, argv: list[str]):
    """Point rite at `root` via the documented override, not only by patching
    the private resolver.

    Patching `_find_project_root` alone left `_has_project_in_scope` walking
    up from the REAL cwd — which, when the suite runs from rite's own
    checkout, found rite's `.rite/` and answered True by accident. These
    tests passed because of that accident. `RITE_PROJECT_ROOT` is the one
    signal every resolver reads."""
    with patch.dict(os.environ, {"RITE_PROJECT_ROOT": str(root)}):
        with patch("rite_ai.cli.main._find_project_root", return_value=root):
            return CliRunner().invoke(cli, argv)


# --- an empty write is a deletion ------------------------------------------


class TestAnEmptyWriteDoesNotSilentlyDiscardTheSnapshot:
    """Every write replaces that session's snapshot entirely, so a write
    carrying nothing is a deletion — and it reported success. Measured: a
    worker recorded `DEF-12`, "migration 60% done", a next step and the
    open blocker "needs DB credentials"; the next scheduled call lost its
    arguments, printed `handover snapshot written for alpha`, exited 0,
    and left five `(none)` lines where the blocker had been.

    `rite handover write`'s own docstring tells callers to run it "on a
    schedule (every few minutes)", which is a great many chances for a
    wrapper to drop its arguments — and the one thing the snapshot exists
    to carry across a session's death is exactly that blocker.
    """

    def _with_real_content(self, tmp_path: Path) -> Path:
        root = _project(tmp_path)
        write_snapshot(
            root,
            worker="alpha",
            ticket="DEF-12",
            progress="migration 60% done",
            next_step="run backfill on staging",
            blockers=["needs DB credentials"],
        )
        return root

    def test_the_previous_snapshot_survives(self, tmp_path: Path):
        root = self._with_real_content(tmp_path)

        result = _run(root, ["handover", "write", "--worker", "alpha"])

        surviving = read_snapshot(root)
        assert surviving is not None
        assert surviving.blockers == ["needs DB credentials"], (
            "an argument-less write discarded the open blocker the snapshot "
            "existed to carry"
        )
        assert surviving.ticket == "DEF-12"
        assert surviving.progress == "migration 60% done"
        assert result.exit_code != 0, "and reported success while doing it"

    def test_the_refusal_says_what_to_pass_and_how_to_mean_it(self, tmp_path: Path):
        root = self._with_real_content(tmp_path)

        result = _run(root, ["handover", "write", "--worker", "alpha"])

        assert "--progress" in result.output
        assert "--clear" in result.output, (
            "no way offered to clear a snapshot deliberately"
        )

    def test_clear_still_clears(self, tmp_path: Path):
        """Refusing must not remove the capability, only make it deliberate."""
        root = self._with_real_content(tmp_path)

        result = _run(root, ["handover", "write", "--worker", "alpha", "--clear"])

        assert result.exit_code == 0, result.output
        snapshot = read_snapshot(root)
        assert snapshot is not None
        assert snapshot.blockers == []
        assert snapshot.ticket == ""

    @pytest.mark.parametrize(
        "argv",
        [
            ["--ticket", "DEF-1"],
            ["--progress", "half done"],
            ["--next-step", "run it"],
            ["--blocker", "waiting on review"],
        ],
    )
    def test_any_one_field_is_enough(self, tmp_path: Path, argv):
        """The guard must not be so strict that the ordinary call breaks."""
        root = self._with_real_content(tmp_path)

        result = _run(root, ["handover", "write", "--worker", "alpha", *argv])

        assert result.exit_code == 0, result.output

    def test_whitespace_is_not_content(self, tmp_path: Path):
        root = self._with_real_content(tmp_path)

        result = _run(
            root, ["handover", "write", "--worker", "alpha", "--progress", "   "]
        )

        assert result.exit_code != 0
        assert read_snapshot(root).blockers == ["needs DB credentials"]


# --- a snapshot that cannot be read is not an absent one --------------------


class TestAnUnreadableSnapshotIsNotNothing:
    """`_load` returned `None` for an unparseable file and every reader
    dropped it, so a truncated `handover/alpha.json` made

        $ rite handover show
        no handover snapshot recorded yet

    — the exact words printed when nothing has ever been written — while
    an open blocker sat inside that file. `rite start` omitted the
    handover line altogether and printed `ready`. `rite status` said
    nothing had been recorded. The watchdog, whose job is to notice a
    blocked worker unattended, stopped seeing that worker at all.

    `rite_ai.state`'s module docstring already settles this for the claims
    ledger: "An empty file is a real state ... A corrupt file means 'I do
    not know' ... and the two must never render the same." Handover's
    WRITE half was converted in that sweep; its read half was not.
    """

    def _corrupted(self, tmp_path: Path) -> Path:
        root = _project(tmp_path)
        write_snapshot(
            root, worker="alpha", ticket="DEF-7", blockers=["needs review from w2"]
        )
        path = root / ".rite" / "handover" / "alpha.json"
        path.write_text(path.read_text()[:40])  # a kill mid-write, a full disk
        return root

    def test_it_is_not_dropped_from_the_listing(self, tmp_path: Path):
        root = self._corrupted(tmp_path)

        snapshots = read_snapshots(root)

        assert len(snapshots) == 1, "the damaged snapshot disappeared entirely"
        assert snapshots[0].unreadable
        assert snapshots[0].worker == "alpha", "and took the worker's name with it"

    def test_handover_show_does_not_say_nothing_was_recorded(self, tmp_path: Path):
        root = self._corrupted(tmp_path)

        result = _run(root, ["handover", "show"])

        assert "no handover snapshot recorded yet" not in result.output, (
            "renders a damaged snapshot with the same words as a clean slate"
        )
        assert "UNREADABLE" in result.output
        assert "alpha" in result.output
        assert result.exit_code != 0

    def test_rite_start_does_not_orient_silently(self, tmp_path: Path):
        root = self._corrupted(tmp_path)

        result = _run(root, ["start", str(root)])

        assert "UNREADABLE" in result.output, (
            "the orientation command dropped the line and said ready"
        )
        assert "handover" in result.output.lower()

    def test_rite_status_does_not_say_nothing_was_recorded(self, tmp_path: Path):
        root = self._corrupted(tmp_path)

        result = _run(root, ["status", "--no-board"])

        assert "no handover snapshot recorded yet" not in result.output
        assert "UNREADABLE" in result.output

    def test_the_watchdog_reports_it(self, tmp_path: Path):
        from rite_ai.watchdog import run_watchdog_check

        root = self._corrupted(tmp_path)

        verdict = run_watchdog_check(root)

        assert verdict.needs_attention, (
            "the one unattended check went quiet about the worker whose "
            "recorded state had been damaged"
        )
        joined = " ".join(verdict.reasons)
        assert "UNREADABLE" in joined and "alpha" in joined

    def test_the_watchdog_does_not_call_it_blocked(self, tmp_path: Path):
        """This module's own reasoning: "blocked" must stay unmistakable,
        because reading it as "stalled" sends a Manager to force-release a
        live session's claims. An unreadable file does not say the worker
        is waiting — it says nobody can tell."""
        from rite_ai.watchdog import _blocked_workers

        root = self._corrupted(tmp_path)

        assert _blocked_workers(root) == []

    def test_a_readable_snapshot_is_still_not_flagged(self, tmp_path: Path):
        root = _project(tmp_path)
        write_snapshot(root, worker="alpha", ticket="DEF-7", progress="fine")

        snapshots = read_snapshots(root)

        assert len(snapshots) == 1
        assert snapshots[0].unreadable == ""

    def test_a_genuinely_absent_snapshot_still_reads_as_absent(self, tmp_path: Path):
        """The distinction cuts both ways: "nothing recorded yet" is a real
        answer and §9.10.1 carves it out explicitly."""
        root = _project(tmp_path)

        result = _run(root, ["handover", "show"])

        assert result.exit_code == 0
        assert "no handover snapshot recorded yet" in result.output


# --- how old is this, and is that session still alive -----------------------


class TestEveryReaderSaysHowOldTheSnapshotIs:
    """`rite handover show` printed `recorded: 2026-09-12 01:05:14` and
    nothing else; `rite status`'s handover block carried NO time at all;
    `rite start` carried none either. All three are read by a session that
    has just started and does not know what time the previous one stopped
    — which makes "three hours dead, holding a blocker" and "written a
    moment ago, mid-task" the same output in form.
    """

    def _three_hours_dead(self, tmp_path: Path) -> Path:
        root = _project(tmp_path)
        write_snapshot(
            root,
            worker="alpha",
            ticket="DEF-7",
            progress="rewrote the parser",
            blockers=["needs review from w2"],
        )
        _age(root, "alpha", 3 * 3600)
        return root

    def test_handover_show_says_how_long_ago(self, tmp_path: Path):
        root = self._three_hours_dead(tmp_path)

        result = _run(root, ["handover", "show"])

        assert "3h" in result.output, (
            f"a reader still has to know the current time and subtract:\n"
            f"{result.output}"
        )
        assert "ago" in result.output

    def test_rite_status_carries_a_time_at_all(self, tmp_path: Path):
        root = self._three_hours_dead(tmp_path)

        result = _run(root, ["status", "--no-board"])

        block = result.output.split("handover snapshot")[1]
        assert "3h" in block and "ago" in block, (
            f"the handover block has no time in it, so a dead session's "
            f"blocker reads as current:\n{block}"
        )

    def test_rite_status_shows_progress(self, tmp_path: Path):
        """`progress` was dropped here and nowhere else, and it is the
        field that says where the work actually stands."""
        root = self._three_hours_dead(tmp_path)

        result = _run(root, ["status", "--no-board"])

        assert "rewrote the parser" in result.output

    def test_rite_start_says_how_long_ago(self, tmp_path: Path):
        root = self._three_hours_dead(tmp_path)

        result = _run(root, ["start", str(root)])

        assert "3h" in result.output and "ago" in result.output, result.output

    def test_a_fresh_snapshot_reads_differently_from_a_dead_one(self, tmp_path: Path):
        """The point is the DIFFERENCE, not the presence of a string."""
        dead = self._three_hours_dead(tmp_path / "dead")
        fresh = _project(tmp_path / "fresh")
        write_snapshot(fresh, worker="alpha", ticket="DEF-7", progress="x")

        dead_out = _run(dead, ["handover", "show"]).output
        fresh_out = _run(fresh, ["handover", "show"]).output

        dead_age = [ln for ln in dead_out.splitlines() if "ago" in ln][0]
        fresh_age = [ln for ln in fresh_out.splitlines() if "ago" in ln][0]
        assert "3h ago" in dead_age
        assert "3h ago" not in fresh_age

    def test_the_age_is_never_negative(self, tmp_path: Path):
        """A clock adjustment can put a recorded time in the future, and
        "-6s ago" reads as a bug in rite rather than as the clock change
        it is — `format_duration`'s own rule, applied here too."""
        root = _project(tmp_path)
        write_snapshot(root, worker="alpha", ticket="DEF-7")
        _age(root, "alpha", -600)

        result = _run(root, ["handover", "show"])

        assert (
            "-"
            not in [ln for ln in result.output.splitlines() if "ago" in ln][0].split(
                "("
            )[1]
        )
