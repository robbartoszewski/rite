from decimal import Decimal

import pytest
from tally import parse_amount


def test_parses_a_plain_amount():
    assert parse_amount(" 12.50 ") == Decimal("12.50")


@pytest.mark.parametrize("bad", ["", "abc", "NaN", "Infinity"])
def test_refuses_what_is_not_an_amount(bad):
    with pytest.raises(ValueError):
        parse_amount(bad)
