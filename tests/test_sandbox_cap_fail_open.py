"""Regression tests for the sandbox worker-cap fail-open.

`count_active_sandboxes()` returned 0 whenever `yoloai ls` could not be
read — a broken daemon, a JSON format it did not recognise, a non-zero
exit. `start_worker` compared that 0 against
`sandbox.max_concurrent_workers` and let every start through, reporting
success each time. The cap silently stopped capping while the operator
still believed they had one.

It needs yoloai present-and-broken to trigger, which is why it never
showed up in testing — and why two tests in test_sandbox.py were passing
*because* of it: they mocked `subprocess.run` to answer every call with
blank output, so the `ls` call looked like "zero sandboxes running".

Each test below reproduces the original symptom.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.config.models import SandboxConfig
from rite_ai.sandbox import (
    CountUnavailable,
    count_active_sandboxes,
    start_worker,
    worker_sandbox_status,
)

YOLOAI = "/usr/local/bin/yoloai"

# The ways `yoloai ls` can fail to answer, each of which used to be
# indistinguishable from "nothing is running".
BROKEN_LS = {
    "non-zero exit": MagicMock(
        returncode=1, stdout="", stderr="could not connect to yoloai daemon"
    ),
    "empty output": MagicMock(returncode=0, stdout="", stderr=""),
    "not json": MagicMock(returncode=0, stdout="NAME    STATUS\nrite-a  up\n"),
    "json but not an object": MagicMock(returncode=0, stdout="[]"),
}


class TestCountRefusesToInventAZero:
    @pytest.mark.parametrize("label", sorted(BROKEN_LS))
    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_unreadable_ls_is_unavailable_not_zero(self, mock_which, label: str):
        with patch("rite_ai.sandbox.subprocess.run", return_value=BROKEN_LS[label]):
            result = count_active_sandboxes()

        assert isinstance(result, CountUnavailable), (
            f"{label}: reported {result!r} — a number the cap check will act on"
        )
        assert result.reason, "no reason given for a count that could not be taken"

    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_a_genuine_zero_is_still_zero(self, mock_which):
        """The fix must not turn an empty machine into an error."""
        with patch(
            "rite_ai.sandbox.subprocess.run",
            return_value=MagicMock(returncode=0, stdout='{"sandboxes": []}'),
        ):
            assert count_active_sandboxes() == 0


class TestStartRefusesWhenTheCapCannotBeEnforced:
    """The symptom in full: a cap of 1, a broken `ls`, and three sandboxes
    starting in a row with every one reporting success."""

    @pytest.mark.parametrize("label", sorted(BROKEN_LS))
    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_start_refuses_and_never_shells_out(
        self, mock_which, label: str, tmp_path: Path
    ):
        (tmp_path / "workers" / "alpha").mkdir(parents=True)
        calls = []

        def run(args, *a, **kw):
            calls.append(args)
            if "ls" in args:
                return BROKEN_LS[label]
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("rite_ai.sandbox.subprocess.run", side_effect=run):
            result = start_worker(
                tmp_path, "alpha", SandboxConfig(max_concurrent_workers=1)
            )

        assert not result.ok, f"{label}: started a sandbox with the cap unenforceable"
        assert not any("new" in args for args in calls), (
            f"{label}: shelled out to `yoloai new` anyway"
        )

    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_refusal_names_the_cap_and_the_underlying_failure(
        self, mock_which, tmp_path: Path
    ):
        (tmp_path / "workers" / "alpha").mkdir(parents=True)
        with patch(
            "rite_ai.sandbox.subprocess.run", return_value=BROKEN_LS["non-zero exit"]
        ):
            result = start_worker(
                tmp_path, "alpha", SandboxConfig(max_concurrent_workers=3)
            )

        assert not result.ok
        assert "max_concurrent_workers" in result.message
        assert "3" in result.message
        assert "yoloai ls" in result.message, (
            "the operator is told the cap failed but not what to fix"
        )
        assert "yoloai daemon" in result.message  # the real stderr, carried through

    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_the_original_symptom_three_starts_under_a_cap_of_one(
        self, mock_which, tmp_path: Path
    ):
        for name in ("alpha", "beta", "gamma"):
            (tmp_path / "workers" / name).mkdir(parents=True)

        def run(args, *a, **kw):
            if "ls" in args:
                return BROKEN_LS["non-zero exit"]
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("rite_ai.sandbox.subprocess.run", side_effect=run):
            results = [
                start_worker(tmp_path, name, SandboxConfig(max_concurrent_workers=1))
                for name in ("alpha", "beta", "gamma")
            ]

        started = [r for r in results if r.ok]
        assert started == [], (
            f"{len(started)} sandboxes started under a cap of 1 with no way to "
            "count what was already running"
        )

    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_a_readable_count_still_starts_normally(self, mock_which, tmp_path: Path):
        """Refusing on an unreadable count must not refuse on a readable
        one — the cap still has to permit what it always permitted."""
        (tmp_path / "workers" / "alpha").mkdir(parents=True)
        running = json.dumps({"sandboxes": [{"environment": {"name": "rite-beta"}}]})

        def run(args, *a, **kw):
            if "ls" in args:
                return MagicMock(returncode=0, stdout=running, stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("rite_ai.sandbox.subprocess.run", side_effect=run):
            result = start_worker(
                tmp_path, "alpha", SandboxConfig(max_concurrent_workers=5)
            )

        assert result.ok, result.message

    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_the_cap_itself_still_refuses_over_the_limit(
        self, mock_which, tmp_path: Path
    ):
        (tmp_path / "workers" / "alpha").mkdir(parents=True)
        # THIS project's sandboxes, named the way §8.10 names them. They used
        # to be `rite-w0…` — legacy names with no project in them — which
        # counted only because the cap counted every `rite-` sandbox on the
        # machine. The cap is per Manager (SPEC §2.5.9), so the fixture now
        # says which project these belong to, as a real one would.
        from rite_ai.sandbox import sandbox_name

        running = json.dumps(
            {
                "sandboxes": [
                    {"environment": {"name": sandbox_name(f"w{i}", tmp_path)}}
                    for i in range(5)
                ]
            }
        )

        def run(args, *a, **kw):
            if "ls" in args:
                return MagicMock(returncode=0, stdout=running, stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("rite_ai.sandbox.subprocess.run", side_effect=run):
            result = start_worker(
                tmp_path, "alpha", SandboxConfig(max_concurrent_workers=5)
            )

        assert not result.ok
        assert "refused, not clamped" in result.message


class TestStatusDistinguishesAbsentFromUncheckable:
    """`worker_sandbox_status` collapsed the same two facts: a `yoloai ls`
    that failed reported `not found`, exactly as it would for a worker
    that genuinely has no sandbox."""

    @pytest.mark.parametrize("label", sorted(BROKEN_LS))
    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_unreadable_ls_is_not_reported_as_not_found(self, mock_which, label: str):
        with patch("rite_ai.sandbox.subprocess.run", return_value=BROKEN_LS[label]):
            status = worker_sandbox_status("alpha")

        assert status.value != "not found", (
            f"{label}: a broken yoloai looked exactly like an absent sandbox"
        )
        assert not status.known

    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_cli_exits_non_zero_when_the_status_is_unknown(self, mock_which):
        with patch(
            "rite_ai.sandbox.subprocess.run", return_value=BROKEN_LS["non-zero exit"]
        ):
            result = CliRunner().invoke(cli, ["sandbox", "status", "alpha"])

        assert result.exit_code == 1, (
            "an undeterminable status was reported as a successful answer"
        )
        assert "unknown" in result.output

    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_cli_exits_zero_for_a_genuine_absence(self, mock_which):
        with patch(
            "rite_ai.sandbox.subprocess.run",
            return_value=MagicMock(returncode=0, stdout='{"sandboxes": []}'),
        ):
            result = CliRunner().invoke(cli, ["sandbox", "status", "alpha"])

        assert result.exit_code == 0
        assert "not found" in result.output


class TestSandboxEnabledDocstringDescribesWhatItGoverns:
    """`sandbox.enabled` does not gate these commands — `rite sandbox
    start` works regardless. The group's help said "Optional
    (`sandbox.enabled` in config.yaml) — a single-project setup that
    doesn't opt in needs none of this", which reads as though it does."""

    def test_help_does_not_imply_the_key_gates_these_commands(self):
        help_text = cli.commands["sandbox"].help or ""
        assert "sandbox.enabled" in help_text
        assert "needs none of this" not in help_text

    def test_help_names_what_the_key_actually_governs(self):
        help_text = cli.commands["sandbox"].help or ""
        assert "rite doctor" in help_text  # sandbox-verification severity

    def test_help_does_not_claim_the_key_scopes_credentials(self):
        """It governed `rite add worker`'s per-Worker token provisioning
        until that was decoupled — so turning sandboxing on changed the
        credential model too."""
        help_text = cli.commands["sandbox"].help or ""
        assert "--scoped-token" in help_text
        assert "provisions a\n    scoped sandbox token" not in help_text


class TestTheCapCountsThisProjectsSandboxes:
    """Measured: six `rite-` sandboxes consumed a cap of five and not one
    belonged to the project that hit it — four leftover test probes and a
    different project's live Worker. The run reported "at capacity" with
    nothing of its own running, and the workaround was to raise the number.

    `sandbox.max_concurrent_workers` is a per-project key and SPEC §2.5.9
    caps "concurrent Workers per Manager"; D-47 puts cross-project totals in
    the hub. Counting machine-wide was the wrong denominator.
    """

    def _ls(self, *names):
        return json.dumps({"sandboxes": [{"environment": {"name": n}} for n in names]})

    def _count(self, tmp_path, payload, workers=None):
        from rite_ai.sandbox import count_active_sandboxes

        def run(args, *a, **kw):
            return MagicMock(returncode=0, stdout=payload, stderr="")

        with (
            patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI),
            patch("rite_ai.sandbox.subprocess.run", side_effect=run),
        ):
            return count_active_sandboxes(tmp_path, workers)

    def test_another_projects_sandboxes_do_not_fill_this_cap(self, tmp_path):
        from rite_ai.sandbox import sandbox_name

        other = tmp_path / "elsewhere"
        other.mkdir()
        payload = self._ls(
            sandbox_name("w1", tmp_path),
            sandbox_name("w1", other),
            "rite-kct-9303d6",  # a real leftover, from a third project
        )

        assert self._count(tmp_path, payload) == 1

    def test_a_rename_does_not_orphan_this_projects_own_sandboxes(self, tmp_path):
        """`project_slug`'s readable half comes from `brief.yaml` and changes
        when somebody renames the project; the digest is over the resolved
        path and does not. Matching the whole slug would stop counting this
        project's own Workers after a rename — the under-count failure, which
        is worse than the over-count it replaces."""
        from rite_ai.label import project_digest

        digest = project_digest(tmp_path)
        payload = self._ls(f"rite-old-name-{digest}-w1", f"rite-new-name-{digest}-w2")

        assert self._count(tmp_path, payload) == 2

    def test_a_legacy_name_counts_when_it_is_one_of_our_workers(self, tmp_path):
        """Pre-§8.10 `rite-<worker>` carries no project, so the roster is the
        only evidence. Checked against configured Workers rather than any
        string, or one project's `rite-w1` would count for another's."""
        payload = self._ls("rite-w1", "rite-somebody-elses")

        assert self._count(tmp_path, payload, workers=["w1"]) == 1

    def test_the_agent_field_is_not_consulted(self, tmp_path):
        """`agent: idle` reads the same for a sandbox nobody will return to
        and one whose agent is between turns — measured on a live Worker with
        unapplied changes whose Owner considered it busy. Excluding on it
        would let the cap be exceeded."""
        from rite_ai.sandbox import sandbox_name

        payload = json.dumps(
            {
                "sandboxes": [
                    {
                        "environment": {"name": sandbox_name("w1", tmp_path)},
                        "agent": "idle",
                    }
                ]
            }
        )

        assert self._count(tmp_path, payload) == 1

    def test_a_non_dict_entry_does_not_crash_the_cap(self, tmp_path):
        """`entry.get(...)` was called straight on each list element, so one
        bare string from yoloAI raised AttributeError out of the worker cap."""
        payload = json.dumps({"sandboxes": ["a bare string", None, 7]})

        assert self._count(tmp_path, payload) == 0

    def test_without_a_root_it_counts_the_machine_as_it_always_did(self, tmp_path):
        from rite_ai.sandbox import count_active_sandboxes

        payload = self._ls("rite-a", "rite-b", "unrelated")

        def run(args, *a, **kw):
            return MagicMock(returncode=0, stdout=payload, stderr="")

        with (
            patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI),
            patch("rite_ai.sandbox.subprocess.run", side_effect=run),
        ):
            assert count_active_sandboxes() == 2
