"""Regression tests for the defects found by rehearsal round 7.

Round 7 ran the review convention the way a user runs it — `rite review`'s
merged checklist handed verbatim to real review agents against a real change
— and closed the open question about the scheduler's window-boundary return
path.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from rite_ai.claims.ledger import ClaimsLedger
from rite_ai.schedule import Moment, ResolvedZone
from rite_ai.scheduler import run_tick


def _project(tmp_path: Path, windows: str) -> Path:
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir(parents=True)
    (rite_dir / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    (rite_dir / "config.yaml").write_text(
        f"ticket_backend:\n  type: none\nschedule:\n  timezone: UTC\n{windows}"
    )
    return tmp_path


# 09:00-18:00 has workers, the rest of the day has none.
_DAY = (
    "  windows:\n"
    "    - hours: '00:00-09:00'\n      workers: 0\n"
    "    - hours: '09:00-18:00'\n      workers: 3\n"
    "    - hours: '18:00-24:00'\n      workers: 0\n"
)


class TestTheWindowOpeningIsReported:
    """`_run_tick_locked` had exactly one boundary branch — the transition
    INTO zero (§2.7.3, D-46). The return path wrote the new worker count to
    `schedule-state.json` and said nothing, so the tick printed

        nothing to report — no stalled workers, no window transition

    in the same minute a window opened. Measured against a real project at
    the 0→3 boundary.

    Not STARTING anything on the return is correct and deliberate: §2.5 is
    explicit that "Managers/Owner still need a human to start ... nothing
    spawns a session automatically", and every pooled slot runs `claude`
    against a weekly quota no cleanup gets back. The defect is the
    assertion, not the inaction — a log a human reads the morning after
    said no transition happened at the moment one did.
    """

    def test_the_opening_boundary_is_not_reported_as_no_transition(
        self, tmp_path: Path
    ):
        root = _project(tmp_path, _DAY)
        (root / ".rite" / "schedule-state.json").write_text("0")

        with patch(
            "rite_ai.scheduler.current_moment",
            return_value=Moment(600, 2, ResolvedZone("UTC", machine_local=False)),
        ):
            result = run_tick(root)  # 10:00, inside the 3-worker window

        assert result.messages, (
            "the tick reported nothing in the minute a window opened, so the "
            "CLI printed 'no window transition' over a real transition"
        )
        joined = " ".join(result.messages)
        assert "window boundary" in joined, joined

    def test_it_says_the_opening_starts_nothing(self, tmp_path: Path):
        """The non-action is the part a reader needs told. A boundary line
        that only announced the window would read as "workers are coming"."""
        root = _project(tmp_path, _DAY)
        (root / ".rite" / "schedule-state.json").write_text("0")

        with patch(
            "rite_ai.scheduler.current_moment",
            return_value=Moment(600, 2, ResolvedZone("UTC", machine_local=False)),
        ):
            result = run_tick(root)

        joined = " ".join(result.messages)
        assert "nothing started automatically" in joined, joined
        assert "pool fill" in joined, "does not name the explicit action"

    def test_it_starts_nothing(self, tmp_path: Path):
        """§2.5, and the standing rule that spent quota is the one damage
        no cleanup reverses. Nothing here may ever spawn."""
        root = _project(tmp_path, _DAY)
        (root / ".rite" / "schedule-state.json").write_text("0")

        with (
            patch(
                "rite_ai.scheduler.current_moment",
                return_value=Moment(600, 2, ResolvedZone("UTC", machine_local=False)),
            ),
            patch("rite_ai.pool.fill") as fill,
        ):
            run_tick(root)

        fill.assert_not_called()

    def test_a_repeat_tick_inside_the_window_stays_quiet(self, tmp_path: Path):
        """Idempotent: the boundary check acts on a genuine transition, not
        on a repeated reading of the same state."""
        root = _project(tmp_path, _DAY)
        (root / ".rite" / "schedule-state.json").write_text("3")

        with patch(
            "rite_ai.scheduler.current_moment",
            return_value=Moment(600, 2, ResolvedZone("UTC", machine_local=False)),
        ):
            result = run_tick(root)

        assert not any("window boundary" in m for m in result.messages), result.messages

    def test_the_closing_boundary_still_hands_over(self, tmp_path: Path):
        """The direction that already worked, kept honest."""
        root = _project(tmp_path, _DAY)
        ledger = ClaimsLedger(root / ".rite" / "claims.json")
        ledger.claim(["src/a.ts"], "alpha", ticket="ABC-1")
        (root / ".rite" / "schedule-state.json").write_text("3")

        with patch(
            "rite_ai.scheduler.current_moment",
            return_value=Moment(1200, 2, ResolvedZone("UTC", machine_local=False)),
        ):
            result = run_tick(root)  # 20:00, outside the window

        assert any("handed over worker 'alpha'" in m for m in result.messages)
        assert ledger.list_claims() == []

    def test_the_first_ever_tick_still_acts_on_neither_boundary(self, tmp_path: Path):
        """No prior state is not a transition in either direction."""
        root = _project(tmp_path, _DAY)

        with patch(
            "rite_ai.scheduler.current_moment",
            return_value=Moment(600, 2, ResolvedZone("UTC", machine_local=False)),
        ):
            result = run_tick(root)

        assert not any("window boundary" in m for m in result.messages), result.messages
        assert (root / ".rite" / "schedule-state.json").read_text().strip() == "3"


# --- what the review convention found in round 6's own fix ------------------


class TestTheAggregateSurfacesAPartialRead:
    """Round 6 made the cross-project line tell its failure states apart,
    keyed on `ProjectStatus.errors`. It closed three doors of four.

    A corrupt `.rite/pool.json` is recorded in `pool_unreadable`, a field
    `errors` never sees, so a project whose pool state could not be read
    still rendered

        bravo (manager): ok, 0 active claim(s), 0 stalled worker(s)

    — identical to the healthy projects either side of it. Found by three
    independent review agents working this project's own
    `.rite/review-checklist.md` against the commit that added the field;
    all three reached it by running the command, and all three reported it
    under the checklist's "generated content states only what was actually
    measured" line.

    The counts stay, because claims and workers really were read. What
    goes is the word "ok".
    """

    def _three_projects(self, tmp_path: Path, corrupt: str) -> str:
        from rite_ai.dispatch import add_project

        hub = tmp_path / "hub"
        hub.mkdir(parents=True)
        for name in ("alpha", "bravo", "charlie"):
            root = _project(tmp_path / name, _DAY)
            if name == corrupt:
                (root / ".rite" / "pool.json").write_text('"not an object"')
            add_project(hub, name, root, "manager")
        with (
            patch("rite_ai.cli.main._has_project_in_scope", return_value=False),
            patch.dict("os.environ", {"RITE_DISPATCH_DIR": str(hub)}),
        ):
            from click.testing import CliRunner

            from rite_ai.cli.main import cli

            return CliRunner().invoke(cli, ["status", "--no-board"]).output

    def test_the_unreadable_project_is_not_called_ok(self, tmp_path: Path):
        out = self._three_projects(tmp_path, corrupt="bravo")

        line = [ln for ln in out.splitlines() if ln.strip().startswith("bravo")][0]
        assert not line.split(":", 1)[1].strip().startswith("ok"), (
            f"a project whose pool state could not be read reads as ok: {line}"
        )
        assert "pool state unreadable" in line, line

    def test_the_healthy_projects_are_untouched(self, tmp_path: Path):
        out = self._three_projects(tmp_path, corrupt="bravo")

        for name in ("alpha", "charlie"):
            line = [ln for ln in out.splitlines() if ln.strip().startswith(name)][0]
            assert "ok," in line, line
            assert "unreadable" not in line, line

    def test_the_counts_that_were_read_are_still_reported(self, tmp_path: Path):
        """Claims and workers WERE read. Dropping them would lose true
        information in order to report a different failure."""
        out = self._three_projects(tmp_path, corrupt="bravo")

        line = [ln for ln in out.splitlines() if ln.strip().startswith("bravo")][0]
        assert "active claim(s)" in line and "stalled worker(s)" in line, line


class TestEveryUnreadableFieldReachesTheAggregate:
    """The guard is on the CLASS, not this instance.

    "A zero that means I could not look" has now been fixed four times in
    three rounds, and this last one recurred INSIDE its own fix — round 6
    enumerated the failure states it knew about and a new carrier field
    appeared one commit earlier. Enumerating by hand is what failed; so
    the enumeration is checked against the dataclass instead.

    `boards_unreached` is deliberately excluded and named as such: the
    aggregate never queries a ticket backend, so "not reached" is the
    expected state on every line and reporting it would be noise.
    """

    def test_no_unreadable_field_is_left_out(self):
        from dataclasses import fields

        from rite_ai.cli.main import UNREADABLE_FIELDS
        from rite_ai.reporting.status import ProjectStatus

        carriers = {
            f.name for f in fields(ProjectStatus) if f.name.endswith("_unreadable")
        }
        listed = {name for name, _ in UNREADABLE_FIELDS}

        assert carriers <= listed, (
            f"{sorted(carriers - listed)} records that part of a project "
            "could not be read, and the cross-project view never looks at "
            "it — so a project with that failure reads as ok. Add it to "
            "UNREADABLE_FIELDS, or exclude it there with a reason."
        )

    def test_the_deliberate_exclusion_is_still_deliberate(self):
        """If `boards_unreached` is ever renamed to match the convention,
        this test's sibling above starts requiring it — and the reason it
        is excluded has to be re-argued rather than silently inherited."""
        from rite_ai.reporting.status import ProjectStatus

        names = {f.name for f in __import__("dataclasses").fields(ProjectStatus)}
        assert "boards_unreached" in names, (
            "renamed — re-read UNREADABLE_FIELDS' comment before listing it"
        )


class TestTheChecklistSaysWhatAVerdictMeans:
    """Handed to three independent reviewers against one real change, the
    checklist produced two findings about itself.

    Five of its 24 lines could not fire on that diff at all, and every
    reviewer recorded them as PASS — a PASS meaning "does not apply" is
    indistinguishable in a tally from one meaning "I checked". And two
    lines could not be applied where they SHOULD have fired: "what the
    ticket asked", when no ticket exists (all three, same cause), and
    "this project's existing conventions", which drew three different
    verdicts from three reviewers.
    """

    def _checklists(self) -> list[Path]:
        root = Path(__file__).resolve().parents[1]
        return [
            root / "templates" / "review-checklist.md",
            root / ".rite" / "review-checklist.md",
        ]

    def test_a_line_that_cannot_fire_has_its_own_verdict(self):
        for path in self._checklists():
            text = path.read_text()
            assert "N/A" in text, f"{path.name}: no verdict for an inapplicable line"
            assert "CANNOT-EVALUATE" in text, (
                f"{path.name}: no way to report a line that defeated the reviewer"
            )

    def test_the_ticket_line_names_a_fallback(self):
        for path in self._checklists():
            text = " ".join(path.read_text().split())
            assert "Where there is no ticket, the commit message is the ask" in text, (
                f"{path.name}: still unevaluable on a change with no ticket"
            )

    def test_the_conventions_line_names_its_arbiter(self):
        for path in self._checklists():
            text = " ".join(path.read_text().split())
            assert "declared convention and the practised baseline disagree" in text, (
                f"{path.name}: still ambiguous about which convention is meant"
            )

    def test_both_copies_stay_in_step(self):
        """The template ships to every project `rite init` creates; the
        repo's own copy is what `rite review` prints here. A fix to one is
        a fix nobody else gets."""
        template, own = (p.read_text() for p in self._checklists())
        for phrase in (
            "Three verdicts, not two",
            "Where there is no ticket",
            "declared convention and the practised baseline disagree",
        ):
            assert (phrase in template) == (phrase in own), phrase
