"""Every delivery outcome reaches the Manager that asked, fix included.

The Manager's instructions promise it hears every outcome in its next
instruction (`publishing.requests.instructions`). A refusal that reaches only
the terminal leaves a Manager waiting on work that went nowhere (DF13's
shape). So each test here goes the whole way: a request file as the Manager
writes it, `requests.honour` as the supervisor calls it, then the inbox taken
and composed into an instruction exactly as `supervise` does (`take` then
`delivery_note`), and the check is on THAT text.

**No length limit is assumed.** Nothing between `tell_manager` and the
instruction cuts a note (read, 2026-09-29; the 200-character cut is
`broker.honour`'s, on the Worker-start path). The property tested is that
the fix arrives whole, and `test_control_*` proves the check would see a cut
if one were ever added to this path.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from rite_ai.managers.mailbox import INBOX, delivery_note
from rite_ai.managers.mailbox import take as take_mail
from rite_ai.publishing import requests
from rite_ai.publishing.deliver import Delivered, Outcome, Refused
from tests.test_rite_delivers_a_finished_task import TICKET, Project

MANAGER = "lead"
LONG_TICKET = "T-" + "x" * 62  # the longest ticket id a request may carry


def _ask(root: Path, body: dict | str) -> None:
    where = requests.requests_dir(root, MANAGER)
    where.mkdir(parents=True, exist_ok=True)
    text = body if isinstance(body, str) else json.dumps(body)
    (where / "1.json").write_text(text)


def _next_instruction(root: Path) -> str:
    """What the Manager's next session is given, composed as `supervise`
    composes it."""
    return delivery_note(take_mail(root, MANAGER, INBOX))


def _honour(root: Path) -> list[str]:
    said: list[str] = []
    requests.honour(root, MANAGER, said.append)
    return said


def _assert_told(root: Path, *parts: str) -> None:
    told = _next_instruction(root)
    for part in parts:
        assert part in told, f"{part!r} did not reach the Manager:\n{told}"


# --- the real path ------------------------------------------------------------------


def test_a_real_delivery_is_told_to_the_manager(tmp_path):
    p = Project(tmp_path, "commit")
    p.start()
    p.work()
    _ask(p.root, {"worker": "alpha", "ticket": TICKET})
    from rite_ai.sandbox import SandboxResult, SandboxStatus

    with (
        patch(
            "rite_ai.sandbox.worker_sandbox_status",
            return_value=SandboxStatus("stopped"),
        ),
        patch(
            "rite_ai.sandbox.stop_worker", return_value=SandboxResult(True, "stopped")
        ),
        patch("rite_ai.sandbox.existing_sandbox_name", return_value="rite-x-alpha"),
        patch("rite_ai.sandbox._sandbox_copy", return_value=p.copy),
        patch(
            "rite_ai.sandbox.destroy_worker", return_value=SandboxResult(True, "gone")
        ),
    ):
        _honour(p.root)
    assert p.branch(TICKET) is not None
    _assert_told(
        p.root,
        f"Delivered svc/{TICKET}: committed locally on {TICKET} in svc/",
        "alpha can start its next ticket",
    )
    assert not list(requests.requests_dir(p.root, MANAGER).glob("*.json"))


def test_a_mid_run_change_reaches_the_manager_with_its_command(tmp_path):
    p = Project(tmp_path, "pull_request")
    p.start()
    p.work()
    p.set_publish("commit")
    _ask(p.root, {"worker": "alpha", "ticket": TICKET})
    from rite_ai.sandbox import SandboxResult, SandboxStatus

    with (
        patch(
            "rite_ai.sandbox.worker_sandbox_status",
            return_value=SandboxStatus("stopped"),
        ),
        patch(
            "rite_ai.sandbox.stop_worker", return_value=SandboxResult(True, "stopped")
        ),
        patch("rite_ai.sandbox.existing_sandbox_name", return_value="rite-x-alpha"),
        patch("rite_ai.sandbox._sandbox_copy", return_value=p.copy),
    ):
        _honour(p.root)
    _assert_told(
        p.root,
        "pull_request→commit",
        "Ask the User to run: rite deliver alpha",
    )


# --- every refusal kind -------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "fix"),
    [
        ("not json", "the request is not JSON"),
        ({"worker": "alpha", "ticket": TICKET, "strategy": "push"}, "'strategy'"),
        ({"worker": "../x", "ticket": TICKET}, "Write the request again"),
        ({"worker": "alpha", "ticket": "y" * 65}, "1 to 64 characters"),
    ],
)
def test_a_malformed_request_is_refused_to_the_manager(tmp_path, body, fix):
    root = tmp_path
    with patch(
        "rite_ai.publishing.deliver.deliver",
        side_effect=AssertionError("a malformed request reached delivery"),
    ):
        _ask(root, body)
        _honour(root)
    _assert_told(root, "NOT delivered", fix)


def test_a_refused_delivery_reaches_the_manager(tmp_path):
    _ask(tmp_path, {"worker": "alpha", "ticket": TICKET})
    with patch(
        "rite_ai.publishing.deliver.deliver",
        return_value=Refused("alpha has no sandbox, so no work waits in one"),
    ):
        said = _honour(tmp_path)
    _assert_told(tmp_path, f"NOT delivered alpha/{TICKET}", "no work waits in one")
    assert any("no work waits in one" in s for s in said)  # and the terminal


def _long_outcome() -> Outcome:
    """§3.1's example at worst case: the ticket named twice, 64 characters
    each."""
    return Outcome(
        "svc",
        LONG_TICKET,
        False,
        "publish changed pull_request→commit since the Worker started; "
        f"committed locally on {LONG_TICKET} in svc/ only",
        "Ask the User to run: rite deliver alpha",
    )


def _deliver_long(root: Path) -> None:
    _ask(root, {"worker": "alpha", "ticket": LONG_TICKET})
    with patch(
        "rite_ai.publishing.deliver.deliver",
        return_value=Delivered([_long_outcome()], "sandbox kept"),
    ):
        _honour(root)


def test_a_280_character_outcome_reaches_the_manager_whole(tmp_path):
    note = _long_outcome().note()
    assert len(note) >= 280, len(note)  # longer than any cut this could meet
    _deliver_long(tmp_path)
    _assert_told(tmp_path, note)


def test_control_a_cut_at_200_on_this_path_would_be_seen(tmp_path):
    """The control. The same check, with the delivery path patched to keep
    200 characters (`broker.honour`'s cut), MUST fail: otherwise the test
    above would pass whether or not the fix arrives."""
    from rite_ai.managers import telling

    real = telling.note

    def cut(about, text, **kw):
        return real(about, text[:200], **kw)

    with patch.object(telling, "note", side_effect=cut):
        _deliver_long(tmp_path)
    with pytest.raises(AssertionError, match="did not reach the Manager"):
        _assert_told(tmp_path, _long_outcome().note())


# --- the promise, and where it is kept ----------------------------------------------


def test_the_manager_is_promised_every_outcome_and_given_the_directory(tmp_path):
    from rite_ai.managers.prompt import for_manager

    text = for_manager(MANAGER, root=tmp_path)
    assert "rite tells you every outcome" in text
    assert str(requests.requests_dir(tmp_path, MANAGER)) in text
    assert "never merge a pull request" in text


def test_the_directory_is_inside_the_one_its_boundary_lets_it_write(tmp_path):
    from rite_ai.managers import manager_dir

    assert requests.requests_dir(tmp_path, MANAGER).parent == manager_dir(
        tmp_path, MANAGER
    )


def test_deliveries_are_honoured_before_worker_requests():
    """Structural, because the order is the property: "deliver alpha, then
    start alpha on its next ticket" in one cycle needs the delivery to have
    removed alpha's sandbox first."""
    import rite_ai.managers.supervise as supervise

    source = Path(supervise.__file__).read_text()
    assert source.index("honour_deliveries(root, manager, say)") < source.index(
        "_honour_worker_requests(root, manager, broker, say)\n            if"
    )


def test_asking_for_a_delivery_counts_as_progress(tmp_path):
    from rite_ai.managers.progress import footprint

    before = footprint(tmp_path, MANAGER)
    _ask(tmp_path, {"worker": "alpha", "ticket": TICKET})
    assert "deliveries" in footprint(tmp_path, MANAGER).differs_from(before)
