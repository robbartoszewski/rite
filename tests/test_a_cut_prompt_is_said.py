"""Ollama cutting a local Manager's prompt is said, or rite says it cannot tell.

Observed 2026-09-28 (plan, Track MS): a 79,296-token prompt to a model pinned
at 40,960 was cut to 20,482 tokens from the front, with a normal finish and no
error, and Goose exited 0 with a wrong answer. Only Ollama's log recorded it.
`local.truncation` reads that log.

The rule these tests hold it to: **"clean" only on positive evidence.** A
remote endpoint, an unverified Ollama version, an unreadable or stale or
rotated log, a log with no request in the cycle, or truncation wording rite
does not recognise all answer "cannot tell", never "no cut". A detector that
fails silent is the failure it exists to fix.

The log lines below are VERBATIM from the real `~/.ollama/logs/server.log`
of that run (Ollama 0.34.2), with only the timestamps chosen, so the parser is
held to the format as it was seen.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from rite_ai.local import truncation as T

LOCAL = "http://localhost:11434/v1"
T0 = datetime(2026, 9, 28, 8, 38, 0).astimezone()


def _iso(minute: int, second: int = 0) -> str:
    return T0.replace(minute=minute, second=second).isoformat(timespec="milliseconds")


def _gin(minute: int, second: int = 0, what='POST     "/v1/chat/completions"') -> str:
    stamp = T0.replace(minute=minute, second=second).strftime("%Y/%m/%d - %H:%M:%S")
    return f"[GIN] {stamp} | 200 |         1m36s |       127.0.0.1 | {what}"


def _cut(minute: int, sent: int = 79296) -> str:
    return (
        f"time={_iso(minute)} level=WARN source=llama_server.go:318 "
        f'msg="truncating input prompt" limit=20482 prompt={sent} keep=4 new=20482'
    )


SLOT = (
    "slot      release: id  0 | task 1419 | stop processing: "
    "n_tokens = 21102, truncated = 0"
)
START = f'time={_iso(30)} level=INFO source=routes.go:1 msg="server config"'


def _window(a: int, b: int) -> tuple[float, float]:
    return (
        T0.replace(minute=a).timestamp(),
        T0.replace(minute=b, second=59).timestamp(),
    )


class Version:
    def __init__(self, version="0.34.2"):
        self.version = version

    def __call__(self, url):
        assert url.endswith("/api/version"), url
        if isinstance(self.version, Exception):
            raise self.version

        class R:
            pass

        r = R()
        r.json = lambda: {"version": self.version}
        return r


def _check(tmp_path, lines, window=(38, 43), version="0.34.2", endpoint=LOCAL):
    log = tmp_path / "server.log"
    log.write_text("\n".join(lines) + "\n")
    return T.check_cycle(endpoint, *_window(*window), get=Version(version), log=log)


class TestWhatItSays:
    def test_a_cut_inside_the_cycle_is_reported_with_its_numbers(self, tmp_path):
        got = _check(tmp_path, [START, _gin(39), _cut(39), SLOT, _gin(41)])

        assert got.known and got.cut
        assert [(c.sent, c.kept) for c in got.cuts] == [(79296, 20482)]
        line = T.describe("helper", 40960, got)
        assert "CUT a prompt 1 time(s)" in line
        assert "79,296 tokens" in line and "20,482" in line
        assert "does not name the model" in line

    def test_a_cycle_with_requests_and_no_cut_is_clean_and_says_nothing(self, tmp_path):
        """llama.cpp's own `truncated = 0` slot line is not a truncation."""
        got = _check(tmp_path, [START, SLOT, _gin(39), SLOT])

        assert got.known and not got.cut
        assert T.describe("helper", 40960, got) == ""

    def test_a_cut_outside_the_cycle_is_not_this_cycles(self, tmp_path):
        got = _check(tmp_path, [START, _cut(35), _gin(39)])
        assert got.known and not got.cut


class TestWhereItCannotTellItSaysSo:
    @pytest.mark.parametrize(
        "endpoint", ["http://gpu.lan:11434/v1", "http://10.0.0.5:11434"]
    )
    def test_a_remote_endpoint(self, tmp_path, endpoint):
        got = _check(tmp_path, [START, _gin(39)], endpoint=endpoint)
        assert not got.known and "not on this machine" in got.why

    @pytest.mark.parametrize("version", ["0.35.0", "", ConnectionError("down")])
    def test_an_ollama_version_the_line_was_not_verified_on(self, tmp_path, version):
        """An upgrade must show up as "cannot tell", not as a quiet return to
        detecting nothing: a changed wording looks exactly like no cut."""
        got = _check(tmp_path, [START, _cut(39), _gin(39)], version=version)
        assert not got.known and "verified only on 0.34.2" in got.why

    def test_a_log_that_cannot_be_read(self, tmp_path):
        got = T.check_cycle(
            LOCAL, *_window(38, 43), get=Version(), log=tmp_path / "missing.log"
        )
        assert not got.known and "cannot be read" in got.why

    def test_a_log_that_starts_after_the_cycle_did(self, tmp_path):
        """Rotated during the cycle: a cut early in it would not be here."""
        got = _check(tmp_path, [_gin(40), _gin(41)])
        assert not got.known and "does not reach back" in got.why

    def test_a_log_with_no_request_in_the_cycle(self, tmp_path):
        """A stale file: `ollama serve` in a terminal logs to the terminal."""
        got = _check(tmp_path, [START, _gin(39, what='GET      "/api/version"')])
        assert not got.known and "records no model request" in got.why

    def test_truncation_worded_in_a_way_rite_does_not_recognise(self, tmp_path):
        changed = (
            f'time={_iso(39)} level=WARN msg="input truncated to fit" tokens=79296'
        )
        got = _check(tmp_path, [START, _gin(39), changed])
        assert not got.known and "does not recognise" in got.why
        assert "input truncated to fit" in got.why

    def test_an_untimestamped_truncation_line_is_checked_at_its_neighbours_time(
        self, tmp_path
    ):
        got = _check(
            tmp_path, [START, _gin(39), "llm: prompt truncated, 59000 dropped"]
        )
        assert not got.known and "does not recognise" in got.why

    def test_the_can_not_tell_line_names_the_reason(self, tmp_path):
        got = _check(tmp_path, [START, _gin(39)], version="9.9.9")
        line = T.describe("helper", 40960, got)
        assert line.startswith("cannot tell whether Ollama cut 'helper'")
        assert "9.9.9" in line


class TestTheSupervisorSaysIt:
    def _project(self, tmp_path, agent="goose", endpoint=LOCAL):
        (tmp_path / ".rite").mkdir()
        (tmp_path / ".rite" / "config.yaml").write_text(
            "coordination:\n  managers: [helper]\n  manager_roles:\n"
            "  - {name: helper, engine: 'local:small', preset: lead, "
            f"endpoint: '{endpoint}', model: 'qwen3:8b', agent: {agent}, "
            "context_window: 40960}\n"
        )
        return tmp_path

    def _run(self, root, verdict, monkeypatch, agent="goose", told=None):
        from rite_ai.managers import supervise as sup

        monkeypatch.setattr(T, "check_cycle", lambda endpoint, a, b: verdict)
        said: list[str] = []
        cuts = sup._say_if_the_window_was_cut(
            root,
            "helper",
            agent,
            0.0,
            1.0,
            said.append,
            told if told is not None else set(),
        )
        return said, cuts

    def test_a_cut_is_said_and_returned_for_the_check_in(self, tmp_path, monkeypatch):
        verdict = T.Verdict(True, cuts=[T.Cut(at=0.5, sent=79296, kept=20482)])
        said, cuts = self._run(self._project(tmp_path), verdict, monkeypatch)
        assert len(said) == 1 and "CUT a prompt" in said[0]
        assert cuts == verdict.cuts

    def test_cannot_tell_is_said_once_per_reason_per_run(self, tmp_path, monkeypatch):
        root = self._project(tmp_path)
        told: set[str] = set()
        verdict = T.Verdict(False, why="version 9.9.9")
        first, _ = self._run(root, verdict, monkeypatch, told=told)
        second, _ = self._run(root, verdict, monkeypatch, told=told)
        assert len(first) == 1 and "cannot tell" in first[0]
        assert second == []

    def test_every_cut_is_said_even_when_it_repeats(self, tmp_path, monkeypatch):
        root = self._project(tmp_path)
        told: set[str] = set()
        verdict = T.Verdict(True, cuts=[T.Cut(at=0.5, sent=79296, kept=20482)])
        first, _ = self._run(root, verdict, monkeypatch, told=told)
        second, _ = self._run(root, verdict, monkeypatch, told=told)
        assert first and second == first

    def test_a_clean_cycle_says_nothing(self, tmp_path, monkeypatch):
        said, cuts = self._run(self._project(tmp_path), T.Verdict(True), monkeypatch)
        assert said == [] and cuts == []

    def test_not_a_goose_manager_is_not_checked(self, tmp_path, monkeypatch):
        verdict = T.Verdict(True, cuts=[T.Cut(at=0.5, sent=9, kept=1)])
        said, _ = self._run(
            self._project(tmp_path), verdict, monkeypatch, agent="claude"
        )
        assert said == []
