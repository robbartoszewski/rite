"""Ticket `claude-total`'s agreed verify. Fails until that ticket is delivered."""

from decimal import Decimal

import tally


def test_total_adds_amounts():
    assert tally.total(["1.10", "2.20", " 3 "]) == Decimal("6.30")


def test_total_of_nothing_is_zero():
    assert tally.total([]) == Decimal("0")
