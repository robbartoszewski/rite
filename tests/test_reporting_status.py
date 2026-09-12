from pathlib import Path

from rite_ai.claims.ledger import ClaimsLedger
from rite_ai.coordination_cost import record_refused_claim
from rite_ai.handover import write_snapshot
from rite_ai.reporting.status import collect_status, format_status


def _full_project(tmp_path: Path) -> Path:
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    (rite_dir / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    (rite_dir / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    return tmp_path


class TestCollectStatus:
    def test_no_rite_dir_reports_an_error(self, tmp_path: Path):
        status = collect_status(tmp_path)
        assert status.errors
        assert "no .rite/" in status.errors[0]

    def test_claims_readable_even_without_brief_yaml(self, tmp_path: Path):
        """Regression: `rite claim` only needs `.rite/` to exist, not a
        fully-populated project — status used to hide all claims on a
        minimally-initialised project because it returned early on the
        brief.yaml parse error before ever reading claims.json."""
        rite_dir = tmp_path / ".rite"
        rite_dir.mkdir()
        ledger = ClaimsLedger(rite_dir / "claims.json")
        ledger.claim(["src/a.ts"], "alpha")

        status = collect_status(tmp_path)
        assert len(status.claims) == 1
        assert status.claims[0].worker == "alpha"

    def test_full_project_populates_name_role_modules_workers(self, tmp_path: Path):
        root = _full_project(tmp_path)
        status = collect_status(root)
        assert status.name == "acme"
        assert status.role == "owner"
        assert status.errors == []

    def test_includes_coordination_cost_counts(self, tmp_path: Path):
        root = _full_project(tmp_path)
        record_refused_claim(root)
        record_refused_claim(root)
        status = collect_status(root)
        assert status.coordination_cost.refused_claims == 2

    def test_includes_handover_snapshot(self, tmp_path: Path):
        root = _full_project(tmp_path)
        write_snapshot(root, ticket="RW-9", next_step="add tests")
        status = collect_status(root)
        assert len(status.handovers) == 1
        assert status.handovers[0].ticket == "RW-9"

    def test_includes_every_workers_snapshot(self, tmp_path: Path):
        """One per worker — collapsing them to the newest is how two of
        three workers' handovers stopped being visible."""
        root = _full_project(tmp_path)
        write_snapshot(root, ticket="RW-1", worker="alice")
        write_snapshot(root, ticket="RW-2", worker="bob")
        status = collect_status(root)
        assert {s.worker for s in status.handovers} == {"alice", "bob"}
        assert {s.ticket for s in status.handovers} == {"RW-1", "RW-2"}

    def test_no_handover_snapshot_is_empty_not_an_error(self, tmp_path: Path):
        root = _full_project(tmp_path)
        status = collect_status(root)
        assert status.handovers == []


class TestFormatStatus:
    def test_no_claims_says_so(self, tmp_path: Path):
        status = collect_status(_full_project(tmp_path))
        text = format_status(status)
        assert "no active claims" in text

    def test_coordination_cost_always_shown(self, tmp_path: Path):
        status = collect_status(_full_project(tmp_path))
        text = format_status(status)
        assert "refused claim attempts: 0" in text

    def test_uninstrumented_counters_say_so_rather_than_reading_zero(
        self, tmp_path: Path
    ):
        """Same class as the machine-wide burn rate below: the number was
        right and the presentation implied something it didn't mean. Only
        `refused_claims` has a collection point, so the other two can only
        ever be zero — an unqualified "0" reads as "measured, none found"
        when it means "never measured"."""
        status = collect_status(_full_project(tmp_path))
        text = format_status(status)
        assert '"nothing safe to start": 0 — not instrumented yet' in text
        assert "merge conflicts: 0 — not instrumented yet" in text
        # The one that IS instrumented stays a bare number.
        assert "refused claim attempts: 0\n" in text + "\n"

    def test_instrumented_counter_stays_unqualified_when_nonzero(self, tmp_path: Path):
        root = _full_project(tmp_path)
        record_refused_claim(root)
        text = format_status(collect_status(root))
        assert "refused claim attempts: 1" in text
        assert "refused claim attempts: 1 — not instrumented" not in text

    def test_board_state_says_it_was_not_queried_when_it_was_not(self, tmp_path: Path):
        """Generated content must state only what was actually measured —
        no line may imply board state was checked when it wasn't.

        The query is off by default, so the default rendering has to say
        so. What changed is that "I did not ask" is now distinguishable
        from "I asked and could not get an answer", which the old single
        "not shown" line conflated."""
        text = format_status(collect_status(_full_project(tmp_path)))
        assert "board state: not queried (skipped with --no-board)" in text
        # Nothing that would read as a measured result.
        assert "tickets —" not in text

    def test_burn_rate_shown_with_no_configured_budget(self, tmp_path: Path):
        status = collect_status(_full_project(tmp_path))
        text = format_status(status)
        assert "burn rate" in text
        # "current rate" used to name the week-to-date AVERAGE. Both
        # numbers are reported now, and the average says what it is.
        assert "average since" in text
        assert "0 tokens/hour" in text
        assert "% of weekly_token_budget" not in text  # nothing configured

    def test_burn_rate_is_explicitly_labelled_machine_wide(self, tmp_path: Path):
        """Found by hand: `rite status` in a brand-new, empty project
        showed a multi-billion-token burn rate — this machine's ENTIRE
        Claude Code usage across every unrelated project, presented with
        no indication it wasn't scoped to the project being asked about.
        The number is real (Anthropic's quota is account-wide); the
        missing piece was saying so."""
        status = collect_status(_full_project(tmp_path))
        text = format_status(status)
        assert "WHOLE MACHINE" in text
        assert "not just this one" in text

    def test_burn_rate_shows_no_percentage_against_a_per_project_budget(
        self, tmp_path: Path
    ):
        """`rite status` renders through the same `format_burn_rate`, so it
        printed the same category error the reported `rite budget` output
        did — a machine-wide total as a percentage of one project's target."""
        root = _full_project(tmp_path)
        (root / ".rite" / "config.yaml").write_text(
            "ticket_backend:\n  type: none\nbudget:\n  weekly_token_budget: 1000\n"
        )
        status = collect_status(root)
        text = format_status(status)
        assert "% of weekly_token_budget" not in text, text
        assert "exhaust" not in text, text
        assert "no percentage" in text


class TestCollectStatusBurnRate:
    def test_reads_real_transcript_lines_under_the_project(self, tmp_path: Path):
        import json
        from datetime import UTC, datetime

        from rite_ai.budget import default_transcripts_dir

        transcripts_dir = default_transcripts_dir()  # isolated by conftest.py
        proj_dir = transcripts_dir / "some-project"
        proj_dir.mkdir(parents=True)
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        (proj_dir / "s1.jsonl").write_text(
            json.dumps(
                {
                    "timestamp": now,
                    "sessionId": "s1",
                    "message": {"usage": {"input_tokens": 500, "output_tokens": 500}},
                }
            )
            + "\n"
        )
        status = collect_status(_full_project(tmp_path))
        assert status.burn_rate is not None
        assert status.burn_rate.tokens_this_week >= 1000

    def test_pool_reports_zero_live_with_no_pool_state_and_no_tmux_calls(
        self, tmp_path: Path
    ):
        """No pool.json means no recorded slots, which means no liveness
        probe subprocess call at all — collect_status must never spawn
        or shell out on the read-only path."""
        from unittest.mock import patch

        with patch("rite_ai.pool.subprocess.run") as mock_run:
            status = collect_status(_full_project(tmp_path))
        mock_run.assert_not_called()
        assert status.pool is not None
        assert status.pool.live == []
        assert status.pool.warn is True

    def test_unrecognised_timezone_leaves_burn_rate_unset(self, tmp_path: Path):
        root = _full_project(tmp_path)
        (root / ".rite" / "config.yaml").write_text(
            "ticket_backend:\n  type: none\nschedule:\n  timezone: Not/A_Real_Zone\n"
        )
        status = collect_status(root)
        assert status.burn_rate is None
