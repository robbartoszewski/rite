"""Ticket `gpu-slug`'s agreed verify. Fails until that ticket is delivered."""

import tally


def test_slugify_lowercases_and_joins_with_dashes():
    assert tally.slugify("  Rent  for March! ") == "rent-for-march"


def test_slugify_of_nothing_is_empty():
    assert tally.slugify("!!!") == ""
