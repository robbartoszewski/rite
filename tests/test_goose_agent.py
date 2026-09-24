"""Goose behind the harness's Agent protocol (B4a).

⚠ **THE PROPERTY THAT MATTERS IS A NEGATIVE ONE:** the adapter must not be
able to accidentally trust `goose run`'s exit status. Measured 2026-09-24
with the provider pointed at a dead port:

    exit=0   secs=109   file created: NO
    "Network error: Could not connect to localhost:1"

Zero, for 109 seconds of retries and no work. An adapter reading that code
reports a clean success over nothing.
"""

from __future__ import annotations

import subprocess

from rite_ai.local.decomposition import Subtask
from rite_ai.local.goose_agent import GooseAgent, session_name
from rite_ai.local.harness import Context


class _Probe:
    def __init__(self, problems=()):
        self.problems = list(problems)


def _context(intent="add a function", scope=("a.py",)):
    return Context(
        subtask=Subtask(id="s1", intent=intent, scope=scope, verify="true"),
        spec_slice="the spec says a.py must have f()",
        ticket="RT-14",
    )


def _completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _agent(**kw):
    kw.setdefault("probe", lambda: _Probe())
    return GooseAgent(model="qwen3:8b", endpoint="http://localhost:11434/v1", **kw)


class TestItNeverTrustsTheExitCode:
    def test_a_zero_exit_over_a_network_error_is_not_success(self):
        """⚠ The measured case, verbatim. goose returned 0."""
        agent = _agent(
            launch=lambda *a: _completed(
                stdout="goose is ready\nNetwork error: Could not connect to "
                "localhost:1 — check your network connection and try again.",
                returncode=0,
            )
        )
        report = agent.run(_context(), "/tmp")
        assert not report.claimed_success, (
            "a clean exit code over a network error was taken as success — "
            "which is the 109-second silent failure this adapter exists to "
            "stop"
        )
        assert "infrastructure" in report.summary

    def test_a_nonzero_exit_is_not_what_makes_it_a_failure_either(self):
        """The mirror. The adapter must not READ the code at all — not
        trust it when 0 and not trust it when 1. A tool that exits non-zero
        having done the work would otherwise be reported as failed, and the
        verify is what decides (R7)."""
        agent = _agent(launch=lambda *a: _completed(stdout="done", returncode=1))
        report = agent.run(_context(), "/tmp")
        assert report.claimed_success, (
            "the exit code decided the verdict; goose's exit status is not a "
            "signal this adapter is allowed to use"
        )

    def test_the_word_returncode_appears_nowhere_in_the_adapter(self):
        """⚠ Structural, not behavioural. The two tests above can be
        satisfied by code that reads the exit status and happens to agree;
        this one makes the property unrepresentable."""
        from pathlib import Path

        import rite_ai.local.goose_agent as module

        source = Path(module.__file__).read_text()
        code = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )
        body = code.split('"""', 2)[-1]
        assert "returncode" not in body, (
            "the adapter reads returncode somewhere outside a comment"
        )


class TestItChecksBeforeItSpends:
    def test_an_unreachable_endpoint_is_refused_without_running_goose(self):
        """109 seconds of retries becomes an immediate named refusal, using
        the probe that already exists rather than another tool's prose."""
        launched = []
        agent = _agent(
            probe=lambda: _Probe(
                ["manager local: its engine endpoint is not answering"]
            ),
            launch=lambda *a: launched.append(a) or _completed(),
        )
        report = agent.run(_context(), "/tmp")
        assert not launched, "goose was started against an endpoint known to be down"
        assert not report.claimed_success
        assert "not answering" in report.summary

    def test_a_too_small_context_window_is_also_a_refusal(self):
        """B7's warning, reaching the place that would waste a night on it."""
        agent = _agent(
            probe=lambda: _Probe(
                [
                    "manager local: its model is being served with a 4096-token context window"
                ]
            ),
            launch=lambda *a: _completed(),
        )
        report = agent.run(_context(), "/tmp")
        assert not report.claimed_success
        assert "4096" in report.summary


class TestTheHandleIsOurs:
    def test_rite_chooses_the_session_name_and_stores_nothing(self):
        """Goose's direction of control, not Claude's — the contract's first
        axis. Deterministic from the ticket and subtask, so there is nothing
        to discover afterwards and nothing to persist."""
        assert session_name("RT-14", "s1") == "rite-rt-14-s1"
        assert session_name("RT-14", "s1") == session_name("RT-14", "s1")

    def test_the_name_survives_characters_a_path_would_not(self):
        assert "/" not in session_name("a/b", "c d")
        assert " " not in session_name("a/b", "c d")

    def test_the_name_is_passed_to_goose(self):
        seen = {}
        agent = _agent(
            launch=lambda argv, ws, env: (
                seen.update(argv=argv, env=env) or _completed(stdout="ok")
            )
        )
        agent.run(_context(), "/tmp")
        assert "-n" in seen["argv"]
        assert seen["argv"][seen["argv"].index("-n") + 1] == "rite-rt-14-s1"
        assert "-p" not in seen["argv"] and "--resume" not in seen["argv"]


class TestPermissionGoesToTheEnvironment:
    def test_the_mode_is_an_env_var_not_a_flag(self):
        """The contract's second axis. Goose takes GOOSE_MODE; writing a flag
        would be Claude's vocabulary on a tool that would reject it."""
        seen = {}
        agent = _agent(
            launch=lambda argv, ws, env: (
                seen.update(argv=argv, env=env) or _completed(stdout="ok")
            )
        )
        agent.run(_context(), "/tmp")
        assert seen["env"]["GOOSE_MODE"] == "auto"
        assert not any(a.startswith("--dangerously") for a in seen["argv"])


class TestTheInstructionIsAFileOutsideTheWorkspace:
    def test_the_instruction_is_passed_as_a_file(self, tmp_path):
        seen = {}
        agent = _agent(
            launch=lambda argv, ws, env: (
                seen.update(argv=argv) or _completed(stdout="ok")
            )
        )
        agent.run(_context(), str(tmp_path))
        assert "-i" in seen["argv"]
        written = seen["argv"][seen["argv"].index("-i") + 1]
        assert not written.startswith(str(tmp_path)), (
            "the instruction file was written INTO the workspace, where the "
            "agent sees it as part of the work and the committer inspects it"
        )

    def test_the_instruction_carries_the_slice_and_the_scope(self, tmp_path):
        captured = {}

        def launch(argv, ws, env):
            path = argv[argv.index("-i") + 1]
            captured["text"] = open(path).read()
            return _completed(stdout="ok")

        _agent(launch=launch).run(_context(), str(tmp_path))
        assert "the spec says a.py must have f()" in captured["text"]
        assert "a.py" in captured["text"]
        assert "RT-14" in captured["text"]

    def test_the_instruction_file_is_removed_afterwards(self, tmp_path):
        paths = []
        agent = _agent(
            launch=lambda argv, ws, env: (
                paths.append(argv[argv.index("-i") + 1]) or _completed(stdout="ok")
            )
        )
        agent.run(_context(), str(tmp_path))
        import os

        assert not os.path.exists(paths[0])
