"""The insufficiency rate (S-7).

The one number that catches a slice being too small — including the case the
reference graph cannot see, a sparse spec whose dependencies are real but never
written down. So its readings have to be impossible to misread: no data must
never look like a perfect score.
"""

import multiprocessing
from pathlib import Path

from rite_ai.cli.init.scaffold import AUTHORED_CONFIG
from rite_ai.spec.telemetry import (
    FALLBACKS_ONLY,
    MEASURED,
    NO_DATA,
    UNREADABLE,
    insufficiency_rate,
    read_events,
    record_fallback,
    record_retrieval,
    telemetry_path,
)

DAY = 86400.0
NOW = 1_800_000_000.0


def test_no_data_is_not_a_zero_rate(tmp_path: Path):
    rate = insufficiency_rate(tmp_path, now=NOW)
    assert rate.status == NO_DATA
    assert rate.value is None
    assert "no evidence either way" in rate.describe()


def test_retrievals_with_no_fallback_measure_zero(tmp_path: Path):
    record_retrieval(tmp_path, "5.3.3", now=NOW - 60)
    rate = insufficiency_rate(tmp_path, now=NOW)
    assert rate.status == MEASURED
    assert rate.value == 0.0


def test_the_rate_is_fallbacks_over_retrievals(tmp_path: Path):
    for unit in ("D-1", "D-2", "scope", "architecture"):
        record_retrieval(
            tmp_path, unit, worker="alpha", slice_ratio=0.04, now=NOW - 3600
        )
    record_fallback(tmp_path, "architecture", worker="alpha", now=NOW - 1800)

    rate = insufficiency_rate(tmp_path, now=NOW)

    assert (rate.retrievals, rate.fallbacks, rate.value) == (4, 1, 0.25)
    assert "1 of 4" in rate.describe() and "25.0%" in rate.describe()


def test_only_the_window_counts(tmp_path: Path):
    record_retrieval(tmp_path, "D-1", now=NOW - 30 * DAY)
    record_fallback(tmp_path, "D-1", now=NOW - 30 * DAY)
    record_retrieval(tmp_path, "D-2", now=NOW - DAY)

    rate = insufficiency_rate(tmp_path, window_seconds=7 * DAY, now=NOW)

    assert (rate.retrievals, rate.fallbacks) == (1, 0)


def test_fallbacks_without_retrievals_say_the_slices_are_skipped(tmp_path: Path):
    record_fallback(tmp_path, "D-4", now=NOW - 60)
    rate = insufficiency_rate(tmp_path, now=NOW)
    assert rate.status == FALLBACKS_ONLY
    assert rate.value is None
    assert "skipped" in rate.describe()


def test_a_damaged_line_is_counted_not_skipped(tmp_path: Path):
    record_retrieval(tmp_path, "D-1", now=NOW - 60)
    with telemetry_path(tmp_path).open("a") as handle:
        handle.write('{"kind": "retrieval", "unit": \n')
        handle.write('{"kind": "guess", "unit": "D-9", "at": 1}\n')
    record_fallback(tmp_path, "D-1", now=NOW - 30)

    rate = insufficiency_rate(tmp_path, now=NOW)

    assert (rate.retrievals, rate.fallbacks, rate.unreadable_lines) == (1, 1, 2)
    assert "2 unreadable line(s)" in rate.describe()


def test_a_log_that_cannot_be_read_is_unreadable_not_empty(tmp_path: Path):
    telemetry_path(tmp_path).mkdir(parents=True)  # a directory where the file goes
    rate = insufficiency_rate(tmp_path, now=NOW)
    assert rate.status == UNREADABLE
    assert rate.value is None
    assert "cannot be read" in rate.describe()


def test_a_retrieval_keeps_its_slice_ratio_and_worker(tmp_path: Path):
    record_retrieval(
        tmp_path, "2.4/promotion", worker="beta", slice_ratio=0.093, now=NOW
    )
    (event,) = read_events(tmp_path).events
    assert (event.unit, event.worker, event.slice_ratio) == (
        "2.4/promotion",
        "beta",
        0.093,
    )


def test_the_log_is_runtime_state_outside_the_committed_digest(tmp_path: Path):
    rel = telemetry_path(tmp_path).relative_to(tmp_path).as_posix()
    assert not rel.startswith(".rite/spec/")
    assert not any(
        rel == p or rel.startswith(p) for p in AUTHORED_CONFIG if p.endswith("/")
    )
    assert rel not in AUTHORED_CONFIG


def _record_many(root: str, worker: str) -> None:
    for i in range(50):
        record_retrieval(Path(root), f"D-{i}", worker=worker, now=NOW - 60)


def test_concurrent_workers_do_not_tear_lines(tmp_path: Path):
    ctx = multiprocessing.get_context("fork")
    procs = [
        ctx.Process(target=_record_many, args=(str(tmp_path), f"w{n}"))
        for n in range(4)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0

    log = read_events(tmp_path)

    assert log.unreadable_lines == 0
    assert len(log.events) == 200
