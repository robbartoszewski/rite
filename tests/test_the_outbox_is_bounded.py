"""The outbox is bounded by age AND size, and never loses an unread message (C23).

Readers stopped deleting when the outbox gained a second reader (A1), so
nothing did. Robert's rule: time-based, and capped by store size with the
OLDEST PROCESSED items going first. An unread message is never removed by
either bound — that would be silent loss, the failure the mailbox exists to
prevent — and a box full of unread messages is reported, not resolved.
"""

from __future__ import annotations

import os
import time

from rite_ai.managers.mailbox import (
    OUTBOX,
    full_warning,
    mark_read,
    prune,
    read,
    send,
    unread,
)

DAY = 86400.0


def _texts(root):
    return [m.text for m in read(root, "lead", OUTBOX)]


def _send_all(root, names, size=100):
    for name in names:
        send(root, "lead", OUTBOX, name + " " + "x" * size)
        time.sleep(0.002)  # distinct millisecond prefixes, so order is send order


def _read_all(root, reader="connect"):
    mark_read(root, "lead", OUTBOX, reader, unread(root, "lead", OUTBOX, reader))


def _age(root, name, days):
    for m in read(root, "lead", OUTBOX):
        if m.text.split()[0] == name:
            t = time.time() - days * DAY
            os.utime(m.path, (t, t))


def _names(root):
    return [t.split()[0] for t in _texts(root)]


def test_the_size_cap_removes_the_oldest_processed_first(tmp_path):
    _send_all(tmp_path, ["P0", "P1", "P2", "P3"])
    _read_all(tmp_path)
    _send_all(tmp_path, ["U4", "U5"])
    # Budgeted from the files themselves: sizes differ by a byte or two with
    # the timestamp's repr, so "4 × the first one" can sit one byte short.
    newest_four = sum(
        os.path.getsize(m.path) for m in read(tmp_path, "lead", OUTBOX)[2:]
    )
    got = prune(tmp_path, "lead", max_bytes=newest_four)
    assert _names(tmp_path) == ["P2", "P3", "U4", "U5"]
    assert len(got.removed) == 2 and not got.full_of_unread


def test_an_unread_message_survives_a_size_cap_eviction(tmp_path):
    """The load-bearing half. At the cap with only unread messages left, the
    box stays over its cap and SAYS so — nothing unread is deleted."""
    _send_all(tmp_path, ["P0"])
    _read_all(tmp_path)
    _send_all(tmp_path, ["U1", "U2", "U3"])
    got = prune(tmp_path, "lead", max_bytes=1)
    assert _names(tmp_path) == ["U1", "U2", "U3"]
    assert got.full_of_unread
    assert "will not delete an unread message" in full_warning(got, "lead")


def test_the_age_bound_removes_processed_and_keeps_unread(tmp_path):
    _send_all(tmp_path, ["P0", "P1"])
    _read_all(tmp_path)
    _send_all(tmp_path, ["U2"])
    _age(tmp_path, "P0", 31)
    _age(tmp_path, "U2", 31)
    got = prune(tmp_path, "lead")
    assert _names(tmp_path) == ["P1", "U2"]
    assert got.unread_over_age == 1


def test_read_by_one_reader_is_not_processed_while_another_lags(tmp_path):
    """Processed means EVERY reader with a cursor has passed it. A Slack relay
    that has not caught up must not lose what the local chat already saw."""
    _send_all(tmp_path, ["A0"])
    _read_all(tmp_path, "slack")
    _send_all(tmp_path, ["B1", "B2"])
    _read_all(tmp_path, "connect")  # connect has seen all three; slack only A0
    for name in ("A0", "B1", "B2"):
        _age(tmp_path, name, 31)
    prune(tmp_path, "lead", max_bytes=1)
    assert _names(tmp_path) == ["B1", "B2"]


def test_a_box_nobody_has_read_loses_nothing(tmp_path):
    _send_all(tmp_path, ["U0", "U1"])
    for name in ("U0", "U1"):
        _age(tmp_path, name, 365)
    assert not prune(tmp_path, "lead", max_bytes=1).removed
    assert _names(tmp_path) == ["U0", "U1"]


def test_reading_is_what_triggers_retention(tmp_path):
    """Wired, not merely available: advancing a cursor runs the pass."""
    _send_all(tmp_path, ["P0"])
    _age(tmp_path, "P0", 31)
    _read_all(tmp_path)
    assert _names(tmp_path) == []
