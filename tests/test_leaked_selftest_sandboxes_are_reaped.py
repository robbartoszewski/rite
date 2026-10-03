"""A leaked self-test sandbox whose creator is dead gets reaped; one whose
creator is alive, or that holds unapplied work, does not (SCRUM-37).

A `rite-selftest-<pid>-<uuid>` sandbox is torn down in a `finally` and on
SIGTERM, but a SIGKILL or a crash bypasses both and leaves it `active`,
counting against `machine.max_sandboxes` — the orphan that helped trip the
broker worker-cap incident. `reap_dead_selftest_sandboxes` is the crash-safety
net the `finally` cannot be.

Every dependency is injected, so these are pure functions over hand-built
`SandboxEntry` listings — no real yoloai, and the assertions are about what
rite DOES with each sandbox, never about yoloai's output.
"""

from __future__ import annotations

from pathlib import Path

from rite_ai.sandbox import (
    CountUnavailable,
    SandboxEntry,
    reap_dead_selftest_sandboxes,
)

DEAD = 4751  # the pid in the orphan the ticket's docstring names
LIVE = 99_999_999


def _reap(entries, *, destroyed=None, self_pid=700, dry_run=False):
    destroyed = destroyed if destroyed is not None else []

    def destroy(name):
        destroyed.append(name)
        return True

    return (
        reap_dead_selftest_sandboxes(
            lister=lambda: list(entries),
            is_alive=lambda pid: pid == LIVE,
            destroy=destroy,
            self_pid=self_pid,
            dry_run=dry_run,
        ),
        destroyed,
    )


def test_a_dead_creators_probe_with_no_work_is_reaped():
    destroyed: list[str] = []
    result, destroyed = _reap(
        [SandboxEntry(name=f"rite-selftest-{DEAD}-d0775d81", has_changes=False)],
        destroyed=destroyed,
    )
    assert result.reaped == (f"rite-selftest-{DEAD}-d0775d81",)
    assert destroyed == [f"rite-selftest-{DEAD}-d0775d81"]  # really destroyed


def test_a_live_creators_probe_is_kept():
    result, destroyed = _reap(
        [SandboxEntry(name=f"rite-selftest-{LIVE}-aaaa1111", has_changes=False)]
    )
    assert result.reaped == ()
    assert destroyed == []
    assert any("is alive" in why for _, why in result.kept)


def test_a_dead_creators_probe_with_unapplied_work_is_kept():
    # NEVER destroy unapplied work — even a self-test's, even with a dead
    # creator. The guard outranks collecting one stray probe.
    result, destroyed = _reap(
        [SandboxEntry(name=f"rite-selftest-{DEAD}-bbbb2222", has_changes=True)]
    )
    assert result.reaped == ()
    assert destroyed == []
    assert any("unapplied" in why for _, why in result.kept)


def test_a_projects_worker_is_never_a_candidate():
    # Only `rite-selftest-*` is matched at all; a Worker is never reaped, never
    # even listed as "kept" (there is nothing to keep it FROM).
    result, destroyed = _reap(
        [SandboxEntry(name="rite-w1-abc123def-01", has_changes=False)]
    )
    assert result.reaped == ()
    assert destroyed == []
    assert result.kept == ()


def test_this_processs_own_probe_is_kept():
    # A probe this very process created (its `finally` will clean it) is not
    # reaped out from under an in-flight run.
    result, destroyed = _reap(
        [SandboxEntry(name="rite-selftest-700-cccc3333", has_changes=False)],
        self_pid=700,
    )
    assert result.reaped == ()
    assert destroyed == []
    assert any("this process" in why for _, why in result.kept)


def test_dry_run_reports_but_destroys_nothing():
    result, destroyed = _reap(
        [SandboxEntry(name=f"rite-selftest-{DEAD}-d0775d81", has_changes=False)],
        dry_run=True,
    )
    assert result.reaped == (f"rite-selftest-{DEAD}-d0775d81",)  # would reap
    assert destroyed == []  # but destroyed nothing


def test_a_mixed_machine_reaps_only_the_safe_dead_ones():
    entries = [
        SandboxEntry(name=f"rite-selftest-{DEAD}-11111111", has_changes=False),
        SandboxEntry(name=f"rite-selftest-{LIVE}-22222222", has_changes=False),
        SandboxEntry(name=f"rite-selftest-{DEAD}-33333333", has_changes=True),
        SandboxEntry(name="rite-alpha-9f9f9f-02", has_changes=False),
        SandboxEntry(name="rite-selftest-700-44444444", has_changes=False),
    ]
    result, destroyed = _reap(entries, self_pid=700)
    assert set(result.reaped) == {f"rite-selftest-{DEAD}-11111111"}
    assert destroyed == [f"rite-selftest-{DEAD}-11111111"]


def test_yoloai_unavailable_is_reported_not_crashed():
    result = reap_dead_selftest_sandboxes(
        lister=lambda: CountUnavailable("yoloai not found on PATH"),
        is_alive=lambda pid: False,
        destroy=lambda name: True,
    )
    assert result.unavailable == "yoloai not found on PATH"
    assert result.reaped == ()
    assert "could not reap" in result.summary


def test_a_destroy_that_fails_keeps_the_sandbox_named():
    # yoloai refusing to drop it is not a crash and not a silent loss: it stays,
    # named in `kept` with the reason, so the next pass tries again.
    result = reap_dead_selftest_sandboxes(
        lister=lambda: [
            SandboxEntry(name=f"rite-selftest-{DEAD}-ee55", has_changes=False)
        ],
        is_alive=lambda pid: False,
        destroy=lambda name: False,  # yoloai would not destroy it
        self_pid=1,
    )
    assert result.reaped == ()
    assert any("would not destroy" in why for _, why in result.kept)


# --- the periodic sweep is wired into the scheduler tick --------------------


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    rite = root / ".rite"
    rite.mkdir(parents=True)
    (rite / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n  site: test.atlassian.net\n"
        "  projects: {workers: ABC, board: XYZ}\n  credential: ''\n"
        "expertise: {}\npublish_gate:\n  scan_patterns: []\n"
        "  gitleaks_config: .rite/gitleaks.toml\n"
        "heartbeat:\n  interval_minutes: 10\n  stall_threshold: 3\n"
    )
    return root


def test_the_scheduler_tick_runs_the_reap(tmp_path, monkeypatch):
    # The periodic-sweep half of SCRUM-37: `run_tick` invokes the reap. A tick
    # that reaps something says so in its messages; a reap that cannot run must
    # never fail the tick.
    import rite_ai.sandbox as sb
    from rite_ai.scheduler import run_tick

    calls: list[bool] = []

    def fake_reap(**kwargs):
        calls.append(True)
        return sb.SelftestReap(reaped=("rite-selftest-4751-dead",))

    monkeypatch.setattr(sb, "reap_dead_selftest_sandboxes", fake_reap)
    result = run_tick(_project(tmp_path))
    assert calls, "the scheduler tick did not run the self-test reap"
    assert result.ok
    assert any("reaped leaked self-test" in m for m in result.messages)


def test_a_reap_that_raises_does_not_break_the_tick(tmp_path, monkeypatch):
    import rite_ai.sandbox as sb
    from rite_ai.scheduler import run_tick

    def boom(**kwargs):
        raise RuntimeError("yoloai exploded")

    monkeypatch.setattr(sb, "reap_dead_selftest_sandboxes", boom)
    result = run_tick(_project(tmp_path))
    assert result.ok  # the tick's real job (the watchdog) still ran
    assert any("reap skipped" in m for m in result.messages)


def test_a_listing_entry_missing_has_changes_is_kept(monkeypatch):
    # The fail-safe default, pinned: `list_rite_sandboxes` reads a MISSING
    # `has_changes` as "yes" (holds work) so a renamed/absent field can never
    # make the reaper destroy something. Flipping that default to "no" turns
    # this red — exactly the unpinned fail-safe the review flagged.
    import json
    import subprocess

    import rite_ai.sandbox as sb

    name = f"rite-selftest-{DEAD}-abadcafe"
    listing = {
        "sandboxes": [
            {
                # NOTE: no "has_changes" key — the whole point of this test.
                "status": "idle",
                "agent": "idle",
                "environment": {"name": name, "dirs": []},
            }
        ]
    }

    def fake_run(args, **kwargs):
        assert "ls" in args  # the `yoloai ls --active --json` call
        return subprocess.CompletedProcess(
            args, 0, stdout=json.dumps(listing), stderr=""
        )

    monkeypatch.setattr(sb, "_yoloai_binary", lambda: "yoloai")
    monkeypatch.setattr(sb.subprocess, "run", fake_run)

    destroyed: list[str] = []
    # Real lister (exercises the has_changes default); creator is dead.
    result = sb.reap_dead_selftest_sandboxes(
        is_alive=lambda pid: False,
        destroy=lambda n: destroyed.append(n) or True,
        self_pid=1,
    )
    assert destroyed == [], "a probe with an unknown has_changes was destroyed"
    assert result.reaped == ()
    assert any(name == kept and "unapplied" in why for kept, why in result.kept)
