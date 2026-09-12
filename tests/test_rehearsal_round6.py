"""Regression tests for the defects found by rehearsal round 6.

Round 6 drove `rite projects` — the Dispatch hub's cross-project registry —
under real concurrent processes, and then read the aggregate view (§8.9)
the way a Manager sweeping several projects reads it.

The registry itself held: twenty-four processes registering at once lost
nothing, add and remove interleaved correctly, and the file stayed
well-formed. `test_the_registry_survives_concurrent_writers` below locks
that in, because it had never been tested and the sibling file with the
same shape (the claims ledger) lost claims in ten of twenty rounds.

What did not hold was the view built on top of it.
"""

from __future__ import annotations

import multiprocessing as mp
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.dispatch import add_project, load_registry

WRITERS = 12


def _register_one(args: tuple[str, int]) -> int:
    dispatch_dir, index = args
    add_project(Path(dispatch_dir), f"p{index}", Path(dispatch_dir), "owner")
    return index


def _project(root: Path, name: str = "acme") -> Path:
    rite_dir = root / ".rite"
    rite_dir.mkdir(parents=True, exist_ok=True)
    (rite_dir / "brief.yaml").write_text(
        f"project:\n  name: {name}\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    (rite_dir / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "heartbeat:\n  interval_minutes: 10\n  stall_threshold: 3\n"
    )
    return root


def _aggregate(tmp_path: Path, entries: dict[str, Path], monkeypatch) -> str:
    """`rite status` run from outside any project, with `entries`
    registered — the §8.9 cross-project view.

    Driven through `RITE_DISPATCH_DIR`, the documented override, rather
    than by patching the lookup: `default_dispatch_dir` is imported inside
    the command, so there is no `cli.main` attribute to patch, and the env
    var is what a user redirecting a hub actually sets.
    """
    hub = tmp_path / "hub"
    hub.mkdir(parents=True, exist_ok=True)
    for alias, path in entries.items():
        add_project(hub, alias, path, "manager")
    monkeypatch.setenv("RITE_DISPATCH_DIR", str(hub))
    with patch("rite_ai.cli.main._has_project_in_scope", return_value=False):
        return CliRunner().invoke(cli, ["status", "--no-board"]).output


# --- the registry itself, which was fine -----------------------------------


class TestTheRegistrySurvivesConcurrentWriters:
    """`rite_ai.state`'s docstring names "the Dispatch registry" among the
    files the atomic-write sweep left behind, and the claims ledger — the
    same read-modify-write shape — lost at least one claim in ten of
    twenty rounds before its lock was fixed. Measured here across five
    rounds of twenty-four concurrent processes: nothing lost, nothing
    corrupted. This test exists so that stays true."""

    def test_every_concurrent_registration_survives(self, tmp_path: Path):
        for round_number in range(3):
            hub = tmp_path / f"round{round_number}"
            hub.mkdir()
            with mp.Pool(WRITERS) as pool:
                pool.map(_register_one, [(str(hub), i) for i in range(WRITERS)])

            registered = set(load_registry(hub).projects)
            assert registered == {f"p{i}" for i in range(WRITERS)}, (
                f"round {round_number}: lost "
                f"{sorted({f'p{i}' for i in range(WRITERS)} - registered)}"
            )


# --- the view built on top of it, which was not ----------------------------


class TestTheAggregateViewTellsItsFailuresApart:
    """The cross-project line was

        health = "no .rite/ found" if s.errors else "ok"

    followed by the claim and stalled-worker counts, unconditionally. So a
    directory that had been moved away, a real project whose
    `.rite/brief.yaml` would not parse, and a path that was never a project
    all printed the same sentence:

        moved  (manager): no .rite/ found, 0 active claim(s), 0 stalled worker(s)
        broken (manager): no .rite/ found, 0 active claim(s), 0 stalled worker(s)
        bare   (manager): no .rite/ found, 0 active claim(s), 0 stalled worker(s)

    For `broken` that sentence is simply untrue — its `.rite/` is right
    there — and it sends someone hunting a directory that is not missing.

    The zeros are the worse half. `0 stalled worker(s)` from a project
    nothing opened reads exactly like `0 stalled worker(s)` from one that
    was read and is fine, in the view whose whole purpose is deciding
    where NOT to look. `CountUnavailable` in `rite_ai.sandbox` is this
    same distinction, already drawn elsewhere in this codebase.
    """

    def _three_broken_and_one_alive(self, tmp_path: Path) -> dict[str, Path]:
        alive = _project(tmp_path / "alive", "alive")
        worker = alive / "workers" / "w1"
        worker.mkdir(parents=True)
        (worker / "worker.yml").write_text('worker:\n  name: "w1"\n  modules: []\n')

        moved = tmp_path / "moved"  # registered, then never created
        broken = _project(tmp_path / "broken", "broken")
        (broken / ".rite" / "brief.yaml").write_text("project: [unclosed\n")
        bare = tmp_path / "bare"
        bare.mkdir()
        return {"alive": alive, "moved": moved, "broken": broken, "bare": bare}

    def test_the_three_unreadable_states_do_not_render_alike(
        self, tmp_path: Path, monkeypatch
    ):
        out = _aggregate(
            tmp_path, self._three_broken_and_one_alive(tmp_path), monkeypatch
        )

        lines = {
            alias: [ln for ln in out.splitlines() if ln.strip().startswith(alias)][0]
            for alias in ("moved", "broken", "bare")
        }
        rendered = {alias: ln.split(":", 1)[1] for alias, ln in lines.items()}
        assert len(set(rendered.values())) == 3, (
            "a moved directory, an unparseable config and a non-project all "
            f"still read the same:\n{chr(10).join(lines.values())}"
        )

    def test_an_unparseable_config_is_not_called_a_missing_directory(
        self, tmp_path: Path, monkeypatch
    ):
        out = _aggregate(
            tmp_path, self._three_broken_and_one_alive(tmp_path), monkeypatch
        )

        line = [ln for ln in out.splitlines() if ln.strip().startswith("broken")][0]
        assert "no .rite/ found" not in line, (
            f"says the directory is missing when it is right there: {line}"
        )
        assert "brief.yaml" in line, "does not name the file that will not parse"
        assert "invalid YAML" in line, (
            "discards the diagnosis the single-project view already produces"
        )

    def test_a_project_that_was_not_read_reports_no_counts(
        self, tmp_path: Path, monkeypatch
    ):
        out = _aggregate(
            tmp_path, self._three_broken_and_one_alive(tmp_path), monkeypatch
        )

        for alias in ("moved", "broken", "bare"):
            line = [ln for ln in out.splitlines() if ln.strip().startswith(alias)][0]
            assert "0 active claim(s)" not in line, (
                f"reports a count taken from a project it never opened: {line}"
            )
            assert "0 stalled worker(s)" not in line, line

    def test_a_missing_directory_says_so(self, tmp_path: Path, monkeypatch):
        out = _aggregate(
            tmp_path, self._three_broken_and_one_alive(tmp_path), monkeypatch
        )

        line = [ln for ln in out.splitlines() if ln.strip().startswith("moved")][0]
        assert "GONE" in line or "no longer exists" in line, line

    def test_a_healthy_project_still_reports_real_counts(
        self, tmp_path: Path, monkeypatch
    ):
        """The whole point of the view. It must not become noise."""
        entries = self._three_broken_and_one_alive(tmp_path)
        out = _aggregate(tmp_path, entries, monkeypatch)

        line = [ln for ln in out.splitlines() if ln.strip().startswith("alive")][0]
        assert "ok," in line, line
        assert "active claim(s)" in line and "stalled worker(s)" in line, line


class TestTheErrorShorteningKeepsTheDiagnosis:
    """First cut truncated the raw `"<absolute path>: <message>"` string,
    and a scratch project path is long enough to spend the entire budget —
    printing `UNREADABLE — /private/tmp/.../agg/.` with the reason cut off
    altogether. The path is the part the line has already named."""

    def test_the_path_goes_and_the_message_stays(self):
        from rite_ai.cli.main import _short_error

        out = _short_error(
            "/very/long/path/to/a/project/.rite/brief.yaml: invalid YAML"
        )

        assert out.startswith("brief.yaml:")
        assert "invalid YAML" in out
        assert "/very/long" not in out

    def test_a_cut_message_is_marked_as_cut(self):
        from rite_ai.cli.main import _short_error

        out = _short_error("f.yaml: " + "x" * 400)

        assert out.endswith("…"), "a sentence stopping mid-excerpt reads as whole"
        assert len(out) <= 121

    def test_a_message_with_no_path_is_left_alone(self):
        from rite_ai.cli.main import _short_error

        assert _short_error("no .rite/ directory — run `rite init`") == (
            "no .rite/ directory — run `rite init`"
        )
