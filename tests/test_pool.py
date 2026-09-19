import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from rite_ai.claims.ledger import ClaimsLedger
from rite_ai.config.models import PoolConfig
from rite_ai.pool import (
    archive,
    fill,
    is_tmux_session_alive,
    probe,
    read_archive,
    slot_name,
)


def _write_pool_state(root: Path, slots: list[dict]) -> None:
    (root / ".rite").mkdir(exist_ok=True)
    (root / ".rite" / "pool.json").write_text(json.dumps({"slots": slots}))


class TestSlotName:
    def test_deterministic_and_project_scoped(self, tmp_path: Path):
        assert slot_name(tmp_path, 0) == slot_name(tmp_path, 0)
        assert slot_name(tmp_path, 0) != slot_name(tmp_path, 1)

    def test_differs_across_projects(self, tmp_path: Path):
        other = tmp_path / "other"
        other.mkdir()
        assert slot_name(tmp_path, 0) != slot_name(other, 0)


class TestIsTmuxSessionAlive:
    @patch("rite_ai.pool.shutil.which", return_value=None)
    def test_false_when_tmux_missing(self, mock_which):
        assert is_tmux_session_alive("whatever") is False

    @patch("rite_ai.pool.shutil.which", return_value="/usr/local/bin/tmux")
    @patch("rite_ai.pool.subprocess.run")
    def test_true_on_zero_exit(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(returncode=0)
        assert is_tmux_session_alive("s") is True

    @patch("rite_ai.pool.shutil.which", return_value="/usr/local/bin/tmux")
    @patch("rite_ai.pool.subprocess.run")
    def test_false_on_nonzero_exit(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(returncode=1)
        assert is_tmux_session_alive("s") is False


class TestFill:
    @patch("rite_ai.pool.shutil.which", return_value=None)
    def test_missing_tmux_refuses_cleanly(self, mock_which, tmp_path: Path):
        result = fill(tmp_path, PoolConfig(coordinator_standby=2))
        assert not result.ok
        assert "tmux not found" in result.message

    @patch("rite_ai.pool.shutil.which", return_value="/usr/local/bin/tmux")
    @patch("rite_ai.pool.time.sleep")
    @patch("rite_ai.pool.is_tmux_session_alive", return_value=True)
    @patch("rite_ai.pool.subprocess.run")
    def test_starts_up_to_target_depth(
        self, mock_run, mock_alive, mock_sleep, mock_which, tmp_path: Path
    ):
        """⚠ This used to patch `is_tmux_session_alive` to FALSE and still
        assert three sessions started successfully — which is a contradiction
        the code was happy to satisfy, because nothing checked whether a
        started session lived. It encoded the facade rather than the
        behaviour, and it passed throughout.

        True now, because the assertion is that fill starts three sessions
        WHEN TMUX WORKS. `time.sleep` is patched only for speed: the settle
        window is real and `tests/test_pool_fill_really_starts.py` exercises
        it against the real binary, which is where a mocked test cannot go.
        """
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = fill(tmp_path, PoolConfig(coordinator_standby=3))
        assert result.ok
        assert len(result.started) == 3
        state = json.loads((tmp_path / ".rite" / "pool.json").read_text())
        assert len(state["slots"]) == 3

    @patch("rite_ai.pool.shutil.which", return_value="/usr/local/bin/tmux")
    @patch("rite_ai.pool.subprocess.run")
    def test_existing_live_slots_count_toward_target(
        self, mock_run, mock_which, tmp_path: Path
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        existing_name = slot_name(tmp_path, 0)
        _write_pool_state(
            tmp_path,
            [{"name": existing_name, "created_at": 1.0, "last_live_at": 1.0}],
        )
        with patch("rite_ai.pool.is_tmux_session_alive", return_value=True):
            result = fill(tmp_path, PoolConfig(coordinator_standby=2))
        assert result.ok
        # only one NEW session needed since the existing one is live
        assert len(result.started) == 1
        mock_run.assert_called_once()

    @patch("rite_ai.pool.shutil.which", return_value="/usr/local/bin/tmux")
    @patch("rite_ai.pool.is_tmux_session_alive", return_value=False)
    @patch("rite_ai.pool.subprocess.run")
    def test_passes_the_configured_command(
        self, mock_run, mock_alive, mock_which, tmp_path: Path
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        fill(tmp_path, PoolConfig(coordinator_standby=1), command="claude")
        args = mock_run.call_args[0][0]
        assert args[-1] == "claude"
        assert "-d" in args

    @patch("rite_ai.pool.shutil.which", return_value="/usr/local/bin/tmux")
    @patch("rite_ai.pool.is_tmux_session_alive", return_value=False)
    @patch("rite_ai.pool.subprocess.run")
    def test_tmux_failure_reports_partial_progress(
        self, mock_run, mock_alive, mock_which, tmp_path: Path
    ):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="server exited"
        )
        result = fill(tmp_path, PoolConfig(coordinator_standby=2))
        assert not result.ok
        assert "server exited" in result.message


class TestProbe:
    def test_no_state_reports_zero_live(self, tmp_path: Path):
        status = probe(tmp_path, PoolConfig(coordinator_standby=2))
        assert status.live == []
        assert status.warn is True

    @patch("rite_ai.pool.is_tmux_session_alive", return_value=True)
    def test_live_session_within_lease_window_counts_as_live(
        self, mock_alive, tmp_path: Path
    ):
        name = slot_name(tmp_path, 0)
        now = time.time()
        _write_pool_state(
            tmp_path, [{"name": name, "created_at": now, "last_live_at": now}]
        )
        status = probe(tmp_path, PoolConfig(coordinator_standby=1), now=now + 10)
        assert status.live == [name]
        assert status.stale == []

    @patch("rite_ai.pool.is_tmux_session_alive", return_value=False)
    def test_dead_tmux_session_is_stale(self, mock_alive, tmp_path: Path):
        name = slot_name(tmp_path, 0)
        now = time.time()
        _write_pool_state(
            tmp_path, [{"name": name, "created_at": now, "last_live_at": now}]
        )
        status = probe(tmp_path, PoolConfig(coordinator_standby=1), now=now + 10)
        assert status.live == []
        assert status.stale == [name]

    @patch("rite_ai.pool.is_tmux_session_alive", return_value=True)
    def test_lease_not_refreshed_within_expiry_is_stale_even_if_alive(
        self, mock_alive, tmp_path: Path
    ):
        """Verify-don't-assume (§2.5.2): a slot's lease must have been
        refreshed recently, not merely have an alive tmux session — this
        is what distinguishes 'the Manager actually checked' from
        'nobody has checked in a while and we're hoping.'"""
        name = slot_name(tmp_path, 0)
        stale_since = time.time() - 3600  # an hour ago — past the 15-min expiry
        _write_pool_state(
            tmp_path,
            [{"name": name, "created_at": stale_since, "last_live_at": stale_since}],
        )
        status = probe(tmp_path, PoolConfig(coordinator_standby=1), now=time.time())
        assert status.live == []
        assert status.stale == [name]

    @patch("rite_ai.pool.is_tmux_session_alive", return_value=True)
    def test_warn_threshold_fires_below_configured_fraction(
        self, mock_alive, tmp_path: Path
    ):
        name = slot_name(tmp_path, 0)
        now = time.time()
        _write_pool_state(
            tmp_path, [{"name": name, "created_at": now, "last_live_at": now}]
        )
        status = probe(
            tmp_path, PoolConfig(coordinator_standby=4, warn_threshold=0.5), now=now
        )
        # 1 live out of target 4, warn threshold 0.5 -> need >= 2 to avoid warning
        assert status.warn is True
        assert "1/4" in status.message

    @patch("rite_ai.pool.is_tmux_session_alive", return_value=True)
    def test_no_warning_when_at_or_above_threshold(self, mock_alive, tmp_path: Path):
        names = [slot_name(tmp_path, i) for i in range(2)]
        now = time.time()
        _write_pool_state(
            tmp_path,
            [{"name": n, "created_at": now, "last_live_at": now} for n in names],
        )
        status = probe(
            tmp_path, PoolConfig(coordinator_standby=2, warn_threshold=0.5), now=now
        )
        assert status.warn is False


MINUTE = 60
TMUX = "/usr/local/bin/tmux"


def _ledger(root: Path) -> ClaimsLedger:
    return ClaimsLedger(root / ".rite" / "claims.json")


def _alive_except(*dead: str):
    """Liveness stub: everything answers the probe except the named
    sessions — the shape of every real case here, where one session died
    and its neighbours did not."""

    def _check(name: str) -> bool:
        return name not in dead

    return _check


class TestStaleClaimFromASessionThatSkippedItsShutdownHook:
    """The production symptom, reproduced.

    A pooled coordinator is promoted onto real work and claims paths.
    Its session then ends *without* running its shutdown hook — killed,
    crashed, machine restarted — so `rite stop`/`perform_handover` never
    runs and the claim is never released. The claim outlives the session
    and keeps reading as live work: `rite status` lists it, and it goes
    on refusing every overlapping claim another session makes. That has
    twice been reported as a live worker holding a path when nothing was
    running at all.
    """

    def test_orphaned_claim_reads_as_live_and_blocks_a_real_worker(
        self, tmp_path: Path
    ):
        dead = slot_name(tmp_path, 0)
        now = time.time()
        _write_pool_state(
            tmp_path,
            [{"name": dead, "created_at": now, "last_live_at": now, "worker": dead}],
        )
        ledger = _ledger(tmp_path)
        assert ledger.claim(["src/payments"], dead, "PAY-1").ok

        # The session dies. Nothing releases the claim.
        with patch(
            "rite_ai.pool.is_tmux_session_alive", side_effect=_alive_except(dead)
        ):
            status = probe(tmp_path, PoolConfig(coordinator_standby=1), now=now + 10)

        assert status.stale == [dead]  # the POOL knows the session is gone...
        # ...but the ledger does not, so the claim still reads as live work
        # and refuses a real worker's overlapping claim.
        assert [c.worker for c in ledger.list_claims()] == [dead]
        contended = ledger.claim(["src/payments/checkout.ts"], "pool-b", "PAY-2")
        assert not contended.ok
        assert "held by" in contended.overlaps[0]

    def test_archive_releases_the_orphaned_claim_and_unblocks_the_worker(
        self, tmp_path: Path
    ):
        dead = slot_name(tmp_path, 0)
        now = time.time()
        _write_pool_state(
            tmp_path,
            [
                {
                    "name": dead,
                    "created_at": now - 60 * MINUTE,
                    "last_live_at": now - 60 * MINUTE,
                    "worker": dead,
                    "unreachable_since": now - 45 * MINUTE,
                }
            ],
        )
        ledger = _ledger(tmp_path)
        assert ledger.claim(["src/payments"], dead, "PAY-1").ok

        with (
            patch("rite_ai.pool.shutil.which", return_value=TMUX),
            patch(
                "rite_ai.pool.is_tmux_session_alive", side_effect=_alive_except(dead)
            ),
        ):
            result = archive(
                tmp_path,
                PoolConfig(coordinator_standby=1, archive_after_minutes=30),
                ledger=ledger,
                now=now,
            )

        assert result.ok
        assert [a.name for a in result.archived] == [dead]
        assert result.released_claims == 1
        assert ledger.list_claims() == []
        # The claim that was refused above now succeeds.
        assert ledger.claim(["src/payments/checkout.ts"], "pool-b", "PAY-2").ok
        # And the slot record is gone, so nothing reports it as capacity.
        state = json.loads((tmp_path / ".rite" / "pool.json").read_text())
        assert state["slots"] == []

    def test_the_release_is_recorded_with_a_reason(self, tmp_path: Path):
        dead = slot_name(tmp_path, 0)
        now = time.time()
        _write_pool_state(
            tmp_path,
            [
                {
                    "name": dead,
                    "created_at": now - 60 * MINUTE,
                    "last_live_at": now - 60 * MINUTE,
                    "worker": dead,
                    "unreachable_since": now - 45 * MINUTE,
                }
            ],
        )
        ledger = _ledger(tmp_path)
        ledger.claim(["src/payments"], dead, "PAY-1")

        with (
            patch("rite_ai.pool.shutil.which", return_value=TMUX),
            patch(
                "rite_ai.pool.is_tmux_session_alive", side_effect=_alive_except(dead)
            ),
        ):
            archive(
                tmp_path,
                PoolConfig(archive_after_minutes=30),
                ledger=ledger,
                now=now,
            )

        records = read_archive(tmp_path)
        assert len(records) == 1
        assert records[0]["slot"] == dead
        assert "shutdown hook" in records[0]["reason"]
        assert records[0]["released_claims"] == [
            {"paths": ["src/payments"], "ticket": "PAY-1"}
        ]


class TestArchiveRefusesToGuess:
    """Archiving force-releases another session's claims, so every
    ambiguous case has to resolve to "do nothing"."""

    def test_a_live_session_is_never_archived_however_old_the_record(
        self, tmp_path: Path
    ):
        name = slot_name(tmp_path, 0)
        now = time.time()
        _write_pool_state(
            tmp_path,
            [
                {
                    "name": name,
                    "created_at": now - 10 * 60 * MINUTE,
                    "last_live_at": now - 10 * 60 * MINUTE,
                    "worker": name,
                    # A stale clock left behind by an earlier outage.
                    "unreachable_since": now - 10 * 60 * MINUTE,
                }
            ],
        )
        ledger = _ledger(tmp_path)
        ledger.claim(["src/api"], name, "API-9")

        with (
            patch("rite_ai.pool.shutil.which", return_value=TMUX),
            patch("rite_ai.pool.is_tmux_session_alive", return_value=True),
        ):
            result = archive(
                tmp_path, PoolConfig(archive_after_minutes=0), ledger=ledger, now=now
            )

        assert result.archived == []
        assert len(ledger.list_claims()) == 1
        # Seeing it alive also clears the stale clock.
        state = json.loads((tmp_path / ".rite" / "pool.json").read_text())
        assert state["slots"][0]["unreachable_since"] is None

    def test_missing_tmux_archives_nothing(self, tmp_path: Path):
        """Without tmux every probe fails, so every slot *looks* dead.
        Believing that would release the whole project's claims at once."""
        name = slot_name(tmp_path, 0)
        now = time.time()
        _write_pool_state(
            tmp_path,
            [
                {
                    "name": name,
                    "created_at": now - 60 * MINUTE,
                    "last_live_at": now - 60 * MINUTE,
                    "worker": name,
                    "unreachable_since": now - 60 * MINUTE,
                }
            ],
        )
        ledger = _ledger(tmp_path)
        ledger.claim(["src/api"], name, "API-9")

        with patch("rite_ai.pool.shutil.which", return_value=None):
            result = archive(tmp_path, PoolConfig(), ledger=ledger, now=now)

        assert not result.ok
        assert "cannot verify" in result.message
        assert result.archived == []
        assert len(ledger.list_claims()) == 1

    def test_unreachable_but_inside_the_window_is_left_alone(self, tmp_path: Path):
        name = slot_name(tmp_path, 0)
        now = time.time()
        _write_pool_state(
            tmp_path,
            [
                {
                    "name": name,
                    "created_at": now - 20 * MINUTE,
                    "last_live_at": now - 20 * MINUTE,
                    "worker": name,
                    "unreachable_since": now - 10 * MINUTE,
                }
            ],
        )
        with (
            patch("rite_ai.pool.shutil.which", return_value=TMUX),
            patch("rite_ai.pool.is_tmux_session_alive", return_value=False),
        ):
            result = archive(
                tmp_path,
                PoolConfig(archive_after_minutes=30),
                ledger=_ledger(tmp_path),
                now=now,
            )

        assert result.archived == []
        assert result.waiting == [name]
        assert "not yet past the threshold" in result.message

    def test_dry_run_changes_nothing(self, tmp_path: Path):
        name = slot_name(tmp_path, 0)
        now = time.time()
        _write_pool_state(
            tmp_path,
            [
                {
                    "name": name,
                    "created_at": now - 60 * MINUTE,
                    "last_live_at": now - 60 * MINUTE,
                    "worker": name,
                    "unreachable_since": now - 60 * MINUTE,
                }
            ],
        )
        ledger = _ledger(tmp_path)
        ledger.claim(["src/api"], name, "API-9")

        with (
            patch("rite_ai.pool.shutil.which", return_value=TMUX),
            patch("rite_ai.pool.is_tmux_session_alive", return_value=False),
        ):
            result = archive(
                tmp_path,
                PoolConfig(archive_after_minutes=30),
                ledger=ledger,
                now=now,
                dry_run=True,
            )

        assert result.dry_run
        assert [a.name for a in result.archived] == [name]
        assert "would archive" in result.message
        assert len(ledger.list_claims()) == 1
        assert (
            len(json.loads((tmp_path / ".rite" / "pool.json").read_text())["slots"])
            == 1
        )
        assert read_archive(tmp_path) == []


class TestStalenessRuleIsConfigurable:
    def test_archive_window_comes_from_config(self, tmp_path: Path):
        name = slot_name(tmp_path, 0)
        now = time.time()
        _write_pool_state(
            tmp_path,
            [
                {
                    "name": name,
                    "created_at": now - 20 * MINUTE,
                    "last_live_at": now - 20 * MINUTE,
                    "worker": name,
                    "unreachable_since": now - 20 * MINUTE,
                }
            ],
        )
        with (
            patch("rite_ai.pool.shutil.which", return_value=TMUX),
            patch("rite_ai.pool.is_tmux_session_alive", return_value=False),
        ):
            patient = archive(
                tmp_path,
                PoolConfig(archive_after_minutes=60),
                ledger=_ledger(tmp_path),
                now=now,
                dry_run=True,
            )
            impatient = archive(
                tmp_path,
                PoolConfig(archive_after_minutes=10),
                ledger=_ledger(tmp_path),
                now=now,
                dry_run=True,
            )
        assert patient.archived == []
        assert [a.name for a in impatient.archived] == [name]

    def test_after_minutes_overrides_config_for_one_run(self, tmp_path: Path):
        name = slot_name(tmp_path, 0)
        now = time.time()
        _write_pool_state(
            tmp_path,
            [
                {
                    "name": name,
                    "created_at": now - 5 * MINUTE,
                    "last_live_at": now - 5 * MINUTE,
                    "worker": name,
                    "unreachable_since": now - 5 * MINUTE,
                }
            ],
        )
        with (
            patch("rite_ai.pool.shutil.which", return_value=TMUX),
            patch("rite_ai.pool.is_tmux_session_alive", return_value=False),
        ):
            result = archive(
                tmp_path,
                PoolConfig(archive_after_minutes=60),
                after_minutes=1,
                ledger=_ledger(tmp_path),
                now=now,
                dry_run=True,
            )
        assert [a.name for a in result.archived] == [name]

    @patch("rite_ai.pool.is_tmux_session_alive", return_value=True)
    def test_lease_expiry_window_comes_from_config(self, mock_alive, tmp_path: Path):
        name = slot_name(tmp_path, 0)
        now = time.time()
        _write_pool_state(
            tmp_path,
            [
                {
                    "name": name,
                    "created_at": now - 30 * MINUTE,
                    "last_live_at": now - 30 * MINUTE,
                }
            ],
        )
        generous = probe(
            tmp_path,
            PoolConfig(coordinator_standby=1, lease_expiry_minutes=60),
            now=now,
        )
        assert generous.live == [name]
        # Re-stamped by the probe above, so reset the record before re-reading.
        _write_pool_state(
            tmp_path,
            [
                {
                    "name": name,
                    "created_at": now - 30 * MINUTE,
                    "last_live_at": now - 30 * MINUTE,
                }
            ],
        )
        strict = probe(
            tmp_path, PoolConfig(coordinator_standby=1, lease_expiry_minutes=5), now=now
        )
        assert strict.stale == [name]


class TestDeadSlotRecordsSurviveUntilArchived:
    """`fill` used to drop dead slots from the state file and hand their
    names straight back to a new session. That erased the only link
    between an orphaned claim and the session that died holding it — and
    worse, re-attributed the claim to the live session that inherited the
    name."""

    @patch("rite_ai.pool.shutil.which", return_value=TMUX)
    @patch("rite_ai.pool.subprocess.run")
    def test_a_dead_slots_record_and_name_are_not_recycled(
        self, mock_run, mock_which, tmp_path: Path
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        dead = slot_name(tmp_path, 0)
        now = time.time()
        _write_pool_state(
            tmp_path,
            [{"name": dead, "created_at": now, "last_live_at": now, "worker": dead}],
        )
        ledger = _ledger(tmp_path)
        ledger.claim(["src/payments"], dead, "PAY-1")

        with patch(
            "rite_ai.pool.is_tmux_session_alive", side_effect=_alive_except(dead)
        ):
            result = fill(tmp_path, PoolConfig(coordinator_standby=1))

        assert result.ok
        # The dead slot does not count as capacity, so one is started...
        assert len(result.started) == 1
        # ...under a NEW name, not the dead one the claim still points at.
        assert dead not in result.started
        names = {
            s["name"]
            for s in json.loads((tmp_path / ".rite" / "pool.json").read_text())["slots"]
        }
        assert dead in names  # the record survives for `archive` to act on

    @patch("rite_ai.pool.shutil.which", return_value=TMUX)
    @patch("rite_ai.pool.subprocess.run")
    def test_fill_starts_the_dead_clock_so_archive_can_act(
        self, mock_run, mock_which, tmp_path: Path
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        dead = slot_name(tmp_path, 0)
        now = time.time()
        _write_pool_state(
            tmp_path, [{"name": dead, "created_at": now, "last_live_at": now}]
        )
        with patch(
            "rite_ai.pool.is_tmux_session_alive", side_effect=_alive_except(dead)
        ):
            fill(tmp_path, PoolConfig(coordinator_standby=1))

        slots = json.loads((tmp_path / ".rite" / "pool.json").read_text())["slots"]
        record = next(s for s in slots if s["name"] == dead)
        assert record["unreachable_since"] is not None


class TestStateFileCompatibility:
    def test_a_record_written_before_slots_had_an_identity_still_reads(
        self, tmp_path: Path
    ):
        name = slot_name(tmp_path, 0)
        now = time.time()
        _write_pool_state(
            tmp_path, [{"name": name, "created_at": now, "last_live_at": now}]
        )
        ledger = _ledger(tmp_path)
        ledger.claim(["src/api"], name, "API-9")  # claimed under the slot name

        with (
            patch("rite_ai.pool.shutil.which", return_value=TMUX),
            patch("rite_ai.pool.is_tmux_session_alive", return_value=False),
        ):
            result = archive(
                tmp_path,
                PoolConfig(archive_after_minutes=0),
                ledger=ledger,
                now=now,
            )

        assert [a.worker for a in result.archived] == [name]
        assert ledger.list_claims() == []

    def test_archiving_records_the_release_in_the_audit_trail(self, tmp_path: Path):
        """The point of the change, tested where it actually happens.

        An archived slot's claims went through plain `release()`, so an
        automatic release left no trace in `force-releases.jsonl` while a
        human one did — "why did my claim disappear" depended on which
        subsystem removed it. The ledger unit test proves `force_release`
        writes a record; only this proves `archive` reaches it.
        """
        dead = slot_name(tmp_path, 0)
        now = time.time()
        _write_pool_state(
            tmp_path,
            [
                {
                    "name": dead,
                    "created_at": now - 60 * MINUTE,
                    "last_live_at": now - 60 * MINUTE,
                    "worker": dead,
                    "unreachable_since": now - 45 * MINUTE,
                }
            ],
        )
        ledger = _ledger(tmp_path)
        assert ledger.claim(["src/payments"], dead, "PAY-1").ok

        with (
            patch("rite_ai.pool.shutil.which", return_value=TMUX),
            patch(
                "rite_ai.pool.is_tmux_session_alive", side_effect=_alive_except(dead)
            ),
        ):
            archive(
                tmp_path,
                PoolConfig(coordinator_standby=1, archive_after_minutes=30),
                ledger=ledger,
                now=now,
            )

        records = ledger.force_release_audit()
        assert records, "an automatic release left no audit record"
        assert records[0]["by"] == "rite pool archive"
        assert records[0]["released"][0]["worker"] == dead
        assert dead in records[0]["reason"]
