"""A force-release by path does not reach across Managers (MM3).

SPEC §5.4.8's P4 — "releasing or destroying names the Manager whose thing it
is" — could not be expressed before this: `Claim` carried a worker and no
Manager, so `rite release --force <path>` released whoever held the path,
and two Managers sharing one checkout share one ledger. A Manager tidying
what it believes are its own orphans took a sibling's live claim, and the
audit trail recorded a release nobody could attribute.

⚠ **The narrowing is on the PATH-matched case only, and that is the whole
point rather than a shortcut.** `force_release(worker=...)` names the holder,
so it is already exact — the reaper in `pool.archive` knows precisely whose
slot died, and scoping it by Manager as well would stop a Manager reaping a
dead Worker that another Manager had started. What is dangerous across
Managers is "clear this path, I do not care who has it", because with more
than one Manager in a checkout the answer to "who has it" is no longer
"someone I am responsible for".

⚠ **An unowned claim stays releasable, deliberately.** A claim made from a
human's own shell carries no Manager (`manager == ""`), and `rite release
--force` typed by a human is the case `force_release`'s docstring calls
legitimate. Refusing those would break the command for its main user to
protect it from a case that needs two Managers.
"""

from __future__ import annotations

import pytest

from rite_ai.claims.ledger import ClaimsLedger


@pytest.fixture
def ledger(tmp_path):
    return ClaimsLedger(tmp_path / "claims.json")


class TestAClaimRecordsTheManagerThatMadeIt:
    def test_a_claim_made_as_a_manager_carries_its_name(self, ledger):
        ledger.claim(["src/a.py"], "w1", manager="alpha")
        assert [c.manager for c in ledger.list_claims()] == ["alpha"]

    def test_a_claim_made_outside_a_manager_carries_none(self, ledger):
        ledger.claim(["src/a.py"], "w1")
        assert [c.manager for c in ledger.list_claims()] == [""]

    def test_a_ledger_written_before_this_release_still_reads(self, tmp_path):
        """Claims on disk from an older rite have no `manager` key at all."""
        (tmp_path / "claims.json").write_text(
            '[{"paths": ["src/a.py"], "worker": "w1", "ticket": "", "timestamp": 1.0}]'
        )
        assert [
            c.manager for c in ClaimsLedger(tmp_path / "claims.json").list_claims()
        ] == [""]


class TestAForceReleaseByPathStopsAtAnotherManager:
    """The property MM3 is filed for: two Managers hold claims on different
    paths, and A's force-release by path refuses to touch B's."""

    def test_it_leaves_another_managers_claim_alone(self, ledger):
        ledger.claim(["src/a.py"], "w-a", manager="alpha")
        ledger.claim(["src/b.py"], "w-b", manager="beta")

        released = ledger.force_release(
            ["src/b.py"], by="alpha", reason="tidying", manager="alpha"
        )

        assert released == 0
        assert {c.worker for c in ledger.list_claims()} == {"w-a", "w-b"}

    def test_it_says_whose_it_is(self, ledger):
        ledger.claim(["src/b.py"], "w-b", manager="beta")

        ledger.force_release(
            ["src/b.py"], by="alpha", reason="tidying", manager="alpha"
        )

        assert ledger.last_refused_other_managers == [("beta", "w-b", "src/b.py")]

    def test_it_still_releases_its_own(self, ledger):
        ledger.claim(["src/a.py"], "w-a", manager="alpha")
        ledger.claim(["src/b.py"], "w-b", manager="beta")

        released = ledger.force_release(
            ["src/a.py"], by="alpha", reason="mine", manager="alpha"
        )

        assert released == 1
        assert {c.worker for c in ledger.list_claims()} == {"w-b"}

    def test_it_still_releases_a_claim_no_manager_owns(self, ledger):
        ledger.claim(["src/a.py"], "w-a")

        released = ledger.force_release(
            ["src/a.py"], by="alpha", reason="orphan", manager="alpha"
        )

        assert released == 1
        assert ledger.list_claims() == []

    def test_a_human_with_no_manager_releases_across_managers(self, ledger):
        """`manager=None` is "I am not a Manager", not "match everything by
        default" — the human typing the command is the case the exact-path
        release exists for."""
        ledger.claim(["src/b.py"], "w-b", manager="beta")

        released = ledger.force_release(["src/b.py"], by="rob", reason="stale")

        assert released == 1

    def test_the_audit_trail_names_the_manager(self, ledger):
        ledger.claim(["src/a.py"], "w-a", manager="alpha")

        ledger.force_release(["src/a.py"], by="alpha", reason="mine", manager="alpha")

        [record] = ledger.force_release_audit()
        assert record["released"][0]["manager"] == "alpha"


class TestNamingTheHolderIsStillExact:
    """`worker=` is a caller that knows whose claim it is — the reaper that
    watched a slot die. It is not narrowed, and a test says so, because
    narrowing it would stop a Manager reaping a Worker another one started."""

    def test_a_worker_scoped_release_reaches_another_managers_worker(self, ledger):
        ledger.claim(["src/b.py"], "w-b", manager="beta")

        released = ledger.force_release(
            worker="w-b", by="alpha", reason="its slot died", manager="alpha"
        )

        assert released == 1


class TestTheCommandIsHeldToTheManagerItRunsAs:
    """⚠ **The ledger tests above cannot reach the rule MM3 is actually
    filed for, and this class is where it is observable.**

    "No default that matches across Managers" is applied at the CLI, not in
    the ledger: `current_manager()` returns `""` outside a session, and
    `main.py` is what turns that into `manager=None`. Drop the `or None` and
    every ledger test above still passes, while `rite release --force` typed
    by a human stops clearing anything a Manager holds — the command's main
    user, broken to protect them from a case that needs two Managers.
    Measured: that one-line change fails
    `test_a_human_outside_any_session_still_clears_the_path` and nothing else.

    The identity comes from the session's environment (`RITE_MANAGER`), so
    the only way to test the join is to set it and run the command.
    """

    @staticmethod
    def _project(tmp_path, monkeypatch):
        import subprocess

        import rite_ai.sandbox as sb
        from rite_ai.cli.init import run_init

        root = tmp_path / "proj"
        root.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        monkeypatch.chdir(root)
        monkeypatch.setattr(sb, "platform_can_sandbox", lambda: False)
        run_init(root, yes=True)
        return root

    def test_a_manager_cannot_force_release_another_managers_claim(
        self, tmp_path, monkeypatch
    ):
        from click.testing import CliRunner

        from rite_ai.cli.main import cli

        root = self._project(tmp_path, monkeypatch)
        ledger = ClaimsLedger(root / ".rite" / "claims.json")
        ledger.claim(["src/b.py"], "w-b", "T-2", manager="beta")

        monkeypatch.setenv("RITE_MANAGER", "alpha")
        result = CliRunner().invoke(
            cli,
            ["release", "--force", "src/b.py", "--by", "alpha", "--reason", "tidying"],
        )

        assert result.exit_code == 0, result.output
        assert "force-released 0 claim(s)" in result.output
        # Whose it is, in the output — "0 claim(s)" alone is what sends
        # somebody away believing the path is clear.
        assert "not yours: src/b.py" in result.output
        assert "under Manager 'beta'" in result.output
        assert ledger.list_claims() != []

    def test_a_claim_made_in_a_session_records_that_session(
        self, tmp_path, monkeypatch
    ):
        from click.testing import CliRunner

        from rite_ai.cli.main import cli

        root = self._project(tmp_path, monkeypatch)
        monkeypatch.setenv("RITE_MANAGER", "alpha")

        result = CliRunner().invoke(cli, ["claim", "src/a.py", "-w", "w-a"])

        assert result.exit_code == 0, result.output
        claims = ClaimsLedger(root / ".rite" / "claims.json").list_claims()
        assert [c.manager for c in claims] == ["alpha"]

    def test_a_human_outside_any_session_still_clears_the_path(
        self, tmp_path, monkeypatch
    ):
        """The other half of the same rule: `RITE_MANAGER` unset must not be
        read as a Manager named "", or `rite release --force` stops working
        for the person it was written for."""
        from click.testing import CliRunner

        from rite_ai.cli.main import cli

        root = self._project(tmp_path, monkeypatch)
        ledger = ClaimsLedger(root / ".rite" / "claims.json")
        ledger.claim(["src/b.py"], "w-b", "T-2", manager="beta")

        monkeypatch.delenv("RITE_MANAGER", raising=False)
        result = CliRunner().invoke(
            cli,
            ["release", "--force", "src/b.py", "--by", "rob", "--reason", "stale"],
        )

        assert result.exit_code == 0, result.output
        assert "force-released 1 claim(s)" in result.output
        assert ledger.list_claims() == []
