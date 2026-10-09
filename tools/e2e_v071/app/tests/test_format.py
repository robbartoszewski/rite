"""Ticket `claude-format`'s agreed verify. Fails until that ticket is delivered."""

from decimal import Decimal

import tally


def test_formats_two_places_with_thousands():
    assert tally.format_amount(Decimal("1234.5")) == "1,234.50"


def test_formats_negative():
    assert tally.format_amount(Decimal("-0.5")) == "-0.50"
