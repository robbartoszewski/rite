import json
from datetime import UTC, datetime
from pathlib import Path

from rite_ai.budget import (
    UsageSample,
    compute_burn_rate,
    current_week_start,
    format_burn_rate,
    read_usage_since,
)


def _write_transcript(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def _usage_line(timestamp: str, session_id: str, **usage) -> dict:
    return {
        "timestamp": timestamp,
        "sessionId": session_id,
        "message": {"usage": usage},
    }


def _response_records(
    message_id: str, timestamps: list[str], blocks: list[str], **usage
) -> list[dict]:
    """One API response as Claude Code actually writes it: one JSONL record
    per content block, every record repeating the same `message.id` and the
    same whole-response `usage`. Copied from the real shape on disk — a
    14-record `tool_use` response was what made the machine-wide reading
    80% too high."""
    return [
        {
            "timestamp": ts,
            "sessionId": "s1",
            "uuid": f"{message_id}-{i}",
            "message": {
                "id": message_id,
                "content": [{"type": block}],
                "usage": usage,
            },
        }
        for i, (ts, block) in enumerate(zip(timestamps, blocks, strict=True))
    ]


class TestReadUsageSince:
    def test_sums_the_four_usage_fields_per_message(self, tmp_path: Path):
        _write_transcript(
            tmp_path / "proj" / "s1.jsonl",
            [
                _usage_line(
                    "2026-01-05T10:00:00Z",
                    "s1",
                    input_tokens=100,
                    output_tokens=50,
                    cache_creation_input_tokens=20,
                    cache_read_input_tokens=30,
                )
            ],
        )
        samples = read_usage_since(
            datetime(2026, 1, 1, tzinfo=UTC), transcripts_dir=tmp_path
        )
        assert len(samples) == 1
        assert samples[0].tokens == 200
        assert samples[0].session_id == "s1"

    def test_excludes_lines_before_since(self, tmp_path: Path):
        _write_transcript(
            tmp_path / "proj" / "s1.jsonl",
            [
                _usage_line("2026-01-01T00:00:00Z", "s1", input_tokens=10),
                _usage_line("2026-01-10T00:00:00Z", "s1", input_tokens=20),
            ],
        )
        samples = read_usage_since(
            datetime(2026, 1, 5, tzinfo=UTC), transcripts_dir=tmp_path
        )
        assert len(samples) == 1
        assert samples[0].tokens == 20

    def test_skips_lines_with_no_usage_block(self, tmp_path: Path):
        _write_transcript(
            tmp_path / "proj" / "s1.jsonl",
            [
                {"timestamp": "2026-01-05T00:00:00Z", "sessionId": "s1", "message": {}},
                {"timestamp": "2026-01-05T00:00:00Z", "type": "summary"},
            ],
        )
        samples = read_usage_since(
            datetime(2026, 1, 1, tzinfo=UTC), transcripts_dir=tmp_path
        )
        assert samples == []

    def test_malformed_json_line_is_skipped_not_a_crash(self, tmp_path: Path):
        path = tmp_path / "proj" / "s1.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text("not json at all\n")
        samples = read_usage_since(
            datetime(2026, 1, 1, tzinfo=UTC), transcripts_dir=tmp_path
        )
        assert samples == []

    def test_missing_directory_returns_empty_not_an_error(self, tmp_path: Path):
        samples = read_usage_since(
            datetime(2026, 1, 1, tzinfo=UTC), transcripts_dir=tmp_path / "nope"
        )
        assert samples == []

    def test_scans_across_multiple_project_subdirectories(self, tmp_path: Path):
        _write_transcript(
            tmp_path / "proj-a" / "s1.jsonl",
            [_usage_line("2026-01-05T00:00:00Z", "s1", input_tokens=10)],
        )
        _write_transcript(
            tmp_path / "proj-b" / "s2.jsonl",
            [_usage_line("2026-01-05T00:00:00Z", "s2", input_tokens=20)],
        )
        samples = read_usage_since(
            datetime(2026, 1, 1, tzinfo=UTC), transcripts_dir=tmp_path
        )
        assert {s.session_id for s in samples} == {"s1", "s2"}


class TestCurrentWeekStart:
    def test_wednesday_resolves_to_the_preceding_monday(self):
        now = datetime(2026, 1, 7, 15, 30, tzinfo=UTC)  # a Wednesday
        result = current_week_start(now, "UTC", "monday")
        assert result == datetime(2026, 1, 5, 0, 0, tzinfo=UTC)

    def test_on_the_boundary_day_itself_resolves_to_midnight_today(self):
        now = datetime(2026, 1, 5, 23, 0, tzinfo=UTC)  # a Monday
        result = current_week_start(now, "UTC", "monday")
        assert result == datetime(2026, 1, 5, 0, 0, tzinfo=UTC)

    def test_sunday_resolves_to_the_monday_six_days_earlier(self):
        now = datetime(2026, 1, 11, 12, 0, tzinfo=UTC)  # a Sunday
        result = current_week_start(now, "UTC", "monday")
        assert result == datetime(2026, 1, 5, 0, 0, tzinfo=UTC)

    def test_respects_a_non_monday_start_day(self):
        now = datetime(2026, 1, 7, 12, 0, tzinfo=UTC)  # a Wednesday
        result = current_week_start(now, "UTC", "sunday")
        assert result == datetime(2026, 1, 4, 0, 0, tzinfo=UTC)  # preceding Sunday

    def test_unknown_timezone_returns_none(self):
        assert current_week_start(datetime.now(UTC), "Not/A_Zone", "monday") is None

    def test_unknown_weekday_returns_none(self):
        assert current_week_start(datetime.now(UTC), "UTC", "funday") is None


class TestComputeBurnRate:
    def _samples(self, *token_counts):
        return [
            UsageSample(timestamp=datetime.now(UTC), session_id="s", tokens=t)
            for t in token_counts
        ]

    def test_raw_rate_with_no_budget_configured(self):
        week_start = datetime(2026, 1, 5, tzinfo=UTC)
        now = week_start.replace(hour=10)  # 10 hours elapsed
        report = compute_burn_rate(
            self._samples(1000, 1000), week_start, now, weekly_token_budget=None
        )
        assert report.tokens_this_week == 2000
        assert report.tokens_per_hour == 200

    def test_configured_budget_is_carried_but_not_divided_into_anything(self):
        week_start = datetime(2026, 1, 5, tzinfo=UTC)
        now = week_start.replace(hour=10)
        report = compute_burn_rate(
            self._samples(1_000_000), week_start, now, weekly_token_budget=10_000_000
        )
        assert report.weekly_token_budget == 10_000_000
        assert not hasattr(report, "pct_used")
        assert not hasattr(report, "pct_projected")
        assert not hasattr(report, "early_exhaustion_warning")

    def test_zero_samples_does_not_divide_by_zero(self):
        week_start = datetime(2026, 1, 5, tzinfo=UTC)
        report = compute_burn_rate([], week_start, week_start, weekly_token_budget=1000)
        assert report.tokens_per_hour == 0


class TestOneSamplePerApiResponse:
    """Claude Code writes one record per content block, all sharing a
    `message.id` and a `usage` object. Counting lines counts the same billed
    response once per block."""

    def test_a_multi_block_response_is_counted_once(self, tmp_path: Path):
        _write_transcript(
            tmp_path / "proj" / "s1.jsonl",
            _response_records(
                "msg_abc",
                [f"2026-01-05T10:00:0{i}Z" for i in range(5)],
                ["thinking", "text", "tool_use", "tool_use", "tool_use"],
                input_tokens=10,
                output_tokens=90,
                cache_creation_input_tokens=100,
                cache_read_input_tokens=100_000,
            ),
        )
        samples = read_usage_since(datetime(2026, 1, 1, tzinfo=UTC), tmp_path)

        assert len(samples) == 1
        assert samples[0].tokens == 100_200  # once, not 5x
        assert samples[0].cache_read_tokens == 100_000

    def test_distinct_responses_are_still_counted_separately(self, tmp_path: Path):
        entries = _response_records(
            "msg_one", ["2026-01-05T10:00:00Z"], ["text"], output_tokens=5
        ) + _response_records(
            "msg_two", ["2026-01-05T10:00:01Z"], ["text"], output_tokens=7
        )
        _write_transcript(tmp_path / "proj" / "s1.jsonl", entries)

        samples = read_usage_since(datetime(2026, 1, 1, tzinfo=UTC), tmp_path)

        assert len(samples) == 2
        assert sum(s.tokens for s in samples) == 12

    def test_a_streamed_partial_does_not_undercount_the_final_usage(
        self, tmp_path: Path
    ):
        """692 of this machine's weekly responses had records whose
        `output_tokens` grew between writes — a record captured mid-stream.
        Keeping the first would undercount, so the largest usage wins."""
        entries = _response_records(
            "msg_grow", ["2026-01-05T10:00:00Z"], ["text"], output_tokens=11
        ) + _response_records(
            "msg_grow", ["2026-01-05T10:00:02Z"], ["text"], output_tokens=237
        )
        _write_transcript(tmp_path / "proj" / "s1.jsonl", entries)

        samples = read_usage_since(datetime(2026, 1, 1, tzinfo=UTC), tmp_path)

        assert len(samples) == 1
        assert samples[0].tokens == 237
        # Earliest timestamp: when the response began, not when it finished.
        assert samples[0].timestamp == datetime(2026, 1, 5, 10, 0, 0, tzinfo=UTC)

    def test_records_without_a_message_id_are_kept(self, tmp_path: Path):
        """Nothing to deduplicate against — dropping them would silently
        undercount instead of silently overcounting."""
        _write_transcript(
            tmp_path / "proj" / "s1.jsonl",
            [
                _usage_line("2026-01-05T10:00:00Z", "s1", output_tokens=3),
                _usage_line("2026-01-05T10:00:01Z", "s1", output_tokens=4),
            ],
        )
        samples = read_usage_since(datetime(2026, 1, 1, tzinfo=UTC), tmp_path)
        assert sum(s.tokens for s in samples) == 7


class TestDisplaySplitsCacheReads:
    def test_total_is_split_so_cache_reads_cannot_be_misread_as_new_work(self):
        from rite_ai.budget import format_burn_rate

        samples = [
            UsageSample(
                timestamp=datetime(2026, 1, 5, tzinfo=UTC),
                session_id="s",
                tokens=1_000_000,
                message_id="m1",
                cache_read_tokens=985_000,
            )
        ]
        report = compute_burn_rate(
            samples,
            week_start=datetime(2026, 1, 5, tzinfo=UTC),
            now=datetime(2026, 1, 6, tzinfo=UTC),
            weekly_token_budget=None,
        )
        body = "\n".join(format_burn_rate(report))

        assert "cache reads: 985,000 (98.5%)" in body
        assert "new tokens:  15,000 (1.5%)" in body

    def test_configured_budget_is_named_and_never_called_a_quota(self):
        from rite_ai.budget import format_burn_rate

        report = compute_burn_rate(
            [
                UsageSample(
                    timestamp=datetime(2026, 1, 5, tzinfo=UTC),
                    session_id="s",
                    tokens=500,
                    message_id="m1",
                    cache_read_tokens=0,
                )
            ],
            week_start=datetime(2026, 1, 5, tzinfo=UTC),
            now=datetime(2026, 1, 6, tzinfo=UTC),
            weekly_token_budget=1000,
        )
        body = "\n".join(format_burn_rate(report))

        # The configured figure is named as the project's own target. It is
        # no longer expressed as a percentage of anything (see
        # TestNoPerProjectPercentage), and it was never a quota: rite cannot
        # see an account's real weekly limit, so it must not imply one.
        assert "weekly_token_budget" in body
        assert "quota" not in body.lower()


class TestNoPerProjectPercentage:
    """The burn-rate percentage divided a MACHINE-WIDE token total by
    `budget.weekly_token_budget`, a key in ONE project's config.yaml.

    Reported output, from a real machine with a 1,000,000 per-project
    target set:

        used: 215126.9% of weekly_token_budget
        projected: 392845.2% of weekly_token_budget
        warning: at the current rate, weekly_token_budget is projected to
                 exhaust after 0.0 day(s) — before the Monday week-end boundary

    2,151,268,863 machine-wide tokens over a 1,000,000 per-project budget.
    The warning fires for any project on a machine doing real work, whatever
    its target, and two projects with different targets got contradictory
    readings off the same underlying number.
    """

    WEEK_START = datetime(2026, 1, 5, tzinfo=UTC)
    REPORTED_TOKENS = 2_151_268_863
    REPORTED_BUDGET = 1_000_000

    def _reported_report(self, budget: int | None = REPORTED_BUDGET):
        samples = [
            UsageSample(
                timestamp=self.WEEK_START,
                session_id="s",
                tokens=self.REPORTED_TOKENS,
            )
        ]
        now = self.WEEK_START.replace(hour=10)
        return compute_burn_rate(samples, self.WEEK_START, now, budget)

    def test_the_reported_percentage_is_not_printed(self):
        lines = format_burn_rate(self._reported_report())
        rendered = "\n".join(lines)

        # The exact figure the ratio produced: 2151268863 / 1e6 * 100.
        assert "215126.9" not in rendered, rendered
        assert "% of weekly_token_budget" not in rendered, rendered

    def test_no_false_exhaustion_warning(self):
        rendered = "\n".join(format_burn_rate(self._reported_report()))
        assert "exhaust" not in rendered, rendered
        assert "0.0 day(s)" not in rendered, rendered

    def test_the_two_readings_agree_regardless_of_configured_target(self):
        """A project with a target and a project without one describe the
        same machine in the same terms. They used to disagree completely:
        one printed a six-figure percentage and an exhaustion warning, the
        other printed nothing at all."""
        with_budget = format_burn_rate(self._reported_report(self.REPORTED_BUDGET))
        without = format_burn_rate(self._reported_report(None))

        shared = [ln for ln in with_budget if "weekly_token_budget is set" not in ln]
        assert shared == without

    def test_two_different_targets_do_not_change_the_measurement(self):
        small = format_burn_rate(self._reported_report(1_000_000))
        large = format_burn_rate(self._reported_report(900_000_000))

        def measurement(lines):
            return [ln for ln in lines if "weekly_token_budget is set" not in ln]

        assert measurement(small) == measurement(large)

    def test_it_says_why_there_is_no_percentage(self):
        rendered = "\n".join(format_burn_rate(self._reported_report()))
        assert "no percentage" in rendered
        assert "not attributable to one project" in rendered

    def test_a_configured_target_is_named_not_silently_ignored(self):
        rendered = "\n".join(format_burn_rate(self._reported_report()))
        assert "1,000,000" in rendered, (
            "the user set a key and the output never mentions it"
        )
        assert "cannot measure against it" in rendered

    def test_no_target_configured_says_nothing_about_one(self):
        rendered = "\n".join(format_burn_rate(self._reported_report(None)))
        assert "weekly_token_budget is set" not in rendered

    def test_the_machine_wide_figures_are_still_reported(self):
        """Removing the ratio must not remove the measurement."""
        rendered = "\n".join(format_burn_rate(self._reported_report()))
        assert "average since" in rendered
        assert "projected week-end total:" in rendered
        assert f"{self.REPORTED_TOKENS:,}" in rendered


class TestTheRecentRateIsReportedBesideTheAverage:
    """Only the week-to-date average was reported, and it was labelled
    "current rate". Measured on a live corpus mid-session: that line read
    32,431,427 tokens/hour while the preceding hour had actually run at
    448,977,101 — fourteen times higher.

    The average is the right basis for the week-end projection and the
    wrong answer to "what is happening now", which is what a person
    reading a line called "current" is asking. Both are reported now, and
    each says which it is."""

    def test_a_recent_burst_shows_up_in_the_recent_rate_only(self):
        from datetime import UTC, datetime, timedelta

        from rite_ai.budget import compute_burn_rate

        now = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
        week_start = now - timedelta(hours=100)
        samples = [
            # A quiet week...
            UsageSample(week_start + timedelta(hours=1), "s", 1_000, "a"),
            # ...and one very busy final hour.
            UsageSample(now - timedelta(minutes=30), "s", 1_000_000, "b"),
        ]

        report = compute_burn_rate(samples, week_start, now, None)

        assert report.recent_tokens_per_hour == 1_000_000
        assert report.tokens_per_hour < 20_000
        assert report.recent_tokens_per_hour > report.tokens_per_hour * 50

    def test_both_numbers_are_rendered_and_labelled(self):
        from datetime import UTC, datetime, timedelta

        from rite_ai.budget import compute_burn_rate, format_burn_rate

        now = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
        week_start = now - timedelta(hours=100)
        samples = [UsageSample(now - timedelta(minutes=10), "s", 500, "a")]

        rendered = "\n".join(
            format_burn_rate(compute_burn_rate(samples, week_start, now, None))
        )

        assert "rate over the last hour:" in rendered
        assert "average since" in rendered
        # The label that conflated them is gone.
        assert "current rate:" not in rendered

    def test_a_silent_last_hour_reads_as_zero_not_as_missing(self):
        from datetime import UTC, datetime, timedelta

        from rite_ai.budget import compute_burn_rate

        now = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
        week_start = now - timedelta(hours=100)
        samples = [UsageSample(week_start + timedelta(hours=1), "s", 5_000, "a")]

        report = compute_burn_rate(samples, week_start, now, None)

        assert report.recent_tokens_per_hour == 0.0
        assert report.tokens_per_hour > 0

    def test_a_week_younger_than_the_window_reports_no_recent_rate(self):
        """Extrapolating a fraction of an hour to an hourly figure is how
        a burn rate reads as astronomical at 00:05 on a Monday."""
        from datetime import UTC, datetime, timedelta

        from rite_ai.budget import compute_burn_rate, format_burn_rate

        now = datetime(2026, 9, 11, 0, 10, tzinfo=UTC)
        week_start = now - timedelta(minutes=10)
        samples = [UsageSample(now - timedelta(minutes=1), "s", 5_000, "a")]

        report = compute_burn_rate(samples, week_start, now, None)

        assert report.recent_tokens_per_hour is None
        assert "rate over the last hour" not in "\n".join(format_burn_rate(report))


class TestOnlyThisWeeksTranscriptsAreOpened:
    """`rite status` re-parsed the entire Claude Code transcript corpus on
    every invocation. Measured on this machine: 3,478 files, 2,693 MB,
    960,940 JSON lines, 14 seconds — for a window that only 137 files and
    217 MB fell inside. It is the command a Dispatch session runs to
    orient itself, and it got slower every week the machine was used,
    with nothing bounding it.

    A file whose last modification predates the window cannot hold an
    entry inside it: entries are appended as they happen. Verified
    directly against the real corpus — of 3,341 files the skip would
    drop, zero contained an in-window usage entry."""

    def _transcript(self, root, name, when, tokens):
        import json
        import os

        path = root / f"{name}.jsonl"
        path.write_text(
            json.dumps(
                {
                    "timestamp": when.isoformat().replace("+00:00", "Z"),
                    "sessionId": name,
                    "message": {
                        "id": f"msg_{name}",
                        "usage": {"input_tokens": tokens},
                    },
                }
            )
            + "\n"
        )
        os.utime(path, (when.timestamp(), when.timestamp()))
        return path

    def test_a_transcript_older_than_the_window_is_not_opened(self, tmp_path):
        from datetime import UTC, datetime, timedelta

        from rite_ai.budget import read_usage_since

        now = datetime.now(UTC)
        since = now - timedelta(days=2)
        old = self._transcript(tmp_path, "old", now - timedelta(days=30), 999)
        self._transcript(tmp_path, "new", now - timedelta(hours=1), 5)

        opened = []
        real_read = type(old).read_text

        def spy(self, *a, **k):
            opened.append(self.name)
            return real_read(self, *a, **k)

        import rite_ai.budget as budget_module

        original = budget_module.Path.read_text
        budget_module.Path.read_text = spy
        try:
            samples = read_usage_since(since, transcripts_dir=tmp_path)
        finally:
            budget_module.Path.read_text = original

        assert "old.jsonl" not in opened, "read a file that cannot contribute"
        assert [s.tokens for s in samples] == [5]

    def test_the_total_is_unchanged_by_the_skip(self, tmp_path):
        from datetime import UTC, datetime, timedelta

        from rite_ai.budget import read_usage_since

        now = datetime.now(UTC)
        since = now - timedelta(days=2)
        for i in range(5):
            self._transcript(tmp_path, f"old{i}", now - timedelta(days=10 + i), 100)
        for i in range(3):
            self._transcript(tmp_path, f"new{i}", now - timedelta(hours=i + 1), 7)

        samples = read_usage_since(since, transcripts_dir=tmp_path)

        assert sorted(s.tokens for s in samples) == [7, 7, 7]

    def test_a_file_that_cannot_be_stat_ed_is_read_rather_than_dropped(
        self, tmp_path, monkeypatch
    ):
        """A file rite cannot measure is not a file rite may silently
        leave out of a total."""
        from datetime import UTC, datetime, timedelta

        from rite_ai.budget import _may_contain_entries_since

        path = tmp_path / "x.jsonl"
        path.write_text("{}\n")

        def boom(self, *a, **k):
            raise OSError("no")

        monkeypatch.setattr(type(path), "stat", boom)
        cutoff = (datetime.now(UTC) - timedelta(days=1)).timestamp()

        assert _may_contain_entries_since(path, cutoff) is True
