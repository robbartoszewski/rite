"""Whose refusals rite is reporting (B4b, break 4).

`_say_refusals` read **Claude Code's** transcripts for every engine. Goose
writes none, so a Goose Manager's refusals were invisible — and the empty
list that came back was indistinguishable from "nothing was refused".

⚠ **That is two defect classes in one call.** An instrument that reports the
absence of a thing it never looked for is measuring nothing (class 1), and
reading that absence as zero is class 2.

⚠ **What a refusal IS differs by engine, and that is the axis.** Claude
refuses one command and carries on, leaving a record naming it. Goose in a
headless run has no per-command refusal at all — measured 2026-09-24: under
`GOOSE_MODE=auto` nothing is refused, and under `approve` the whole session
dies on the first tool call with *"Tool approval required in non-interactive
mode ... Approve/SmartApprove modes require an interactive terminal."*

So rite does not scan for something that is not recorded. It looks for the
refusal the engine can actually produce, and says the one thing that is true.
"""

from __future__ import annotations

from rite_ai.managers.engines import CLAUDE, GOOSE
from rite_ai.managers.session import approval_blocked


class TestTheAxisIsNamedRatherThanInferred:
    def test_claude_refuses_per_command_and_goose_does_not(self):
        assert CLAUDE.per_command_refusals
        assert not GOOSE.per_command_refusals

    def test_it_is_a_separate_axis_from_where_the_mode_is_kept(self):
        """⚠ They correlate today, and using one to stand for the other is
        the conflation this codebase keeps having to undo. What a refusal
        looks like is not the same question as where the mode lives."""
        fields = GOOSE.__dataclass_fields__
        assert "per_command_refusals" in fields
        assert "permission_env" in fields


class TestClaudesReportingIsUnchanged:
    def test_a_claude_manager_still_gets_the_line_that_permits_it(
        self, tmp_path, monkeypatch
    ):
        import rite_ai.managers.supervise as sup

        monkeypatch.setattr(sup, "refused_commands", lambda *a, **k: ["curl http://x"])
        said: list[str] = []
        sup._say_refusals(tmp_path, 0.0, said.append, "claude", "", "")
        assert '"Bash(curl:*)"' in said[0]

    def test_the_default_engine_is_still_claude(self, tmp_path, monkeypatch):
        """`_say_refusals` is called with an engine now; the empty default
        must not quietly turn Claude's reporting off."""
        import rite_ai.managers.supervise as sup

        monkeypatch.setattr(sup, "refused_commands", lambda *a, **k: ["curl http://x"])
        said: list[str] = []
        sup._say_refusals(tmp_path, 0.0, said.append)
        assert said and "curl" in said[0]


class TestAWholeSessionEngineIsNotScannedForSomethingItNeverRecords:
    def test_claudes_transcript_scan_is_not_run_for_goose(self, tmp_path, monkeypatch):
        """⚠ The break itself. Scanning here cannot find anything, and the
        nothing it finds reads as 'nothing was refused'."""
        import rite_ai.managers.supervise as sup

        scanned: list = []

        def spy(*a, **k):
            scanned.append(a)
            return ["curl http://x"]

        monkeypatch.setattr(sup, "refused_commands", spy)
        said: list[str] = []
        sup._say_refusals(tmp_path, 0.0, said.append, "local:tier", "goose", "")
        assert scanned == [], "Claude's transcript scan ran for a Goose Manager"
        assert said == []

    def test_the_whole_session_refusal_IS_reported_with_its_remedy(
        self, tmp_path, monkeypatch
    ):
        import rite_ai.managers.supervise as sup

        monkeypatch.setattr(sup, "approval_blocked", lambda pane: True)
        said: list[str] = []
        sup._say_refusals(tmp_path, 0.0, said.append, "local:tier", "goose", "a-pane")
        assert len(said) == 1
        assert "whole session" in said[0]
        assert "non-interactive" in said[0]
        assert "GOOSE_MODE" in said[0]

    def test_a_healthy_goose_cycle_says_nothing(self, tmp_path, monkeypatch):
        import rite_ai.managers.supervise as sup

        monkeypatch.setattr(sup, "approval_blocked", lambda pane: False)
        said: list[str] = []
        sup._say_refusals(tmp_path, 0.0, said.append, "local:tier", "goose", "a-pane")
        assert said == []


class TestTheDetectorMatchesToDETECTAndNeverRelaysThePane:
    """⚠ Same rule as `_authentication_looks_broken` beside it: a pane can
    carry secrets, so echoing it back is a leak waiting for the right launch
    line."""

    def test_an_unknown_pane_is_not_an_approval_failure(self):
        """Absence is not an exception — and it is not evidence either."""
        assert approval_blocked("no-such-pane-anywhere") is False

    def test_no_pane_name_is_not_an_approval_failure(self):
        assert approval_blocked("") is False

    def test_the_message_does_not_quote_the_pane(self, tmp_path, monkeypatch):
        import rite_ai.managers.supervise as sup

        monkeypatch.setattr(sup, "approval_blocked", lambda pane: True)
        said: list[str] = []
        sup._say_refusals(tmp_path, 0.0, said.append, "local:tier", "goose", "a-pane")
        assert "a-pane" not in said[0]
