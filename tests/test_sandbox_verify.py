"""A sandbox is verified by running one, not by finding a file.

`is_available()` was `shutil.which("yoloai") is not None` — "a file
exists under that name" standing in for "a sandbox will contain a
worker". Present-but-broken is the gap between those two, and it is the
case that silently switched the worker cap off.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from rite_ai.sandbox import (
    SELFTEST_AGENT,
    BackendAvailability,
    CountUnavailable,
    available_backends,
    is_installed,
    verify_sandbox,
)

YOLOAI = "/usr/local/bin/yoloai"
ACTIVE = '{"sandboxes": [{"environment": {"name": "%s"}}]}'


def _runner(calls: list, *, new_rc=0, new_err="", ls_stdout=None, ls_rc=0):
    """Answer `new` / `ls` / `destroy` the way yoloAI would."""

    def run(args, **kwargs):
        calls.append(args)
        if "new" in args:
            return MagicMock(returncode=new_rc, stdout="", stderr=new_err)
        if "ls" in args:
            name = next(a for a in calls[0] if str(a).startswith("rite-selftest-"))
            body = ACTIVE % name if ls_stdout is None else ls_stdout
            return MagicMock(returncode=ls_rc, stdout=body, stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    return run


class TestTheProbeActuallyRunsASandbox:
    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_a_successful_round_trip(self, mock_which):
        calls: list = []
        with patch("rite_ai.sandbox.subprocess.run", side_effect=_runner(calls)):
            result = verify_sandbox("seatbelt")
        assert result.ok, result.detail
        assert any("new" in c for c in calls)
        assert any("destroy" in c for c in calls)

    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_it_starts_the_sandbox_rather_than_only_creating_it(self, mock_which):
        """A created-but-not-started sandbox does not appear in `ls
        --active` at all, so "count it, expect 1" would be satisfied by a
        sandbox that never ran."""
        calls: list = []
        with patch("rite_ai.sandbox.subprocess.run", side_effect=_runner(calls)):
            verify_sandbox("seatbelt")
        new_call = next(c for c in calls if "new" in c)
        assert "--no-start" not in new_call

    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_it_uses_the_no_op_agent(self, mock_which):
        """Starting a real coding agent on every `rite doctor` would spawn
        an assistant and need credentials to answer a yes/no question."""
        calls: list = []
        with patch("rite_ai.sandbox.subprocess.run", side_effect=_runner(calls)):
            verify_sandbox("seatbelt")
        new_call = next(c for c in calls if "new" in c)
        assert new_call[new_call.index("--agent") + 1] == SELFTEST_AGENT

    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_a_sandbox_that_will_not_start_is_a_failure(self, mock_which):
        calls: list = []
        runner = _runner(
            calls,
            new_rc=1,
            new_err="yoloai: connect to podman backend: podman is not installed\n"
            "Run 'yoloai new -h' for help",
        )
        with patch("rite_ai.sandbox.subprocess.run", side_effect=runner):
            result = verify_sandbox("podman")
        assert not result.ok
        assert "podman is not installed" in result.detail
        # The usage hint is not the reason.
        assert "-h' for help" not in result.detail

    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_a_sandbox_that_starts_but_is_not_counted_is_a_failure(self, mock_which):
        """Exit 0 from `new` is not proof it stayed up."""
        calls: list = []
        runner = _runner(calls, ls_stdout='{"sandboxes": []}')
        with patch("rite_ai.sandbox.subprocess.run", side_effect=runner):
            result = verify_sandbox("seatbelt")
        assert not result.ok
        assert "did not stay up" in result.detail

    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_an_unreadable_count_is_a_failure_not_a_pass(self, mock_which):
        calls: list = []
        runner = _runner(calls, ls_rc=1)
        with patch("rite_ai.sandbox.subprocess.run", side_effect=runner):
            result = verify_sandbox("seatbelt")
        assert not result.ok
        assert "could not confirm" in result.detail

    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_somebody_elses_sandbox_does_not_make_the_probe_pass(self, mock_which):
        """Counting every `rite-` sandbox would let a Worker's real one
        answer for the probe, which verifies nothing."""
        calls: list = []
        runner = _runner(
            calls, ls_stdout='{"sandboxes": [{"environment": {"name": "rite-alpha"}}]}'
        )
        with patch("rite_ai.sandbox.subprocess.run", side_effect=runner):
            result = verify_sandbox("seatbelt")
        assert not result.ok

    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_the_sandbox_is_destroyed_even_when_the_probe_fails(self, mock_which):
        """A probe that leaks a sandbox adds to max_concurrent_workers
        forever after."""
        calls: list = []
        runner = _runner(calls, ls_stdout='{"sandboxes": []}')
        with patch("rite_ai.sandbox.subprocess.run", side_effect=runner):
            verify_sandbox("seatbelt")
        assert any("destroy" in c for c in calls)

    @patch("rite_ai.sandbox.shutil.which", return_value=None)
    def test_a_missing_binary_is_reported_as_not_installed(self, mock_which):
        result = verify_sandbox("seatbelt")
        assert not result.ok
        assert result.installed is False
        assert "not installed" in result.detail

    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_each_probe_uses_a_unique_name(self, mock_which):
        names = []
        for _ in range(2):
            calls: list = []
            with patch("rite_ai.sandbox.subprocess.run", side_effect=_runner(calls)):
                verify_sandbox("seatbelt")
            new_call = next(c for c in calls if "new" in c)
            names.append(next(a for a in new_call if a.startswith("rite-selftest-")))
        assert names[0] != names[1]


class TestBackendAvailability:
    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_it_reports_each_backend_and_why(self, mock_which):
        payload = (
            '{"backends": ['
            '{"name": "seatbelt", "available": true},'
            '{"name": "podman", "available": false, "note": "not installed"}]}'
        )
        with patch(
            "rite_ai.sandbox.subprocess.run",
            return_value=MagicMock(returncode=0, stdout=payload, stderr=""),
        ):
            backends = available_backends()
        assert backends == [
            BackendAvailability("seatbelt", True, ""),
            BackendAvailability("podman", False, "not installed"),
        ]

    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_unreadable_output_is_unavailable_not_empty(self, mock_which):
        """An empty list would read as "no backends exist", which is a
        different and much more alarming claim than "could not ask"."""
        with patch(
            "rite_ai.sandbox.subprocess.run",
            return_value=MagicMock(returncode=0, stdout="not json", stderr=""),
        ):
            assert isinstance(available_backends(), CountUnavailable)

    @patch("rite_ai.sandbox.shutil.which", return_value=None)
    def test_no_binary_is_unavailable(self, mock_which):
        assert isinstance(available_backends(), CountUnavailable)


class TestIsInstalledIsHonestlyNamed:
    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_it_only_claims_the_file_exists(self, mock_which):
        assert is_installed() is True

    @patch("rite_ai.sandbox.shutil.which", return_value=None)
    def test_absent_binary(self, mock_which):
        assert is_installed() is False


class TestABinaryThatWillNotExec:
    """`installed` and `works` are different answers — the gap this check
    exists to close. Measured on a tester's machine: yoloai on PATH built
    for another architecture, `OSError: [Errno 8] Exec format error`."""

    EXEC_FORMAT = OSError(8, "Exec format error")

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    def test_it_is_a_failed_check_not_a_traceback(self, _which):
        with patch("rite_ai.sandbox.subprocess.run", side_effect=self.EXEC_FORMAT):
            check = verify_sandbox("seatbelt")
        assert not check.ok
        assert "/usr/local/bin/yoloai" in check.detail
        assert "Exec format error" in check.detail
        assert "Reinstall" in check.detail

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    def test_a_teardown_that_cannot_run_does_not_replace_the_answer(self, _which):
        """The `finally` runs with another return value in flight: an
        exception raised there replaces it, which is how a failed probe
        became a crash."""
        calls = []

        def run(args, *a, **kw):
            calls.append(args[1])
            if args[1] == "destroy":
                raise self.EXEC_FORMAT
            return MagicMock(returncode=1, stdout="", stderr="backend unavailable")

        with patch("rite_ai.sandbox.subprocess.run", side_effect=run):
            check = verify_sandbox("seatbelt")
        assert "destroy" in calls
        assert not check.ok
        assert "backend unavailable" in check.detail
