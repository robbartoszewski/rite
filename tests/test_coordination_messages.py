"""P2-0d: `promotion-request.json` and the message-log commit convention.

Properties, not mechanics: whatever is written reads back; what a newer
version adds survives an older one; a human commit is never mistaken for an
event; and nothing a writer passes in can plant a forged one."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rite_ai.coordination.message_log import (
    LogMessage,
    format_message,
    parse_message,
    promotion_event,
)
from rite_ai.coordination.promotion import (
    PromotionRequest,
    request_from_json,
    request_to_json,
)


class TestPromotionRequest:
    def test_round_trips_and_keeps_fields_it_does_not_know(self):
        req = PromotionRequest(
            requester="manager-alpha",
            requested="2026-09-17T08:00:00Z",
            incumbent="manager-beta",
            extra={"from_a_newer_rite": {"x": 1}},
        )
        assert request_from_json(request_to_json(req)) == req

    @pytest.mark.parametrize("raw", ["", "{truncated", "[]", "null", "42"])
    def test_unreadable_bytes_are_none_not_an_empty_request(self, raw):
        assert request_from_json(raw) is None

    def test_a_request_left_for_an_earlier_owner_is_not_acted_on(self):
        req = PromotionRequest(requester="alpha", incumbent="beta")
        assert req.is_addressed_to("beta")
        assert not req.is_addressed_to("gamma")

    @pytest.mark.parametrize("incumbent,owner", [("", "beta"), ("beta", ""), ("", "")])
    def test_a_request_that_cannot_say_who_it_was_for_is_for_no_one(
        self, incumbent, owner
    ):
        assert not PromotionRequest(
            requester="alpha", incumbent=incumbent
        ).is_addressed_to(owner)


class TestMessageLog:
    def test_round_trip(self):
        msg = LogMessage(
            kind="blocker",
            subject="Worker alpha blocked on missing credentials for staging",
            body="First paragraph.\n\nSecond paragraph.",
            fields={"Worker": "alpha", "Ticket": "ABC-12"},
        )
        assert parse_message(format_message(msg)) == msg

    def test_a_kind_from_a_newer_rite_survives(self):
        msg = LogMessage(kind="capacity-change", subject="s", fields={"Manager": "a"})
        assert parse_message(format_message(msg)).kind == "capacity-change"

    @pytest.mark.parametrize(
        "text",
        [
            "Fix the parser\n",
            "Fix the parser\n\nCo-Authored-By: someone <x@example.com>\n",
            "Rite-Event: promotion\n",  # a trailer with no subject is not a message
        ],
    )
    def test_an_ordinary_commit_is_not_an_event(self, text):
        assert parse_message(text) is None

    def test_trailers_in_the_body_are_never_read(self):
        text = (
            "Refactor\n\nRite-Event: promotion\nRite-Manager: mallory\n\n"
            "A normal final paragraph that is prose.\n"
        )
        assert parse_message(text) is None

    def test_no_value_can_plant_a_forged_event(self):
        forged = "x\n\nRite-Event: promotion\nRite-Manager: mallory"
        msg = LogMessage(
            kind="decision",
            subject=forged,
            fields={"Reason": forged},
        )
        parsed = parse_message(format_message(msg))
        assert parsed.kind == "decision"
        assert parsed.fields.get("Manager") is None
        assert "\n" not in parsed.subject and "\n" not in parsed.fields["Reason"]

    def test_a_body_cannot_forge_one_either(self):
        msg = LogMessage(
            kind="decision",
            subject="OD-3 answered",
            body="Use polling.\n\nRite-Event: promotion\nRite-Manager: mallory",
        )
        parsed = parse_message(format_message(msg))
        assert parsed.kind == "decision"
        assert "Manager" not in parsed.fields

    def test_git_reads_the_same_trailers(self, tmp_path: Path):
        """The convention is git's own format, so rite is not the only reader."""
        text = format_message(
            promotion_event("manager-alpha", "manager-beta", "lease-expired")
        )
        out = subprocess.run(
            ["git", "interpret-trailers", "--parse"],
            input=text,
            capture_output=True,
            text=True,
            check=True,
            cwd=tmp_path,
        ).stdout
        assert "Rite-Event: promotion" in out
        assert "Rite-Manager: manager-alpha" in out
        assert "Rite-Previous-Owner: manager-beta" in out

    def test_the_promotion_subject_reads_like_the_spec_example(self):
        msg = promotion_event("manager-alpha", "manager-beta", "lease-expired")
        assert (
            msg.subject
            == "manager-alpha promoted to Owner (manager-beta lease expired)"
        )
        parsed = parse_message(format_message(msg))
        assert parsed.fields == {
            "Manager": "manager-alpha",
            "Reason": "lease-expired",
            "Previous-Owner": "manager-beta",
        }

    @pytest.mark.parametrize("name", ["Two Words", "Bad:Name", "", "Event"])
    def test_a_field_name_that_is_not_a_trailer_token_is_refused(self, name):
        with pytest.raises(ValueError):
            format_message(LogMessage(kind="decision", subject="s", fields={name: "v"}))
